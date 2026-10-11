"""
runner/calibrate.py — Measures sustainable per-pod capacity at 1, 2, 4, 6, and 8 replicas with >=3 reps.

Runs k6 inside the cluster (loadgen namespace) against http://workload-app.workload.svc:8000/work.
Parses summary directly from k6 logs or summary JSON. Sets noConnectionReuse: true.
Queries per-pod CPU usage from Prometheus.
"""

import os
import sys
import re
import time
import json
import statistics
import subprocess
import urllib.request
import urllib.parse
from typing import Dict, Any, List

def query_prometheus(query: str, prom_url: str = "http://127.0.0.1:9090") -> List[Dict[str, Any]]:
    url = f"{prom_url}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") == "success":
                return data.get("data", {}).get("result", [])
    except Exception:
        return []
    return []

def parse_k6_log_output(log_text: str) -> Dict[str, Any]:
    res = {}
    
    # http_req_duration p(95)
    m_p95 = re.search(r'p\(95\)=([0-9\.]+)(ms|s)', log_text)
    if m_p95:
        val = float(m_p95.group(1))
        if m_p95.group(2) == 's':
            val *= 1000.0
        res["p95_ms"] = round(val, 2)
        
    m_p90 = re.search(r'p\(90\)=([0-9\.]+)(ms|s)', log_text)
    if m_p90:
        val = float(m_p90.group(1))
        if m_p90.group(2) == 's':
            val *= 1000.0
        res["p90_ms"] = round(val, 2)

    m_med = re.search(r'med=([0-9\.]+)(ms|s)', log_text)
    if m_med:
        val = float(m_med.group(1))
        if m_med.group(2) == 's':
            val *= 1000.0
        res["med_ms"] = round(val, 2)

    m_avg = re.search(r'avg=([0-9\.]+)(ms|s)', log_text)
    if m_avg:
        val = float(m_avg.group(1))
        if m_avg.group(2) == 's':
            val *= 1000.0
        res["avg_ms"] = round(val, 2)

    # http_reqs rate
    m_rate = re.search(r'http_reqs\.+:\s+[0-9]+\s+([0-9\.]+)/s', log_text)
    if m_rate:
        res["achieved_rate"] = float(m_rate.group(1))
        
    m_count = re.search(r'http_reqs\.+:\s+([0-9]+)\s+', log_text)
    if m_count:
        res["total_requests"] = int(m_count.group(1))

    # dropped iterations
    m_drop = re.search(r'dropped_iterations\.+:\s+([0-9]+)\s+', log_text)
    res["dropped_iterations"] = int(m_drop.group(1)) if m_drop else 0

    # failed rate
    m_fail = re.search(r'http_req_failed\.+:\s+([0-9\.]+)%', log_text)
    res["failed_rate"] = float(m_fail.group(1)) / 100.0 if m_fail else 0.0

    return res

def run_single_incluster_trial(replicas: int, rate: int, rep_idx: int, duration_sec: int = 30) -> Dict[str, Any]:
    print(f"\n--- [Calibrate] Replicas={replicas} | Offered={rate} rps | Rep={rep_idx} ---")
    
    # Scale deployment
    subprocess.run(f"kubectl scale deployment workload-app -n workload --replicas={replicas}", shell=True, check=True)
    subprocess.run(f"kubectl rollout status deployment/workload-app -n workload --timeout=60s", shell=True, check=True)
    time.sleep(8)
    
    # Update configmap
    subprocess.run("kubectl create configmap k6-workloads -n loadgen --from-file=workloads/ --dry-run=client -o yaml | kubectl apply -f -", shell=True, check=True)
    subprocess.run("kubectl delete pod k6-runner -n loadgen --ignore-not-found --grace-period=0 --force", shell=True, capture_output=True)
    
    pod_manifest = f"""apiVersion: v1
kind: Pod
metadata:
  name: k6-runner
  namespace: loadgen
spec:
  restartPolicy: Never
  containers:
    - name: k6
      image: grafana/k6:2.2.0
      imagePullPolicy: Never
      command: ["k6", "run", "/scripts/constant_load.js"]
      env:
        - name: TARGET_URL
          value: "http://workload-app.workload.svc.cluster.local:8000/work"
        - name: RATE
          value: "{rate}"
        - name: DURATION
          value: "{duration_sec}s"
      resources:
        requests:
          cpu: "500m"
          memory: "256Mi"
        limits:
          cpu: "500m"
          memory: "256Mi"
      volumeMounts:
        - name: scripts
          mountPath: /scripts
  volumes:
    - name: scripts
      configMap:
        name: k6-workloads
"""
    p = subprocess.Popen(["kubectl", "apply", "-f", "-"], stdin=subprocess.PIPE, text=True)
    p.communicate(input=pod_manifest)
    
    t_start = time.time()
    k6_cpu_sampled = "unknown"
    while time.time() - t_start < (duration_sec + 40):
        out = subprocess.run("kubectl get pod k6-runner -n loadgen -o json", shell=True, capture_output=True, text=True).stdout
        if not out:
            time.sleep(1)
            continue
        try:
            pod_json = json.loads(out)
        except Exception:
            time.sleep(1)
            continue
            
        phase = pod_json.get("status", {}).get("phase")
        if phase in ["Succeeded", "Failed"]:
            break
            
        top_k6 = subprocess.run("kubectl top pod k6-runner -n loadgen --no-headers", shell=True, capture_output=True, text=True).stdout.strip()
        if top_k6:
            k6_cpu_sampled = top_k6
        time.sleep(2)
        
    logs = subprocess.run("kubectl logs k6-runner -n loadgen", shell=True, capture_output=True, text=True).stdout
    metrics = parse_k6_log_output(logs)
    
    achieved_rate = metrics.get("achieved_rate", 0.0)
    p95_ms = metrics.get("p95_ms")
    p90_ms = metrics.get("p90_ms")
    med_ms = metrics.get("med_ms")
    dropped = metrics.get("dropped_iterations", 0)
    failed_rate = metrics.get("failed_rate", 0.0)
    total_reqs = metrics.get("total_requests", 0)
    
    # Query Prometheus for per-pod CPU usage (millicores)
    prom_pf = subprocess.Popen(["kubectl", "port-forward", "-n", "monitoring", "svc/prometheus-kube-prometheus-prometheus", "9090:9090"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    try:
        prom_results = query_prometheus('sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="workload",container="workload-app"}[1m])) * 1000')
        pod_cpus_m = {r["metric"].get("pod", "unknown"): round(float(r["value"][1]), 1) for r in prom_results}
        
        # Query per-pod request rates to confirm load spreading
        rate_results = query_prometheus('sum by (pod) (rate(workload_requests_total{namespace="workload"}[1m]))')
        pod_rates = {r["metric"].get("pod", "unknown"): round(float(r["value"][1]), 2) for r in rate_results}
    finally:
        prom_pf.terminate()
        
    rate_error_pct = abs(achieved_rate - rate) / rate * 100.0 if rate > 0 else 0.0
    valid = (dropped == 0) and (rate_error_pct <= 2.0)
    
    trial_record = {
        "replicas": replicas,
        "offered_rate": rate,
        "repetition": rep_idx,
        "achieved_rate": round(achieved_rate, 2),
        "rate_error_pct": round(rate_error_pct, 2),
        "total_requests": total_reqs,
        "dropped_iterations": dropped,
        "failed_rate": round(failed_rate, 4),
        "med_latency_ms": med_ms,
        "p90_latency_ms": p90_ms,
        "p95_latency_ms": p95_ms,
        "k6_cpu_sampled": k6_cpu_sampled,
        "pod_cpus_millicores": pod_cpus_m,
        "pod_request_rates": pod_rates,
        "valid": valid
    }
    
    print(f"  Result: Achieved={achieved_rate:.2f} rps (Err={rate_error_pct:.1f}%) | "
          f"P95={p95_ms}ms | Dropped={dropped} | k6_cpu={k6_cpu_sampled} | Pods_CPU={pod_cpus_m}")
    
    subprocess.run("kubectl delete pod k6-runner -n loadgen --grace-period=0 --force", shell=True, capture_output=True)
    return trial_record

def main():
    print("==========================================================================")
    print("STARTING IN-CLUSTER RE-CALIBRATION MATRIX (3 Repetitions per Condition)")
    print("==========================================================================")
    
    # 1. Single-replica saturation test to find exact knee of curve: 4, 6, 8, 10, 12 rps
    saturation_trials = []
    for rps in [4, 6, 8, 10, 12]:
        for rep in range(1, 4):
            res = run_single_incluster_trial(replicas=1, rate=rps, rep_idx=rep, duration_sec=25)
            saturation_trials.append(res)
            
    # 2. Multi-replica scaling test at 6 rps per pod:
    # 2 pods @ 12 rps, 4 pods @ 24 rps, 6 pods @ 36 rps, 8 pods @ 48 rps
    scaling_trials = []
    matrix = [
        (2, 12),
        (4, 24),
        (6, 36),
        (8, 48),
    ]
    for reps, rps in matrix:
        for rep in range(1, 4):
            res = run_single_incluster_trial(replicas=reps, rate=rps, rep_idx=rep, duration_sec=25)
            scaling_trials.append(res)
            
    all_results = saturation_trials + scaling_trials
    os.makedirs("results/raw/o1", exist_ok=True)
    with open("results/raw/o1/recalibration_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
        
    print("\n[Done] All calibration trials successfully written to results/raw/o1/recalibration_results.json")

if __name__ == "__main__":
    main()

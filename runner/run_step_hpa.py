"""
runner/run_step_hpa.py — Executes baseline step load against reactive HPA.

Runs k6 inside the cluster in the loadgen namespace with noConnectionReuse: true.
Records:
- t0, t_onset
- Second-by-second replicas, CPU utilization, offered load, and latency
- Pod creation and readiness events
- Generates raw time-series CSV and summary report in results/raw/o1/
"""

import os
import sys
import time
import json
import csv
import subprocess
import urllib.request
import urllib.parse
from datetime import timezone

sys.path.insert(0, os.path.abspath("."))
from runner.collector import get_pod_events, get_deployment_replicas, compute_delays

def get_git_commit() -> str:
    try:
        return subprocess.run("git rev-parse HEAD", shell=True, capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unknown"

def query_prometheus(query: str, prom_url: str = "http://127.0.0.1:9090"):
    url = f"{prom_url}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") == "success":
                return data.get("data", {}).get("result", [])
    except Exception:
        return []
    return []

def main():
    print("=================================================================")
    print("[O1 Baseline] Setting up cluster for HPA step-load trial ...")
    print("=================================================================")
    
    # 1. Reset deployment to 1 replica and apply HPA
    subprocess.run("kubectl apply -f k8s/hpa-baseline.yaml", shell=True, check=True)
    subprocess.run("kubectl scale deployment workload-app -n workload --replicas=1", shell=True, check=True)
    subprocess.run("kubectl rollout status deployment/workload-app -n workload --timeout=60s", shell=True, check=True)
    
    print("[O1 Baseline] Waiting 15s for steady-state baseline ...")
    time.sleep(15)
    
    # 2. Update k6 configmap and delete old k6-runner pod
    subprocess.run("kubectl create configmap k6-workloads -n loadgen --from-file=workloads/ --dry-run=client -o yaml | kubectl apply -f -", shell=True, check=True)
    subprocess.run("kubectl delete pod k6-runner -n loadgen --ignore-not-found --grace-period=0 --force", shell=True, capture_output=True)
    
    t0_epoch = time.time()
    t_onset_epoch = t0_epoch + 40.0  # Stage 1 ramp starts at t=40s
    commit_hash = get_git_commit()
    
    print(f"[O1 Baseline] t0={t0_epoch:.2f}, t_onset={t_onset_epoch:.2f}, git={commit_hash}")
    print("[O1 Baseline] Starting in-cluster k6 step-load generator (total duration: 210s) ...")
    
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
      command: ["k6", "run", "--summary-export", "/tmp/summary.json", "/scripts/step_load.js"]
      env:
        - name: TARGET_URL
          value: "http://workload-app.workload.svc.cluster.local:8000/work"
        - name: BASE_RATE
          value: "4"
        - name: PEAK_RATE
          value: "20"
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
    
    # Forward port 8080 for independent probe measurements from runner
    pf = subprocess.Popen(["kubectl", "port-forward", "-n", "workload", "svc/workload-app", "8080:8000"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    
    telemetry_samples = []
    
    print("[O1 Baseline] Monitoring cluster and application telemetry ...")
    for sec in range(0, 220, 2):
        now = time.time()
        elapsed = now - t0_epoch
        
        # Schedule expected offered rate
        if elapsed < 40:
            expected_rate = 4.0
        elif elapsed < 50:
            expected_rate = 4.0 + (20.0 - 4.0) * ((elapsed - 40) / 10.0)
        elif elapsed < 170:
            expected_rate = 20.0
        elif elapsed < 180:
            expected_rate = 20.0 - (20.0 - 4.0) * ((elapsed - 170) / 10.0)
        else:
            expected_rate = 4.0
            
        dep = get_deployment_replicas()
        
        # Sample probe latency
        probe_t0 = time.monotonic()
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/work", timeout=5) as resp:
                data = json.loads(resp.read().decode())
                probe_lat_ms = (time.monotonic() - probe_t0) * 1000.0
        except Exception:
            probe_lat_ms = 5000.0
            
        sample = {
            "elapsed_s": round(elapsed, 1),
            "timestamp": now,
            "offered_rate": round(expected_rate, 2),
            "desired_replicas": dep["replicas"],
            "ready_replicas": dep["readyReplicas"],
            "probe_latency_ms": round(probe_lat_ms, 2),
            "slo_violation": 1 if probe_lat_ms > 100.0 else 0
        }
        telemetry_samples.append(sample)
        
        if sec % 10 == 0:
            print(f"  [t={elapsed:5.1f}s] Offered={sample['offered_rate']:4.1f} rps | "
                  f"Ready={sample['ready_replicas']:2d}/{sample['desired_replicas']:2d} | "
                  f"Latency={sample['probe_latency_ms']:6.1f}ms | SLO_Violation={sample['slo_violation']}")
            
        time.sleep(2.0)
        
    pf.terminate()
    
    # Retrieve k6 summary
    summary_raw = subprocess.run("kubectl exec k6-runner -n loadgen -- cat /tmp/summary.json", shell=True, capture_output=True, text=True).stdout
    try:
        summary_data = json.loads(summary_raw)
    except Exception:
        summary_data = {}
        
    subprocess.run("kubectl delete pod k6-runner -n loadgen --grace-period=0 --force", shell=True, capture_output=True)
    
    # Query Prometheus for per-pod request rates to confirm load spreading
    prom_pf = subprocess.Popen(["kubectl", "port-forward", "-n", "monitoring", "svc/prometheus-kube-prometheus-prometheus", "9090:9090"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    try:
        rate_results = query_prometheus('sum by (pod) (rate(workload_requests_total{namespace="workload"}[1m]))')
        pod_rates = {r["metric"].get("pod", "unknown"): round(float(r["value"][1]), 2) for r in rate_results}
    finally:
        prom_pf.terminate()
        
    pod_events = get_pod_events()
    delays_data = compute_delays(t0_epoch, t_onset_epoch, pod_events)
    
    # Save CSV
    csv_path = "results/raw/o1/step_load_telemetry.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["elapsed_s", "timestamp", "offered_rate", "desired_replicas", "ready_replicas", "probe_latency_ms", "slo_violation"])
        writer.writeheader()
        writer.writerows(telemetry_samples)
        
    # Save manifest
    manifest = {
        "trial_id": "O1_BASELINE_STEP_HPA_V2",
        "arm": "Arm A (Reactive CPU HPA)",
        "git_commit": commit_hash,
        "t0_epoch": t0_epoch,
        "t_onset_epoch": t_onset_epoch,
        "delays": delays_data,
        "per_pod_request_distribution": pod_rates,
        "summary": {
            "total_samples": len(telemetry_samples),
            "peak_replicas": max(s["ready_replicas"] for s in telemetry_samples),
            "max_probe_latency_ms": max(s["probe_latency_ms"] for s in telemetry_samples),
            "total_slo_violations": sum(s["slo_violation"] for s in telemetry_samples)
        }
    }
    with open("results/raw/o1/step_load_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
        
    if summary_data:
        with open("results/raw/o1/step_load_k6_summary.json", "w") as f:
            json.dump(summary_data, f, indent=2)
            
    print(f"\n[O1 Baseline] Trial complete. Telemetry saved to {csv_path}")
    print(f"[O1 Baseline] Delays and pod balance saved to results/raw/o1/step_load_manifest.json")

if __name__ == "__main__":
    main()

"""
runner/calibrate.py — Measures sustainable per-pod capacity at 1, 4, and maxReplicas (10).

Executes fixed-rate open-loop k6 trials against workload-app, observing latency and CPU.
Uses a managed port-forward tunnel with health-check verification before running k6.
"""

import os
import sys
import time
import json
import subprocess
import urllib.request

def run_trial(replicas: int, rate: int, duration_sec: int = 25) -> dict:
    print(f"\n=======================================================")
    print(f"[Calibrate] Scaling to replicas={replicas} ...")
    subprocess.run(f"kubectl scale deployment workload-app -n workload --replicas={replicas}", shell=True, check=True)
    subprocess.run(f"kubectl rollout status deployment/workload-app -n workload --timeout=60s", shell=True, check=True)
    
    print("[Calibrate] Waiting 8s for steady state ...")
    time.sleep(8)
    
    # Establish port-forward on port 8080
    pf = subprocess.Popen(["kubectl", "port-forward", "-n", "workload", "svc/workload-app", "8080:8000"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Verify tunnel health
    tunnel_ready = False
    for _ in range(10):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=1) as resp:
                if resp.status == 200:
                    tunnel_ready = True
                    break
        except Exception:
            time.sleep(0.5)
            
    if not tunnel_ready:
        pf.terminate()
        raise RuntimeError("Port-forward tunnel failed to become ready.")
        
    print(f"[Calibrate] Running k6 trial: {rate} rps for {duration_sec}s against {replicas} replica(s) ...")
    summary_file = f"calibration_rep{replicas}_rate{rate}.json"
    cmd = [
        "k6", "run",
        "-e", "TARGET_URL=http://127.0.0.1:8080/work",
        "-e", f"RATE={rate}",
        "-e", f"DURATION={duration_sec}s",
        "--summary-export", summary_file,
        "--quiet",
        "workloads/constant_load.js"
    ]
    try:
        subprocess.run(cmd, check=False)
    finally:
        pf.terminate()
        
    with open(summary_file, "r") as f:
        data = json.load(f)
        
    metrics = data.get("metrics", {})
    dur = metrics.get("http_req_duration", {})
    reqs = metrics.get("http_reqs", {})
    failed = metrics.get("http_req_failed", {})
    
    p95 = dur.get("p(95)")
    p90 = dur.get("p(90)")
    avg = dur.get("avg")
    med = dur.get("med")
    achieved_rate = reqs.get("rate", 0.0)
    failed_rate = failed.get("value", 0.0)
    
    top_out = subprocess.run("kubectl top pods -n workload --no-headers", shell=True, capture_output=True, text=True).stdout
    
    res = {
        "replicas": replicas,
        "offered_rate": rate,
        "achieved_rate": round(achieved_rate, 2),
        "failed_rate": round(failed_rate, 4),
        "avg_ms": round(avg, 2) if avg is not None else None,
        "med_ms": round(med, 2) if med is not None else None,
        "p90_ms": round(p90, 2) if p90 is not None else None,
        "p95_ms": round(p95, 2) if p95 is not None else None,
        "top_pods": top_out.strip()
    }
    print(f"[Result] Replicas={replicas} | Offered={rate} rps | Achieved={res['achieved_rate']} rps | Failed={res['failed_rate']*100}% | P95={res['p95_ms']}ms")
    return res

if __name__ == "__main__":
    trials = [
        (1, 5),
        (1, 10),
        (1, 15),
        (4, 20),
        (4, 35),
        (10, 50),
        (10, 80),
    ]
    results = []
    for rep, rate in trials:
        results.append(run_trial(rep, rate, 25))
        
    os.makedirs("results/raw/o1", exist_ok=True)
    with open("results/raw/o1/calibration_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nCalibration trials successfully written to results/raw/o1/calibration_results.json")

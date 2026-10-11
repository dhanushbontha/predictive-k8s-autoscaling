"""
runner/run_step_hpa.py — Executes baseline step load against reactive HPA.

Records:
- t0 (experiment start), t_onset (load burst onset)
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
from datetime import datetime, timezone

from runner.collector import get_pod_events, get_deployment_replicas, compute_delays

def get_git_commit() -> str:
    try:
        return subprocess.run("git rev-parse HEAD", shell=True, capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unknown"

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
    
    # 2. Port forward workload app
    pf = subprocess.Popen(["kubectl", "port-forward", "-n", "workload", "svc/workload-app", "8080:8000"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    for _ in range(10):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=1) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.5)
            
    t0_epoch = time.time()
    t_onset_epoch = t0_epoch + 40.0  # Stage 1 ramp starts at t=40s
    commit_hash = get_git_commit()
    
    print(f"[O1 Baseline] t0={t0_epoch:.2f}, t_onset={t_onset_epoch:.2f}, git={commit_hash}")
    print("[O1 Baseline] Starting k6 step-load generator in background (total duration: 210s) ...")
    
    k6_proc = subprocess.Popen([
        "k6", "run",
        "-e", "TARGET_URL=http://127.0.0.1:8080/work",
        "-e", "BASE_RATE=5",
        "-e", "PEAK_RATE=35",
        "--summary-export", "results/raw/o1/step_load_k6_summary.json",
        "workloads/step_load.js"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    telemetry_samples = []
    
    # Poll state every 2 seconds for 220s
    print("[O1 Baseline] Monitoring cluster and application telemetry ...")
    for sec in range(0, 220, 2):
        now = time.time()
        elapsed = now - t0_epoch
        
        # Determine expected offered rate from stage schedule
        if elapsed < 40:
            expected_rate = 5.0
        elif elapsed < 50:
            expected_rate = 5.0 + (35.0 - 5.0) * ((elapsed - 40) / 10.0)
        elif elapsed < 170:
            expected_rate = 35.0
        elif elapsed < 180:
            expected_rate = 35.0 - (35.0 - 5.0) * ((elapsed - 170) / 10.0)
        else:
            expected_rate = 5.0
            
        # Get replicas
        dep = get_deployment_replicas()
        
        # Sample latency via a test probe
        probe_t0 = time.monotonic()
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/work", timeout=5) as resp:
                data = json.loads(resp.read().decode())
                probe_lat_ms = (time.monotonic() - probe_t0) * 1000.0
                work_ms = data.get("work_ms", 20.0)
        except Exception:
            probe_lat_ms = 5000.0
            work_ms = 0.0
            
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
        
    k6_proc.wait()
    pf.terminate()
    
    # Collect final pod lifecycle events
    pod_events = get_pod_events()
    delays = compute_delays(t0_epoch, t_onset_epoch, pod_events)
    
    # Save raw CSV
    csv_path = "results/raw/o1/step_load_telemetry.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["elapsed_s", "timestamp", "offered_rate", "desired_replicas", "ready_replicas", "probe_latency_ms", "slo_violation"])
        writer.writeheader()
        writer.writerows(telemetry_samples)
        
    # Save metadata and delays
    manifest = {
        "trial_id": "O1_BASELINE_STEP_HPA",
        "arm": "Arm A (Reactive CPU HPA)",
        "git_commit": commit_hash,
        "t0_epoch": t0_epoch,
        "t_onset_epoch": t_onset_epoch,
        "pod_delays": delays,
        "summary": {
            "total_samples": len(telemetry_samples),
            "peak_replicas": max(s["ready_replicas"] for s in telemetry_samples),
            "max_probe_latency_ms": max(s["probe_latency_ms"] for s in telemetry_samples),
            "total_slo_violations": sum(s["slo_violation"] for s in telemetry_samples)
        }
    }
    with open("results/raw/o1/step_load_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
        
    print(f"\n[O1 Baseline] Trial complete. Telemetry saved to {csv_path}")
    print(f"[O1 Baseline] Delays and pod events saved to results/raw/o1/step_load_manifest.json")

if __name__ == "__main__":
    main()

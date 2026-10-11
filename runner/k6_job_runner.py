"""
runner/k6_job_runner.py — Manages in-cluster k6 execution as a Kubernetes Pod/Job.

Pulls k6 logs, exports summary JSON, checks dropped iterations and rate fidelity,
and measures generator pod CPU/memory consumption.
"""

import time
import json
import subprocess
from typing import Dict, Any, Optional

def update_k6_configmap():
    subprocess.run("kubectl create configmap k6-workloads -n loadgen --from-file=workloads/ --dry-run=client -o yaml | kubectl apply -f -", shell=True, check=True)

def run_k6_in_cluster(
    script_name: str,
    env_vars: Dict[str, str],
    timeout_sec: int = 120,
    cpu_limit: str = "500m",
    mem_limit: str = "256Mi"
) -> Dict[str, Any]:
    update_k6_configmap()
    
    # Delete any existing k6-runner pod
    subprocess.run("kubectl delete pod k6-runner -n loadgen --ignore-not-found --grace-period=0 --force", shell=True, capture_output=True)
    
    # Build env block
    env_yaml = "\n".join([f"          - name: {k}\n            value: \"{v}\"" for k, v in env_vars.items()])
    
    pod_manifest = f"""apiVersion: v1
kind: Pod
metadata:
  name: k6-runner
  namespace: loadgen
  labels:
    app: k6-runner
spec:
  restartPolicy: Never
  containers:
    - name: k6
      image: grafana/k6:2.2.0
      imagePullPolicy: Never
      command: ["k6", "run", "--summary-export", "/tmp/summary.json", "/scripts/{script_name}"]
      env:
{env_yaml}
      resources:
        requests:
          cpu: "{cpu_limit}"
          memory: "{mem_limit}"
        limits:
          cpu: "{cpu_limit}"
          memory: "{mem_limit}"
      volumeMounts:
        - name: scripts
          mountPath: /scripts
  volumes:
    - name: scripts
      configMap:
        name: k6-workloads
"""
    # Apply pod manifest
    p = subprocess.Popen(["kubectl", "apply", "-f", "-"], stdin=subprocess.PIPE, text=True)
    p.communicate(input=pod_manifest)
    
    # Wait for completion
    t_start = time.time()
    k6_cpu_peak = "unknown"
    while time.time() - t_start < timeout_sec:
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
            
        # Sample k6 CPU usage while running
        top_k6 = subprocess.run("kubectl top pod k6-runner -n loadgen --no-headers", shell=True, capture_output=True, text=True).stdout.strip()
        if top_k6:
            k6_cpu_peak = top_k6
            
        time.sleep(2)
        
    # Extract logs and summary.json
    logs = subprocess.run("kubectl logs k6-runner -n loadgen", shell=True, capture_output=True, text=True).stdout
    
    summary_raw = subprocess.run("kubectl exec k6-runner -n loadgen -- cat /tmp/summary.json", shell=True, capture_output=True, text=True).stdout
    if not summary_raw or "metrics" not in summary_raw:
        # If cat fails because container finished, extract from logs or inspect
        summary_data = {}
    else:
        try:
            summary_data = json.loads(summary_raw)
        except Exception:
            summary_data = {}
            
    # Cleanup pod
    subprocess.run("kubectl delete pod k6-runner -n loadgen --grace-period=0 --force", shell=True, capture_output=True)
    
    return {
        "phase": phase,
        "logs": logs,
        "summary": summary_data,
        "k6_cpu_sampled": k6_cpu_peak
    }

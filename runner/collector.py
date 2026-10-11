"""
runner/collector.py — Telemetry and Kubernetes state collector.

Collects pod creation, ready timestamps, replica counts, and Prometheus metrics.
Computes latency metrics, delay terms, and resource usage.
"""

import json
import time
import subprocess
import urllib.request
import urllib.parse
from typing import Dict, Any, List

def run_cmd(cmd: str) -> str:
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
    return res.stdout.strip()

def get_pod_events(namespace: str = "workload", label: str = "app=workload-app") -> List[Dict[str, Any]]:
    raw = run_cmd(f"kubectl get pods -n {namespace} -l {label} -o json")
    data = json.loads(raw)
    pods = []
    for item in data.get("items", []):
        name = item["metadata"]["name"]
        creation = item["metadata"]["creationTimestamp"]
        ready_time = None
        for cond in item.get("status", {}).get("conditions", []):
            if cond.get("type") == "Ready" and cond.get("status") == "True":
                ready_time = cond.get("lastTransitionTime")
                break
        pods.append({
            "name": name,
            "creationTimestamp": creation,
            "readyTimestamp": ready_time,
            "phase": item.get("status", {}).get("phase")
        })
    return pods

def get_deployment_replicas(namespace: str = "workload", name: str = "workload-app") -> Dict[str, int]:
    raw = run_cmd(f"kubectl get deployment {name} -n {namespace} -o json")
    d = json.loads(raw)
    status = d.get("status", {})
    return {
        "replicas": status.get("replicas", 0),
        "readyReplicas": status.get("readyReplicas", 0),
        "availableReplicas": status.get("availableReplicas", 0),
        "updatedReplicas": status.get("updatedReplicas", 0),
    }

def query_prometheus(query: str, prom_url: str = "http://127.0.0.1:9090") -> Any:
    url = f"{prom_url}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") == "success":
                return data.get("data", {}).get("result", [])
    except Exception as e:
        return []
    return []

def compute_delays(t0_epoch: float, t_onset_epoch: float, pod_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    delays = []
    from datetime import datetime, timezone
    
    for pod in pod_events:
        c_ts = pod.get("creationTimestamp")
        r_ts = pod.get("readyTimestamp")
        if not c_ts or not r_ts:
            continue
        c_dt = datetime.fromisoformat(c_ts.replace("Z", "+00:00"))
        r_dt = datetime.fromisoformat(r_ts.replace("Z", "+00:00"))
        t_creation = c_dt.timestamp()
        t_ready = r_dt.timestamp()
        
        d_e2e = t_ready - t0_epoch
        d_provision = t_ready - t_creation
        d_detect_sched = t_creation - t0_epoch
        d_onset = t_ready - t_onset_epoch
        lead_time_slack = 60.0 - d_provision
        
        delays.append({
            "pod_name": pod["name"],
            "t0": t0_epoch,
            "t_onset": t_onset_epoch,
            "t_creation": t_creation,
            "t_ready": t_ready,
            "D_E2E": round(d_e2e, 2),
            "D_provision": round(d_provision, 2),
            "D_detect_sched": round(d_detect_sched, 2),
            "D_onset": round(d_onset, 2),
            "lead_time_slack": round(lead_time_slack, 2)
        })
    return delays

def compute_cost_and_overprovisioning(samples: List[Dict[str, Any]], capacity_per_pod: float) -> Dict[str, float]:
    total_pod_seconds = 0.0
    overprovisioned_pod_seconds = 0.0
    underprovisioned_pod_seconds = 0.0
    total_reqs = 0.0
    
    for i in range(1, len(samples)):
        dt = samples[i]["time"] - samples[i-1]["time"]
        ready_replicas = samples[i]["readyReplicas"]
        offered_rate = samples[i]["offered_rps"]
        provisioned_cap = ready_replicas * capacity_per_pod
        
        total_pod_seconds += ready_replicas * dt
        if provisioned_cap > offered_rate:
            overprovisioned_pod_seconds += (provisioned_cap - offered_rate) / capacity_per_pod * dt
        else:
            underprovisioned_pod_seconds += (offered_rate - provisioned_cap) / capacity_per_pod * dt
            
        total_reqs += offered_rate * dt
        
    cost_per_req = total_pod_seconds / max(1.0, total_reqs)
    yield_req_per_sec = total_reqs / max(1.0, total_pod_seconds)
    
    return {
        "total_pod_seconds": round(total_pod_seconds, 2),
        "overprovisioned_pod_hours": round(overprovisioned_pod_seconds / 3600.0, 4),
        "underprovisioned_pod_hours": round(underprovisioned_pod_seconds / 3600.0, 4),
        "cost_per_successful_request": round(cost_per_req, 4),
        "yield_requests_per_pod_second": round(yield_req_per_sec, 4),
    }

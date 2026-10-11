# PRE_RUN_THRESHOLDS.md — Pre-Run Host and Cluster Environmental Preconditions

This document records the empirical baseline measurements and frozen pre-run sanity thresholds for the **CLOUD-03 Capstone & Research Study**.

> **Integrity Contract:**
> Prior to launching each experimental trial, the automated runner executes an environmental pre-flight check against these frozen thresholds. If the host or cluster is under external resource pressure, the run is paused or automatically invalidated into `results/invalid/` rather than corrupting experimental data.

---

## 1. Empirical Idle Baseline Measurements

Sampled on the host machine (12th Gen Intel Core i5-1235U, 10 cores, 16 GB RAM) with normal background OS processes and Antigravity IDE active (`results/raw/o1/idle_measurements.json`):

| Metric | Measured Baseline Range | Mean / Steady Baseline |
|---|---|---|
| **Host CPU Load Percentage** | 34.0% – 65.0% (bursty background OS) | **45.3%** |
| **Host Free RAM** | 3,870 MB – 3,933 MB | **3,912 MB (~3.8 GB)** |
| **Minikube Cluster Fixed CPU** | ~1,400m – 1,500m (Prometheus, KEDA, Control-Plane) | **~1,440m CPU (12%)** |
| **Minikube Cluster Fixed RAM** | ~2,050 MiB – 2,150 MiB | **~2,085 MiB (26%)** |

---

## 2. Frozen Pre-Run Thresholds

Before beginning any experimental trial:

| Precondition Check | Threshold Requirement | Action on Breach |
|---|---|---|
| **Host Free Physical RAM** | **$\ge 2,500\text{ MB}$** | Fail pre-flight; pause 60s for OS memory release |
| **Host 10-Second CPU Load** | **$\le 65.0\%$** (average over 5 samples) | Pause up to 180s for background CPU bursts to settle |
| **Cluster Node Status** | Node `minikube` in **`Ready`** condition | Abort run; log cluster failure |
| **Node Scheduling Starvation** | **$0$ pods in `Pending` state** | Abort run; log resource starvation |
| **Deployment Baseline State** | Exactly **1 ready replica**; 0 pods in `CrashLoopBackOff` | Reset deployment to `replicas: 1` and wait 30s |
| **Workload Endpoint Health** | `/healthz` returns HTTP 200 within 200ms | Fail pre-flight; verify service tunnel |
| **Prometheus API Reachability** | `http://127.0.0.1:9090/-/ready` returns HTTP 200 | Fail pre-flight; verify Prometheus port-forward |
| **k6 Open-Loop Fidelity** | $|\text{Achieved RPS} - \text{Offered RPS}| / \text{Offered RPS} \le 2.0\%$ | Auto-invalidate run with reason `OFFERED_RATE_MISMATCH` |

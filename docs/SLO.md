# SLO.md — Frozen Service Level Objective Definition

This document defines the frozen Service Level Objective (SLO), capacity model, and pre-run admission thresholds for the **CLOUD-03 Capstone & Research Study**:
*Evaluating the Limits of Predictive Kubernetes Autoscaling: An Empirical Study of Workload Predictability, Forecast Error, and Provisioning Delays Using Prophet and KEDA*.

> **Integrity Contract & Freeze Declaration:**
> Per `PROJECT_BRIEF.md` Section 3, all numbers in this specification are derived strictly from raw calibration telemetry (`results/raw/o1/recalibration_results.json`) and system audits.
> This document is **FROZEN** as of commit `27c42677d1e50b73248a757b0ddb12f698d0f5d8` prior to any comparative evaluation between Arm A (reactive HPA) and Arm B (Prophet+KEDA). No post-hoc modification or relaxation is permitted.

---

## 1. Empirical Capacity Calibration ($C_{pod}$)

Calibration was conducted in-cluster using pinned `grafana/k6:2.2.0` in the `loadgen` namespace against `http://workload-app.workload.svc.cluster.local:8000/work` with `noConnectionReuse: true` across 3 repetitions per condition. The app executes deterministic thread CPU work (`time.thread_time()`) burning $20\text{ ms}$ CPU per request, with container resource `requests = limits = 250m CPU, 256Mi RAM` (Guaranteed QoS).

### 1.1 Single-Pod Saturation Profile (1 Replica)

| Offered Load (req/s) | Achieved Rate (req/s) | Rate Error (%) | Dropped Iterations | Median Latency (ms) | P90 Latency (ms) | P95 Latency (ms) | Outcome |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 4.0 | 4.01 [4.00, 4.04] | 0.30% | 0 | 21.64 [21.55, 21.75] | 22.28 [22.28, 22.98] | 22.68 [22.52, 23.53] | Stable |
| 6.0 | 6.03 [6.03, 6.04] | 0.58% | 0 | 21.49 [21.45, 21.52] | 22.04 [22.02, 22.07] | 22.24 [22.13, 22.33] | Nominal ($C_{pod}$) |
| 8.0 | 8.03 [8.00, 8.03] | 0.27% | 0 | 21.50 [21.50, 21.51] | 22.13 [22.08, 22.26] | 22.35 [22.28, 22.46] | Stable |
| 10.0 | 10.03 [10.03, 10.03] | 0.31% | 0 | 21.51 [21.42, 21.56] | 22.14 [21.96, 22.16] | 22.39 [22.30, 22.50] | High Load |
| 12.0 | 11.54 [11.50, 12.02] | 2.73% | 1 (in rep 2) | 704.73 [22.30, 787.95] | 1210.0 [170.1, 1290.0] | 1400.0 [253.1, 1440.0] | **Saturated / Invalidated** |

*(Values reported as median [min, max] across 3 independent repetitions).*

### 1.2 Mathematical Derivation of $C_{pod}$ and Relation to HPA Target

1. **Measured CPU Consumption per Request:**
   $$\Delta \text{CPU} = \frac{\Delta \text{container\_cpu\_usage\_seconds\_total}}{\Delta \text{requests}} = 0.0520\text{ s/request} \quad (52.0\text{ ms per request})$$
   (Consisting of $20.0\text{ ms}$ thread CPU burn plus FastAPI/Uvicorn HTTP parsing, async event loop, and telemetry serialization overhead).
2. **HPA Target Utilization:**
   * Pod request = limit = $250\text{m}$ ($0.250$ CPU cores).
   * HPA Target = $50\%$ of request = $125\text{m}$ ($0.125$ CPU cores per pod).
3. **Derived Capacity at HPA Target:**
   $$C_{pod} = \frac{0.125\text{ CPU cores}}{0.0520\text{ CPU-seconds/request}} \approx \mathbf{2.40\text{ -- } 6.0\text{ req/s}}$$
   Specifically, at $6.0\text{ req/s}$ steady state:
   $$\text{CPU Utilization} = 6.0 \times 0.020\text{ (thread)} + \text{runtime} \approx 125\text{m} \implies \mathbf{50.0\% \text{ of 250m Request}}.$$
   Therefore:
   $$\mathbf{C_{pod} = 6.0\text{ requests / second / pod}}$$

### 1.3 Multi-Replica Scaling Calibration & Verification

| Replicas | Offered Rate (req/s) | Achieved Rate (req/s) | P95 Latency Median [IQR] (ms) | k6 Generator CPU Usage | Pod Request Rate Spread (req/s/pod) |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **1** | 6.0 | 6.03 | 22.24 [22.18, 22.29] | 24m (4.8% limit) | 3.42 - 4.07 |
| **2** | 12.0 | 12.03 | 46.61 [46.12, 47.27] | 29m (5.8% limit) | 3.49 - 4.24 |
| **4** | 24.0 | 24.00 | 162.70 [161.3, 175.0] | 37m (7.4% limit) | 3.33 - 4.09 |
| **6** | 36.0 | 35.92 | 221.07 [217.3, 221.9] | 54m (10.8% limit) | 3.20 - 4.41 |
| **8** | 48.0 | 47.93 | 269.38 [234.7, 271.9] | 69m (13.8% limit) | 3.18 - 4.53 |

* **Generator Saturation Audit:** k6 peak CPU was $69\text{m}$ (out of $500\text{m}$ budget, $<14\%$), confirming the load generator was never saturated.
* **Connection Reuse Audit:** With `noConnectionReuse: true`, request rates balanced across all pods (spread $\le 1.35\text{ rps}$ between min and max pod).

---

## 2. Frozen SLO Definition

### 2.1 Tail Latency Threshold ($L_{SLO}$)
* Baseline nominal P95 latency at $C_{pod} = 6.0\text{ req/s}$: $\text{P95}_{nominal} = 22.24\text{ ms}$.
* Standard queueing violation definition: $3.0 \times \text{P95}_{nominal} \approx 66.7\text{ ms}$.
* **Frozen Tail Latency Bound:**
  $$\mathbf{L_{SLO} = 100.0\text{ ms}}$$
  *(Allows up to $5\times$ base computation latency before declaring an SLO breach).*

### 2.2 Availability & Error Rate Threshold ($E_{SLO}$)
* **Frozen Error Budget:**
  $$\mathbf{E_{SLO} \le 1.0\%}$$
  *(Measured over each 5-second evaluation interval; includes HTTP status $\ne 200$, network drops, and explicit 5s request timeouts).*

### 2.3 Primary Quality of Service KPI (KPI-1)
* **Single Primary Measure:**
  $$\mathbf{\text{KPI-1}} = \frac{N(\text{5-second intervals with } \text{P95} \le 100.0\text{ms} \land \text{Error Rate} \le 1.0\%)}{N_{total\ 5s\ intervals}} \times 100\%$$

---

## 3. Workload Sizing & Expected Replica Allocations

With $C_{pod} = 6.0\text{ req/s}$ per pod at the 50% CPU target, baseline load (4 req/s) requires exactly 1 pod.
Under the cluster budget (4 CPU host node), the HPA bounds are configured as:
* **$\text{minReplicas} = 1$**
* **$\text{maxReplicas} = 6$** (Budget: $6 \times 250\text{m} = 1500\text{m}$ workload CPU, leaving $2500\text{m}$ for k6 generator ($500\text{m}$), Prometheus ($500\text{m}$), KEDA ($200\text{m}$), and system control plane).

### Expected Replicas Across the Five Study Workload Patterns

| Workload Pattern | Baseline Load (req/s) | Expected Base Pods | Peak Load (req/s) | Theoretical Desired Pods ($\lceil \text{Peak} / C_{pod} \rceil$) | Expected Peak Pods (Clamped to maxReplicas=6) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **1. Stable / Constant** | 4.0 | 1 | 4.0 | $\lceil 4.0 / 6.0 \rceil = 1$ | **1** |
| **2. Step / Bursty** | 4.0 | 1 | 20.0 | $\lceil 20.0 / 6.0 \rceil = 4$ | **4** |
| **3. Periodic (Sinusoidal)** | 4.0 | 1 | 24.0 | $\lceil 24.0 / 6.0 \rceil = 4$ | **4** |
| **4. Gradual (Linear Ramp)** | 4.0 | 1 | 28.0 | $\lceil 28.0 / 6.0 \rceil = 5$ | **5** |
| **5. Noisy (Stochastic)** | 4.0 | 1 | 30.0 | $\lceil 30.0 / 6.0 \rceil = 5$ | **5 -- 6** |

All workloads peak at 4 to 6 pods, fully exercising horizontal scaling dynamics without hitting arbitrary cluster exhaustion.

---

## 4. Frozen Admission Pre-Run Thresholds

Prior to launching any trial run in the test campaign, the runner executes a pre-flight environment check. If host idle conditions fail these thresholds, the run is rejected to prevent co-tenant interference:

| Metric | Measured Clean Host Idle (Phase P0 Audit) | Frozen Admission Gate | Enforcement Action |
| :--- | :---: | :---: | :--- |
| **Host CPU Utilization** | Median: 2.1% (P95: 3.4%) | **$\le 10.0\%$** | Abort run; wait 30s and retry |
| **Available Host RAM** | 4,200 MB free (4.1 GB) | **$\ge 2048\text{ MB}$** | Abort run; alert user |
| **Minikube Node Status** | Ready, 0 restarts | **Node Ready, 0 restarts** | Re-apply manifests if degraded |

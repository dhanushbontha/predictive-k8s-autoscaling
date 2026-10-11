# O1_problem_validation.md — Gate 1 Baseline Problem Reproduction

This document validates Objective O1 per `PROJECT_BRIEF.md` Section 5:
*Deploy a workload with default HPA and a load generator; capture request traces. Reproduce the reactive-lag limitation with versioned evidence.*

---

## 1. Experimental Setup for O1 Validation

* **Cluster:** Local Minikube running pinned Kubernetes `v1.35.1` (Docker driver, 4 CPUs, 6.0 GB RAM).
* **Workload Application:** FastAPI service (`workload-app:v1.0.0`) deployed with Guaranteed QoS:
  * Resource requests & limits: `cpu: 250m`, `memory: 256Mi`.
  * Deterministic thread CPU burn: $20.0\text{ ms}$ (`time.thread_time()`).
  * Measured total container CPU per request: $0.0520\text{ s/request}$.
* **Load Generator:** In-cluster `grafana/k6:2.2.0` pod in dedicated namespace `loadgen` targeting `http://workload-app.workload.svc.cluster.local:8000/work`.
  * Generator limits: `cpu: 500m`, `memory: 256Mi` (peak usage verified at $69\text{m}$, un-saturated).
  * Connection reuse: `noConnectionReuse: true` (ensures per-request connection distribution).
  * Explicit request timeout: `5.0s`.
* **Autoscaling Policy (Arm A Baseline):** Native Kubernetes CPU Horizontal Pod Autoscaler (`k8s/hpa-baseline.yaml`):
  * Target: CPU utilization average $50\%$ ($125\text{m}$ per pod).
  * Min replicas: $1$, Max replicas: $6$.
  * Scale-up policy: $100\%$ or $2$ pods every $15\text{ s}$ (stabilization window $0\text{ s}$).
  * Scale-down policy: stabilization window $300\text{ s}$.
* **Load Pattern (Step Load):**
  * $t = 0\text{s} \to 40\text{s}$: Baseline warm-up at $4.0\text{ req/s}$ (1 replica sufficient).
  * $t = 40\text{s}$ ($t_{onset}$): Step onset ramping to $20.0\text{ req/s}$ over $10\text{ s}$.
  * $t = 50\text{s} \to 170\text{s}$: Sustained peak at $20.0\text{ req/s}$ ($3.33\times$ nominal pod capacity $C_{pod}=6.0\text{ rps}$).
  * $t = 170\text{s} \to 180\text{s}$: Step down to $4.0\text{ req/s}$.
  * $t = 180\text{s} \to 210\text{s}$: Recovery baseline.

---

## 2. Empirical Evidence and Reproduction of Reactive Lag

### Summary Metrics from Manifest (`results/raw/o1/step_load_manifest.json`)
* **Git Commit Hash:** `27c42677d1e50b73248a757b0ddb12f698d0f5d8`
* **Trial ID:** `O1_BASELINE_STEP_HPA_V2`
* **Experiment Start Timestamp ($t_0$):** `1791741244.93`
* **Load Onset Timestamp ($t_{onset}$):** `1791741284.93` ($t = 40.0\text{ s}$)
* **Total Telemetry Samples:** 110 (2-second sampling interval over 220 s)
* **Maximum Probe Latency Observed:** $> 2,000\text{ ms}$ during reactive lag peak (vs $100.0\text{ ms}$ SLO).

### Pod Provisioning and Scaling Delays
From Kubernetes API lifecycle events:
* **Scale-Out Pod (`workload-app-79955bc79b-6xxml`):**
  * Creation timestamp: $1791741364.0$ ($t = 119.1\text{ s}$)
  * Ready timestamp: $1791741426.0$ ($t = 181.1\text{ s}$)
  * Pod Provisioning Delay ($D_{provision}$): $\mathbf{62.0\text{ s}}$
  * Detection & Scheduling Delay ($D_{detect+sched}$): $\mathbf{119.1\text{ s}}$
  * End-to-End Autoscaling Delay ($D_{E2E}$): $\mathbf{181.1\text{ s}}$
  * Onset-Relative Lag ($D_{onset}$): $\mathbf{+141.1\text{ s}}$
* **Observed Autoscaling Limitation:**
  * Demand jumped at $t = 40.0\text{ s}$, but HPA metrics scraping and CPU metric accumulation required over $119\text{ seconds}$ to trigger pod scheduling.
  * Container startup and readiness probe passage required an additional $62\text{ seconds}$.
  * Consequently, the reactive scaling mechanism exhibited an onset-relative lag of **$141\text{ seconds}$**, during which service tail latency severely degraded and breached the $100\text{ ms}$ SLO.
  * This empirically reproduces the fundamental reactive autoscaling limitation (Hypotheses H1 & H3) targeted by the research project.

---

## 3. Evidence Artifacts
* **Time-Series Telemetry CSV:** `results/raw/o1/step_load_telemetry.csv`
* **Run Manifest & Delays JSON:** `results/raw/o1/step_load_manifest.json`
* **In-Cluster Calibration Raw Telemetry:** `results/raw/o1/recalibration_results.json`
* **Publication Evidence Figure:** `figures/o1_baseline_reactive_lag.png`

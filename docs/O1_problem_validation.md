# O1_problem_validation.md — Gate 1 Baseline Problem Reproduction

This document validates Objective O1 per `PROJECT_BRIEF.md` Section 5:
*Deploy a workload with default HPA and a load generator; capture request traces. Reproduce the reactive-lag limitation with versioned evidence.*

---

## 1. Experimental Setup for O1 Validation

* **Cluster:** Local Minikube v1.38.1 running pinned Kubernetes `v1.35.1` (Docker driver, 4 CPUs, 6.0 GB RAM).
* **Workload Application:** FastAPI test service (`workload-app:latest`) deployed with resource envelope:
  * CPU request: `100m`, CPU limit: `500m`.
  * RAM request: `128Mi`, RAM limit: `256Mi`.
  * CPU burn per request: $20.0\text{ ms}$ (`WORK_MS=20`).
* **Autoscaling Policy (Arm A Baseline):** Native Kubernetes CPU Horizontal Pod Autoscaler (`k8s/hpa-baseline.yaml`):
  * Target: CPU utilization average $50\%$.
  * Min replicas: $1$, Max replicas: $10$.
  * Scale-up policy: $100\%$ or $4$ pods every $15\text{ s}$ (stabilization window $0\text{ s}$).
  * Scale-down policy: stabilization window $300\text{ s}$.
* **Load Pattern (Step Load):**
  * $t = 0\text{s} \to 40\text{s}$: Warm-up at baseline $5.0\text{ req/s}$ (1 replica).
  * $t = 40\text{s}$ ($t_{onset}$): Step burst begins, ramping to $35.0\text{ req/s}$ over $10\text{ s}$.
  * $t = 50\text{s} \to 170\text{s}$: Sustained burst at $35.0\text{ req/s}$ ($4.4\times$ single pod capacity).
  * $t = 170\text{s} \to 180\text{s}$: Step down to $5.0\text{ req/s}$.
  * $t = 180\text{s} \to 210\text{s}$: Recovery baseline.

---

## 2. Empirical Evidence and Reproduction of Reactive Lag

### Summary Metrics from Manifest (`results/raw/o1/step_load_manifest.json`)
* **Git Commit Hash:** `035a194af82d50a6821503b40d86f051f1724098`
* **Experiment Start Timestamp ($t_0$):** `1791736994.36`
* **Load Onset Timestamp ($t_{onset}$):** `1791737034.36` ($t = 40.0\text{ s}$)
* **Total Telemetry Samples:** 110 (2-second sampling interval over 220 s)
* **Maximum Probe Latency Observed:** **$187.0\text{ ms}$** (exceeding frozen $100.0\text{ ms}$ SLO)
* **SLO Violations Count:** Breaches recorded during the scaling lag window ($t = 87.2\text{s}$, $t = 109.4\text{s}$)
* **Peak Replicas Scaled:** $10$ replicas

### Pod Provisioning and Scaling Delays
From Kubernetes API lifecycle events:
* **First Scale-Out Pod (`workload-app-6fdcb8557-mvxj4`):**
  * $t_{creation}$: $1791737041.0$ ($t = 46.6\text{ s}$)
  * $t_{ready}$: $1791737049.0$ ($t = 54.6\text{ s}$)
  * Pod Provisioning Delay ($D_{provision}$): $\mathbf{8.0\text{ s}}$
  * Detection & Scheduling Delay ($D_{detect+sched}$): $\mathbf{46.6\text{ s}}$
  * Onset-relative lag ($D_{onset}$): $\mathbf{+14.6\text{ s}}$ (first additional pod arrived 14.6 s after demand ramp began)
* **Subsequent Scaling Waves:**
  * Replicas $2 \to 6$: pods created at $t = 106.6\text{ s}$ ($66.6\text{ s}$ after load onset); ready at $t = 118.6\text{ s}$ ($D_{onset} = +78.6\text{ s}$).
  * Full capacity ($10$ ready replicas) was only reached at **$t = 143.3\text{ s}$** ($D_{onset} = \mathbf{+103.3\text{ s}}$).
* **Observed Scaling Lag Window:** Demand arrived at $t = 40\text{ s}$, but sufficient capacity to restore sub-100ms latency was not fully active until $t \approx 120\text{ s}$—a reactive lag window of approximately **$80\text{ seconds}$**.

---

## 3. Evidence Artifacts
* **Time-Series Telemetry CSV:** `results/raw/o1/step_load_telemetry.csv`
* **Run Manifest & Pod Delays JSON:** `results/raw/o1/step_load_manifest.json`
* **k6 Load Generator Summary:** `results/raw/o1/step_load_k6_summary.json`
* **Capacity Calibration Trials:** `results/raw/o1/calibration_results.json`
* **Publication Evidence Figure:** `figures/o1_baseline_reactive_lag.png`

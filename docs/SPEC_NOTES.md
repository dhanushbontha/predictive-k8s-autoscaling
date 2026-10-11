# SPEC_NOTES.md — Specification Inconsistencies, Ambiguities, and Operational Risks

This document records all discrepancies, ambiguous definitions, and template artifacts discovered during the Phase P0 audit across:
1. Capstone Project Specification (`docs/CLOUD-03_Predictive_Autoscaling_in_Kubernetes_with_Load_Forecasting.pdf`)
2. Master Project Brief (`PROJECT_BRIEF.md`)
3. IEEE Research Paper Draft (`docs/Evaluating_the_Limits_of_Predictive_Kubernetes_Autoscaling_updated_tracked.docx`)

As required by non-negotiable integrity rule 24, no arbitrary single interpretation is silently adopted; both readings are recorded and flagged here for supervisor/mentor defense.

---

## 1. Specification Ambiguities and Inconsistencies (Spec PDF vs Brief vs Paper)

### Spec Issue 1: KPI-2 "Cost per successful transaction" Direction and Target (Obvious Template Artifact)
* **Location in Spec PDF:** Page 8, Table row `KPI-2` (`cost per successful transaction`).
* **The Conflict:**
  * Spec text lists: Direction = `higher is better`, Target = `% ≥ O2 reference + 3 percentage points, or within 1 point while improving the primary resource KPI by ≥ 20%`.
  * In standard systems engineering and economic definitions, cost per transaction is **lower is better**. A higher cost per successful transaction indicates worse operational efficiency.
* **Analysis & Resolution:**
  * This is an unedited copy-paste template artifact from `KPI-1` (SLO adherence).
  * **Dual reporting contract:**
    1. *Engineering/Economic Reading (Standard):* Compute `cost = total_pod_seconds / successful_requests` (lower is better; reduction target $\le 0.80 \times \text{baseline}$).
    2. *Inverted Efficiency Metric (Literal Template Reading):* Compute transactions per resource unit (throughput yield: `successful_requests / pod_seconds`, higher is better).
  * Both metrics will be computed by analysis scripts and logged in `results/processed/`.

### Spec Issue 2: Scored Evaluation Window Duration (180 s vs 300 s)
* **Location in Documents:** Paper draft (Section IV-E/F) vs `PROJECT_BRIEF.md` (Section 3).
* **The Conflict:**
  * Early iterations of the draft referenced a 180-second evaluation window.
  * `PROJECT_BRIEF.md` locks the scored evaluation window at **300 s** with a warm-up phase preceding it and a cool-down phase following it.
  * HPA's default scale-down stabilization window is 300 s. In a 180 s window, no scale-down action could ever occur, and fewer than 2 burst/periodic cycles would complete.
* **Resolution:**
  * The 300 s scored window is locked per `PROJECT_BRIEF.md`. The paper draft text has been verified to align with this fixed parameter.

### Spec Issue 3: HPA Comparison Baseline (O1 Default CPU HPA vs O2 Reference Forecaster)
* **Location in Spec PDF:** Pages 4 & 7–9 (O1, O2, KPI Baseline columns).
* **The Conflict:**
  * Spec PDF lists the Baseline column for KPI-1 through KPI-6 as "O2 reference measured with the same evidence, workload and resource envelope".
  * However, O2 in the spec is defined as "Build a load-forecasting model (Prophet/LSTM) on the traces" (an offline time-series model), while O1 is the actual reactive Kubernetes HPA baseline.
  * Offline forecasters do not serve HTTP traffic or run Kubernetes pods; they have no pods, no SLO adherence, and no latency.
* **Resolution:**
  * In all A-vs-B comparisons, Arm A is the live **reactive HPA (CPU)** from O1, and Arm B is the live **Prophet+KEDA** predictive autoscaler.
  * O2 serves as the standalone forecasting reference (offline comparison of Prophet vs. LSTM vs. Seasonal-Naive on workload history traces).

### Spec Issue 4: End-to-End Autoscaling Lag Definition ($D_{E2E} = t_{ready} - t_0$) with Predictive Scaling
* **Location in Documents:** Paper draft (Section IV-F, Eq. 3) and `PROJECT_BRIEF.md` (Section 3).
* **The Conflict:**
  * Paper Eq. 3 defines $D_{E2E} = t_{ready} - t_0$, where $t_0$ is the experiment start timestamp.
  * When predictive autoscaling provisions a pod ahead of a demand spike (e.g., preparing for a step at $t = 120\text{ s}$ by starting a pod at $t = 100\text{ s}$ that becomes ready at $t = 115\text{ s}$), calculating delay relative to $t_0$ is meaningless or can distort comparisons across differing arrival curves.
* **Resolution:**
  * Retain paper Eq. 3–5 exactly as written for strict paper fidelity.
  * Log the generator **load-onset timestamp** ($t_{onset}$) for every ramp, step, and burst.
  * Additionally compute and report:
    1. Onset-relative lead/lag: $D_{onset} = t_{ready} - t_{onset}$ (negative values prove proactive readiness before load arrival).
    2. Lead-time slack: $\text{Slack} = 60\text{ s} - D_{provision}$.

### Spec Issue 5: Kubernetes API Timestamp Resolution
* **Location in Documents:** Kubernetes core API specifications vs research measurement requirements.
* **The Conflict:**
  * Kubernetes API timestamps (`metadata.creationTimestamp`, condition transition times such as `PodReady`) have **1-second granularity** (`time.RFC3339`).
  * Sub-second provisioning nuances can be quantized by this ceiling.
* **Resolution:**
  * Document 1-second API timestamp resolution explicitly in paper Section VIII (Threats to Validity and Limitations).
  * Supplement API timestamps with high-resolution client-side runner observation timestamps recorded when pod events are streamed via the Kubernetes watch API.

---

## 2. Technical and Operational Risks

| Risk ID | Description | Severity | Mitigation Strategy |
|---|---|---|---|
| **R-01** | **Host CPU Over-subscription:** Docker VM and k6 load generator running on the same host (10 cores, i5-1235U) could steal CPU cycles from workload pods during peak load. | High | Pod CPU requests capped at `100m` and limits at `500m`; cluster default allocated 4 CPUs; k6 runs with lightweight arrival-rate script; precondition check verifies idle CPU before each run. |
| **R-02** | **KEDA Controller & Metrics Scraping Overhead:** Prophet service scraping Prometheus and KEDA polling on a 15 s cadence could introduce controller delays or timeout spikes. | Medium | Set KEDA `pollingInterval: 15` equal to Prometheus scrape interval; containerize forecaster with pinned lightweight memory profile; enforce strict query timeout and fallback trigger. |
| **R-03** | **Closed-loop Load Generator Distortion:** Virtual users waiting for slow server responses unintentionally throttle traffic arrival, disguising tail latency spikes. | Critical | Enforce strict open-loop arrival-rate executors in k6 (`constant-arrival-rate`, `ramping-arrival-rate`). Compare offered rate vs achieved rate on every run; auto-invalidate if deviation $> 2\%$. |
| **R-04** | **Stale Minikube Cache / Network Failures:** Docker Desktop WSL2 or Minikube driver network flakiness causing dropped requests. | Medium | Pin Kubernetes node version; use local pre-loaded container images (`imagePullPolicy: Never`); run automated precondition connectivity checks before each run. |
| **R-05** | **Prophet Cold-Start & Fitting Latency:** Prophet fitting latency on historical series could exceed the 60 s prediction horizon if history window is too wide. | High | Fit Prophet on sliding downsampled history windows (e.g. 5–10 min at 5 s resolution); measure fit latency; count fit time against the 60 s lead time. |

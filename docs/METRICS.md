# METRICS.md — Formal Metric Definitions and Evaluation Mathematics

This document formalizes all mathematical and operational definitions of metrics used throughout the **CLOUD-03 Capstone & Research Study**:
*Evaluating the Limits of Predictive Kubernetes Autoscaling: An Empirical Study of Workload Predictability, Forecast Error, and Provisioning Delays Using Prophet and KEDA*.

> **Integrity Contract & Freeze Declaration:**
> Per `PROJECT_BRIEF.md` Section 3, all metric formulas are rigorously implemented and tested against unit tests (`tests/test_metrics.py`).
> This document is **FROZEN** as of commit `27c42677d1e50b73248a757b0ddb12f698d0f5d8`.

---

## 1. Primary Service Quality KPI (KPI-1)

To evaluate service level compliance without distortion from idle windows, the study designates a single primary quality-of-service measure:

$$\mathbf{\text{KPI-1}} = \frac{\sum_{k=1}^{M} \mathbb{I}\left(\text{P95}_k \le L_{SLO} \land E_k \le E_{SLO}\right)}{M} \times 100\%$$

Where:
* $M$ is the total count of non-overlapping 5-second observation intervals across the evaluation window.
* $\text{P95}_k$ is the 95th percentile request latency within interval $k$, with $L_{SLO} = 100.0\text{ ms}$.
* $E_k$ is the percentage of failed requests (HTTP status $\ne 200$, network drops, or timeouts $>5.0\text{s}$) in interval $k$, with $E_{SLO} = 1.0\%$.
* $\mathbb{I}(\cdot)$ is the indicator function ($1$ if conditions are satisfied, $0$ otherwise).

---

## 2. Workload Forecasting Metrics (Section IV-C)

Let $y_t$ denote the observed request rate at evaluation step $t$, and $\hat{y}_t$ denote the emitted forecast for step $t$, over $N$ discrete evaluation points.

### 2.1 Mean Absolute Error (MAE)
$$\text{MAE} = \frac{1}{N} \sum_{t=1}^{N} |y_t - \hat{y}_t|$$

### 2.2 Root Mean Squared Error (RMSE)
$$\text{RMSE} = \sqrt{\frac{1}{N} \sum_{t=1}^{N} (y_t - \hat{y}_t)^2}$$

### 2.3 Zero-Safe Symmetric Mean Absolute Percentage Error (sMAPE)
To prevent division-by-zero during periods of zero request traffic, sMAPE is defined with an explicit zero-handling identity:
$$\text{sMAPE} = \frac{100\%}{N} \sum_{t=1}^{N} \begin{cases} 
0.0, & \text{if } |y_t| + |\hat{y}_t| = 0 \\
\frac{|y_t - \hat{y}_t|}{(|y_t| + |\hat{y}_t|) / 2}, & \text{otherwise}
\end{cases}$$

### 2.4 Mean Absolute Scaled Error (MASE)
To benchmark forecast accuracy against the standard naive one-step persistence baseline:
$$\text{MASE} = \frac{\frac{1}{N} \sum_{t=1}^{N} |y_t - \hat{y}_t|}{\frac{1}{N - 1} \sum_{t=2}^{N} |y_t - y_{t-1}|}$$
* If the historical training denominator $\frac{1}{N-1}\sum |y_t - y_{t-1}| = 0$ (constant series), MASE is evaluated as $0.0$ if $\text{MAE}=0$, else $\infty$.

---

## 3. Scaling and Provisioning Delays (Section IV-F)

For each pod $i$ in the workload deployment:
* $t_0$: Experiment launch timestamp (epoch seconds).
* $t_{onset}$: Stored workload step/ramp onset timestamp emitted by the load generator (epoch seconds).
* $t_{creation, i}$: Pod creation timestamp recorded by Kubernetes API (`metadata.creationTimestamp`).
* $t_{ready, i}$: Timestamp when pod condition `Ready` transitioned to `True` (`pod.status.conditions`).
* $t_{term, i}$: Timestamp when the pod is deleted/terminated (or experiment end $t_{end}$ if still active).

### 3.1 Delay Filtering and Boundary Rules
1. **Pre-existing Pods ($t_{creation, i} < t_0$):**
   * Baseline pods created during deployment initialization before $t_0$ are excluded from scale-out delay calculations.
2. **Runs Without Scale-Out Events:**
   * If a stable or low-load run produces no new pods ($N_{scaled} = 0$), scaling delay terms $D_{E2E}$, $D_{provision}$, and $D_{detect+sched}$ are recorded as `None` / `NaN` (omitted from delay summaries, not arbitrarily assigned zero).
3. **Onset Precedence:**
   * For pods created during scale-out, $t_{onset}$ serves as the reference for reactivity.

### 3.2 Individual Delay Definitions
* **Pod Provisioning Delay ($D_{provision, i}$):**
  $$D_{provision, i} = t_{ready, i} - t_{creation, i}$$
  *(Captures container scheduling, image initialization, application startup, and readiness check passing).*
* **Detection and Scheduling Delay ($D_{detect+sched, i}$):**
  $$D_{detect+sched, i} = t_{creation, i} - t_0$$
* **End-to-End Autoscaling Delay ($D_{E2E, i}$):**
  $$D_{E2E, i} = t_{ready, i} - t_0 = D_{detect+sched, i} + D_{provision, i}$$
* **Onset-Relative Lead/Lag ($D_{onset, i}$):**
  $$D_{onset, i} = t_{ready, i} - t_{onset}$$
  * $D_{onset, i} > 0$: Reactive lag (pod ready after demand arrival).
  * $D_{onset, i} \le 0$: Predictive advantage (pod ready prior to or synchronous with demand arrival).

### 3.3 Delay Aggregation
Across all $K$ pods provisioned during a scale event:
* Primary summary metric: **Median Delay** $\text{med}(D)$ with Interquartile Range (IQR).

---

## 4. Resource Efficiency and Cost Metrics

### 4.1 Primary Cost per Request: Allocated Pod-Seconds
To reflect true cloud infrastructure billing where resources are reserved from pod inception to termination:

$$\mathbf{\text{Cost}_{allocated}} = \frac{\sum_{i=1}^{P} \left( \min(t_{term, i}, t_{end}) - \max(t_{creation, i}, t_0) \right)}{N_{success}} \quad \left(\frac{\text{allocated pod-seconds}}{\text{successful request}}\right)$$

Where $P$ is the total set of pods active during the window $[t_0, t_{end}]$.

### 4.2 Secondary Efficiency Metrics
* **Ready Pod-Seconds Cost:**
  $$\text{Cost}_{ready} = \frac{\int_{t_0}^{t_{end}} R_{ready}(t) \, dt}{N_{success}}$$
* **Over-provisioned Pod-Hours:**
  $$\text{Overprovisioned} = \frac{1}{3600} \int_{t_0}^{t_{end}} \max\left(0, \frac{R_{ready}(t) \times C_{pod} - \text{Offered}(t)}{C_{pod}}\right) dt$$
* **Under-provisioned Pod-Hours:**
  $$\text{Underprovisioned} = \frac{1}{3600} \int_{t_0}^{t_{end}} \max\left(0, \frac{\text{Offered}(t) - R_{ready}(t) \times C_{pod}}{C_{pod}}\right) dt$$
  *(Evaluated with calibrated nominal capacity $C_{pod} = 6.0\text{ req/s}$)*.

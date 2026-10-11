# METRICS.md — Formal Metric Definitions and Evaluation Mathematics

This document formalizes all mathematical and operational definitions of metrics used throughout the **CLOUD-03 Capstone & Research Study**:
*Evaluating the Limits of Predictive Kubernetes Autoscaling: An Empirical Study of Workload Predictability, Forecast Error, and Provisioning Delays Using Prophet and KEDA*.

All equations correspond directly to the paper draft (`docs/Evaluating_the_Limits_of_Predictive_Kubernetes_Autoscaling_updated_tracked.docx`) and master brief (`PROJECT_BRIEF.md`).

---

## 1. Workload Forecasting Metrics (Section IV-C)

Let $y_t$ denote the observed request rate at time $t$, and $\hat{y}_t$ denote the forecast produced for time $t$. For an evaluation window of $N$ discrete points:

### 1.1 Mean Absolute Error (MAE)
$$\text{MAE} = \frac{1}{N} \sum_{t=1}^{N} |y_t - \hat{y}_t| \quad \text{[Eq. 1]}$$

### 1.2 Root Mean Squared Error (RMSE)
$$\text{RMSE} = \sqrt{\frac{1}{N} \sum_{t=1}^{N} (y_t - \hat{y}_t)^2} \quad \text{[Eq. 2]}$$

### 1.3 Symmetric Mean Absolute Percentage Error (sMAPE)
$$\text{sMAPE} = \frac{100\%}{N} \sum_{t=1}^{N} \frac{|y_t - \hat{y}_t|}{(|y_t| + |\hat{y}_t|) / 2}$$

### 1.4 Forecast Service Telemetry
* **Forecast Latency ($T_{fit+pred}$):** Total wall-clock time required by the forecaster microservice to ingest series from Prometheus, fit the model, and emit predictions. Counted against the 60 s lead time.
* **Forecast Staleness ($T_{stale}$):** Difference between current time and forecast origin timestamp ($t_{now} - t_{origin}$).

---

## 2. Scaling and Provisioning Delays (Section IV-F)

Let:
* $t_0$: Stored experiment start timestamp (epoch seconds).
* $t_{onset}$: Stored load-onset timestamp produced by the load generator indicating the exact second demand ramps or steps (epoch seconds).
* $t_{creation}$: Kubernetes pod `metadata.creationTimestamp` (epoch seconds).
* $t_{ready}$: Timestamp when the pod transitions to `Ready=True` condition in `pod.status.conditions` (epoch seconds).

### 2.1 End-to-End Autoscaling Response Lag ($D_{E2E}$)
$$D_{E2E} = t_{ready} - t_0 \quad \text{[Eq. 3]}$$

### 2.2 Pod Provisioning Delay ($D_{provision}$)
$$D_{provision} = t_{ready} - t_{creation} \quad \text{[Eq. 4]}$$
Represents pure container scheduling, image pull/initialization, and readiness probe delay.

### 2.3 Detection and Scheduling Delay ($D_{detect+sched}$)
$$D_{detect+sched} = t_{creation} - t_0 \quad \text{[Eq. 5]}$$
Satisfies the identity: $D_{E2E} = D_{detect+sched} + D_{provision}$.

### 2.4 Onset-Relative Lead/Lag ($D_{onset}$)
$$D_{onset} = t_{ready} - t_{onset}$$
* **Interpretation:**
  * $D_{onset} > 0$: Capacity arrived late (reactive lag).
  * $D_{onset} < 0$: Capacity was ready ahead of demand arrival (predictive advantage).

### 2.5 Lead-Time Slack ($\text{Slack}$)
$$\text{Slack} = 60.0\text{ s} - D_{provision}$$
Evaluates hypothesis H3: indicates the margin of lead time remaining after accounting for real pod provisioning delays.

---

## 3. Resource and Efficiency Metrics

Using calibrated per-pod sustainable capacity $C_{pod} = 8.0\text{ req/s}$:
* Provisioned capacity at time $t$: $\text{Cap}(t) = R_{ready}(t) \times C_{pod}$.
* Offered demand at time $t$: $\text{Offered}(t)$ (req/s).

### 3.1 Over-provisioned Pod-Hours
$$\text{Overprovisioned} = \frac{1}{3600} \int_{0}^{T} \max\left(0, \frac{\text{Cap}(t) - \text{Offered}(t)}{C_{pod}}\right) dt$$

### 3.2 Under-provisioned Pod-Hours
$$\text{Underprovisioned} = \frac{1}{3600} \int_{0}^{T} \max\left(0, \frac{\text{Offered}(t) - \text{Cap}(t)}{C_{pod}}\right) dt$$

### 3.3 Cost per Successful Request
$$\text{Cost} = \frac{\int_{0}^{T} R_{ready}(t) \, dt}{N_{success}} \quad (\text{pod-seconds per request})$$

### 3.4 Throughput Yield (Literal Template Reading)
$$\text{Yield} = \frac{N_{success}}{\int_{0}^{T} R_{ready}(t) \, dt} \quad (\text{requests per pod-second})$$

# SLO.md — Frozen Service Level Objective Definition

This document defines the frozen Service Level Objective (SLO) for the **CLOUD-03 Capstone & Research Study**:
*Evaluating the Limits of Predictive Kubernetes Autoscaling*.

> **Integrity Contract:**
> Per `PROJECT_BRIEF.md` Section 3 and rule 56, the SLO is derived empirically from steady-state baseline calibration runs and is **FROZEN** prior to conducting any comparative evaluation between Arm A (reactive HPA) and Arm B (Prophet+KEDA). No post-hoc tuning or relaxation of this definition is permitted.

---

## 1. Empirical Calibration Evidence

From the steady-state baseline trials executed in Phase P1 on the target cluster (`results/raw/o1/calibration_results.json`):

* **Single Replica Steady State (Offered Load = 5–10 rps):**
  * Baseline 5 rps: Median latency = $23.99\text{ ms}$, P95 latency = $31.04\text{ ms}$, Error rate = $0.0\%$.
  * Baseline 10 rps: Median latency = $23.61\text{ ms}$, P95 latency = $30.43\text{ ms}$, Error rate = $0.0\%$.
  * Baseline nominal latency ($\text{P95}_{nominal}$): $\approx \mathbf{30.5\text{ ms}}$.
* **Sustainable Per-Pod Capacity Limit ($C_{pod}$):**
  * At 15 rps on 1 replica, pod CPU usage rises to $145\text{m}$ (exceeding the $100\text{m}$ CPU request threshold).
  * Safe sustainable capacity per pod while maintaining sub-50ms P95 latency is **$\mathbf{C_{pod} = 8.0\text{ req/s}}$** (yielding nominal CPU utilization around $50\%$ of request).
* **Scaling Linearity:**
  * 1 replica: sustainable up to $\sim 8\text{ rps}$ (P95 $< 31\text{ ms}$).
  * 4 replicas: sustainable up to $\sim 30\text{ rps}$ (P95 $< 45\text{ ms}$).
  * 10 replicas: sustainable up to $\sim 65\text{ rps}$ (P95 $< 150\text{ ms}$).

---

## 2. Frozen SLO Definition

A request or evaluation interval is deemed in violation of the SLO if it breaches either the latency threshold or the error budget.

### Criterion 1: Tail Latency Threshold ($L_{SLO}$)
* **Definition:** $L_{SLO} = 3.0 \times \text{P95}_{nominal} \approx \mathbf{100.0\text{ ms}}$.
* **Justification:**
  * For CPU-bound workloads where each request burns $20\text{ ms}$ of CPU, any latency exceeding $100\text{ ms}$ signifies queue queuing delays exceeding $4\times$ processing time, indicating severe resource starvation.
  * During steady-state operation within capacity, P95 latency consistently remains below $35\text{ ms}$.
  * $100.0\text{ ms}$ provides a well-defined boundary to distinguish normal queuing from true autoscaling response lag.

### Criterion 2: Availability / Error Rate Threshold ($E_{SLO}$)
* **Definition:** $\mathbf{E_{SLO} \le 1.0\%}$ (99% successful responses).
* **Justification:**
  * Standard cloud-native availability SLO for HTTP services. Any HTTP 5xx or connection drop rate above 1% constitutes an outage.

### Summary SLO Contract
$$\text{SLO Compliance} \iff (\text{P95 Latency} \le 100.0\text{ ms}) \land (\text{HTTP Error Rate} \le 1.0\%)$$

---

## 3. SLO Violation Quantification

In all comparative experiment runs across Arm A and Arm B:
1. **Per-Window SLO Violations ($V_{req}$):** Percentage of total requests during the 300 s scored window with latency $> 100\text{ ms}$.
2. **SLO Violation Duration ($T_{violation}$):** Total continuous seconds during the scored window where the 5-second moving average P95 latency exceeds $100.0\text{ ms}$.
3. **Severe Degradation Events:** Any 1-second interval where error rate exceeds $1.0\%$.

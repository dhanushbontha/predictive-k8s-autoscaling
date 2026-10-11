# PLAN.md — Master Project Plan & Execution Roadmap

This plan governs the engineering, experimentation, and paper production for the **CLOUD-03 Capstone & Research Study**:
*Evaluating the Limits of Predictive Kubernetes Autoscaling: An Empirical Study of Workload Predictability, Forecast Error, and Provisioning Delays Using Prophet and KEDA*.

---

## 1. Pinned-Version Candidates

All versions are empirically verified from current official registries as of October 2026:

| Component | Pinned Version | Source / Image / Registry | Status |
|---|---|---|---|
| **Kubernetes (Minikube)** | `v1.35.1` (or `v1.36.1`) | minikube Docker driver / `registry.k8s.io` | Verified running |
| **KEDA (Helm Chart)** | `2.21.0` | `kedacore/keda` (`https://kedacore.github.io/charts`) | Pinned |
| **kube-prometheus-stack** | `92.1.1` | `prometheus-community/kube-prometheus-stack` | Pinned |
| **k6** | `v2.2.0` (commit `00a9a1b7f5`) | Local official installation (`C:\Program Files\k6\k6.exe`) | Verified installed |
| **Python** | `3.12.7` / base `python:3.12-slim` | Official Docker Hub | Verified installed |
| **Prophet** | `1.5.0` | PyPI (`prophet==1.5.0`) | Verified current |
| **FastAPI** | `0.142.2` | PyPI (`fastapi==0.142.2`) | Verified installed |
| **Uvicorn** | `0.54.0` | PyPI (`uvicorn[standard]==0.54.0`) | Verified installed |
| **Prometheus Client (Python)** | `0.26.0` | PyPI (`prometheus-client==0.26.0`) | Verified installed |
| **Pytest** | `9.1.1` | PyPI (`pytest==9.1.1`) | Verified installed |

---

## 2. Project Phases and Deliverables (P0 → P7)

```
[P0 Audit & Plan] ➔ [P1 O1 Baseline & SLO] ➔ [P2 Workloads & O2 Forecaster] ➔ [P3 O3 Predictive Scaler]
        │
        ▼
[P4 O4 Campaign Runner & Pilot] ➔ [P5 O5 Negative Tests & Degraded Mode] ➔ [P6 Analysis & Acceptance] ➔ [P7 Paper & Reproducibility]
```

### Phase P0: Environment Audit, Discrepancy Reconciliation & Master Plan
* **Status:** In Progress.
* **Deliverables:** Hardware audit, machine capacity model, campaign time model, `docs/SPEC_NOTES.md`, `PLAN.md`.

### Phase P1 (O1): Cluster Scaffolding, App, Load Generator, Baseline HPA, and Calibration
* **Focus:** Build and calibrate the foundation.
* **Deliverables:**
  * Automated cluster setup script (`scripts/setup-cluster.ps1`).
  * FastAPI workload app (`app/main.py`), containerized and pre-loaded.
  * Open-loop k6 arrival-rate scripts (`workloads/k6_generator.js`).
  * Runner skeleton tracking $t_0$, $t_{onset}$, offered vs. achieved rate, and Kubernetes pod lifecycle events.
  * Baseline HPA (CPU) deployment reproducing reactive lag under a step load (`results/raw/o1/`).
  * Calibration of sustainable per-pod capacity and freezing of `docs/SLO.md` and `docs/METRICS.md`.
  * Unit tests for metric formulas against an independent synthetic oracle.
* **Gate 1 Evidence:** Baseline lag plot, raw trace files, passing tests, frozen SLO doc.

### Phase P2 (O2): Workload Generative Specs, Predictability Quantifiers & Forecaster Comparison
* **Focus:** Synthesize workloads and benchmark time-series forecasters.
* **Deliverables:**
  * Five generative workload specifications (Stable, Periodic, Gradual, Bursty, Noisy) in `workloads/SPEC.md`.
  * Independent warm-up history traces and acceptance seeds (hashed before tuning).
  * Workload predictability metrics (spectral entropy, dominant-lag autocorrelation, seasonal-naive MASE).
  * Real trace comparison against public traces (e.g. NASA-HTTP / Wikimedia) validating realism.
  * O2 offline forecaster comparison: Prophet (custom short-period seasonality) vs. LSTM vs. Seasonal-Naive on 60 s horizon.
  * `docs/O2_reference.md` documenting accuracy, fit time, memory, and selection rationale.
* **Gate 2 Evidence:** Model comparison benchmark tables, seed hash records, oracle test suite.

### Phase P3 (O3): Forecaster Microservice & KEDA Predictive Scaler Integration
* **Focus:** Deploy live proactive autoscaling on Kubernetes.
* **Deliverables:**
  * Containerized Forecaster service querying Prometheus, refitting periodically, and exposing `forecast_request_rate_60s` with staleness/health telemetry.
  * KEDA `ScaledObject` (Prometheus trigger) configuring proactive scaling for Arm B.
  * Strict controller fairness configuration in `docs/FAIRNESS.md` (identical min/max replicas, scale-down stabilization, polling intervals).
  * Pre-run configuration validator script.
  * Vertical slice demonstration: Bursty run for Arm A vs. Arm B on a single time axis.
* **Gate 3 Evidence:** Time-aligned multi-panel plot (offered load, forecast, desired/ready replicas, latency), fairness check output.

### Phase P4 (O4): Experiment Campaign Execution & Pilot
* **Focus:** Execute the randomized comparative evaluation.
* **Deliverables:**
  * Resumable, append-only campaign runner (`runner/campaign.py`) with pre-flight checks, automatic invalidation logging, and per-run telemetry capture.
  * Pilot campaign (1–2 reps per pattern × arm) verifying that $\ge 2$ scale-out events occur per run and measuring exact reset overhead.
  * Protocol freezing document (`docs/PROTOCOL_FROZEN.md`) with commit hash and frozen seed manifest.
  * Execution of Block 1 (50 final runs: 5 patterns × 2 arms × 5 reps).
* **Gate 4 Evidence:** Pilot verification report, raw run directories in `results/raw/block1/`, integrity audit report.

### Phase P5 (O5): Negative Testing, Fault Injection & Degraded Mode
* **Focus:** Verify resilience, failure semantics, and safety guardrails.
* **Deliverables:**
  * Scripted test harnesses for NT-1 (forecast errors causing under-provisioning), NT-2 (scaling lag and cold starts), NT-3 (synthetic load non-resemblance), NT-4 (control-plane/dependency outage), and NT-5 (configuration drift).
  * Degraded-mode fallback mechanism (seamless fallback to reactive triggers on forecaster unavailability).
  * Evidence recorded in `results/raw/nt/` and summarized in `acceptance/negative_tests.md`.
* **Gate 5 Evidence:** Scripted fault logs, recovery time measurements, zero-silent-loss proofs.

### Phase P6: Analysis Pipeline, KPI Synthesis & Acceptance Report
* **Focus:** Script-driven statistical analysis and figure generation.
* **Deliverables:**
  * Python analysis pipeline (`make analyze` / `analysis/compute_kpis.py`) transforming `results/raw/` to `results/processed/`.
  * KPI-1 through KPI-6 summary table with medians, IQRs, bootstrap 95% CIs, Mann-Whitney U tests, and Holm corrections.
  * Empirical answers to RQ1, RQ2 (H2), and RQ3 (H3) with publication-quality 300 dpi figures.
  * Final acceptance document (`docs/ACCEPTANCE_REPORT.md`) verifying AC-1 through AC-4.
* **Gate 6 Evidence:** Fully automated build reproducing all figures and tables from raw logs.

### Phase P7: Paper Completion, Reproducibility Package & Mentor Demo
* **Focus:** Publication integration and defense preparation.
* **Deliverables:**
  * IEEE research paper Sections V (Setup), VI (Results), VII (Discussion), VIII (Threats/Limitations), and IX (Conclusion), with direct data traces for all numerical values.
  * Reproducibility package (`make reproduce`, evidence manifest, quick-start guide).
  * Mentor demo script (10–12 minute live comparison of Arm A vs Arm B on Bursty and Periodic patterns).
  * Viva defense guide with anticipated examiner questions and honest technical answers.
* **Gate 7 Evidence:** Clean-room reproduction run, finalized paper draft, demo walkthrough.

---

## 3. Repository Scaffolding

```
predictive-k8s-autoscaling/
├── app/                  # FastAPI workload app, Dockerfile, requirements
│   ├── main.py
│   ├── Dockerfile
│   └── tests/
├── k8s/                  # Kubernetes manifests (Namespace, Deployment, Service, ServiceMonitor, HPA, KEDA)
│   ├── namespace.yaml
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── service-monitor.yaml
│   ├── hpa-baseline.yaml
│   └── keda-scaledobject.yaml
├── workloads/            # Open-loop k6 scripts, pattern definitions, seeds
│   ├── SPEC.md
│   └── k6_generator.js
├── forecaster/           # Prophet/LSTM service, forecast API, Dockerfile
│   ├── app.py
│   ├── model.py
│   └── Dockerfile
├── runner/               # Automated experiment harness, pre-flight checks, collector
│   ├── campaign.py
│   ├── metrics_collector.py
│   └── validator.py
├── analysis/             # Statistical processing, bootstrap CI, figure generation
│   ├── compute_kpis.py
│   └── plot_figures.py
├── acceptance/           # Scripted fault injection harnesses (NT1-NT5)
│   └── test_faults.py
├── results/
│   ├── raw/              # Immutable raw trial data (per-run CSVs, pod events, configs)
│   ├── invalid/          # Auto-invalidated runs with failure reasons
│   └── processed/        # Aggregated KPI tables and statistical tests
├── figures/              # 300 dpi publication plots (PNG and PDF)
├── docs/                 # Engineering specifications, decision records, reports
│   ├── SPEC_NOTES.md
│   ├── SLO.md
│   ├── METRICS.md
│   ├── FAIRNESS.md
│   ├── PROTOCOL_FROZEN.md
│   └── ACCEPTANCE_REPORT.md
├── scripts/              # Utility and cluster setup scripts
│   └── setup-cluster.ps1
├── Makefile              # Automation targets: setup, test, run, analyze, demo, reproduce
├── PROJECT_BRIEF.md      # Master research rules and non-negotiables
├── PLAN.md               # Master phase schedule and risk model
└── CHANGELOG.md          # Chronological record of major changes
```

---

## 4. Key Milestones and Review Gates

| Gate | Phase | Milestone Output | Verification Criteria |
|---|---|---|---|
| **Gate 0** | P0 | Environment & Plan | Machine audit approved; `SPEC_NOTES.md` and `PLAN.md` verified. |
| **Gate 1** | P1 | Baseline & Frozen SLO | HPA reactive lag reproduced; sustainable capacity calibrated; SLO frozen. |
| **Gate 2** | P2 | Workloads & O2 Forecaster | 5 workload traces characterized; Prophet vs. LSTM offline trade-off evaluated. |
| **Gate 3** | P3 | Live Predictive Scaler | Arm B live vertical slice demonstrates proactive scaling; controller fairness validated. |
| **Gate 4** | P4 | Block 1 Experiment Campaign | Pilot completed; 50 comparative runs executed across 5 patterns × 2 arms. |
| **Gate 5** | P5 | Negative Testing | NT-1 through NT-5 fault injection evaluated; degraded-mode verified. |
| **Gate 6** | P6 | Analysis & Statistical KPIs | KPI-1 through KPI-6 computed with bootstrap CIs; RQ1–RQ3 figures generated. |
| **Gate 7** | P7 | Paper & Defense Package | IEEE paper sections completed from data; `make demo` and `make reproduce` verified. |

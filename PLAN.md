# PLAN.md — Master Project Plan & Execution Roadmap

This plan governs the engineering, experimentation, and paper production for the **CLOUD-03 Capstone & Research Study**:
*Evaluating the Limits of Predictive Kubernetes Autoscaling: An Empirical Study of Workload Predictability, Forecast Error, and Provisioning Delays Using Prophet and KEDA*.

---

## 1. Machine and Time Estimate

### Host Machine Profile
* **Operating System:** Microsoft Windows 11 Home Single Language (Build 10.0.26200 / 25H2, 64-bit).
* **Processor (CPU):** 12th Gen Intel(R) Core(TM) i5-1235U.
  * **Total Physical Cores:** 10 cores (2 Performance-cores supporting Hyper-Threading [4 threads] + 8 Efficient-cores [8 threads]).
  * **Total Logical Processors:** 12 logical threads.
  * **Clock Speed:** Base 1.30 GHz (E-cores) / 1.70 GHz (P-cores), Turbo up to 4.40 GHz.
  * **Architectural Constraint & Limitation:** Intel Alder Lake hybrid P/E core architecture introduces heterogeneous core performance. Foreground OS scheduling or background thread assignment between P and E cores can cause inter-trial execution variance. This is documented explicitly in the paper's threats to validity.
* **Host RAM:** 16,440 MB (~16.0 GB total visible physical RAM). Typically ~3.6 GB to ~6.0 GB free prior to starting heavy containers.
* **Storage (Disk C:):** NTFS filesystem, 475.6 GB total, **272.8 GB free** (ample headroom for container caches, Minikube VM state, and raw logs).

### Docker Desktop & WSL2 Resource Allocation
* **WSL2 / Docker VM Allocation (`docker info`):**
  * **Allocated CPUs:** 12 logical CPUs.
  * **Allocated Memory:** 8,157,372,416 bytes (~7.6 GB RAM).
* **Minikube Cluster Profile (`scripts/setup-cluster.ps1` defaults):**
  * **Driver:** Docker driver (runs as a Docker container within WSL2).
  * **CPU Allocation:** **4 CPUs** (leaves 8 threads for host OS, Docker background workers, and k6 generator).
  * **Memory Allocation:** **6,144 MB (6.0 GB)** (leaves ~1.6 GB in the WSL2 VM and ~8.4 GB for Windows host operations).

### Realistic Cluster Sizing & Maximum Replicas without Pending Pods
* **System Overhead inside Minikube:**
  * Control-plane components + metrics-server: ~400m CPU, ~800 MiB RAM.
  * `kube-prometheus-stack` (Prometheus Operator, Prometheus Server, Alertmanager): ~500m CPU, ~1,200 MiB RAM.
  * KEDA Operator + Metrics Adapter: ~200m CPU, ~250 MiB RAM.
  * Forecaster Microservice (Arm B): ~300m CPU request, ~500 MiB RAM.
  * **Total Fixed Cluster Overhead:** ~1,400m CPU, ~2,750 MiB RAM.
* **Remaining Headroom for Workload App Pods:**
  * **CPU Headroom:** $4,000\text{m} - 1,400\text{m} = \mathbf{2,600\text{m} \text{ CPU}}$.
  * **RAM Headroom:** $6,144\text{ MiB} - 2,750\text{ MiB} = \mathbf{3,394\text{ MiB} \text{ RAM}}$.
* **Workload Pod Resource Envelope (frozen across both Arm A and Arm B):**
  * `resources.requests`: CPU `100m`, Memory `128Mi`.
  * `resources.limits`: CPU `500m`, Memory `256Mi`.
* **Maximum Replicas Calculation:**
  * By CPU requests: $2,600\text{m} / 100\text{m} = \mathbf{26\text{ replicas}}$.
  * By RAM requests: $3,394\text{ MiB} / 128\text{ MiB} = \mathbf{26.5\text{ replicas}}$.
* **Safe Operational Limit:**
  * Set `minReplicas: 1` and `maxReplicas: 10` (or `12`).
  * At 10 replicas under peak burst, total pod requests consume $1,000\text{m}$ CPU and $1,280\text{ MiB}$ RAM.
  * This guarantees **zero `Pending` pods due to node scheduling starvation**. Pod provisioning delay ($D_{provision}$) will reflect pure container creation and startup latency, completely uncorrupted by resource queuing.

### Realistic Per-Run Time & Campaign Time Estimates
Every individual experimental run follows a strict sequential lifecycle:
1. **Pre-flight & Reset:** Verify preconditions (idle CPU, reachable endpoints), drain previous pods, reset deployment to `replicas: 1`, wait for ready state: **60 s**.
2. **Warm-up / Baseline Ingestion:** Generator begins at steady baseline load; establishes baseline metrics in Prometheus: **90 s**.
3. **Scored Measurement Window:** Fixed evaluation duration per brief: **300 s (5.0 min)**.
4. **Cool-down Phase:** Offload demand, allow queues to settle: **30 s**.
5. **Telemetry Collection & Ingestion:** Pull pod lifecycle events, query Prometheus series, export raw JSON/CSV, verify integrity: **30 s**.
* **Total Time per Experimental Run:** $\mathbf{510\text{ s} \approx 8.5\text{ minutes}}$.

#### Campaign Duration Calculations
* **Design Matrix:** 5 workload patterns (Stable, Periodic, Gradual, Bursty, Noisy) $\times$ 2 arms (Arm A: HPA CPU, Arm B: Prophet+KEDA) $\times$ 5 repetitions.
* **Block 1 Campaign (50 runs):**
  $$50\text{ runs} \times 8.5\text{ min} = 425\text{ minutes} = \mathbf{7.1\text{ hours}}.$$
* **Optional Block 2 Campaign (additional 50 runs, 100 runs total):**
  $$100\text{ runs} \times 8.5\text{ min} = 850\text{ minutes} = \mathbf{14.2\text{ hours}}.$$
* **Ancillary Validation Runs:**
  * Pilot runs (1–2 reps across conditions): $\sim 10\text{ runs} \approx 1.5\text{ hours}$.
  * Dev parameter tuning (dev seeds only): $\sim 20\text{ runs} \approx 2.8\text{ hours}$.
  * Negative Tests (NT-1 through NT-5): $\sim 15\text{ runs} \approx 2.1\text{ hours}$.
* **Total End-to-End Time:**
  * Core campaign (Pilot + Tuning + Block 1 + NTs): $\approx \mathbf{13.5\text{ hours}}$.
  * Maximum campaign (including Block 2): $\approx \mathbf{20.6\text{ hours}}$.
* **Feasibility Conclusion:**
  * **Fits comfortably within 2–3 days of machine time.** Block 1 can execute completely overnight. The runner will be built to be resumable and append-only, saving each run's raw artifacts to disk immediately upon completion.

---

## 2. Laptop Run Protocol and Host Environmental Controls

Running continuous benchmarking on a consumer laptop introduces thermal throttling and scheduling noise. To ensure experimental validity:
1. **Power Supply:** Laptop must remain plugged into AC mains power at all times. Running on battery drops CPU frequencies and alters clock stepping.
2. **Windows Power Profile:** Set Windows Power Mode to **"Best Performance"** (`Power & battery` settings).
3. **Sleep & Screen Timeout:** Configure Windows Sleep to **"Never"** when plugged in; disable automatic screen turn-off or hibernation.
4. **Application Isolation:** Close all non-essential applications (browsers, Discord, IDE background indexing, cloud sync agents like OneDrive) before launching campaigns.
5. **Pre-Run Idle-CPU Precondition:** The automated campaign runner must check host CPU utilization prior to starting each run. If 10-second average CPU utilization exceeds **10%**, the runner will pause and wait up to 3 minutes for background tasks to settle before proceeding, or fail with a precondition error.
6. **Hardware Heterogeneity Limitation:** Document in the paper that experiments were conducted on a heterogeneous 10-core (2P + 8E) Intel Core i5 processor, where OS thread scheduling between performance and efficiency cores may introduce latency dispersion.

---

## 3. Automation Strategy: Windows / PowerShell Equivalents for `make`

An audit confirmed that **GNU `make` is NOT installed** on this Windows host.
Rather than forcing external third-party toolchains (like MSYS2 or Chocolatey make), all project workflows will be supported with:
1. A cross-platform **Python automation runner (`run.py`)** or standard **Makefile** (for CI/Linux users).
2. Native **PowerShell automation scripts** located in `scripts/`:
   * `scripts/run-demo.ps1` (equivalent to `make demo`): runs the 10-minute live demonstration comparing Arm A vs. Arm B on periodic/bursty load with live console telemetry.
   * `scripts/run-reproduce.ps1` (equivalent to `make reproduce`): clean-room execution of the entire setup, calibration, and validation pipeline.
   * `scripts/run-analyze.ps1` (equivalent to `make analyze`): invokes `analysis/compute_kpis.py` and `analysis/plot_figures.py` to regenerate all tables and figures from `results/raw/`.

---

## 4. Pinned Component Versions with Verification Proofs

All pinned versions have been empirically verified on the local host and upstream registries:

| Component | Pinned Version | Verification Command | Actual Command Output Proof |
|---|---|---|---|
| **Minikube** | `v1.38.1` | `minikube version` | `minikube version: v1.38.1 commit: c93a4cb9311efc66b90d33ea03f75f2c4120e9b0` |
| **Kubernetes (Client)** | `v1.36.1` | `kubectl version --client` | `Client Version: v1.36.1 Kustomize Version: v5.8.1` |
| **Kubernetes (Minikube)**| `v1.35.1` (or `v1.36.1`) | `minikube start --help` | Supports official `v1.35.1` / dynamic `v1.36.1` preload |
| **Helm** | `v4.2.4` | `helm version` | `version.BuildInfo{Version:"v4.2.4", GitCommit:"3900f434fd3ef2b84065dc04508df48f288dba00", ...}` |
| **Docker Desktop / Engine**| `4.88.1` / `29.7.2` | `docker version` | `Server: Docker Desktop 4.88.1 Engine: Version 29.7.2 API version: 1.55` |
| **k6** | `v2.2.0` | `k6 version` | `k6.exe v2.2.0 (commit/00a9a1b7f5, go1.26.5, windows/amd64)` |
| **KEDA Helm Chart** | `2.21.0` | `helm search repo kedacore/keda --versions` | `kedacore/keda 2.21.0 2.21.0 Event-based autoscaler for workloads on Kubernetes` |
| **kube-prometheus-stack**| `92.1.1` | `helm search repo prometheus-community/kube-prometheus-stack --versions` | `prometheus-community/kube-prometheus-stack 92.1.1 v0.94.1 kube-prometheus-stack collects ...` |
| **Python** | `3.12.7` | `python --version` | `Python 3.12.7` |
| **FastAPI** | `0.142.2` | `python -m pip index versions fastapi` | PyPI verified: `0.142.2` |
| **Uvicorn** | `0.54.0` | `python -m pip index versions uvicorn` | PyPI verified: `0.54.0` |
| **Starlette** | `1.7.0` | `python -m pip index versions starlette` | PyPI verified: `1.7.0` |
| **prometheus-client** | `0.26.0` | `python -m pip index versions prometheus-client` | PyPI verified: `0.26.0` |
| **pytest** | `9.1.1` | `python -m pip index versions pytest` | PyPI verified: `9.1.1` |
| **pytest-asyncio** | `1.4.0` | `python -m pip index versions pytest-asyncio` | PyPI verified: `1.4.0` |
| **httpx** | `0.28.1` | `python -m pip index versions httpx` | PyPI verified: `0.28.1` |
| **anyio** | `4.9.0` | `python -m pip index versions anyio` | PyPI verified: `4.9.0` |
| **Prophet** | `1.5.0` | `python -m pip index versions prophet` | `prophet (1.5.0) Available: 1.5.0, 1.4.0, ... LATEST: 1.5.0` |
| **cmdstanpy** | `1.3.0` | `python -m pip index versions cmdstanpy` | PyPI verified: `1.3.0` |
| **PyTorch (LSTM CPU)** | `2.14.1+cpu` (or `2.14.1`) | `python -m pip index versions torch` | PyPI verified: `torch==2.14.1` (lightweight CPU build for O2 baseline) |
| **numpy** | `2.5.4` | `python -m pip index versions numpy` | PyPI verified: `2.5.4` |
| **pandas** | `3.0.6` | `python -m pip index versions pandas` | PyPI verified: `3.0.6` |
| **scipy** | `1.18.1` | `python -m pip index versions scipy` | PyPI verified: `1.18.1` |
| **statsmodels** | `0.15.0` | `python -m pip index versions statsmodels` | PyPI verified: `0.15.0` |
| **matplotlib** | `3.11.2` | `python -m pip index versions matplotlib` | PyPI verified: `3.11.2` |

---

## 5. Project Phases, Deliverables, and Rough Effort Estimates

| Phase | Description & Core Deliverables | Rough Effort Estimate |
|---|---|---|
| **P0** | **Environment Audit, Discrepancy Reconciliation & Master Plan**<br>Hardware audit, machine capacity model, campaign time model, `docs/SPEC_NOTES.md`, `PLAN.md`. | ~4–6 hours (Completed) |
| **P1 (O1)** | **Cluster, App, Load Generator, Baseline HPA, and Calibration**<br>Automate cluster deployment, build FastAPI app, create open-loop k6 scripts, reproduce HPA reactive lag, calibrate sustainable per-pod capacity, freeze `docs/SLO.md` and `docs/METRICS.md`, write oracle unit tests. | ~8–12 engineering hours |
| **P2 (O2)** | **Workloads, Predictability Quantifiers & Forecaster Comparison**<br>Generative specs for 5 patterns in `workloads/SPEC.md`, dev/acceptance seed generation and hashing, predictability metrics, real-trace validation, offline comparison of Prophet vs. LSTM vs. Seasonal-Naive on 60 s horizon, write `docs/O2_reference.md`. | ~10–14 engineering hours |
| **P3 (O3)** | **Forecaster Service & KEDA Predictive Scaler Integration**<br>Containerized Forecaster service, KEDA `ScaledObject` for Arm B, strict controller fairness configuration in `docs/FAIRNESS.md`, config validator, vertical slice demonstration plot (bursty run Arm A vs Arm B). | ~10–12 engineering hours |
| **P4 (O4)** | **Experiment Campaign Runner & Campaign Execution**<br>Resumable append-only campaign runner, pilot runs (1–2 reps), real overhead measurement, protocol freezing document (`docs/PROTOCOL_FROZEN.md`), execution of Block 1 (50 runs). | ~8 engineering hours + ~8–10 machine runtime hours |
| **P5 (O5)** | **Negative Tests, Degraded Mode & Recovery**<br>Scripted fault injection for NT-1 through NT-5, degraded-mode fallback implementation and verification, recovery measurements in `results/raw/nt/`, `acceptance/negative_tests.md`. | ~8–10 engineering hours |
| **P6** | **Analysis Pipeline, KPI Synthesis & Acceptance**<br>Script-driven analysis pipeline, compute KPI-1 through KPI-6 with bootstrap 95% CIs and Holm correction, RQ1–RQ3 answers, generate 300 dpi publication plots, write `docs/ACCEPTANCE_REPORT.md`. | ~10–14 engineering hours |
| **P7** | **Paper Sections, Reproducibility Package & Mentor Demo**<br>Complete IEEE paper Sections V–IX directly from CSV results, create reproducibility package (`scripts/run-reproduce.ps1`), 10-minute mentor demo script (`scripts/run-demo.ps1`), viva Q&A defense document. | ~12–16 engineering hours |

---

## 6. Repository Layout

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
├── scripts/              # PowerShell automation scripts (setup, demo, reproduce, analyze)
│   ├── setup-cluster.ps1
│   ├── run-demo.ps1
│   ├── run-reproduce.ps1
│   └── run-analyze.ps1
├── Makefile              # CI / Linux automation target wrapper
├── run.py                # Cross-platform entrypoint (Python task runner)
├── PROJECT_BRIEF.md      # Master research rules and non-negotiables
├── PLAN.md               # Master phase schedule and risk model
└── CHANGELOG.md          # Chronological record of major changes
```

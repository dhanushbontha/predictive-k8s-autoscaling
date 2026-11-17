# Predictive Kubernetes Autoscaling — Live Monitoring Dashboard

A modern, production-grade React monitoring dashboard built for **Predictive Autoscaling in Kubernetes Using Load Forecasting**. 

This interface allows evaluators, professors, and engineers to observe real-time workload forecasting, Kubernetes cluster utilization, KEDA predictive autoscaling mechanics, and benchmark comparisons between reactive CPU HPA and predictive autoscaling.

---

## 🌟 Key Architecture & Data Flow

```text
Incoming Traffic (Locust)
        │
        ▼
Target Application Pods (port 8080)
        │
        ▼
Prometheus (Scrapes http_requests_total, CPU, Memory)
        │
        ▼
FastAPI Backend (Prophet Time-Series Engine)
  ├── Live PromQL: sum(rate(http_requests_total[1m]))
  ├── Fitted Horizon: ds, yhat, yhat_lower, yhat_upper
  └── Metrics Gauges:
        ├── predicted_http_work_rps
        └── prediction_timestamp_seconds (Unix epoch)
        │
        ▼
KEDA ScaledObject (Target-App Predictive Scaler)
  ├── Freshness Gate: predicted_http_work_rps and (time() - prediction_timestamp_seconds < 45)
  ├── AverageValue Threshold: 10 RPS/pod
  └── Fallback: 2 replicas after 3 consecutive failures
        │
        ▼
Kubernetes HPA (Autoscaling Controller)
  ├── Scale-Up: 0s stabilization window (instant reaction)
  └── Scale-Down: 15s stabilization window
        │
        ▼
React Live Monitoring Dashboard (Vite + Recharts + Tailwind)
```

---

## 🚀 Features & Dashboard Views

### 1. Overview Dashboard (`Overview.jsx`)
- **Key Performance Indicators (KPIs):**
  - Actual Incoming Requests/sec (`http_work_rps`).
  - Predicted Requests/sec (`predicted_http_work_rps`) with provenance badge (`live_prometheus`).
  - Target-App Ready and Desired Pod Replicas.
  - Cluster CPU & Memory Utilization percentages against targets (50% CPU, 70% Memory).
- **Mini-Forecast Preview:**
  - Real-time Recharts visualization of the immediate 30-point prediction horizon.
- **Autoscaler Guard Status:**
  - Active PromQL trigger query, prediction age countdown, and failover state.
- **Pod Status Quick-List:**
  - Live table showing active pods, readiness, phase, restart count, and CPU millicores.

### 2. Workload Forecasting (`Forecasting.jsx`)
- **Interactive Multi-Horizon Chart:**
  - Recharts `ComposedChart` displaying central forecast estimate (`yhat`) alongside 80% credible uncertainty intervals (`yhat_lower`, `yhat_upper`).
  - Horizon selector (30, 50, 100 points).
  - Rich interactive tooltips displaying exact timestamp and prediction bounds.
- **Model Controls & Provenance:**
  - Model provenance indicator (`live_prometheus` vs cached fallback).
  - Force Model Retrain button to trigger an immediate forecast refit and gauge refresh.
- **Forecast Horizon Inspector Table:**
  - Point-by-point tabular view of upcoming predicted timestamps, point values, and uncertainty spread (&plusmn;req/s).

### 3. Kubernetes Cluster & Pods (`Kubernetes.jsx`)
- **Deployment Summary:**
  - Target deployment: `target-app` in namespace `default`.
  - Cluster connectivity status.
  - Resource requests (100m CPU, 128Mi RAM) and limits (500m CPU, 256Mi RAM).
- **Pod Metrics Table:**
  - Pod identifiers, phases (`Running`), container readiness (`1/1 Ready`), restarts, and age.
  - Dynamic visual utilization bars with color-coded thresholds (cyan &lt; 50%, amber 50–80%, rose &gt; 80%).

### 4. Predictive Autoscaler (`Autoscaling.jsx`)
- **KEDA ScaledObject Specification:**
  - Scaler configuration: `target-app-predictive-scaler`.
  - AverageValue threshold: `10` req/s per pod.
- **Freshness Gate Security:**
  - Evaluates `predicted_http_work_rps and (time() - prediction_timestamp_seconds < 45)`.
  - Visual status pill: **Fresh & Active** (&lt;45s) vs **Prediction Stale** (&ge;45s).
  - Explains the architectural safeguard preventing stale metrics from inducing permanent scaling loops.
- **Fallback Protection:**
  - Highlights the 3-failure threshold trigger and automated 2-replica safety floor during Prometheus or backend outages.
- **HPA Behavior Rules:**
  - Visual cards documenting aggressive scale-up (0s stabilization) and controlled scale-down (15s stabilization).

### 5. Workload Generator (`Workload.jsx`)
- **Profile Runner:**
  - Visual cards for all five benchmark profiles:
    1. **Stable:** Steady, uniform traffic baseline.
    2. **Gradual:** Linear ramp-up and ramp-down.
    3. **Bursty:** Sudden high-intensity flash crowds.
    4. **Periodic:** Sinusoidal diurnal wave.
    5. **Noisy:** Stochastic Poisson variations.
- **Parametric Sliders:**
  - Interactive sliders to safely configure Duration (30s to 600s), Concurrent Users (1 to 100), and Spawn Rate (1 to 20 users/s).
- **Safe Execution & Live Progress:**
  - Launches headless Locust instances via bounded backend endpoints.
  - Live animated progress bar showing elapsed time, active PID, and completion percentage.

### 6. Benchmark & Comparative Evaluation (`Experiments.jsx`)
- **Side-by-Side Analysis:**
  - Compares **Reactive CPU HPA (Baseline)** against **Predictive KEDA (Prophet)** across the 5 workload profiles.
  - Metrics compared: Response Time P95 (ms), Response Time P99 (ms), SLO Violations (&gt;200ms %), Scaling Reaction Delay (s), Average CPU %, Max Pod Replicas, and Total Core-Seconds.
- **Scientific Integrity:**
  - Displays real experimental data from `experiments/hpa_<profile>.csv`.
  - When predictive datasets have not yet been generated, cleanly highlights a **"Predictive Benchmark Pending"** banner without fabricating synthetic numbers.

---

## 🛠️ Technology Stack

- **Framework:** React 18
- **Build Tool:** Vite 6
- **Styling:** Tailwind CSS 3.4
- **Charts:** Recharts 2.15
- **Icons:** Lucide React
- **HTTP Client:** Axios

---

## 📦 Getting Started

### Prerequisites

- Node.js (v18 or higher recommended)
- Python 3.10+ (for running the backend)

### 1. Installation

From the repository root:

```bash
cd frontend
npm install
```

### 2. Development Server

Start the development server with Hot Module Replacement (HMR):

```bash
npm run dev
```

The frontend will run on [http://localhost:5173](http://localhost:5173).

> **Note:** The Vite development server automatically proxies `/api`, `/health`, and `/metrics` requests to the backend running at `http://localhost:8080`.

### 3. Production Build

To compile a minified production bundle into `frontend/dist/`:

```bash
npm run build
```

To preview the production build locally:

```bash
npm run preview
```

---

## ⚙️ Environment Variables

A `.env.example` file is provided in `frontend/`:

```env
# Optional: Set this only if the backend is hosted on a different host/port without proxy.
# Leave empty in local development to use the Vite reverse proxy.
VITE_API_BASE_URL=
```

---

## 🔬 Testing & Verification

The frontend communicates with the FastAPI backend endpoints tested in `tests/test_backend.py`. To run the backend test suite:

```bash
pytest tests/test_backend.py -v
```

All 32 backend test cases verify:
- Health and auto-refresh gauge status
- Live Prometheus range metrics fetching and Prophet prediction
- Thread-safe prediction gauge updates
- Freshness gate validation logic
- Kubernetes status parsing
- Autoscaling status and fallback evaluation
- Safe workload profile execution
- Experiment dataset comparison

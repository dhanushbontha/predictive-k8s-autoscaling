# Locust Load-Testing Setup

This directory contains load-testing scripts for benchmarking reactive HPA and predictive autoscaling against the `target-app` service.

## Prerequisites

1. Ensure the Python dependencies are installed:
   ```powershell
   pip install -r experiments/requirements.txt
   ```
2. Verify Locust is installed:
   ```powershell
   locust --version
   ```

---

## 1. Start Kubernetes Port-Forward

Before running load tests, forward traffic from local port `8000` to the Kubernetes `target-app-service`:

```powershell
kubectl port-forward svc/target-app-service 8000:8000
```

Keep this PowerShell terminal open while testing.

---

## 2. Running a Load Test

You can run Locust either headlessly (CLI) or via its web interface.

### Option A: Headless CLI (Automated with CSV Export)

Run a test with 5 simulated users, spawning 1 user per second, for 30 seconds, and exporting metric logs to CSV:

```powershell
locust -f experiments/locustfile.py --headless -u 5 -r 1 --run-time 30s --csv experiments/locust_results
```

- `-u 5`: 5 simulated concurrent users.
- `-r 1`: Spawn rate (1 user added per second).
- `--run-time 30s`: Duration of the test.
- `--csv experiments/locust_results`: Exports test statistics to CSV files prefixed with `locust_results_*.csv`.

### Option B: Interactive Web UI

Start Locust with its browser UI:

```powershell
locust -f experiments/locustfile.py
```

Then open your browser at:
👉 **[http://localhost:8089](http://localhost:8089)**

Enter the user count (e.g. 5 or 10), spawn rate (e.g. 2), and host (`http://127.0.0.1:8000`), then click **Start swarming**.

---

## 3. Monitoring HPA and Pods During the Test

In separate PowerShell windows, observe autoscaling behavior:

```powershell
# Watch HPA CPU percentage and replica adjustments in real time
kubectl get hpa target-app-hpa --watch

# Watch pod status and pod lifecycle events
kubectl get pods -l app=target-app --watch

# View current CPU and memory consumption per pod
kubectl top pods -l app=target-app
```

---

## 4. Stopping the Test and Inspecting Results

- For headless runs, the test stops automatically after `--run-time` expires. To stop prematurely, press `Ctrl + C`.
- In Web UI mode, click the **Stop** button in the top navigation bar.
- CSV metrics (`locust_results_stats.csv`, `locust_results_failures.csv`, `locust_results_stats_history.csv`) contain response times, throughput (RPS), and percentiles for analysis.

---

## 5. Experiment Metrics Collector (`collect_metrics.py`)

The collector queries the Prometheus HTTP API at configurable intervals, sampling CPU, memory, Kubernetes scaling states, and application HTTP metrics, writing them to a structured CSV file for benchmarking and time-series forecasting.

### Running the Collector

1. Forward the Prometheus service port (if not already forwarded):
   ```powershell
   kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090
   ```
2. Start the collector in another terminal:
   ```powershell
   # Collect metrics every 5 seconds until Ctrl+C
   python experiments/collect_metrics.py --output experiments/metrics_collected.csv

   # Collect for a fixed experiment duration (e.g., 120 seconds with 2-second sampling)
   python experiments/collect_metrics.py --interval 2 --duration 120 --output experiments/metrics_collected.csv
   ```

### Command-Line Arguments
- `--prometheus-url`: Base URL of Prometheus (default: `http://127.0.0.1:9090` or `PROMETHEUS_BASE_URL` env var).
- `--output`: Output CSV file path (default: `experiments/metrics_collected.csv`).
- `--interval`: Collection interval in seconds (default: `5.0`).
- `--duration`: Run duration in seconds (default: runs indefinitely until interrupted).
- `--pod-prefix`: Target pod label/prefix (default: `target-app`).
- `--deployment-name`: Deployment name in Prometheus metrics (default: `target-app`).
- `--hpa-name`: HorizontalPodAutoscaler name in Prometheus metrics (default: `target-app-hpa`).
- `--service-name`: Scraped Service name in Prometheus job label (default: `target-app-service`).

### CSV Schema & Units

| Column | Unit / Format | Description |
| :--- | :--- | :--- |
| `timestamp_utc` | ISO-8601 UTC | Sample timestamp (e.g. `2026-10-29T06:53:05.111860+00:00`) |
| `epoch_timestamp` | Seconds | Unix epoch timestamp with millisecond precision |
| `collection_status` | String | `ok` if metrics retrieved, or `no_data_or_unreachable` |
| `cpu_usage_cores` | Cores | Instantaneous CPU core utilization rate over 1 minute |
| `memory_working_set_bytes` | Bytes | Active container memory working set |
| `memory_working_set_mb` | Megabytes | Memory working set converted to MB |
| `pod_count` | Count | Number of active pods for target-app |
| `deployment_replicas` | Count | Replicas defined in target-app Deployment |
| `deployment_available_replicas` | Count | Available (ready) replicas in Deployment |
| `hpa_current_replicas` | Count | Active pod replicas managed by HPA |
| `hpa_desired_replicas` | Count | Desired pod replicas calculated by HPA |
| `http_requests_total` | Count | Cumulative HTTP request counter |
| `http_requests_work_total` | Count | Cumulative HTTP requests to `/api/work` |
| `http_rps` | Requests/sec | Overall request rate across all endpoints (1m rate) |
| `http_work_rps` | Requests/sec | Request rate specifically on `/api/work` (1m rate) |
| `http_error_rps` | Requests/sec | HTTP 5xx error rate (1m rate) |
| `latency_p50_seconds` | Seconds | Median request duration |
| `latency_p95_seconds` | Seconds | 95th percentile request duration |
| `latency_p99_seconds` | Seconds | 99th percentile request duration |

### Troubleshooting
- **All metrics show empty strings / status `no_data_or_unreachable`**:
  Ensure Prometheus is reachable (`Test-NetConnection -ComputerName 127.0.0.1 -Port 9090`). Start port-forwarding with `kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090`.
- **Application HTTP metrics are empty, but CPU/memory are present**:
  Verify the ServiceMonitor is applied and target is up: visit `http://localhost:9090/targets` and verify `serviceMonitor/default/target-app-servicemonitor/0` is `UP`.

---

## 6. SLO Evaluation (`evaluate_slo.py`)

Service Level Objectives (SLOs) define the target performance boundaries for our application during benchmark runs. This analysis evaluates whether the system satisfies latency and reliability goals under fluctuating load.

### Default SLO Targets
- **P95 Latency Target**: $\le 0.200\,\text{s}$ (200 ms).
- **HTTP Error-Rate Target**: $\le 1.0\%$ ($\frac{\text{HTTP 4xx+5xx RPS}}{\text{Total RPS}} \times 100$).

### Usage
```powershell
# Evaluate SLO adherence on a recorded dataset
python experiments/evaluate_slo.py --csv experiments/metrics_collected.csv

# Customize target thresholds
python experiments/evaluate_slo.py --csv experiments/metrics_collected.csv --target-p95-latency 0.150 --target-error-rate 0.5

# Output as JSON for automated pipelines
python experiments/evaluate_slo.py --csv experiments/metrics_collected.csv --json
```

### Calculated Metrics
- Number of valid intervals vs. total recorded rows.
- Number and percentage of intervals violating the P95 latency target.
- Number and percentage of intervals violating the HTTP error-rate target.
- Total wall-clock duration (seconds) spent in violation of latency and error-rate SLOs.
- Peak observed P95 and P99 response latency.
- Mean and peak CPU core utilization and memory working set.
- Peak pod count observed during the experiment.
- Missing data count per metric field.

---

## 7. Scaling Delay Analysis (`analyze_scaling.py`)

Autoscaling delay measures the agility of the autoscaling system. It quantifies how quickly Kubernetes detects a demand surge and provisions additional compute capacity.

### Scaling Delay Definitions
1. **Surge Onset ($T_0$)**: The timestamp where workload intensity (`http_work_rps` or `cpu_usage_cores`) exceeds a sustained threshold (default: $\ge 2$ consecutive samples above threshold).
2. **HPA Decision Delay ($T_{\text{HPA}} - T_0$)**: Wall-clock duration until HPA's desired replica count increases above baseline (`hpa_desired_replicas > baseline`).
3. **Pod Readiness Delay ($T_{\text{Pods}} - T_0$)**: Wall-clock duration until Kubernetes marks new container replicas as running and ready (`pod_count > baseline`).

### Usage
```powershell
# Analyze scaling delay using /api/work throughput trigger
python experiments/analyze_scaling.py --csv experiments/metrics_collected.csv --threshold 5.0

# Analyze using CPU surge trigger
python experiments/analyze_scaling.py --csv experiments/metrics_collected.csv --metric cpu_usage_cores --threshold 0.15

# Output as JSON
python experiments/analyze_scaling.py --csv experiments/metrics_collected.csv --threshold 5.0 --json
```

---

## 8. Measurement Limitations & Benchmarking Guidelines

When comparing **Reactive HPA** with **Predictive Autoscaling (Prophet + KEDA)**:

1. **Sampling Resolution**: Metrics are scraped every 15s by Prometheus and polled every 2s–5s by the collector. Scaling delay precision is bound to the sampling interval ($\pm 2\,\text{s}$).
2. **Histogram Resolution**: P95 and P99 latencies are computed from Prometheus exponential histogram buckets (`http_request_duration_highr_seconds_bucket`). True tail latencies are approximations based on quantile interpolation.
3. **Stabilization Window**: Standard Kubernetes HPA includes cooldown and stabilization periods (default 5-minute scale-down window).
4. **Consistency**: Use identical Locust workload profiles, duration, and SLO thresholds when comparing reactive vs. predictive autoscalers.



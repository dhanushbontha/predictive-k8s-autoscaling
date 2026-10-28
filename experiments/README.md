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

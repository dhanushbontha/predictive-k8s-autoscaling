# FastAPI Backend for Predictive Kubernetes Autoscaling

This directory contains the FastAPI backend service for **Predictive Autoscaling in Kubernetes using Load Forecasting**.

The backend retrieves live application metrics from Prometheus, fits Prophet time-series models online, and provides forward-looking workload projections for proactive scaling (e.g. KEDA External Scalers).

---

## 1. Architecture and Data Flow

```
+--------------------------------------------------------------+
|                Kubernetes Cluster (Minikube)                 |
|                                                              |
|   +---------------------+        +-----------------------+   |
|   | Target Application  | -----> |      Prometheus       |   |
|   |  (GET /api/work)    | metrics|  (kube-prometheus)   |   |
|   +---------------------+        +-----------+-----------+   |
|                                              |               |
|                                     range    | instant       |
|                                     queries  | queries       |
|                                              v               |
|                                  +-----------------------+   |
|                                  |  Forecasting Backend  |   |
|                                  |   (backend/main.py)   |   |
|                                  |   - Prometheus Client |   |
|                                  |   - Prophet Online    |   |
|                                  |   - Model Cache (30s) |   |
|                                  +-----------+-----------+   |
|                                              |               |
|                                              v               |
|                                  +-----------------------+   |
|                                  |  REST Endpoints       |   |
|                                  |  - /health            |   |
|                                  |  - /api/forecast      |   |
|                                  |  - /api/metrics       |   |
|                                  |  - /api/predicted-load|   |
|                                  +-----------------------+   |
+--------------------------------------------------------------+
```

### Provenance Tracking
All forecast and metric responses explicitly report a `provenance` field:
- `live_prometheus`: Model fitted dynamically on live Prometheus observations.
- `static_csv_fallback`: Model derived from pre-generated experimental CSVs when fallback is enabled and Prometheus is unreachable.

---

## 2. Environment Variables

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `PORT` | `8080` | Port for the backend service |
| `PROMETHEUS_URL` | `""` (local) or in cluster: `http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090` | Base URL of Prometheus service |
| `TARGET_SERVICE_NAME` | `target-app-service` | Prometheus scraped job label |
| `PROMETHEUS_QUERY` | `sum(rate(http_requests_total{job="target-app-service", handler="/api/work"}[1m]))` | PromQL query to retrieve request rate on `/api/work` |
| `HISTORY_WINDOW_SECONDS`| `300` (5 minutes) | Lookback history window for training data |
| `QUERY_STEP` | `5s` | Query interval step for Prometheus range queries |
| `FORECAST_HORIZON_STEPS`| `15` | Lookahead steps to project into the future |
| `FORECAST_CACHE_TTL_SECONDS` | `30` | In-memory cache duration to prevent retraining on every request |
| `PREDICTION_HORIZON_STEPS` | `5` | Default steps ahead for `/api/predicted-load` |
| `ENABLE_STATIC_FALLBACK` | `true` | When `false`, returns HTTP error if Prometheus is unreachable |
| `FORECAST_CSV_PATH` | `/app/data/forecast_periodic.csv` | Path for static forecast fallback |
| `METRICS_CSV_PATH` | `/app/data/hpa_periodic.csv` | Path for static metrics fallback |

---

## 3. Endpoints & Example Responses

### 1. GET `/health`
Reports health status and whether Prometheus and static fallback are configured.

```json
{
  "status": "ok",
  "service": "predictive-k8s-autoscaling-backend",
  "version": "1.1.0",
  "prometheus_configured": true,
  "static_fallback_enabled": true
}
```

### 2. GET `/api/forecast`
Generates or retrieves cached Prophet forecast points based on live Prometheus data.

**Query Parameters:**
- `limit` (optional integer): Returns only the last $N$ points.
- `force_refresh` (boolean, default `false`): Bypasses the cache and re-queries Prometheus.

```json
{
  "provenance": "live_prometheus",
  "source_detail": "Prometheus query on 'sum(rate(http_requests_total{job=\"target-app-service\", handler=\"/api/work\"}[1m]))' over 300s window",
  "total_points": 2,
  "data": [
    {
      "ds": "2026-10-29 18:22:22.763000011",
      "yhat": 0.0,
      "yhat_lower": 0.0,
      "yhat_upper": 0.0
    },
    {
      "ds": "2026-10-29 18:22:27.763000011",
      "yhat": 0.0,
      "yhat_lower": 0.0,
      "yhat_upper": 0.0
    }
  ],
  "generated_at_epoch": 1793298073.405
}
```

### 3. GET `/api/metrics`
Returns the latest instantaneous Prometheus request rate measurement alongside the latest forecast point.

```json
{
  "status": "ok",
  "observed": {
    "timestamp_utc": "2026-10-29T18:21:13.421999931+00:00",
    "epoch_timestamp": 1793298073.422,
    "collection_status": "ok",
    "http_work_rps": 0.0
  },
  "forecast": {
    "ds": "2026-10-29 18:22:27.763000011",
    "yhat": 0.0,
    "yhat_lower": 0.0,
    "yhat_upper": 0.0,
    "provenance": "live_prometheus"
  },
  "provenance": "live_prometheus",
  "note": "Provenance indicates whether observed telemetry came from 'live_prometheus' or 'static_csv_fallback'."
}
```

### 4. GET `/api/predicted-load`
Returns a numeric, non-negative target metric (`predicted_http_work_rps`) designed for KEDA external autoscaling.

```json
{
  "metric_name": "predicted_http_work_rps",
  "unit": "requests_per_second",
  "predicted_load": 0.0,
  "target_timestamp": "2026-10-29 18:22:27.763000011",
  "horizon_steps": 5,
  "calculation_method": "point prediction at horizon step 5",
  "provenance": "live_prometheus",
  "keda_integration_status": "ready_for_keda_external_scaler"
}
```

---

## 4. How to Deploy to Minikube

### A. Run Unit Tests
```powershell
python -m pytest tests/test_backend.py -v
```

### B. Build Docker Image
```powershell
docker build -t predictive-forecasting-backend:1.1 -f backend/Dockerfile backend
```

### C. Load Image into Minikube
```powershell
minikube image load predictive-forecasting-backend:1.1
```

### D. Apply Kubernetes Manifests & Rollout
```powershell
kubectl apply -f kubernetes/forecasting-backend-deployment.yaml -f kubernetes/forecasting-backend-service.yaml
kubectl rollout status deployment/forecasting-backend --timeout=90s
```

### E. Port-Forward and Verify
In a separate terminal:
```powershell
kubectl port-forward svc/forecasting-backend-service 8080:8080
```

Verify endpoints:
```powershell
curl http://127.0.0.1:8080/health
curl "http://127.0.0.1:8080/api/forecast?limit=2"
curl http://127.0.0.1:8080/api/metrics
curl http://127.0.0.1:8080/api/predicted-load
```

Verify logs confirm live Prometheus queries:
```powershell
kubectl logs -l app=forecasting-backend --tail=25
```
*(Look for `Retrieving live metrics from Prometheus...` and `Fitting Prophet model on N live Prometheus observations`)*

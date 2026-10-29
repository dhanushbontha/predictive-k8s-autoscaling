# FastAPI Backend for Predictive Kubernetes Autoscaling

This directory contains the FastAPI backend service for **Predictive Autoscaling in Kubernetes using Load Forecasting**.

The backend serves as the integration layer between offline/batch Prophet forecasting models and real-time autoscaling consumers (such as KEDA External Metric Scalers, custom controllers, or monitoring dashboards).

---

## 1. Architecture and Purpose

```
+---------------------------------------+
|  Historical Metrics & Prophet Forecasts|
|  (experiments/*.csv, forecasting/*.csv)|
+-------------------+-------------------+
                    |
                    v
    +-------------------------------+
    |        FastAPI Backend        |
    |       (backend/main.py)       |
    +---------------+---------------+
                    |
      +-------------+-------------+
      |                           |
      v                           v
+---------------+       +-------------------+
|  HTTP Endpoints|       | External Metrics   |
|  /health      |       | for future KEDA    |
|  /api/forecast|       | Scaler Integration|
|  /api/metrics |       | (/api/predicted-  |
+---------------+       |  load)            |
                        +-------------------+
```

The service exposes HTTP endpoints to:
1. Inspect generated Prophet workload forecasts with prediction intervals.
2. Distinguish between historical observed telemetry and forecast projections.
3. Expose a single numeric, non-negative target metric (`predicted_http_work_rps`) designed for KEDA external metric autoscaling.

---

## 2. Installation

Install backend dependencies:

```powershell
pip install -r backend/requirements.txt
```

---

## 3. How to Run Locally

Start the backend server using Uvicorn from the repository root:

```powershell
# Default port 8080
uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload
```

Or run directly via Python:

```powershell
python backend/main.py
```

Interactive OpenAPI documentation is available at:
👉 **[http://localhost:8080/docs](http://localhost:8080/docs)**

---

## 4. Environment Variables

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `PORT` | `8080` | HTTP port for the FastAPI server |
| `FORECAST_CSV_PATH` | `forecasting/forecast_periodic.csv` | Relative or absolute path to generated Prophet forecast CSV |
| `METRICS_CSV_PATH` | `experiments/hpa_periodic.csv` | Relative or absolute path to observed experiment metrics CSV |
| `PREDICTION_HORIZON_STEPS` | `5` | Default number of forecast steps ahead used for autoscaling |

---

## 5. Endpoints and Example Responses

### 1. GET `/health`
Health check endpoint indicating service availability.

**Request:**
```http
GET /health HTTP/1.1
Host: localhost:8080
```

**Response (`200 OK`):**
```json
{
  "status": "ok",
  "service": "predictive-k8s-autoscaling-backend",
  "version": "1.0.0"
}
```

---

### 2. GET `/api/forecast`
Returns the generated Prophet forecast time-series points. Supports an optional `limit` query parameter.

**Request:**
```http
GET /api/forecast?limit=3 HTTP/1.1
Host: localhost:8080
```

**Response (`200 OK`):**
```json
{
  "source_file": "forecast_periodic.csv",
  "total_points": 3,
  "data": [
    {
      "ds": "2026-10-29 10:35:58.620585",
      "yhat": 25.723,
      "yhat_lower": 20.9556,
      "yhat_upper": 30.3445
    },
    {
      "ds": "2026-10-29 10:36:00.620585",
      "yhat": 25.9325,
      "yhat_lower": 21.3943,
      "yhat_upper": 30.4887
    },
    {
      "ds": "2026-10-29 10:36:02.620585",
      "yhat": 26.1419,
      "yhat_lower": 21.2527,
      "yhat_upper": 30.7457
    }
  ]
}
```

---

### 3. GET `/api/metrics`
Returns the latest available historical experiment snapshot alongside the latest predicted forecast value. Clearly distinguishes historical measurements from forecasted projections.

**Request:**
```http
GET /api/metrics HTTP/1.1
Host: localhost:8080
```

**Response (`200 OK`):**
```json
{
  "status": "ok",
  "observed": {
    "timestamp_utc": "2026-10-29T10:35:32.622158+00:00",
    "epoch_timestamp": 1793270132.622,
    "collection_status": "ok",
    "http_work_rps": 18.2341,
    "http_rps": 19.5123,
    "cpu_usage_cores": 0.1425,
    "pod_count": 2,
    "hpa_current_replicas": 2,
    "hpa_desired_replicas": 2
  },
  "forecast": {
    "ds": "2026-10-29 10:36:02.620585",
    "yhat": 26.1419,
    "yhat_lower": 21.2527,
    "yhat_upper": 30.7457
  },
  "note": "Observed metrics represent historical experiment recordings; forecast values are Prophet model projections. These do not represent live real-time cluster telemetry."
}
```

---

### 4. GET `/api/predicted-load`
Returns a numeric, non-negative predicted request rate intended for future consumption by a KEDA external metric scaler.

**Query Parameters:**
- `horizon_steps` (integer, default `5`): Lookahead horizon steps in the forecast series.
- `aggregation` (string, default `point`):
  - `point`: The predicted load exactly at the horizon step.
  - `max`: The peak load across the next `horizon_steps`.
  - `mean`: The average predicted load across the next `horizon_steps`.

**Request:**
```http
GET /api/predicted-load?horizon_steps=5&aggregation=point HTTP/1.1
Host: localhost:8080
```

**Response (`200 OK`):**
```json
{
  "metric_name": "predicted_http_work_rps",
  "unit": "requests_per_second",
  "predicted_load": 26.1419,
  "target_timestamp": "2026-10-29 10:36:02.620585",
  "horizon_steps": 5,
  "calculation_method": "point prediction at horizon step 5",
  "keda_integration_status": "ready_for_keda_external_scaler"
}
```

---

## 6. How Predicted Load is Derived

1. **Source Data**: The endpoint accesses the predicted time-series dataframe produced by Prophet (`forecasting/forecast.py`).
2. **Horizon Selection**: The query extracts the lookahead window (`horizon_steps`) representing projected load seconds into the future.
3. **Aggregation Strategy**:
   - `point`: Proactively scales to the exact future load expected $N$ steps ahead.
   - `max`: Conservative autoscaling that provisions ahead for the worst-case peak within the horizon window to prevent SLO degradation.
   - `mean`: Dampened autoscaling that smooths over sudden transient forecast spikes.
4. **Boundary Checks**: All predicted values are bounded by `max(0.0, yhat)` to guarantee that autoscaling controllers never receive negative request rate targets.

---

## 7. Assumptions and Limitations

- **Not Yet Integrated with KEDA**: This service provides the HTTP metric endpoint. Actual KEDA autoscaling (`ScaledObject` with `external` or `prometheus` scaler) is a subsequent milestone.
- **Data Source**: Endpoints currently read batch/offline forecast CSV outputs. In a full production loop, this service can be paired with a continuous streaming Prometheus scraper and online model updates.
- **Missing Data Handling**: If forecast or experiment metrics CSVs are missing or malformed, the API returns descriptive HTTP error codes (`404 Not Found` or `422 Unprocessable Entity`) instead of failing silently or fabricating false metrics.

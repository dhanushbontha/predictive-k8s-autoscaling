import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Ensure root workspace is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

backend_dir = root_dir / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from backend.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures for deterministic test datasets
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_forecast_csv(tmp_path):
    csv_file = tmp_path / "valid_forecast.csv"
    content = (
        "ds,yhat,yhat_lower,yhat_upper\n"
        "2026-10-29 10:35:44.620585,24.2565,20.4413,28.5056\n"
        "2026-10-29 10:35:46.620585,24.4660,20.4755,28.5081\n"
        "2026-10-29 10:35:48.620585,24.6754,20.7164,29.1028\n"
        "2026-10-29 10:35:50.620585,24.8849,20.6522,28.9962\n"
        "2026-10-29 10:35:52.620585,25.0944,20.9717,29.4902\n"
    )
    csv_file.write_text(content, encoding="utf-8")
    return str(csv_file)


@pytest.fixture
def valid_metrics_csv(tmp_path):
    csv_file = tmp_path / "valid_metrics.csv"
    content = (
        "timestamp_utc,epoch_timestamp,collection_status,cpu_usage_cores,memory_working_set_bytes,"
        "memory_working_set_mb,pod_count,deployment_replicas,deployment_available_replicas,"
        "hpa_current_replicas,hpa_desired_replicas,http_requests_total,http_requests_work_total,"
        "http_rps,http_work_rps,http_error_rps,latency_p50_seconds,latency_p95_seconds,latency_p99_seconds\n"
        "2026-10-29T10:35:40.000000+00:00,1793270140.0,ok,0.152,42344448,40.38,2,2,2,2,2,27263,20836,19.5,18.9,0,0.015,0.035,0.045\n"
    )
    csv_file.write_text(content, encoding="utf-8")
    return str(csv_file)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "predictive-k8s-autoscaling-backend"
    assert "version" in data


def test_forecast_endpoint_valid_data(valid_forecast_csv, monkeypatch):
    monkeypatch.setenv("FORECAST_CSV_PATH", valid_forecast_csv)
    response = client.get("/api/forecast")
    assert response.status_code == 200
    data = response.json()
    assert data["total_points"] == 5
    assert len(data["data"]) == 5
    first = data["data"][0]
    assert first["ds"] == "2026-10-29 10:35:44.620585"
    assert first["yhat"] == 24.2565
    assert first["yhat_lower"] == 20.4413
    assert first["yhat_upper"] == 28.5056


def test_forecast_endpoint_with_limit(valid_forecast_csv, monkeypatch):
    monkeypatch.setenv("FORECAST_CSV_PATH", valid_forecast_csv)
    response = client.get("/api/forecast?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total_points"] == 2
    assert len(data["data"]) == 2
    # Limit takes tail points
    assert data["data"][-1]["yhat"] == 25.0944


def test_metrics_endpoint_valid_data(valid_forecast_csv, valid_metrics_csv, monkeypatch):
    monkeypatch.setenv("FORECAST_CSV_PATH", valid_forecast_csv)
    monkeypatch.setenv("METRICS_CSV_PATH", valid_metrics_csv)
    response = client.get("/api/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "note" in data
    # Check observed section
    observed = data["observed"]
    assert observed is not None
    assert observed["timestamp_utc"] == "2026-10-29T10:35:40.000000+00:00"
    assert observed["http_work_rps"] == 18.9
    assert observed["pod_count"] == 2
    # Check forecast section
    forecast = data["forecast"]
    assert forecast is not None
    assert forecast["yhat"] == 25.0944


def test_predicted_load_endpoint_point(valid_forecast_csv, monkeypatch):
    monkeypatch.setenv("FORECAST_CSV_PATH", valid_forecast_csv)
    response = client.get("/api/predicted-load?horizon_steps=3&aggregation=point")
    assert response.status_code == 200
    data = response.json()
    assert data["metric_name"] == "predicted_http_work_rps"
    assert data["unit"] == "requests_per_second"
    assert isinstance(data["predicted_load"], float)
    assert data["predicted_load"] >= 0.0
    assert data["horizon_steps"] == 3
    assert "point prediction" in data["calculation_method"]
    assert "ready_for_keda_external_scaler" in data["keda_integration_status"]


def test_predicted_load_endpoint_aggregations(valid_forecast_csv, monkeypatch):
    monkeypatch.setenv("FORECAST_CSV_PATH", valid_forecast_csv)

    # Max aggregation
    res_max = client.get("/api/predicted-load?horizon_steps=5&aggregation=max")
    assert res_max.status_code == 200
    assert res_max.json()["predicted_load"] == 25.0944

    # Mean aggregation
    res_mean = client.get("/api/predicted-load?horizon_steps=5&aggregation=mean")
    assert res_mean.status_code == 200
    expected_mean = round((24.2565 + 24.4660 + 24.6754 + 24.8849 + 25.0944) / 5, 4)
    assert res_mean.json()["predicted_load"] == expected_mean


def test_missing_forecast_file(tmp_path, monkeypatch):
    non_existent = str(tmp_path / "missing_file.csv")
    monkeypatch.setenv("FORECAST_CSV_PATH", non_existent)
    response = client.get("/api/forecast")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_empty_forecast_csv(tmp_path, monkeypatch):
    empty_file = tmp_path / "empty.csv"
    empty_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("FORECAST_CSV_PATH", str(empty_file))
    response = client.get("/api/forecast")
    assert response.status_code == 422
    assert "empty" in response.json()["detail"].lower()


def test_missing_required_columns(tmp_path, monkeypatch):
    bad_cols_file = tmp_path / "bad_cols.csv"
    bad_cols_file.write_text("timestamp,value\n2026-01-01,10.0\n", encoding="utf-8")
    monkeypatch.setenv("FORECAST_CSV_PATH", str(bad_cols_file))
    response = client.get("/api/forecast")
    assert response.status_code == 422
    assert "missing required columns" in response.json()["detail"].lower()


def test_non_numeric_forecast_values(tmp_path, monkeypatch):
    corrupt_file = tmp_path / "corrupt.csv"
    corrupt_file.write_text("ds,yhat\n2026-01-01 10:00:00,corrupted_value\n", encoding="utf-8")
    monkeypatch.setenv("FORECAST_CSV_PATH", str(corrupt_file))
    response = client.get("/api/forecast")
    assert response.status_code == 422
    assert "non-numeric" in response.json()["detail"].lower()


def test_invalid_timestamps_in_forecast(tmp_path, monkeypatch):
    bad_ts_file = tmp_path / "bad_ts.csv"
    bad_ts_file.write_text("ds,yhat\nnot-a-timestamp,10.5\n", encoding="utf-8")
    monkeypatch.setenv("FORECAST_CSV_PATH", str(bad_ts_file))
    response = client.get("/api/forecast")
    assert response.status_code == 422
    assert "invalid timestamp" in response.json()["detail"].lower()

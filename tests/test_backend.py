import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

# Ensure root workspace is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

backend_dir = root_dir / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from backend.main import (
    app,
    fetch_prometheus_range_metrics,
    parse_prometheus_to_prophet_dataframe,
    forecast_cache,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures for deterministic test datasets
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure forecast cache is cleared before each test."""
    forecast_cache.clear()
    yield
    forecast_cache.clear()


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
# Unit tests: Health check
# ---------------------------------------------------------------------------

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "predictive-k8s-autoscaling-backend"
    assert "version" in data
    assert "prometheus_configured" in data
    assert "static_fallback_enabled" in data


# ---------------------------------------------------------------------------
# Unit tests: Prometheus parsing and DataFrame conversion
# ---------------------------------------------------------------------------

def test_parse_prometheus_success():
    points = [
        (1700000000.0, 10.5),
        (1700000005.0, 11.0),
        (1700000010.0, 12.0),
        (1700000015.0, 13.5),
        (1700000020.0, 14.0),
    ]
    df = parse_prometheus_to_prophet_dataframe(points, min_observations=5)
    assert len(df) == 5
    assert list(df.columns) == ["ds", "y"]
    assert df["ds"].dt.tz is None
    assert df["ds"].is_monotonic_increasing
    assert list(df["y"]) == [10.5, 11.0, 12.0, 13.5, 14.0]


def test_parse_prometheus_insufficient_points():
    points = [(1700000000.0, 10.5), (1700000005.0, 11.0)]
    with pytest.raises(Exception) as excinfo:
        parse_prometheus_to_prophet_dataframe(points, min_observations=5)
    assert "Insufficient Prometheus observations" in str(excinfo.value.detail)


def test_fetch_prometheus_range_metrics_parsing():
    mock_payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"job": "target-app-service"},
                    "values": [
                        [1700000000.0, "5.2"],
                        [1700000005.0, "6.1"],
                        [1700000010.0, "NaN"],  # Non-finite should be filtered
                        [1700000015.0, "-1.5"], # Negative clamped to 0.0
                    ],
                }
            ],
        },
    }
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_payload
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        pts = fetch_prometheus_range_metrics("http://fake-prom:9090", "up", 100, 200)
        assert len(pts) == 3
        assert pts[0] == (1700000000.0, 5.2)
        assert pts[1] == (1700000005.0, 6.1)
        assert pts[2] == (1700000015.0, 0.0)


def test_fetch_prometheus_timeout():
    import requests
    with patch("requests.get", side_effect=requests.exceptions.Timeout("Connection timed out")):
        with pytest.raises(Exception) as excinfo:
            fetch_prometheus_range_metrics("http://fake-prom:9090", "up", 100, 200)
        assert excinfo.value.status_code == 502
        assert "timed out" in excinfo.value.detail.lower()


# ---------------------------------------------------------------------------
# Unit tests: Live Prometheus forecasting & Provenance
# ---------------------------------------------------------------------------

def test_live_prometheus_forecast_success(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://fake-prom:9090")
    monkeypatch.setenv("ENABLE_STATIC_FALLBACK", "false")

    # Generate 10 mock points
    mock_values = [[1700000000.0 + i * 5, str(10.0 + i * 0.5)] for i in range(10)]
    mock_payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": {}, "values": mock_values}],
        },
    }

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_payload
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        response = client.get("/api/forecast?limit=3")
        assert response.status_code == 200
        data = response.json()
        assert data["provenance"] == "live_prometheus"
        assert len(data["data"]) == 3
        assert all(pt["yhat"] >= 0.0 for pt in data["data"])

        # Subsequent call should hit cache
        cache_resp = client.get("/api/predicted-load")
        assert cache_resp.status_code == 200
        assert cache_resp.json()["provenance"] == "live_prometheus"
        assert cache_resp.json()["predicted_load"] >= 0.0


def test_live_prometheus_failure_with_fallback(valid_forecast_csv, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://fake-prom:9090")
    monkeypatch.setenv("ENABLE_STATIC_FALLBACK", "true")
    monkeypatch.setenv("FORECAST_CSV_PATH", valid_forecast_csv)

    import requests
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError("Connection refused")):
        response = client.get("/api/forecast?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert data["provenance"] == "static_csv_fallback"
        assert len(data["data"]) == 2


def test_live_prometheus_failure_without_fallback_raises_error(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://fake-prom:9090")
    monkeypatch.setenv("ENABLE_STATIC_FALLBACK", "false")

    import requests
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError("Connection refused")):
        response = client.get("/api/forecast")
        assert response.status_code == 502
        assert "failed to query prometheus" in response.json()["detail"].lower()


def test_metrics_endpoint_with_live_instant_query(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://fake-prom:9090")
    monkeypatch.setenv("ENABLE_STATIC_FALLBACK", "false")

    # Mock range query for forecast
    mock_range_values = [[1700000000.0 + i * 5, str(10.0 + i)] for i in range(10)]
    mock_instant_payload = {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1700000050.0, "15.42"]}]},
    }
    mock_range_payload = {
        "status": "success",
        "data": {"resultType": "matrix", "result": [{"metric": {}, "values": mock_range_values}]},
    }

    def mock_get_handler(url, *args, **kwargs):
        mock_r = MagicMock()
        mock_r.status_code = 200
        mock_r.raise_for_status.return_value = None
        if "query_range" in url:
            mock_r.json.return_value = mock_range_payload
        else:
            mock_r.json.return_value = mock_instant_payload
        return mock_r

    with patch("requests.get", side_effect=mock_get_handler):
        response = client.get("/api/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["provenance"] == "live_prometheus"
        assert data["observed"]["http_work_rps"] == 15.42
        assert data["forecast"]["provenance"] == "live_prometheus"


# ---------------------------------------------------------------------------
# Unit tests: Cache Expiration
# ---------------------------------------------------------------------------

def test_forecast_cache_expiration(monkeypatch):
    forecast_cache.update(
        df=MagicMock(), provenance="live_prometheus", source_detail="test"
    )
    # Set timestamp in past
    forecast_cache.timestamp = time.time() - 40
    assert not forecast_cache.is_valid(ttl_seconds=30)
    assert forecast_cache.is_valid(ttl_seconds=60)


# ---------------------------------------------------------------------------
# Unit tests: Prometheus Gauges & /metrics endpoint
# ---------------------------------------------------------------------------

def test_metrics_endpoint_returns_prometheus_format():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")
    content = response.text
    # Verify gauges exist
    assert "predicted_http_work_rps" in content
    assert "prediction_provenance" in content


def test_gauges_updated_on_live_predicted_load(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://fake-prom:9090")
    monkeypatch.setenv("ENABLE_STATIC_FALLBACK", "false")

    mock_values = [[1700000000.0 + i * 5, str(10.0 + i)] for i in range(10)]
    mock_payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": {}, "values": mock_values}],
        },
    }

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_payload
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        # Call /api/predicted-load
        response = client.get("/api/predicted-load?horizon_steps=3&aggregation=point")
        assert response.status_code == 200
        data = response.json()
        assert data["metric_name"] == "predicted_http_work_rps"
        assert data["unit"] == "requests_per_second"
        assert data["provenance"] == "live_prometheus"
        predicted_val = data["predicted_load"]
        assert predicted_val > 0.0

        # Scrape /metrics and verify gauge values
        metrics_resp = client.get("/metrics")
        assert metrics_resp.status_code == 200
        content = metrics_resp.text
        # predicted_http_work_rps should equal predicted_val
        assert f"predicted_http_work_rps {predicted_val}" in content
        # prediction_provenance should be 1.0 for live_prometheus
        assert "prediction_provenance 1.0" in content


def test_gauges_updated_on_static_fallback_predicted_load(valid_forecast_csv, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://fake-prom:9090")
    monkeypatch.setenv("ENABLE_STATIC_FALLBACK", "true")
    monkeypatch.setenv("FORECAST_CSV_PATH", valid_forecast_csv)

    import requests
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError("Connection refused")):
        response = client.get("/api/predicted-load?horizon_steps=1&aggregation=point")
        assert response.status_code == 200
        data = response.json()
        assert data["provenance"] == "static_csv_fallback"
        predicted_val = data["predicted_load"]

        # Scrape /metrics and verify gauge values
        metrics_resp = client.get("/metrics")
        assert metrics_resp.status_code == 200
        content = metrics_resp.text
        assert f"predicted_http_work_rps {predicted_val}" in content
        # prediction_provenance should be 0.0 for static_csv_fallback
        assert "prediction_provenance 0.0" in content


def test_scraping_metrics_endpoint_does_not_call_prometheus(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://fake-prom:9090")
    with patch("requests.get") as mock_get:
        # Scraping /metrics should never trigger requests.get
        response = client.get("/metrics")
        assert response.status_code == 200
        mock_get.assert_not_called()

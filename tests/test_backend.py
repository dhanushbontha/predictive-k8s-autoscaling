import os
import re
import sys
import time
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch
import pandas as pd
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
    get_or_generate_forecast,
    forecast_cache,
    refresh_prediction,
    background_prediction_refresh_loop,
    start_prediction_refresh_loop,
    stop_prediction_refresh_loop,
    lifespan,
    _forecast_lock,
    set_last_forecast_error,
    get_last_forecast_error,
    PREDICTED_LOAD_GAUGE,
    PREDICTION_PROVENANCE_GAUGE,
    PREDICTION_TIMESTAMP_GAUGE,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures for deterministic test datasets
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure forecast cache and gauges are cleared before each test."""
    forecast_cache.clear()
    set_last_forecast_error(None)
    PREDICTED_LOAD_GAUGE.set(0.0)
    PREDICTION_PROVENANCE_GAUGE.set(0.0)
    PREDICTION_TIMESTAMP_GAUGE.set(0.0)
    yield
    forecast_cache.clear()
    set_last_forecast_error(None)
    PREDICTED_LOAD_GAUGE.set(0.0)
    PREDICTION_PROVENANCE_GAUGE.set(0.0)
    PREDICTION_TIMESTAMP_GAUGE.set(0.0)


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


# ---------------------------------------------------------------------------
# Unit tests: Background Refresh Loop, Lifecycle & Gauge Updates
# ---------------------------------------------------------------------------

def test_health_reports_auto_refresh_status():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "auto_refresh_enabled" in data
    assert "refresh_interval_seconds" in data
    assert "background_task_running" in data
    assert data["version"] == "1.2.0"


def test_refresh_prediction_updates_gauges_live():
    mock_df = pd.DataFrame(
        {
            "ds": ["2026-10-29 12:00:00", "2026-10-29 12:00:05"],
            "yhat": [15.2, 22.8],
            "yhat_lower": [12.0, 18.0],
            "yhat_upper": [18.0, 26.0],
        }
    )
    with patch("backend.main.get_or_generate_forecast", return_value=(mock_df, "live_prometheus", "detail")):
        val, prov, ts, method, steps = refresh_prediction(horizon_steps=2, aggregation="point", force_refresh=True)
        assert val == 22.8
        assert prov == "live_prometheus"
        assert steps == 2
        assert "point prediction" in method

        # Verify gauges
        assert PREDICTED_LOAD_GAUGE._value.get() == 22.8
        assert PREDICTION_PROVENANCE_GAUGE._value.get() == 1.0


def test_refresh_prediction_updates_gauges_fallback():
    mock_df = pd.DataFrame(
        {
            "ds": ["2026-10-29 12:00:00"],
            "yhat": [8.5],
            "yhat_lower": [5.0],
            "yhat_upper": [11.0],
        }
    )
    with patch("backend.main.get_or_generate_forecast", return_value=(mock_df, "static_csv_fallback", "fallback detail")):
        val, prov, ts, method, steps = refresh_prediction(horizon_steps=1, aggregation="point", force_refresh=True)
        assert val == 8.5
        assert prov == "static_csv_fallback"

        # Verify gauges
        assert PREDICTED_LOAD_GAUGE._value.get() == 8.5
        assert PREDICTION_PROVENANCE_GAUGE._value.get() == 0.0


def test_refresh_prediction_error_handling_no_fallback():
    from fastapi import HTTPException
    with patch("backend.main.get_or_generate_forecast", side_effect=HTTPException(status_code=502, detail="Prometheus unreachable")):
        with pytest.raises(HTTPException) as excinfo:
            refresh_prediction(force_refresh=True)
        assert excinfo.value.status_code == 502
        assert "Prometheus unreachable" in excinfo.value.detail


@pytest.mark.asyncio
async def test_background_refresh_loop_execution_and_cancellation():
    import asyncio

    call_count = 0

    def mock_refresh(**kwargs):
        nonlocal call_count
        call_count += 1
        PREDICTED_LOAD_GAUGE.set(19.5)
        PREDICTION_PROVENANCE_GAUGE.set(1.0)
        return (19.5, "live_prometheus", "2026-01-01", "point", 5)

    with patch("backend.main.refresh_prediction", side_effect=mock_refresh):
        with patch.dict(os.environ, {"REFRESH_INTERVAL_SECONDS": "0.01"}):
            task = asyncio.create_task(background_prediction_refresh_loop())
            # Allow loop to execute at least one iteration
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert call_count >= 1
    assert PREDICTED_LOAD_GAUGE._value.get() == 19.5
    assert PREDICTION_PROVENANCE_GAUGE._value.get() == 1.0


@pytest.mark.asyncio
async def test_background_refresh_loop_survives_transient_errors():
    import asyncio
    from fastapi import HTTPException

    call_count = 0

    def mock_refresh_flaky(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise HTTPException(status_code=422, detail="Insufficient Prometheus observations")
        elif call_count == 2:
            raise ConnectionError("Prometheus connection timeout")
        else:
            PREDICTED_LOAD_GAUGE.set(14.0)
            PREDICTION_PROVENANCE_GAUGE.set(1.0)
            return (14.0, "live_prometheus", "2026-01-01", "point", 5)

    with patch("backend.main.refresh_prediction", side_effect=mock_refresh_flaky):
        with patch.dict(os.environ, {"REFRESH_INTERVAL_SECONDS": "0.01"}):
            task = asyncio.create_task(background_prediction_refresh_loop())
            # Allow loop to run through failures and succeed
            await asyncio.sleep(0.08)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert call_count >= 3
    assert PREDICTED_LOAD_GAUGE._value.get() == 14.0


@pytest.mark.asyncio
async def test_start_prediction_refresh_loop_singleton():
    import asyncio

    with patch("backend.main.background_prediction_refresh_loop") as mock_loop:
        async def dummy_loop():
            await asyncio.sleep(10)

        mock_loop.side_effect = dummy_loop
        with patch.dict(os.environ, {"ENABLE_AUTO_REFRESH": "true"}):
            task1 = start_prediction_refresh_loop()
            task2 = start_prediction_refresh_loop()
            assert task1 is not None
            assert task1 is task2  # Must be the exact same singleton task

            await stop_prediction_refresh_loop()
            assert task1.done()


@pytest.mark.asyncio
async def test_lifespan_lifecycle():
    import asyncio

    with patch("backend.main.start_prediction_refresh_loop") as mock_start, \
         patch("backend.main.stop_prediction_refresh_loop") as mock_stop:
        mock_stop.return_value = None

        async with lifespan(app):
            mock_start.assert_called_once()
            mock_stop.assert_not_called()

        mock_stop.assert_called_once()


# ---------------------------------------------------------------------------
# Unit tests: Prediction Freshness, Thread Safety & KEDA Resilience
# ---------------------------------------------------------------------------

def test_prediction_timestamp_gauge_updates_on_success():
    mock_df = pd.DataFrame(
        {
            "ds": ["2026-10-29 12:00:00"],
            "yhat": [25.0],
            "yhat_lower": [20.0],
            "yhat_upper": [30.0],
        }
    )
    with patch("backend.main.get_or_generate_forecast", return_value=(mock_df, "live_prometheus", "detail")):
        before_time = time.time() - 0.1
        refresh_prediction(horizon_steps=1, force_refresh=True)
        ts = PREDICTION_TIMESTAMP_GAUGE._value.get()
        assert ts >= before_time
        assert ts <= time.time()

        # Verify /metrics endpoint exposes the timestamp gauge
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "prediction_timestamp_seconds" in resp.text
        match = re.search(r"^prediction_timestamp_seconds\s+([0-9.e+-]+)", resp.text, re.MULTILINE)
        assert match is not None
        assert float(match.group(1)) == pytest.approx(ts, rel=1e-5)


def test_prediction_timestamp_gauge_unchanged_on_failure():
    from fastapi import HTTPException

    # Set prior timestamp
    prior_ts = 1793280000.0
    PREDICTION_TIMESTAMP_GAUGE.set(prior_ts)

    with patch("backend.main.get_or_generate_forecast", side_effect=HTTPException(status_code=502, detail="Prometheus unreachable")):
        with pytest.raises(HTTPException):
            refresh_prediction(force_refresh=True)

        # Must not change
        assert PREDICTION_TIMESTAMP_GAUGE._value.get() == prior_ts


def test_freshness_gated_promql_behavior():
    """
    Simulates PromQL query: predicted_http_work_rps and (time() - prediction_timestamp_seconds < 45)
    Verifies that fresh metrics pass through and stale metrics evaluate to empty/null.
    """
    def evaluate_freshness_gate(metric_value: float, prediction_ts: float, current_time: float, max_age: float = 45.0):
        if (current_time - prediction_ts) < max_age:
            return metric_value
        return None  # Represents empty vector in PromQL

    current_t = 1793280050.0

    # 1. Fresh metric (10s old)
    assert evaluate_freshness_gate(metric_value=25.0, prediction_ts=current_t - 10.0, current_time=current_t) == 25.0

    # 2. Exactly boundary (44.9s old)
    assert evaluate_freshness_gate(metric_value=25.0, prediction_ts=current_t - 44.9, current_time=current_t) == 25.0

    # 3. Stale metric (50s old) -> Drops sample (empty vector)
    assert evaluate_freshness_gate(metric_value=25.0, prediction_ts=current_t - 50.0, current_time=current_t) is None

    # 4. Uninitialized gauge (ts = 0.0) -> Drops sample (empty vector)
    assert evaluate_freshness_gate(metric_value=25.0, prediction_ts=0.0, current_time=current_t) is None


def test_concurrent_refresh_requests_serialized():
    """
    Verifies that multiple concurrent calls to refresh_prediction / Prophet fitting
    are strictly serialized by _forecast_lock and never execute concurrently.
    """
    import threading

    in_flight = 0
    max_concurrent = 0
    lock = threading.Lock()

    def mock_fit(df, horizon_steps=15, freq="5s", seed=42):
        nonlocal in_flight, max_concurrent
        with lock:
            in_flight += 1
            if in_flight > max_concurrent:
                max_concurrent = in_flight
        time.sleep(0.04)  # Simulate CPU-bound Stan model fitting
        with lock:
            in_flight -= 1

        return pd.DataFrame({"ds": ["2026-01-01 00:00:00"], "yhat": [20.0], "yhat_lower": [15.0], "yhat_upper": [25.0]})

    mock_pts = [(1700000000.0 + i * 5, 10.0) for i in range(10)]
    with patch("backend.main.fetch_prometheus_range_metrics", return_value=mock_pts), \
         patch("backend.main.fit_prophet_and_forecast", side_effect=mock_fit), \
         patch.dict(os.environ, {"PROMETHEUS_URL": "http://fake-prom:9090"}):

        threads = []
        for _ in range(5):
            t = threading.Thread(target=refresh_prediction, kwargs={"force_refresh": True})
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

    # Must be strictly 1 (serialized execution, zero concurrent Prophet fits)
    assert max_concurrent == 1


def test_keda_fallback_behavior_empty_vector_simulation():
    """
    Simulates KEDA 2.21.0 Prometheus Scaler handling empty vector from freshness gate:
    - ignoreNullValues: False forces empty vector to increment failure counter.
    - When failures reach failureThreshold (3), fallback replicas (2) are triggered.
    - When fresh metrics resume, scaler deactivates fallback.
    """
    class MockKEDAPrometheusScaler:
        def __init__(self, failure_threshold: int = 3, fallback_replicas: int = 2, threshold_rps: float = 10.0):
            self.failure_threshold = failure_threshold
            self.fallback_replicas = fallback_replicas
            self.threshold_rps = threshold_rps
            self.consecutive_failures = 0
            self.fallback_active = False

        def poll(self, query_result: Optional[float], ignore_null_values: bool = False) -> int:
            if query_result is None:
                if not ignore_null_values:
                    self.consecutive_failures += 1
                if self.consecutive_failures >= self.failure_threshold:
                    self.fallback_active = True
                    return self.fallback_replicas
                return 1  # Before fallback trips, stays at current/min
            else:
                self.consecutive_failures = 0
                self.fallback_active = False
                import math
                return max(1, min(5, math.ceil(query_result / self.threshold_rps)))

    scaler = MockKEDAPrometheusScaler(failure_threshold=3, fallback_replicas=2, threshold_rps=10.0)

    # Cycle 1: Fresh metric (35 RPS) -> Desired = ceil(35/10) = 4 replicas
    assert scaler.poll(query_result=35.0, ignore_null_values=False) == 4
    assert not scaler.fallback_active
    assert scaler.consecutive_failures == 0

    # Cycle 2: Query returns empty vector (stale data) -> Failure #1
    assert scaler.poll(query_result=None, ignore_null_values=False) == 1
    assert not scaler.fallback_active
    assert scaler.consecutive_failures == 1

    # Cycle 3: Query returns empty vector -> Failure #2
    assert scaler.poll(query_result=None, ignore_null_values=False) == 1
    assert not scaler.fallback_active
    assert scaler.consecutive_failures == 2

    # Cycle 4: Query returns empty vector -> Failure #3 -> Trips fallback!
    assert scaler.poll(query_result=None, ignore_null_values=False) == 2
    assert scaler.fallback_active
    assert scaler.consecutive_failures == 3

    # Cycle 5: Prometheus recovers and fresh metric returns (30 RPS) -> Trips fallback off!
    assert scaler.poll(query_result=30.0, ignore_null_values=False) == 3
    assert not scaler.fallback_active
    assert scaler.consecutive_failures == 0


@pytest.mark.asyncio
async def test_recovery_after_prometheus_available_again():
    """
    Verifies that after consecutive failures, as soon as Prometheus metrics recover,
    the background refresh loop immediately resumes updating timestamp and gauges.
    """
    import asyncio

    attempt = 0
    t_start = time.time()

    def mock_flaky_refresh(**kwargs):
        nonlocal attempt
        attempt += 1
        if attempt < 3:
            raise ConnectionError("Prometheus down")
        PREDICTED_LOAD_GAUGE.set(22.0)
        PREDICTION_PROVENANCE_GAUGE.set(1.0)
        PREDICTION_TIMESTAMP_GAUGE.set(time.time())
        return (22.0, "live_prometheus", "2026-01-01", "point", 5)

    with patch("backend.main.refresh_prediction", side_effect=mock_flaky_refresh):
        with patch.dict(os.environ, {"REFRESH_INTERVAL_SECONDS": "0.01"}):
            task = asyncio.create_task(background_prediction_refresh_loop())
            await asyncio.sleep(0.08)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert attempt >= 3
    assert PREDICTED_LOAD_GAUGE._value.get() == 22.0
    assert PREDICTION_TIMESTAMP_GAUGE._value.get() >= t_start


# ---------------------------------------------------------------------------
# Unit tests: New Dashboard API Endpoints
# ---------------------------------------------------------------------------

def test_kubernetes_status_endpoint():
    response = client.get("/api/kubernetes/status")
    assert response.status_code == 200
    data = response.json()
    assert data["deployment_name"] == "target-app"
    assert "current_replicas" in data
    assert "desired_replicas" in data
    assert "cpu_usage_cores" in data
    assert "memory_working_set_mb" in data
    assert isinstance(data["pods"], list)


def test_autoscaling_status_endpoint():
    PREDICTION_TIMESTAMP_GAUGE.set(time.time())
    PREDICTED_LOAD_GAUGE.set(18.5)
    response = client.get("/api/autoscaling/status")
    assert response.status_code == 200
    data = response.json()
    assert data["scaledobject_name"] == "target-app-predictive-scaledobject"
    assert data["metric_name"] == "predicted_http_work_rps"
    assert data["metric_threshold"] == 10.0
    assert data["current_predicted_load"] == 18.5
    assert data["is_fresh"] is True
    assert data["scaledobject_state"] == "Active"


def test_experiment_comparison_endpoint():
    # 1. Default clean research comparison (experiments/results/)
    response = client.get("/api/experiments/comparison")
    assert response.status_code == 200
    data = response.json()
    assert "available_profiles" in data
    assert set(data["available_profiles"]) == {"stable", "periodic", "gradual", "bursty", "noisy"}

    # Initially before new runs, experiments/results/ is empty, so comparisons are clean / awaiting data
    comparisons = data["comparisons"]
    for prof in ["stable", "periodic", "gradual", "bursty", "noisy"]:
        assert prof in comparisons
        assert comparisons[prof]["profile"] == prof

    # 2. Historical backup source (experiments/)
    hist_response = client.get("/api/experiments/comparison?source=historical")
    assert hist_response.status_code == 200
    hist_data = hist_response.json()
    hist_comparisons = hist_data["comparisons"]
    for prof in ["stable", "periodic", "gradual", "bursty", "noisy"]:
        assert prof in hist_comparisons
        hpa_data = hist_comparisons[prof]["hpa"]
        assert hpa_data is not None
        assert hpa_data["total_samples"] > 0
        assert hpa_data["duration_seconds"] > 0
        assert hpa_data["p95_latency_ms"] >= 0
        assert hpa_data["avg_cpu_cores"] >= 0


def test_workload_status_and_validation():
    status_resp = client.get("/api/workload/status")
    assert status_resp.status_code == 200
    assert "is_running" in status_resp.json()

    # Invalid profile should return 400 Bad Request
    start_resp = client.post("/api/workload/start", json={"profile": "invalid_profile_name"})
    assert start_resp.status_code == 400


# ---------------------------------------------------------------------------
# Unit tests: Stale Forecast Handling & Invalidation
# ---------------------------------------------------------------------------

def test_failed_forecast_clears_cache_and_sets_error(monkeypatch):
    """
    Verifies that when live Prometheus returns insufficient observations (<5),
    the forecast cache is cleared and last_forecast_error is set.
    """
    from fastapi import HTTPException
    monkeypatch.setenv("PROMETHEUS_URL", "http://fake-prom:9090")
    monkeypatch.setenv("ENABLE_STATIC_FALLBACK", "false")

    # Populate cache first with dummy data
    dummy_df = pd.DataFrame({"ds": ["2026-01-01 00:00:00"], "yhat": [26.1], "yhat_lower": [20.0], "yhat_upper": [30.0]})
    forecast_cache.update(dummy_df, "live_prometheus", "old forecast")
    forecast_cache.timestamp = time.time() - 5.0
    assert forecast_cache.df is not None

    # Simulate 0 observations from Prometheus
    with patch("backend.main.fetch_prometheus_range_metrics", return_value=[]):
        with pytest.raises(HTTPException) as excinfo:
            get_or_generate_forecast(force_refresh=True)

        assert excinfo.value.status_code == 422
        assert "insufficient prometheus observations" in excinfo.value.detail.lower()
        # Cache must be purged
        assert forecast_cache.df is None
        # Error must be tracked
        assert get_last_forecast_error() is not None
        assert "insufficient prometheus observations" in get_last_forecast_error().lower()


def test_autoscaling_status_exposes_forecast_error_and_waiting_status():
    """
    Verifies that /api/autoscaling/status reports forecast_status='waiting_for_data'
    and includes forecast_error detail when Prometheus observations are insufficient.
    """
    set_last_forecast_error("Insufficient Prometheus observations: found 0, at least 5 required to fit Prophet.")
    PREDICTION_TIMESTAMP_GAUGE.set(0.0)
    PREDICTED_LOAD_GAUGE.set(0.0)

    with patch("backend.main.get_prometheus_url", return_value=""):
        response = client.get("/api/autoscaling/status")
        assert response.status_code == 200
        data = response.json()
        assert data["is_fresh"] is False
        assert data["forecast_status"] == "waiting_for_data"
        assert "insufficient prometheus observations" in data["forecast_error"].lower()


def test_metrics_endpoint_withholds_stale_forecast():
    """
    Verifies that /api/metrics sets forecast=None when prediction is older than 45 seconds,
    preventing stale predictions from being presented as active.
    """
    dummy_df = pd.DataFrame({"ds": ["2026-01-01 00:00:00"], "yhat": [26.1], "yhat_lower": [20.0], "yhat_upper": [30.0]})
    forecast_cache.update(dummy_df, "live_prometheus", "old forecast")
    # Simulate an expired timestamp (>45s ago)
    PREDICTION_TIMESTAMP_GAUGE.set(time.time() - 60.0)

    with patch.dict(os.environ, {"ENABLE_STATIC_FALLBACK": "false", "PROMETHEUS_URL": ""}):
        response = client.get("/api/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["forecast"] is None


def test_successful_forecast_restores_fresh_state():
    """
    Verifies that after an error or stale period, a successful prediction
    restores is_fresh=True, forecast_status='fresh', and clears forecast_error.
    """
    now = time.time()
    PREDICTION_TIMESTAMP_GAUGE.set(now)
    PREDICTED_LOAD_GAUGE.set(32.5)
    set_last_forecast_error(None)

    with patch("backend.main.get_prometheus_url", return_value=""):
        response = client.get("/api/autoscaling/status")
        assert response.status_code == 200
        data = response.json()
        assert data["is_fresh"] is True
        assert data["forecast_status"] == "fresh"
        assert data["forecast_error"] is None
        assert data["current_predicted_load"] == 32.5


# ---------------------------------------------------------------------------
# Unit tests: CPU HPA & Multi-Pattern Benchmark Metrics
# ---------------------------------------------------------------------------

def test_cpu_hpa_status_endpoint():
    """
    Verifies that /api/hpa/status returns valid HPA configuration,
    manifest baseline settings, and active controller status.
    """
    response = client.get("/api/hpa/status")
    assert response.status_code == 200
    data = response.json()

    assert data["hpa_name"] == "target-app-hpa"
    assert data["target_cpu_utilization_percent"] == 50
    assert data["min_replicas"] == 1
    assert data["max_replicas"] == 5
    assert "active_controller" in data
    assert "config" in data
    assert data["config"]["target_cpu_utilization_percent"] == 50
    assert data["config"]["min_replicas"] == 1
    assert data["config"]["max_replicas"] == 5
    assert isinstance(data["conditions"], list)
    assert isinstance(data["scaling_events"], list)


def test_autoscaling_status_active_controller():
    """
    Verifies that /api/autoscaling/status reports controller ownership,
    conditions list, and scaling events without mislabeling.
    """
    response = client.get("/api/autoscaling/status")
    assert response.status_code == 200
    data = response.json()

    assert "active_controller" in data
    assert data["active_controller"] in ("keda", "cpu-hpa", "none")
    assert "active_controller_name" in data
    assert "conditions" in data
    assert isinstance(data["conditions"], list)
    assert "scaling_events" in data
    assert isinstance(data["scaling_events"], list)


def test_experiments_comparison_metrics_fields():
    """
    Verifies that /api/experiments/comparison supports all 5 workload patterns
    and returns comprehensive metrics including error rates, CPU%, MAE, and RMSE.
    """
    response = client.get("/api/experiments/comparison")
    assert response.status_code == 200
    data = response.json()

    expected_profiles = ["stable", "periodic", "gradual", "bursty", "noisy"]
    for prof in expected_profiles:
        assert prof in data["available_profiles"]
        assert prof in data["comparisons"]
        comp = data["comparisons"][prof]
        assert "hpa" in comp
        assert "predictive" in comp

        # Check fields on available benchmark metrics
        for mode in ("hpa", "predictive"):
            metrics = comp[mode]
            if metrics is not None:
                assert "p95_latency_ms" in metrics
                assert "p99_latency_ms" in metrics
                assert "avg_cpu_percent" in metrics
                assert "error_count" in metrics
                assert "error_rate_pct" in metrics
                assert "mae" in metrics
                assert "rmse" in metrics


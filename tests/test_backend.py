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
    assert "static_fallback_enabled" not in data


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


def test_live_prometheus_failure_raises_error(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://fake-prom:9090")

    import requests
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError("Connection refused")):
        response = client.get("/api/forecast")
        assert response.status_code == 502
        assert "failed to query prometheus" in response.json()["detail"].lower()


def test_metrics_endpoint_with_live_instant_query(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://fake-prom:9090")

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
    with patch("backend.main.get_keda_scaledobject_live_status", return_value={"ready": True, "active": True, "fallback": False, "hpa_name": "keda-hpa-target-app-predictive-scaledobject", "failures": 0}):
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
        assert data["prediction_source"] == "Live Prophet only"


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
        if hpa_data is not None:
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

    with patch.dict(os.environ, {"PROMETHEUS_URL": ""}):
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


def test_cpu_hpa_active_controller_detection():
    """
    Verifies that when target-app-hpa exists and no KEDA ScaledObject exists,
    CPU HPA is reported as ACTIVE controller and KEDA is standby.
    """
    mock_hpa = {
        "metadata": {"name": "target-app-hpa"},
        "spec": {"scaleTargetRef": {"name": "target-app"}},
        "status": {
            "currentReplicas": 5,
            "desiredReplicas": 5,
            "currentMetrics": [
                {
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "current": {"averageUtilization": 4}
                    }
                }
            ],
            "conditions": [
                {"type": "AbleToScale", "status": "True", "reason": "ReadyForNewScale", "message": "HPA is active"}
            ]
        }
    }

    with patch("backend.main.get_active_autoscaler_controller", return_value=("cpu-hpa", "Traditional CPU HPA (target-app-hpa)", None, mock_hpa)):
        # Check /api/hpa/status
        hpa_resp = client.get("/api/hpa/status")
        assert hpa_resp.status_code == 200
        hpa_data = hpa_resp.json()
        assert hpa_data["is_applied"] is True
        assert hpa_data["is_active_controller"] is True
        assert hpa_data["active_controller"] == "cpu-hpa"
        assert hpa_data["current_replicas"] == 5
        assert hpa_data["desired_replicas"] == 5
        assert hpa_data["current_cpu_utilization_percent"] == 4.0

        # Check /api/autoscaling/status
        keda_resp = client.get("/api/autoscaling/status")
        assert keda_resp.status_code == 200
        keda_data = keda_resp.json()
        assert keda_data["active_controller"] == "cpu-hpa"
        assert keda_data["active_status"] is False


def test_keda_active_controller_detection():
    """
    Verifies that when KEDA ScaledObject exists and CPU HPA is absent,
    KEDA is reported as ACTIVE controller and CPU HPA is standby.
    """
    mock_keda_hpa = {
        "metadata": {"name": "keda-hpa-target-app-predictive-scaledobject"},
        "spec": {"scaleTargetRef": {"name": "target-app"}},
        "status": {
            "currentReplicas": 2,
            "desiredReplicas": 2,
            "conditions": [
                {"type": "AbleToScale", "status": "True", "reason": "ReadyForNewScale", "message": "KEDA is active"}
            ]
        }
    }

    with patch("backend.main.get_active_autoscaler_controller", return_value=("keda", "KEDA Predictive Autoscaler (keda-hpa-target-app-predictive-scaledobject)", mock_keda_hpa, None)):
        hpa_resp = client.get("/api/hpa/status")
        assert hpa_resp.status_code == 200
        hpa_data = hpa_resp.json()
        assert hpa_data["is_applied"] is False
        assert hpa_data["is_active_controller"] is False
        assert hpa_data["active_controller"] == "keda"

        keda_resp = client.get("/api/autoscaling/status")
        assert keda_resp.status_code == 200
        keda_data = keda_resp.json()
        assert keda_data["active_controller"] == "keda"


def test_neither_controller_active_detection():
    """
    Verifies that when neither controller exists, controller ownership is not falsely inferred.
    """
    with patch("backend.main.subprocess.run") as mock_sub, patch("backend.main.requests.get") as mock_req:
        mock_sub.return_value = MagicMock(returncode=1)
        mock_req.return_value = MagicMock(status_code=200, json=lambda: {"data": {"result": []}})

        from backend.main import get_active_autoscaler_controller
        ctrl_key, ctrl_name, keda, cpu = get_active_autoscaler_controller()
        assert ctrl_key == "none"
        assert keda is None
        assert cpu is None


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


# ---------------------------------------------------------------------------
# Unit tests: Live Data-Driven Prophet Forecasting Pipeline Validation
# ---------------------------------------------------------------------------

def test_live_data_prophet_fits_using_real_prometheus_observations():
    """1. Verify Prophet model fits using real Prometheus observations."""
    mock_points = [(1700000000.0 + i * 5, 10.0 + i * 0.5) for i in range(10)]
    with patch("backend.main.get_prometheus_url", return_value="http://prometheus:9090"):
        with patch("backend.main.fetch_prometheus_range_metrics", return_value=mock_points):
            fcst_df, provenance, detail = get_or_generate_forecast(force_refresh=True)
            assert provenance == "live_prometheus"
            assert len(fcst_df) > 0
            assert "yhat" in fcst_df.columns
            assert (fcst_df["yhat"] >= 0.0).all()

            response = client.get("/api/forecast?limit=5")
            assert response.status_code == 200
            data = response.json()
            assert data["provenance"] == "live_prometheus"
            assert data["valid_historical_observations"] == 10
            assert data["prediction_source"] == "Live Prophet only"
            assert data["last_fitting_timestamp"] is not None


def test_live_data_insufficient_observations_produce_waiting_state():
    """2. Verify fewer than 5 observations produce waiting state and exact required message."""
    mock_points = [(1700000000.0, 5.0), (1700000005.0, 6.0)]
    with patch("backend.main.get_prometheus_url", return_value="http://prometheus:9090"):
        with patch("backend.main.fetch_prometheus_range_metrics", return_value=mock_points):
            response = client.get("/api/forecast?force_refresh=true")
            assert response.status_code == 422
            assert "Waiting for sufficient live Prometheus observations to fit Prophet." in response.json()["detail"]

            status_resp = client.get("/api/autoscaling/status")
            assert status_resp.status_code == 200
            status_data = status_resp.json()
            assert status_data["forecast_status"] == "waiting_for_data"
            assert status_data["model_status"] == "Waiting for data"


def test_live_data_new_observations_trigger_periodic_refitting():
    """3. Verify new observations trigger refitting and update cache."""
    points1 = [(1700000000.0 + i * 5, 8.0) for i in range(8)]
    points2 = [(1700000100.0 + i * 5, 25.0) for i in range(8)]

    with patch("backend.main.get_prometheus_url", return_value="http://prometheus:9090"):
        forecast_cache.clear()
        with patch("backend.main.fetch_prometheus_range_metrics", return_value=points1):
            val1, prov1, _, _, _ = refresh_prediction(force_refresh=True)
            assert prov1 == "live_prometheus"

        # Clear cache to bypass < 1.0s debouncing
        forecast_cache.clear()
        with patch("backend.main.fetch_prometheus_range_metrics", return_value=points2):
            val2, prov2, _, _, _ = refresh_prediction(force_refresh=True)
            assert prov2 == "live_prometheus"
            # New points at 25.0 should produce higher prediction than baseline 8.0
            assert val2 > val1


def test_live_data_predictions_published_to_prometheus():
    """4. Verify predicted request rate is published to Prometheus gauge."""
    mock_points = [(1700000000.0 + i * 5, 15.0) for i in range(10)]
    with patch("backend.main.get_prometheus_url", return_value="http://prometheus:9090"):
        with patch("backend.main.fetch_prometheus_range_metrics", return_value=mock_points):
            val, prov, _, _, _ = refresh_prediction(force_refresh=True)
            assert PREDICTED_LOAD_GAUGE._value.get() == val
            assert PREDICTION_PROVENANCE_GAUGE._value.get() == 1.0

            metrics_resp = client.get("/metrics")
            assert metrics_resp.status_code == 200
            metrics_text = metrics_resp.text
            assert "predicted_http_work_rps" in metrics_text
            assert f"predicted_http_work_rps {val}" in metrics_text
            assert "prediction_provenance 1.0" in metrics_text


def test_live_data_prediction_timestamps_update_correctly():
    """5. Verify prediction timestamps advance on success and halt on failure."""
    before_ts = time.time()
    mock_points = [(1700000000.0 + i * 5, 12.0) for i in range(10)]
    with patch("backend.main.get_prometheus_url", return_value="http://prometheus:9090"):
        with patch("backend.main.fetch_prometheus_range_metrics", return_value=mock_points):
            refresh_prediction(force_refresh=True)
            updated_ts = PREDICTION_TIMESTAMP_GAUGE._value.get()
            assert updated_ts >= before_ts

    # Clear cache and verify that when fitting fails (e.g. 0 points), timestamp should NOT advance
    forecast_cache.clear()
    with patch("backend.main.get_prometheus_url", return_value="http://prometheus:9090"):
        with patch("backend.main.fetch_prometheus_range_metrics", return_value=[]):
            with pytest.raises(Exception):
                refresh_prediction(force_refresh=True)
            # Timestamp remains unchanged
            assert PREDICTION_TIMESTAMP_GAUGE._value.get() == updated_ts
            # Predicted load is reset to 0.0 to prevent ungrounded scaling
            assert PREDICTED_LOAD_GAUGE._value.get() == 0.0


def test_live_data_insufficient_metrics_raises_waiting_detail():
    """6. Verify insufficient observations (<5) raises 422 with clear waiting detail."""
    with patch("backend.main.get_prometheus_url", return_value="http://prometheus:9090"):
        with patch("backend.main.fetch_prometheus_range_metrics", return_value=[]):
            forecast_cache.clear()
            response = client.get("/api/forecast?force_refresh=true")
            assert response.status_code == 422
            assert "waiting for sufficient" in response.json()["detail"].lower()


def test_live_data_missing_or_stale_predictions_handled_safely():
    """7. Verify stale predictions (>45s) are gated out from /api/metrics and status."""
    dummy_df = pd.DataFrame({"ds": ["2026-01-01 00:00:00"], "yhat": [22.0], "yhat_lower": [18.0], "yhat_upper": [26.0]})
    forecast_cache.update(dummy_df, "live_prometheus", "test cache")
    # Set stale timestamp (60 seconds ago)
    PREDICTION_TIMESTAMP_GAUGE.set(time.time() - 60.0)

    status_resp = client.get("/api/autoscaling/status")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["is_fresh"] is False
    assert data["prediction_age_seconds"] >= 45.0
    assert data["forecast_status"] == "stale"


def test_live_data_keda_consumes_genuine_prediction_metric():
    """8. Verify KEDA consumes genuine live prediction metric and query threshold."""
    response = client.get("/api/autoscaling/status")
    assert response.status_code == 200
    data = response.json()
    assert data["metric_name"] == "predicted_http_work_rps"
    assert data["metric_threshold"] == 10.0
    assert "max(predicted_http_work_rps)" in data["query"]
    assert "time() - max(prediction_timestamp_seconds)" in data["query"]


def test_live_data_dashboard_displays_actual_backend_and_forecasting_states():
    """9. Verify dashboard models return required metadata: last fit, count, model status, source."""
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    h_data = health_resp.json()
    assert "last_fitting_timestamp" in h_data
    assert "valid_historical_observations" in h_data
    assert "model_status" in h_data
    assert h_data["prediction_source"] == "Live Prophet only"


# ---------------------------------------------------------------------------
# Unit tests: Research Experiment CSV Export and Download
# ---------------------------------------------------------------------------

def test_download_individual_experiment_csv_success(tmp_path, monkeypatch):
    """Verify downloading an individual completed experiment returns the genuine CSV file."""
    from backend.main import get_experiments_dir
    res_dir = get_experiments_dir("results")
    res_dir.mkdir(parents=True, exist_ok=True)
    test_csv = res_dir / "hpa_stable.csv"
    sample_content = (
        "timestamp_utc,epoch_timestamp,cpu_usage_cores,http_work_rps,latency_p95_seconds\n"
        "2026-11-24T12:00:00+00:00,1793270000.0,0.25,18.5,0.035\n"
        "2026-11-24T12:00:05+00:00,1793270005.0,0.30,22.0,0.040\n"
    )
    test_csv.write_bytes(sample_content.encode("utf-8"))

    try:
        resp = client.get("/api/experiments/download/hpa/stable?source=results")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "attachment; filename=\"hpa_stable.csv\"" in resp.headers.get("content-disposition", "")
        assert resp.content.decode("utf-8").replace("\r\n", "\n") == sample_content
    finally:
        if test_csv.exists():
            test_csv.unlink()


def test_download_experiment_csv_missing_raises_clear_404():
    """Verify downloading a non-existent experiment returns 404 with a clear message."""
    resp = client.get("/api/experiments/download/hpa/nonexistent_profile?source=results")
    assert resp.status_code == 400

    resp404 = client.get("/api/experiments/download/predictive/bursty?source=results")
    assert resp404.status_code == 404
    assert "no completed experiment results available" in resp404.json()["detail"].lower()


def test_download_all_experiments_zip_success(tmp_path):
    """Verify downloading all completed experiments returns a valid zip containing all CSVs."""
    import zipfile
    import io
    from backend.main import get_experiments_dir
    res_dir = get_experiments_dir("results")
    res_dir.mkdir(parents=True, exist_ok=True)
    hpa_file = res_dir / "hpa_bursty.csv"
    pred_file = res_dir / "predictive_bursty.csv"

    hpa_content = "timestamp_utc,http_work_rps\n2026-11-24T12:00:00,10.0\n"
    pred_content = "timestamp_utc,http_work_rps\n2026-11-24T12:00:00,12.0\n"
    hpa_file.write_bytes(hpa_content.encode("utf-8"))
    pred_file.write_bytes(pred_content.encode("utf-8"))

    try:
        resp = client.get("/api/experiments/download-all?source=results")
        assert resp.status_code == 200
        assert "application/zip" in resp.headers.get("content-type", "")
        assert "attachment; filename=" in resp.headers.get("content-disposition", "")

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert "hpa_bursty.csv" in names
        assert "predictive_bursty.csv" in names
        assert zf.read("hpa_bursty.csv").decode("utf-8").replace("\r\n", "\n") == hpa_content
        assert zf.read("predictive_bursty.csv").decode("utf-8").replace("\r\n", "\n") == pred_content
    finally:
        if hpa_file.exists():
            hpa_file.unlink()
        if pred_file.exists():
            pred_file.unlink()


def test_download_all_experiments_empty_raises_404():
    """Verify downloading all experiments when none exist returns 404."""
    from backend.main import get_experiments_dir
    res_dir = get_experiments_dir("results")
    # Ensure results dir has no CSVs
    for f in res_dir.glob("*.csv"):
        f.unlink()

    resp = client.get("/api/experiments/download-all?source=results")
    assert resp.status_code == 404
    assert "no completed experiment results available" in resp.json()["detail"].lower()


def test_to_utc_iso_helper():
    """Verify to_utc_iso converts numeric epochs, naive datetimes, and aware datetimes to UTC ISO-8601."""
    from backend.main import to_utc_iso
    import datetime

    # Epoch in seconds
    epoch_ts = 1764000000.0
    iso_val = to_utc_iso(epoch_ts)
    assert "+00:00" in iso_val or iso_val.endswith("Z")

    # Timezone-naive pandas Timestamp
    naive_ts = pd.Timestamp("2026-11-24 12:00:00")
    iso_naive = to_utc_iso(naive_ts)
    assert iso_naive.startswith("2026-11-24T12:00:00")
    assert "+00:00" in iso_naive or iso_naive.endswith("Z")

    # Timezone-aware timestamp
    aware_ts = pd.Timestamp("2026-11-24 17:30:00+05:30")
    iso_aware = to_utc_iso(aware_ts)
    assert iso_aware.startswith("2026-11-24T12:00:00")
    assert "+00:00" in iso_aware or iso_aware.endswith("Z")


def test_utc_to_ist_conversion():
    """Verify UTC timestamps convert to Asia/Kolkata (IST, UTC+5:30) with exact 5:30 offset."""
    from zoneinfo import ZoneInfo
    from datetime import datetime

    utc_dt = datetime.fromisoformat("2026-11-24T12:00:00+00:00")
    ist_tz = ZoneInfo("Asia/Kolkata")
    ist_dt = utc_dt.astimezone(ist_tz)

    assert ist_dt.hour == 17
    assert ist_dt.minute == 30
    assert ist_dt.second == 0
    assert str(ist_dt.utcoffset()) == "5:30:00"


def test_forecast_endpoint_returns_explicit_utc_iso():
    """Verify /api/forecast returns ds in explicit UTC ISO format for history and forecast points."""
    now_epoch = 1764000000.0
    fake_points = [
        (now_epoch - 60, 2.0),
        (now_epoch - 45, 3.0),
        (now_epoch - 30, 4.0),
        (now_epoch - 15, 5.0),
        (now_epoch, 6.0),
    ]
    fake_fcst = pd.DataFrame({
        "ds": [pd.Timestamp(now_epoch + i * 5, unit="s", tz="UTC") for i in range(15)],
        "yhat": [5.0] * 15,
        "yhat_lower": [4.0] * 15,
        "yhat_upper": [6.0] * 15,
    })

    with patch("backend.main.get_prometheus_url", return_value="http://fake-prom:9090"):
        with patch("backend.main.fetch_prometheus_range_metrics", return_value=fake_points):
            with patch("backend.main.fit_prophet_and_forecast", return_value=fake_fcst):
                resp = client.get("/api/forecast?force_refresh=true")
                assert resp.status_code == 200
                data = resp.json()

                assert data["provenance"] == "live_prometheus"
                assert len(data["data"]) > 0
                for item in data["data"]:
                    assert "T" in item["ds"]
                    assert "+00:00" in item["ds"] or item["ds"].endswith("Z")

                assert len(data["history"]) == 5
                for h in data["history"]:
                    assert "T" in h["ds"]
                    assert "+00:00" in h["ds"] or h["ds"].endswith("Z")


def test_predicted_load_endpoint_target_timestamp_utc_iso():
    """Verify /api/predicted-load returns target_timestamp in explicit UTC ISO format."""
    now_epoch = 1764000000.0
    fake_points = [
        (now_epoch - 60, 2.0),
        (now_epoch - 45, 3.0),
        (now_epoch - 30, 4.0),
        (now_epoch - 15, 5.0),
        (now_epoch, 6.0),
    ]
    fake_fcst = pd.DataFrame({
        "ds": [pd.Timestamp(now_epoch + i * 5, unit="s", tz="UTC") for i in range(15)],
        "yhat": [5.0] * 15,
        "yhat_lower": [4.0] * 15,
        "yhat_upper": [6.0] * 15,
    })

    with patch("backend.main.get_prometheus_url", return_value="http://fake-prom:9090"):
        with patch("backend.main.fetch_prometheus_range_metrics", return_value=fake_points):
            with patch("backend.main.fit_prophet_and_forecast", return_value=fake_fcst):
                resp = client.get("/api/predicted-load?aggregation=point&horizon_steps=5")
                assert resp.status_code == 200
                data = resp.json()
                assert "target_timestamp" in data
                assert "T" in data["target_timestamp"]
                assert "+00:00" in data["target_timestamp"] or data["target_timestamp"].endswith("Z")


def test_cpu_hpa_status_matches_k8s_average_utilization():
    """Verify /api/hpa/status extracts averageUtilization directly from HPA status (e.g. 384%)."""
    mock_cpu_hpa = {
        "metadata": {"name": "target-app-hpa"},
        "status": {
            "currentReplicas": 5,
            "desiredReplicas": 5,
            "currentMetrics": [
                {
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "current": {
                            "averageUtilization": 384,
                            "averageValue": "384m"
                        }
                    }
                }
            ],
            "conditions": []
        }
    }

    with patch("backend.main.get_active_autoscaler_controller", return_value=("cpu-hpa", "Traditional CPU HPA (target-app-hpa)", None, mock_cpu_hpa)):
        resp = client.get("/api/hpa/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_cpu_utilization_percent"] == 384.0
        assert data["target_cpu_utilization_percent"] == 50
        assert data["current_replicas"] == 5
        assert data["desired_replicas"] == 5
        assert data["is_active_controller"] is True


def test_cpu_hpa_status_matches_prometheus_utilization_metric():
    """Verify /api/hpa/status extracts averageUtilization from kube_horizontalpodautoscaler_status_target_metric."""
    mock_cpu_hpa = {
        "metadata": {"name": "target-app-hpa"},
        "source": "prometheus"
    }

    def mock_requests_get(url, params=None, timeout=None):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        query = params.get("query", "") if params else ""
        if "metric_target_type=\"utilization\"" in query:
            mock_resp.json.return_value = {
                "data": {"result": [{"value": [1700000000, "4"]}]}
            }
        elif "status_current_replicas" in query:
            mock_resp.json.return_value = {
                "data": {"result": [{"value": [1700000000, "5"]}]}
            }
        elif "status_desired_replicas" in query:
            mock_resp.json.return_value = {
                "data": {"result": [{"value": [1700000000, "5"]}]}
            }
        elif "container_cpu_usage_seconds_total" in query:
            mock_resp.json.return_value = {
                "data": {"result": [{"value": [1700000000, "0.02"]}]}
            }
        else:
            mock_resp.json.return_value = {"data": {"result": []}}
        return mock_resp

    with patch("backend.main.get_active_autoscaler_controller", return_value=("cpu-hpa", "Traditional CPU HPA (target-app-hpa)", None, mock_cpu_hpa)):
        with patch("backend.main.get_prometheus_url", return_value="http://mock-prometheus:9090"):
            with patch("requests.get", side_effect=mock_requests_get):
                resp = client.get("/api/hpa/status")
                assert resp.status_code == 200
                data = resp.json()
                assert data["current_cpu_utilization_percent"] == 4.0
                assert data["target_cpu_utilization_percent"] == 50
                assert data["current_replicas"] == 5
                assert data["desired_replicas"] == 5
                assert data["raw_cpu_cores"] == 0.02


def test_cpu_hpa_status_returns_none_when_metrics_unavailable():
    """Verify /api/hpa/status returns None when HPA metrics are unavailable (no synthetic calculation)."""
    mock_cpu_hpa = {
        "metadata": {"name": "target-app-hpa"},
        "source": "prometheus"
    }

    def mock_requests_get(url, params=None, timeout=None):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        query = params.get("query", "") if params else ""
        # Return empty for HPA utilization, but provide container CPU
        if "container_cpu_usage_seconds_total" in query:
            mock_resp.json.return_value = {
                "data": {"result": [{"value": [1700000000, "0.384"]}]}
            }
        else:
            mock_resp.json.return_value = {"data": {"result": []}}
        return mock_resp

    with patch("backend.main.get_active_autoscaler_controller", return_value=("cpu-hpa", "Traditional CPU HPA (target-app-hpa)", None, mock_cpu_hpa)):
        with patch("backend.main.get_prometheus_url", return_value="http://mock-prometheus:9090"):
            with patch("requests.get", side_effect=mock_requests_get):
                resp = client.get("/api/hpa/status")
                assert resp.status_code == 200
                data = resp.json()
                # Must be None, NOT 384% or any computed container CPU calculation
                assert data["current_cpu_utilization_percent"] is None
                assert data["raw_cpu_cores"] == 0.384


def test_autoscaling_status_cpu_hpa_mode():
    """Verify /api/autoscaling/status correctly reflects CPU HPA active controller mode with inactive KEDA."""
    mock_cpu_hpa = {"metadata": {"name": "target-app-hpa"}, "status": {"currentReplicas": 2}}

    with patch("backend.main.get_active_autoscaler_controller", return_value=("cpu-hpa", "Traditional CPU HPA (target-app-hpa)", None, mock_cpu_hpa)):
        with patch("backend.main.get_keda_scaledobject_live_status", return_value=None):
            resp = client.get("/api/autoscaling/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["active_controller"] == "cpu-hpa"
            assert data["scaledobject_present"] is False
            assert data["ready_status"] is False
            assert data["active_status"] is False
            assert data["fallback_active"] is False
            assert data["scaledobject_state"] == "Standby"


def test_autoscaling_status_keda_active_mode():
    """Verify /api/autoscaling/status correctly reflects healthy active KEDA controller mode."""
    mock_keda_hpa = {"metadata": {"name": "keda-hpa-target-app-predictive-scaledobject"}, "status": {"currentReplicas": 3}}
    mock_live_keda = {
        "ready": True,
        "active": True,
        "fallback": False,
        "hpa_name": "keda-hpa-target-app-predictive-scaledobject",
        "failures": 0,
    }

    with patch("backend.main.get_active_autoscaler_controller", return_value=("keda", "KEDA Predictive Autoscaler (keda-hpa-target-app-predictive-scaledobject)", mock_keda_hpa, None)):
        with patch("backend.main.get_keda_scaledobject_live_status", return_value=mock_live_keda):
            resp = client.get("/api/autoscaling/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["active_controller"] == "keda"
            assert data["scaledobject_present"] is True
            assert data["ready_status"] is True
            assert data["fallback_active"] is False


def test_autoscaling_status_keda_fallback_mode():
    """Verify /api/autoscaling/status correctly reflects KEDA fallback mode when ScaledObject has failures."""
    mock_keda_hpa = {"metadata": {"name": "keda-hpa-target-app-predictive-scaledobject"}, "status": {"currentReplicas": 2}}
    mock_live_keda = {
        "ready": True,
        "active": True,
        "fallback": True,
        "hpa_name": "keda-hpa-target-app-predictive-scaledobject",
        "failures": 3,
    }

    with patch("backend.main.get_active_autoscaler_controller", return_value=("keda", "KEDA Predictive Autoscaler (keda-hpa-target-app-predictive-scaledobject)", mock_keda_hpa, None)):
        with patch("backend.main.get_keda_scaledobject_live_status", return_value=mock_live_keda):
            resp = client.get("/api/autoscaling/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["active_controller"] == "keda"
            assert data["scaledobject_present"] is True
            assert data["fallback_active"] is True
            assert data["scaledobject_state"] == "Fallback"


def test_autoscaling_status_zero_prediction():
    """Verify /api/autoscaling/status preserves valid zero prediction as 0.0 with fresh status."""
    now = time.time()
    PREDICTION_TIMESTAMP_GAUGE.set(now - 5.0)
    PREDICTED_LOAD_GAUGE.set(0.0)
    PREDICTION_PROVENANCE_GAUGE.set(1.0)
    set_last_forecast_error(None)

    resp = client.get("/api/autoscaling/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_predicted_load"] == 0.0
    assert data["is_fresh"] is True
    assert data["forecast_status"] == "fresh"
    assert data["prediction_age_seconds"] < 45.0


def test_autoscaling_status_positive_prediction():
    """Verify /api/autoscaling/status returns positive predictions normally when fresh."""
    now = time.time()
    PREDICTION_TIMESTAMP_GAUGE.set(now - 10.0)
    PREDICTED_LOAD_GAUGE.set(18.5)
    PREDICTION_PROVENANCE_GAUGE.set(1.0)
    set_last_forecast_error(None)

    resp = client.get("/api/autoscaling/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_predicted_load"] == 18.5
    assert data["is_fresh"] is True
    assert data["forecast_status"] == "fresh"


def test_autoscaling_status_missing_prediction():
    """Verify /api/autoscaling/status returns waiting_for_data and not fresh when predictions are missing."""
    PREDICTION_TIMESTAMP_GAUGE.set(0.0)
    PREDICTED_LOAD_GAUGE.set(0.0)
    PREDICTION_PROVENANCE_GAUGE.set(0.0)
    set_last_forecast_error("Waiting for sufficient live observations")

    with patch("backend.main.get_prometheus_url", return_value=None):
        resp = client.get("/api/autoscaling/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_fresh"] is False
        assert data["forecast_status"] == "waiting_for_data"


def test_autoscaling_status_stale_prediction():
    """Verify /api/autoscaling/status marks predictions stale and is_fresh=False when age >= 45s."""
    now = time.time()
    PREDICTION_TIMESTAMP_GAUGE.set(now - 60.0)
    PREDICTED_LOAD_GAUGE.set(12.0)
    PREDICTION_PROVENANCE_GAUGE.set(1.0)
    set_last_forecast_error(None)

    resp = client.get("/api/autoscaling/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_fresh"] is False
    assert data["forecast_status"] == "stale"
    assert data["prediction_age_seconds"] >= 45.0








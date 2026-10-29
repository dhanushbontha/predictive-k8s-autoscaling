import csv
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Ensure workspace root is in sys.path so 'experiments' package is discoverable
workspace_root = Path(__file__).resolve().parent.parent
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

from experiments.collect_metrics import (
    CSV_HEADERS,
    DEFAULT_QUERIES,
    PrometheusClient,
    collect_snapshot,
)



def test_csv_headers_format():
    assert "timestamp_utc" in CSV_HEADERS
    assert "epoch_timestamp" in CSV_HEADERS
    assert "cpu_usage_cores" in CSV_HEADERS
    assert "memory_working_set_mb" in CSV_HEADERS
    assert "pod_count" in CSV_HEADERS
    assert "hpa_current_replicas" in CSV_HEADERS
    assert "hpa_desired_replicas" in CSV_HEADERS
    assert "http_work_rps" in CSV_HEADERS
    assert "latency_p95_seconds" in CSV_HEADERS


def test_prometheus_client_query_success():
    client = PrometheusClient(base_url="http://mock-prometheus:9090")
    mock_payload = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {},
                    "value": [1793256000.123, "3.14159"],
                }
            ],
        },
    }

    mock_resp = io.BytesIO(json.dumps(mock_payload).encode("utf-8"))
    with patch("urllib.request.urlopen", return_value=mock_resp):
        val = client.query("up")
        assert val == pytest.approx(3.14159)


def test_prometheus_client_query_empty():
    client = PrometheusClient(base_url="http://mock-prometheus:9090")
    mock_payload = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [],
        },
    }

    mock_resp = io.BytesIO(json.dumps(mock_payload).encode("utf-8"))
    with patch("urllib.request.urlopen", return_value=mock_resp):
        val = client.query("up")
        assert val is None


def test_prometheus_client_network_error():
    client = PrometheusClient(base_url="http://mock-prometheus:9090")
    with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
        val = client.query("up")
        assert val is None


def test_collect_snapshot_populated():
    mock_client = MagicMock(spec=PrometheusClient)

    def mock_query(promql):
        if "cpu_usage" in promql:
            return 0.25
        elif "container_memory_working_set_bytes" in promql:
            return 52428800  # 50 MB
        elif "kube_pod_info" in promql:
            return 2.0
        elif "kube_deployment_status_replicas" in promql:
            return 2.0
        elif "kube_horizontalpodautoscaler_status_current_replicas" in promql:
            return 2.0
        elif "kube_horizontalpodautoscaler_status_desired_replicas" in promql:
            return 3.0
        elif "handler=\"/api/work\"" in promql:
            return 12.5
        return None

    mock_client.query.side_effect = mock_query

    snapshot = collect_snapshot(mock_client)
    assert snapshot["collection_status"] == "ok"
    assert snapshot["cpu_usage_cores"] == "0.250000"
    assert snapshot["memory_working_set_mb"] == "50.00"
    assert snapshot["pod_count"] == "2"
    assert snapshot["hpa_current_replicas"] == "2"
    assert snapshot["hpa_desired_replicas"] == "3"
    assert snapshot["http_work_rps"] == "12.500000"
    # Metrics that returned None should remain empty strings, not fabricated numbers
    assert snapshot["http_error_rps"] == ""


def test_collect_snapshot_all_unreachable():
    mock_client = MagicMock(spec=PrometheusClient)
    mock_client.query.return_value = None

    snapshot = collect_snapshot(mock_client)
    assert snapshot["collection_status"] == "no_data_or_unreachable"
    assert snapshot["cpu_usage_cores"] == ""
    assert snapshot["pod_count"] == ""
    assert snapshot["memory_working_set_mb"] == ""
    assert snapshot["http_error_rps"] == ""


def test_collect_snapshot_with_errors():
    mock_client = MagicMock(spec=PrometheusClient)

    def mock_query(promql):
        if "status=~\"[45]..\"" in promql:
            return 3.5
        elif "kube_pod_info" in promql:
            return 1.0
        return 0.0

    mock_client.query.side_effect = mock_query
    snapshot = collect_snapshot(mock_client)
    assert snapshot["collection_status"] == "ok"
    assert snapshot["http_error_rps"] == "3.500000"


def test_collect_snapshot_zero_error_rate():
    mock_client = MagicMock(spec=PrometheusClient)

    def mock_query(promql):
        if "status=~\"[45]..\"" in promql:
            return 0.0
        elif "kube_pod_info" in promql:
            return 1.0
        return 5.0

    mock_client.query.side_effect = mock_query
    snapshot = collect_snapshot(mock_client)
    assert snapshot["collection_status"] == "ok"
    assert snapshot["http_error_rps"] == "0"


def test_collect_snapshot_successful_responses_not_counted_as_errors():
    mock_client = MagicMock(spec=PrometheusClient)

    def mock_query(promql):
        # 100 RPS successful traffic
        if "status=~\"[45]..\"" in promql:
            return 0.0
        elif "rate(http_requests_total" in promql or "sum(http_requests_total" in promql:
            return 100.0
        elif "kube_pod_info" in promql:
            return 1.0
        return None

    mock_client.query.side_effect = mock_query
    snapshot = collect_snapshot(mock_client)
    assert snapshot["http_rps"] == "100"
    assert snapshot["http_error_rps"] == "0"



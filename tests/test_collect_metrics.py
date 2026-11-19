import csv
import io
import json
import os
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
    resolve_autoscaler_hpa,
    resolve_output_path,
)


def test_csv_headers_format():
    assert "timestamp_utc" in CSV_HEADERS
    assert "epoch_timestamp" in CSV_HEADERS
    assert "cpu_usage_cores" in CSV_HEADERS
    assert "memory_working_set_bytes" in CSV_HEADERS
    assert "memory_working_set_mb" in CSV_HEADERS
    assert "pod_count" in CSV_HEADERS
    assert "hpa_current_replicas" in CSV_HEADERS
    assert "hpa_desired_replicas" in CSV_HEADERS
    assert "http_work_rps" in CSV_HEADERS
    assert "latency_p95_seconds" in CSV_HEADERS


def test_prometheus_client_query_success_single_series():
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


def test_prometheus_client_query_multiple_series_aggregation():
    client = PrometheusClient(base_url="http://mock-prometheus:9090")
    # Multiple series returned (e.g. from 3 pods)
    mock_payload = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"pod": "target-app-1"}, "value": [1793256000.0, "1.5"]},
                {"metric": {"pod": "target-app-2"}, "value": [1793256000.0, "2.5"]},
                {"metric": {"pod": "target-app-3"}, "value": [1793256000.0, "3.0"]},
            ],
        },
    }

    mock_resp = io.BytesIO(json.dumps(mock_payload).encode("utf-8"))
    with patch("urllib.request.urlopen", return_value=mock_resp):
        # Default sum aggregation: 1.5 + 2.5 + 3.0 = 7.0 (does not blindly select results[0]=1.5)
        sum_val = client.query("container_cpu_usage_seconds_total", aggregation_op="sum")
        assert sum_val == pytest.approx(7.0)

    # Max aggregation
    mock_resp2 = io.BytesIO(json.dumps(mock_payload).encode("utf-8"))
    with patch("urllib.request.urlopen", return_value=mock_resp2):
        max_val = client.query("container_cpu_usage_seconds_total", aggregation_op="max")
        assert max_val == pytest.approx(3.0)


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

    def mock_query(promql, aggregation_op="sum"):
        if "status=~\"[45]..\"" in promql:
            return None
        elif "cpu_usage" in promql:
            return 0.25
        elif "container_memory_working_set_bytes" in promql:
            return 52428800  # 50 MB
        elif "phase=\"Running\"" in promql or "kube_pod_info" in promql:
            return 2.0
        elif "deployment_replicas" in promql or "kube_deployment_status_replicas" in promql:
            return 2.0
        elif "current_replicas" in promql:
            return 2.0
        elif "desired_replicas" in promql:
            return 3.0
        elif "handler=\"/api/work\"" in promql:
            return 12.5
        elif "http_requests_total" in promql:
            return 15.0
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


def test_collect_snapshot_partial_status():
    mock_client = MagicMock(spec=PrometheusClient)

    def mock_query(promql, aggregation_op="sum"):
        # CPU and Memory present, but HPA metrics missing
        if "cpu_usage" in promql:
            return 0.20
        elif "container_memory_working_set_bytes" in promql:
            return 41943040  # 40 MB
        return None

    mock_client.query.side_effect = mock_query

    snapshot = collect_snapshot(mock_client)
    # Status should be partial because some metrics are present but core HPA metrics are missing
    assert snapshot["collection_status"] == "partial"
    assert snapshot["cpu_usage_cores"] == "0.200000"
    assert snapshot["memory_working_set_mb"] == "40.00"
    assert snapshot["hpa_current_replicas"] == ""


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

    def mock_query(promql, aggregation_op="sum"):
        if "status=~\"[45]..\"" in promql:
            return 3.5
        elif "phase=\"Running\"" in promql:
            return 1.0
        return 0.0

    mock_client.query.side_effect = mock_query
    snapshot = collect_snapshot(mock_client)
    assert snapshot["collection_status"] == "ok"
    assert snapshot["http_error_rps"] == "3.500000"


def test_collect_snapshot_memory_mib_conversion():
    mock_client = MagicMock(spec=PrometheusClient)

    def mock_query(promql, aggregation_op="sum"):
        if "container_memory_working_set_bytes" in promql:
            # 104,857,600 bytes = 100.00 MiB
            return 104857600.0
        return 1.0

    mock_client.query.side_effect = mock_query
    snapshot = collect_snapshot(mock_client)
    assert snapshot["memory_working_set_mb"] == "100.00"


def test_resolve_autoscaler_hpa():
    # HPA CPU baseline
    assert resolve_autoscaler_hpa("hpa", None) == "target-app-hpa"
    assert resolve_autoscaler_hpa("cpu", None) == "target-app-hpa"

    # Predictive KEDA
    assert resolve_autoscaler_hpa("predictive", None) == "keda-hpa-target-app-predictive-scaledobject"
    assert resolve_autoscaler_hpa("keda", None) == "keda-hpa-target-app-predictive-scaledobject"

    # Explicit override takes precedence
    assert resolve_autoscaler_hpa("hpa", "custom-hpa") == "custom-hpa"
    assert resolve_autoscaler_hpa("predictive", "custom-keda") == "custom-keda"


def test_resolve_output_path():
    expected = os.path.join("experiments", "results", "hpa_stable.csv")
    assert resolve_output_path(None, "hpa", "stable") == expected
    assert resolve_output_path("custom/metrics.csv", "hpa", "stable") == "custom/metrics.csv"

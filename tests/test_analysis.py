import csv
import sys
import tempfile
from pathlib import Path
import pytest

# Ensure workspace root is in sys.path
workspace_root = Path(__file__).resolve().parent.parent
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

from experiments.evaluate_slo import evaluate_slo
from experiments.analyze_scaling import analyze_scaling_delay


def create_synthetic_csv(rows, fieldnames=None):
    """Helper to create a temporary synthetic experiment CSV for unit tests."""
    if fieldnames is None:
        fieldnames = [
            "timestamp_utc",
            "epoch_timestamp",
            "collection_status",
            "cpu_usage_cores",
            "memory_working_set_bytes",
            "memory_working_set_mb",
            "pod_count",
            "deployment_replicas",
            "deployment_available_replicas",
            "hpa_current_replicas",
            "hpa_desired_replicas",
            "http_requests_total",
            "http_requests_work_total",
            "http_rps",
            "http_work_rps",
            "http_error_rps",
            "latency_p50_seconds",
            "latency_p95_seconds",
            "latency_p99_seconds",
        ]
    temp_file = tempfile.NamedTemporaryFile(mode="w", newline="", delete=False, encoding="utf-8")
    writer = csv.DictWriter(temp_file, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    temp_file.close()
    return temp_file.name


# =========================================================================
# SLO Evaluation Unit Tests
# =========================================================================


def test_evaluate_slo_clean_run_no_violations():
    # Synthetic dataset: P95 latency is 0.050s (well below 0.200s), error rate is 0%
    rows = [
        {
            "timestamp_utc": "2026-10-29T10:00:00Z",
            "epoch_timestamp": "1700000000.0",
            "collection_status": "ok",
            "cpu_usage_cores": "0.15",
            "memory_working_set_mb": "45.0",
            "pod_count": "1",
            "http_rps": "10.0",
            "http_work_rps": "8.0",
            "http_error_rps": "0.0",
            "latency_p95_seconds": "0.050",
            "latency_p99_seconds": "0.070",
        },
        {
            "timestamp_utc": "2026-10-29T10:00:05Z",
            "epoch_timestamp": "1700000005.0",
            "collection_status": "ok",
            "cpu_usage_cores": "0.20",
            "memory_working_set_mb": "48.0",
            "pod_count": "1",
            "http_rps": "12.0",
            "http_work_rps": "10.0",
            "http_error_rps": "0.0",
            "latency_p95_seconds": "0.060",
            "latency_p99_seconds": "0.080",
        },
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = evaluate_slo(csv_file, target_p95_latency=0.200, target_error_rate_pct=1.0)
        assert res["valid_intervals"] == 2
        assert res["latency_violations_count"] == 0
        assert res["latency_violation_percentage"] == 0.0
        assert res["latency_violation_duration_seconds"] == 0.0
        assert res["error_violations_count"] == 0
        assert res["error_violation_percentage"] == 0.0
        assert res["max_p95_latency_seconds"] == 0.06
        assert res["max_p99_latency_seconds"] == 0.08
        assert res["max_pod_count"] == 1
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_evaluate_slo_latency_violation():
    # Synthetic dataset: 1 normal interval (0.050s), then 2 violating intervals (0.350s, 0.400s)
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "collection_status": "ok", "latency_p95_seconds": "0.050", "http_rps": "10", "http_error_rps": "0"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "collection_status": "ok", "latency_p95_seconds": "0.350", "http_rps": "10", "http_error_rps": "0"},
        {"timestamp_utc": "2026-10-29T10:00:10Z", "epoch_timestamp": "1700000010.0", "collection_status": "ok", "latency_p95_seconds": "0.400", "http_rps": "10", "http_error_rps": "0"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = evaluate_slo(csv_file, target_p95_latency=0.200)
        assert res["valid_intervals"] == 3
        assert res["latency_violations_count"] == 2
        assert res["latency_violation_percentage"] == round(2 / 3 * 100, 2)
        assert res["latency_violation_duration_seconds"] == 10.0  # 5s + 5s
        assert res["max_p95_latency_seconds"] == 0.400
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_evaluate_slo_error_rate_violation():
    # Synthetic dataset: error rate is 2 RPS out of 10 RPS = 20% (violates 1.0% target)
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "collection_status": "ok", "latency_p95_seconds": "0.050", "http_rps": "10.0", "http_error_rps": "2.0"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "collection_status": "ok", "latency_p95_seconds": "0.050", "http_rps": "10.0", "http_error_rps": "0.0"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = evaluate_slo(csv_file, target_error_rate_pct=1.0)
        assert res["error_violations_count"] == 1
        assert res["error_violation_percentage"] == 50.0
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_evaluate_slo_missing_metrics_and_empty_rows():
    # Synthetic dataset with empty fields and collection error
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "collection_status": "no_data_or_unreachable"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "collection_status": "ok", "cpu_usage_cores": "", "latency_p95_seconds": ""},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = evaluate_slo(csv_file)
        assert res["total_rows"] == 2
        assert res["valid_intervals"] == 1
        assert res["missing_data_counts"]["latency_p95_seconds"] == 2
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_evaluate_slo_file_not_found():
    with pytest.raises(FileNotFoundError):
        evaluate_slo("non_existent_file.csv")


# =========================================================================
# Scaling Delay Analysis Unit Tests
# =========================================================================


def test_analyze_scaling_delay_successful_scaleup():
    # Synthetic timeline:
    # t=0s, 5s: Low load (1 RPS), 1 pod
    # t=10s, 15s: Sustained high load (10 RPS) -> Surge onset at t=10s
    # t=20s: HPA desired increases to 3 -> Delay = 10s (20 - 10)
    # t=30s: Pod count increases to 3 -> Delay = 20s (30 - 10)
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "collection_status": "ok", "http_work_rps": "1.0", "pod_count": "1", "hpa_desired_replicas": "1"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "collection_status": "ok", "http_work_rps": "1.0", "pod_count": "1", "hpa_desired_replicas": "1"},
        {"timestamp_utc": "2026-10-29T10:00:10Z", "epoch_timestamp": "1700000010.0", "collection_status": "ok", "http_work_rps": "10.0", "pod_count": "1", "hpa_desired_replicas": "1"},
        {"timestamp_utc": "2026-10-29T10:00:15Z", "epoch_timestamp": "1700000015.0", "collection_status": "ok", "http_work_rps": "10.0", "pod_count": "1", "hpa_desired_replicas": "1"},
        {"timestamp_utc": "2026-10-29T10:00:20Z", "epoch_timestamp": "1700000020.0", "collection_status": "ok", "http_work_rps": "10.0", "pod_count": "1", "hpa_desired_replicas": "3"},
        {"timestamp_utc": "2026-10-29T10:00:25Z", "epoch_timestamp": "1700000025.0", "collection_status": "ok", "http_work_rps": "10.0", "pod_count": "1", "hpa_desired_replicas": "3"},
        {"timestamp_utc": "2026-10-29T10:00:30Z", "epoch_timestamp": "1700000030.0", "collection_status": "ok", "http_work_rps": "10.0", "pod_count": "3", "hpa_desired_replicas": "3"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = analyze_scaling_delay(csv_file, load_metric="http_work_rps", load_threshold=5.0, sustained_samples=2)
        assert res["surge_detected"] is True
        assert res["surge_onset_epoch"] == 1700000010.0
        assert res["hpa_decision_delay_seconds"] == 10.0
        assert res["pod_scaleup_delay_seconds"] == 20.0
        assert res["peak_observed_pods"] == 3
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_analyze_scaling_delay_no_surge():
    # Load never exceeds threshold
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "collection_status": "ok", "http_work_rps": "0.5", "pod_count": "1"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "collection_status": "ok", "http_work_rps": "0.5", "pod_count": "1"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = analyze_scaling_delay(csv_file, load_threshold=5.0)
        assert res["surge_detected"] is False
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_analyze_scaling_delay_no_scaleup():
    # Load surges, but pods never scale up
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "collection_status": "ok", "http_work_rps": "1.0", "pod_count": "1"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "collection_status": "ok", "http_work_rps": "10.0", "pod_count": "1"},
        {"timestamp_utc": "2026-10-29T10:00:10Z", "epoch_timestamp": "1700000010.0", "collection_status": "ok", "http_work_rps": "10.0", "pod_count": "1"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = analyze_scaling_delay(csv_file, load_threshold=5.0, sustained_samples=2)
        assert res["surge_detected"] is True
        assert res["pod_scaleup_detected"] is False
        assert res["pod_scaleup_delay_seconds"] is None
    finally:
        Path(csv_file).unlink(missing_ok=True)

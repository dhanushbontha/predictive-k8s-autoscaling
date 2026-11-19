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
    # Synthetic dataset: P95 latency is 0.050s (below 0.200s), error rate is 0%
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
        assert res["total_core_seconds"] == pytest.approx(0.20 * 5.0)
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_evaluate_slo_latency_violation_denominator_excludes_missing():
    # Synthetic dataset: 4 rows:
    # row 0: valid normal (0.050s)
    # row 1: missing latency
    # row 2: valid violating (0.350s)
    # row 3: valid violating (0.400s)
    # Total rows with latency: 3. Violations: 2. Violation pct: 2/3 * 100 = 66.67%
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "collection_status": "ok", "latency_p95_seconds": "0.050", "http_rps": "10", "http_error_rps": "0"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "collection_status": "partial", "latency_p95_seconds": "", "http_rps": "10", "http_error_rps": "0"},
        {"timestamp_utc": "2026-10-29T10:00:10Z", "epoch_timestamp": "1700000010.0", "collection_status": "ok", "latency_p95_seconds": "0.350", "http_rps": "10", "http_error_rps": "0"},
        {"timestamp_utc": "2026-10-29T10:00:15Z", "epoch_timestamp": "1700000015.0", "collection_status": "ok", "latency_p95_seconds": "0.400", "http_rps": "10", "http_error_rps": "0"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = evaluate_slo(csv_file, target_p95_latency=0.200)
        assert res["valid_p95_samples"] == 3
        assert res["latency_violations_count"] == 2
        assert res["latency_violation_percentage"] == round(2 / 3 * 100, 2)
        assert res["max_p95_latency_seconds"] == 0.400
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_evaluate_slo_error_rate_calculation_and_positive_rps_check():
    # row 0: 0 error RPS out of 10 = 0%
    # row 1: 2 error RPS out of 10 = 20% (violates target 1.0%)
    # row 2: total_rps is 0, error_rps is 0 -> not counted as valid error observation
    # row 3: error_rps is missing -> not counted
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "collection_status": "ok", "http_rps": "10.0", "http_error_rps": "0.0"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "collection_status": "ok", "http_rps": "10.0", "http_error_rps": "2.0"},
        {"timestamp_utc": "2026-10-29T10:00:10Z", "epoch_timestamp": "1700000010.0", "collection_status": "ok", "http_rps": "0.0", "http_error_rps": "0.0"},
        {"timestamp_utc": "2026-10-29T10:00:15Z", "epoch_timestamp": "1700000015.0", "collection_status": "ok", "http_rps": "10.0", "http_error_rps": ""},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = evaluate_slo(csv_file, target_error_rate_pct=1.0)
        assert res["valid_error_rate_samples"] == 2
        assert res["error_violations_count"] == 1
        assert res["error_violation_percentage"] == 50.0
        assert res["missing_data_counts"]["http_error_rps"] == 1
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_evaluate_slo_sampling_gap_exclusion():
    # Large 60-second gap between sample 1 and sample 2 (e.g. pause / network drop).
    # Gap > max_interval_gap (15s) must be excluded from violation duration.
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "collection_status": "ok", "latency_p95_seconds": "0.300"},
        {"timestamp_utc": "2026-10-29T10:01:00Z", "epoch_timestamp": "1700000060.0", "collection_status": "ok", "latency_p95_seconds": "0.350"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = evaluate_slo(csv_file, target_p95_latency=0.200, max_interval_gap=15.0)
        # Because the gap was 60s > 15s, it is excluded (duration is 0.0s rather than 60s)
        assert res["latency_violation_duration_seconds"] == 0.0
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_evaluate_slo_support_partial_and_missing_metrics():
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "collection_status": "no_data_or_unreachable"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "collection_status": "partial", "cpu_usage_cores": "0.10", "memory_working_set_bytes": "52428800"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = evaluate_slo(csv_file)
        assert res["total_rows"] == 2
        assert res["valid_intervals"] == 1
        assert res["mean_cpu_cores"] == 0.10
        # memory fallback from bytes (52428800 / 1048576 = 50 MiB)
        assert res["mean_memory_mb"] == 50.0
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_evaluate_slo_file_not_found():
    with pytest.raises(FileNotFoundError):
        evaluate_slo("non_existent_file.csv")


# =========================================================================
# Scaling Delay Analysis Unit Tests
# =========================================================================


def test_analyze_scaling_delay_three_distinct_delays():
    # Synthetic timeline:
    # t=0s, 5s: Baseline load (1 RPS), 1 pod, hpa desired=1, deployment replicas=1
    # t=10s, 15s: Sustained surge (10 RPS) -> Surge onset at t=10s
    # t=20s: Autoscaler desired increases to 3 -> Autoscaler delay = 10s (20 - 10)
    # t=25s: Deployment replicas increases to 3 -> Deployment delay = 15s (25 - 10)
    # t=35s: Pods available increases to 3 -> Pod readiness delay = 25s (35 - 10)
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "collection_status": "ok", "http_work_rps": "1.0", "pod_count": "1", "deployment_available_replicas": "1", "deployment_replicas": "1", "hpa_desired_replicas": "1"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "collection_status": "ok", "http_work_rps": "1.0", "pod_count": "1", "deployment_available_replicas": "1", "deployment_replicas": "1", "hpa_desired_replicas": "1"},
        {"timestamp_utc": "2026-10-29T10:00:10Z", "epoch_timestamp": "1700000010.0", "collection_status": "ok", "http_work_rps": "10.0", "pod_count": "1", "deployment_available_replicas": "1", "deployment_replicas": "1", "hpa_desired_replicas": "1"},
        {"timestamp_utc": "2026-10-29T10:00:15Z", "epoch_timestamp": "1700000015.0", "collection_status": "ok", "http_work_rps": "10.0", "pod_count": "1", "deployment_available_replicas": "1", "deployment_replicas": "1", "hpa_desired_replicas": "1"},
        {"timestamp_utc": "2026-10-29T10:00:20Z", "epoch_timestamp": "1700000020.0", "collection_status": "ok", "http_work_rps": "10.0", "pod_count": "1", "deployment_available_replicas": "1", "deployment_replicas": "1", "hpa_desired_replicas": "3"},
        {"timestamp_utc": "2026-10-29T10:00:25Z", "epoch_timestamp": "1700000025.0", "collection_status": "ok", "http_work_rps": "10.0", "pod_count": "1", "deployment_available_replicas": "1", "deployment_replicas": "3", "hpa_desired_replicas": "3"},
        {"timestamp_utc": "2026-10-29T10:00:35Z", "epoch_timestamp": "1700000035.0", "collection_status": "ok", "http_work_rps": "10.0", "pod_count": "3", "deployment_available_replicas": "3", "deployment_replicas": "3", "hpa_desired_replicas": "3"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = analyze_scaling_delay(csv_file, load_metric="http_work_rps", surge_ratio=1.5, min_surge_delta=2.0, sustained_samples=2)
        assert res["surge_detected"] is True
        assert res["surge_onset_epoch"] == 1700000010.0
        # 1. Autoscaler decision delay
        assert res["hpa_decision_detected"] is True
        assert res["hpa_decision_delay_seconds"] == 10.0
        # 2. Deployment decision delay
        assert res["deployment_decision_detected"] is True
        assert res["deployment_decision_delay_seconds"] == 15.0
        # 3. Pod availability readiness delay
        assert res["pod_scaleup_detected"] is True
        assert res["pod_scaleup_delay_seconds"] == 25.0
        assert res["peak_observed_pods"] == 3
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_analyze_scaling_delay_relative_baseline_surge():
    # Baseline traffic is 5.0 RPS
    # Single sample at 6.0 RPS (does not exceed baseline + 2.0 delta)
    # Then sustained 12.0 RPS (> max(5*1.5, 5+2) = 7.5 RPS) for 2 samples
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "http_work_rps": "5.0", "pod_count": "1"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "http_work_rps": "5.0", "pod_count": "1"},
        {"timestamp_utc": "2026-10-29T10:00:10Z", "epoch_timestamp": "1700000010.0", "http_work_rps": "6.0", "pod_count": "1"},
        {"timestamp_utc": "2026-10-29T10:00:15Z", "epoch_timestamp": "1700000015.0", "http_work_rps": "12.0", "pod_count": "1"},
        {"timestamp_utc": "2026-10-29T10:00:20Z", "epoch_timestamp": "1700000020.0", "http_work_rps": "12.0", "pod_count": "1"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = analyze_scaling_delay(csv_file, baseline_samples=2, surge_ratio=1.5, min_surge_delta=2.0, sustained_samples=2)
        assert res["surge_detected"] is True
        assert res["baseline_load"] == 5.0
        assert res["effective_threshold"] == 7.5
        assert res["surge_onset_epoch"] == 1700000015.0
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_analyze_scaling_delay_temporary_spike_filtered_by_sustained_samples():
    # Only 1 sample spikes to 10 RPS, then drops back to 1 RPS. Sustained=2 requires 2 samples.
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "http_work_rps": "1.0", "pod_count": "1"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "http_work_rps": "1.0", "pod_count": "1"},
        {"timestamp_utc": "2026-10-29T10:00:10Z", "epoch_timestamp": "1700000010.0", "http_work_rps": "10.0", "pod_count": "1"},  # transient spike
        {"timestamp_utc": "2026-10-29T10:00:15Z", "epoch_timestamp": "1700000015.0", "http_work_rps": "1.0", "pod_count": "1"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = analyze_scaling_delay(csv_file, load_threshold=5.0, sustained_samples=2)
        assert res["surge_detected"] is False
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_analyze_scaling_delay_no_scaleup_does_not_invent_delays():
    # Workload surges, but cluster never scales
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "http_work_rps": "1.0", "pod_count": "1", "hpa_desired_replicas": "1"},
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "http_work_rps": "10.0", "pod_count": "1", "hpa_desired_replicas": "1"},
        {"timestamp_utc": "2026-10-29T10:00:10Z", "epoch_timestamp": "1700000010.0", "http_work_rps": "10.0", "pod_count": "1", "hpa_desired_replicas": "1"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = analyze_scaling_delay(csv_file, load_threshold=5.0, sustained_samples=2)
        assert res["surge_detected"] is True
        assert res["hpa_decision_detected"] is False
        assert res["hpa_decision_delay_seconds"] is None
        assert res["deployment_decision_detected"] is False
        assert res["deployment_decision_delay_seconds"] is None
        assert res["pod_scaleup_detected"] is False
        assert res["pod_scaleup_delay_seconds"] is None
    finally:
        Path(csv_file).unlink(missing_ok=True)


def test_analyze_scaling_delay_non_monotonic_timestamps_handled():
    # Rows slightly out of order due to network arrival
    rows = [
        {"timestamp_utc": "2026-10-29T10:00:05Z", "epoch_timestamp": "1700000005.0", "http_work_rps": "1.0", "pod_count": "1"},
        {"timestamp_utc": "2026-10-29T10:00:00Z", "epoch_timestamp": "1700000000.0", "http_work_rps": "1.0", "pod_count": "1"},
        {"timestamp_utc": "2026-10-29T10:00:15Z", "epoch_timestamp": "1700000015.0", "http_work_rps": "8.0", "pod_count": "1"},
        {"timestamp_utc": "2026-10-29T10:00:10Z", "epoch_timestamp": "1700000010.0", "http_work_rps": "8.0", "pod_count": "1"},
    ]
    csv_file = create_synthetic_csv(rows)
    try:
        res = analyze_scaling_delay(csv_file, load_threshold=5.0, sustained_samples=2)
        assert res["surge_detected"] is True
        # Sorted order confirms surge at 1700000010.0
        assert res["surge_onset_epoch"] == 1700000010.0
    finally:
        Path(csv_file).unlink(missing_ok=True)

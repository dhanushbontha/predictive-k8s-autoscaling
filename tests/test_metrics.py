"""
tests/test_metrics.py — Unit test suite for metrics, delay calculations, sMAPE, MASE, and KPI-1.

Tests D_E2E, D_provision, D_detect+sched, onset-relative lag, lead-time slack,
allocated pod-seconds cost, zero-handling in sMAPE, MASE, and 5-second interval KPI-1 adherence.
"""

import pytest
from runner.collector import (
    compute_delays,
    compute_cost_and_overprovisioning,
    compute_smape,
    compute_mase,
    compute_kpi1_slo_adherence
)

def test_delays_calculation_synthetic_oracle():
    t0_epoch = 1000.0
    t_onset_epoch = 1060.0
    
    pod_events = [
        # Pod 0: Pre-existing baseline pod created before t0 (should be excluded)
        {
            "name": "pod-baseline",
            "creationTimestamp": "1970-01-01T00:15:00Z",  # 900s
            "readyTimestamp": "1970-01-01T00:15:10Z",     # 910s
            "phase": "Running"
        },
        # Pod 1: Proactive scale-up before t_onset
        {
            "name": "pod-1",
            "creationTimestamp": "1970-01-01T00:17:10Z",  # 1030s
            "readyTimestamp": "1970-01-01T00:17:25Z",     # 1045s
            "phase": "Running"
        },
        # Pod 2: Reactive scale-up after t_onset
        {
            "name": "pod-2",
            "creationTimestamp": "1970-01-01T00:18:00Z",  # 1080s
            "readyTimestamp": "1970-01-01T00:18:18Z",     # 1098s
            "phase": "Running"
        }
    ]
    
    result = compute_delays(t0_epoch, t_onset_epoch, pod_events)
    assert result["scale_out_occurred"] is True
    pods = result["pods"]
    assert len(pods) == 2
    
    # Pod 1 checks
    p1 = pods[0]
    assert p1["D_provision"] == 15.0       # 1045 - 1030
    assert p1["D_detect_sched"] == 30.0    # 1030 - 1000
    assert p1["D_E2E"] == 45.0             # 1045 - 1000
    assert p1["D_onset"] == -15.0          # 1045 - 1060 (ready 15s before demand onset)
    assert p1["lead_time_slack"] == 45.0   # 60 - 15
    
    # Pod 2 checks
    p2 = pods[1]
    assert p2["D_provision"] == 18.0       # 1098 - 1080
    assert p2["D_detect_sched"] == 80.0    # 1080 - 1000
    assert p2["D_E2E"] == 98.0             # 1098 - 1000
    assert p2["D_onset"] == 38.0           # 1098 - 1060 (ready 38s after demand onset)
    assert p2["lead_time_slack"] == 42.0   # 60 - 18
    
    # Median aggregations
    assert result["median_D_provision"] == 16.5
    assert result["median_D_E2E"] == 71.5
    assert result["median_D_onset"] == 11.5

def test_delays_no_scale_out_events():
    t0_epoch = 1000.0
    t_onset_epoch = 1060.0
    # Only pre-existing pod
    pod_events = [
        {
            "name": "pod-baseline",
            "creationTimestamp": "1970-01-01T00:15:00Z",
            "readyTimestamp": "1970-01-01T00:15:10Z",
            "phase": "Running"
        }
    ]
    result = compute_delays(t0_epoch, t_onset_epoch, pod_events)
    assert result["scale_out_occurred"] is False
    assert result["pods"] == []
    assert result["median_D_E2E"] is None

def test_cost_and_overprovisioning_synthetic_oracle():
    capacity_per_pod = 10.0  # 10 rps
    samples = [
        {"time": 0.0, "readyReplicas": 1, "replicas": 1, "offered_rps": 5.0},
        {"time": 10.0, "readyReplicas": 1, "replicas": 1, "offered_rps": 10.0},
        {"time": 20.0, "readyReplicas": 2, "replicas": 3, "offered_rps": 25.0},
        {"time": 30.0, "readyReplicas": 4, "replicas": 4, "offered_rps": 20.0},
    ]
    kpis = compute_cost_and_overprovisioning(samples, capacity_per_pod)
    
    # Hand calculation:
    # dt=10 (0->10): allocated=1, pod_sec=10; ready=1, cap=10, offered=10; reqs=100
    # dt=10 (10->20): allocated=3, pod_sec=30; ready=2, cap=20, offered=25; under=(25-20)/10*10=5; reqs=250
    # dt=10 (20->30): allocated=4, pod_sec=40; ready=4, cap=40, offered=20; over=(40-20)/10*10=20; reqs=200
    # Total allocated pod_sec = 80.0
    # Total reqs = 550.0
    # Cost per req = 80 / 550 = 0.1455
    # Overprovisioned pod-hours = 20 / 3600 = 0.0056
    # Underprovisioned pod-hours = 5 / 3600 = 0.0014
    assert kpis["total_allocated_pod_seconds"] == 80.0
    assert kpis["overprovisioned_pod_hours"] == 0.0056
    assert kpis["underprovisioned_pod_hours"] == 0.0014
    assert kpis["cost_per_successful_request"] == 0.1455

def test_smape_zero_handling():
    # Both actual and forecast zero should return 0 without division by zero
    assert compute_smape([0.0, 10.0], [0.0, 10.0]) == 0.0
    # Non-zero case
    assert compute_smape([100.0], [110.0]) == 9.52

def test_mase_synthetic_oracle():
    actual = [10.0, 12.0, 14.0]
    forecast = [11.0, 11.0, 15.0]  # errors: 1, 1, 1 -> MAE = 1.0
    history = [10.0, 12.0, 14.0, 16.0]  # naive diffs: 2, 2, 2 -> naive MAE = 2.0
    # MASE = 1.0 / 2.0 = 0.5
    assert compute_mase(actual, forecast, history) == 0.5

def test_kpi1_slo_adherence():
    intervals = [
        {"p95_ms": 40.0, "error_rate": 0.0},
        {"p95_ms": 60.0, "error_rate": 0.0},
        {"p95_ms": 120.0, "error_rate": 0.0},  # breach latency
        {"p95_ms": 50.0, "error_rate": 0.05},  # breach error rate
    ]
    # 2 out of 4 compliant = 50.0%
    assert compute_kpi1_slo_adherence(intervals, 100.0, 0.01) == 50.0

"""
tests/test_metrics.py — Unit test suite for metrics and delay calculations.

Tests D_E2E, D_provision, D_detect+sched, onset-relative lag, lead-time slack,
and cost/overprovisioning formulas against hand-computed synthetic fixtures.
"""

import pytest
from runner.collector import compute_delays, compute_cost_and_overprovisioning

def test_delays_calculation_synthetic_oracle():
    t0_epoch = 1000.0
    t_onset_epoch = 1060.0
    
    pod_events = [
        {
            "name": "pod-1",
            "creationTimestamp": "1970-01-01T00:17:10Z",  # 1030s
            "readyTimestamp": "1970-01-01T00:17:25Z",     # 1045s
            "phase": "Running"
        },
        {
            "name": "pod-2",
            "creationTimestamp": "1970-01-01T00:18:00Z",  # 1080s
            "readyTimestamp": "1970-01-01T00:18:18Z",     # 1098s
            "phase": "Running"
        }
    ]
    
    delays = compute_delays(t0_epoch, t_onset_epoch, pod_events)
    assert len(delays) == 2
    
    # Pod 1 checks (Proactive scale-up before t_onset)
    p1 = delays[0]
    assert p1["D_provision"] == 15.0       # 1045 - 1030
    assert p1["D_detect_sched"] == 30.0    # 1030 - 1000
    assert p1["D_E2E"] == 45.0             # 1045 - 1000
    assert p1["D_onset"] == -15.0          # 1045 - 1060 (ready 15s before demand onset)
    assert p1["lead_time_slack"] == 45.0   # 60 - 15
    
    # Pod 2 checks (Reactive scale-up after t_onset)
    p2 = delays[1]
    assert p2["D_provision"] == 18.0       # 1098 - 1080
    assert p2["D_detect_sched"] == 80.0    # 1080 - 1000
    assert p2["D_E2E"] == 98.0             # 1098 - 1000
    assert p2["D_onset"] == 38.0           # 1098 - 1060 (ready 38s after demand onset)
    assert p2["lead_time_slack"] == 42.0   # 60 - 18

def test_cost_and_overprovisioning_synthetic_oracle():
    capacity_per_pod = 10.0  # 10 rps
    
    samples = [
        {"time": 0.0, "readyReplicas": 1, "offered_rps": 5.0},
        {"time": 10.0, "readyReplicas": 1, "offered_rps": 10.0},
        {"time": 20.0, "readyReplicas": 2, "offered_rps": 25.0},
        {"time": 30.0, "readyReplicas": 4, "offered_rps": 20.0},
    ]
    
    kpis = compute_cost_and_overprovisioning(samples, capacity_per_pod)
    
    # Hand calculation:
    # dt=10 (0->10): ready=1, pod_sec=10, cap=10, offered=10. over=0, under=0, reqs=100
    # dt=10 (10->20): ready=2, pod_sec=20, cap=20, offered=25. over=0, under=(25-20)/10*10=5, reqs=250
    # dt=10 (20->30): ready=4, pod_sec=40, cap=40, offered=20. over=(40-20)/10*10=20, under=0, reqs=200
    # Total pod_sec = 70.0
    # Total reqs = 550.0
    # Overprovisioned pod-hours = 20 / 3600 = 0.0056
    # Underprovisioned pod-hours = 5 / 3600 = 0.0014
    # Cost per req = 70 / 550 = 0.1273
    # Yield = 550 / 70 = 7.8571
    
    assert kpis["total_pod_seconds"] == 70.0
    assert kpis["overprovisioned_pod_hours"] == 0.0056
    assert kpis["underprovisioned_pod_hours"] == 0.0014
    assert kpis["cost_per_successful_request"] == 0.1273
    assert kpis["yield_requests_per_pod_second"] == 7.8571

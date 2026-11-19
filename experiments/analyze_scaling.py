"""
Autoscaling Response Delay Analyzer
===================================
Evaluates scaling reaction time and readiness delay by detecting a sustained workload surge
relative to baseline traffic and measuring the elapsed time until:
1. Autoscaler desired replicas increase (HPA / KEDA decision delay).
2. Deployment desired replicas increase (Kubernetes API server / controller delay).
3. Additional ready/available application pods become available (Pod startup & readiness delay).

Surge Detection Methodology:
----------------------------
Instead of hardcoding an arbitrary static threshold (e.g. 2 RPS), this analyzer computes
a baseline load from the initial N samples (default: 5 valid samples). A workload surge onset
is detected when the load metric exceeds:
    Surge Threshold = max(baseline_mean * surge_ratio, baseline_mean + min_surge_delta)
for at least M consecutive samples (default: 2), filtering out temporary transient fluctuations.
"""

import argparse
import csv
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple


def parse_float(val: Any) -> Optional[float]:
    """Parse string or number to float safely, returning None if empty or invalid."""
    if val is None:
        return None
    val_str = str(val).strip()
    if val_str == "":
        return None
    try:
        f = float(val_str)
        if f != f or math.isinf(f):  # NaN or Inf check
            return None
        return f
    except (ValueError, TypeError):
        return None


def identify_autoscaler_label(csv_path: str, user_type: Optional[str] = None) -> str:
    """Identifies human-readable autoscaler label from filename or CLI parameter."""
    if user_type and user_type.lower() != "auto":
        u = user_type.lower()
        if u in ("predictive", "keda", "prophet"):
            return "Predictive KEDA (Prophet + KEDA)"
        return "Traditional CPU HPA (Baseline)"

    basename = os.path.basename(csv_path).lower()
    if "predictive" in basename or "keda" in basename:
        return "Predictive KEDA (Prophet + KEDA)"
    elif "hpa" in basename:
        return "Traditional CPU HPA (Baseline)"
    return "Kubernetes Autoscaler"


def analyze_scaling_delay(
    csv_path: str,
    load_metric: str = "http_work_rps",
    load_threshold: Optional[float] = None,
    surge_ratio: float = 1.5,
    min_surge_delta: float = 2.0,
    baseline_samples: int = 5,
    sustained_samples: int = 2,
    autoscaler_type: str = "auto",
) -> Dict[str, Any]:
    """
    Analyzes scaling delay by detecting a sustained workload increase relative to baseline
    and measuring delays to autoscaler decision, deployment scaling, and pod readiness.

    Parameters:
    - csv_path: Path to the experiment CSV file.
    - load_metric: Column representing workload intensity ('http_work_rps', 'http_rps', 'cpu_usage_cores').
    - load_threshold: Optional static threshold. If None, relative baseline threshold is computed.
    - surge_ratio: Multiplier above baseline load to detect surge (default: 1.5x baseline).
    - min_surge_delta: Minimum additive threshold above baseline load (default: 2.0 RPS/cores).
    - baseline_samples: Number of initial valid samples used to establish baseline traffic and replicas.
    - sustained_samples: Number of consecutive samples required to confirm surge onset.
    - autoscaler_type: Label hint ('auto', 'hpa', 'predictive').
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    autoscaler_label = identify_autoscaler_label(csv_path, autoscaler_type)

    raw_rows: List[Dict[str, Any]] = []
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            raw_rows.append(r)

    total_rows = len(raw_rows)
    if total_rows < 2:
        return {
            "csv_path": csv_path,
            "autoscaler_model": autoscaler_label,
            "error": "Dataset too short to evaluate scaling delay (minimum 2 samples required).",
            "total_rows": total_rows,
        }

    # Validate and sort by epoch timestamp, ensuring monotonic timestamps
    valid_rows: List[Dict[str, Any]] = []
    for r in raw_rows:
        ep = parse_float(r.get("epoch_timestamp"))
        if ep is not None:
            valid_rows.append(r)

    if len(valid_rows) < 2:
        return {
            "csv_path": csv_path,
            "autoscaler_model": autoscaler_label,
            "error": "Dataset lacks valid epoch timestamps.",
            "total_rows": total_rows,
        }

    # Sort rows by timestamp to ensure monotonic timeline
    valid_rows.sort(key=lambda r: float(r["epoch_timestamp"]))

    # Step 1: Detect baseline workload and replica metrics from initial samples
    init_slice = valid_rows[:max(baseline_samples, 2)]
    baseline_load_values = [parse_float(r.get(load_metric)) for r in init_slice if parse_float(r.get(load_metric)) is not None]
    baseline_load = (sum(baseline_load_values) / len(baseline_load_values)) if baseline_load_values else 0.0

    # Determine baseline replica counts (autoscaler desired, deployment replicas, available pods)
    init_hpa_des = [parse_float(r.get("hpa_desired_replicas")) for r in init_slice if parse_float(r.get("hpa_desired_replicas")) is not None]
    init_dep_rep = [parse_float(r.get("deployment_replicas")) for r in init_slice if parse_float(r.get("deployment_replicas")) is not None]
    init_avail_pods = [
        parse_float(r.get("deployment_available_replicas")) or parse_float(r.get("pod_count"))
        for r in init_slice
        if (parse_float(r.get("deployment_available_replicas")) or parse_float(r.get("pod_count"))) is not None
    ]

    baseline_hpa_desired = int(init_hpa_des[0]) if init_hpa_des else 1
    baseline_deployment_replicas = int(init_dep_rep[0]) if init_dep_rep else 1
    baseline_available_pods = int(init_avail_pods[0]) if init_avail_pods else 1

    # Step 2: Establish effective surge threshold
    if load_threshold is not None:
        effective_threshold = load_threshold
        threshold_method = f"Static user-defined threshold >= {load_threshold}"
    else:
        # Relative surge detection: max(baseline * ratio, baseline + delta)
        effective_threshold = max(baseline_load * surge_ratio, baseline_load + min_surge_delta)
        threshold_method = (
            f"Relative baseline: max({baseline_load:.2f} * {surge_ratio}, "
            f"{baseline_load:.2f} + {min_surge_delta}) = {effective_threshold:.2f}"
        )

    # Step 3: Find sustained workload increase
    surge_start_row: Optional[Dict[str, Any]] = None
    surge_start_idx: Optional[int] = None
    consecutive_high_count = 0

    for idx, r in enumerate(valid_rows):
        val = parse_float(r.get(load_metric))
        if val is not None and val >= effective_threshold:
            consecutive_high_count += 1
            if consecutive_high_count >= sustained_samples and surge_start_row is None:
                surge_start_idx = idx - sustained_samples + 1
                surge_start_row = valid_rows[surge_start_idx]
        else:
            consecutive_high_count = 0

    if surge_start_row is None or surge_start_idx is None:
        return {
            "csv_path": csv_path,
            "autoscaler_model": autoscaler_label,
            "load_metric": load_metric,
            "baseline_load": round(baseline_load, 4),
            "effective_threshold": round(effective_threshold, 4),
            "threshold_method": threshold_method,
            "surge_detected": False,
            "message": f"No sustained workload increase >= {effective_threshold:.2f} detected in {load_metric}.",
        }

    surge_timestamp_utc = surge_start_row.get("timestamp_utc")
    surge_epoch = float(surge_start_row["epoch_timestamp"])

    # Step 4: Measure 3 distinct reaction points:
    # 1. Autoscaler desired replica count increase
    hpa_decision_row: Optional[Dict[str, Any]] = None
    for r in valid_rows[surge_start_idx:]:
        des = parse_float(r.get("hpa_desired_replicas"))
        if des is not None and des > baseline_hpa_desired:
            hpa_decision_row = r
            break

    # 2. Deployment desired replica count increase
    deployment_decision_row: Optional[Dict[str, Any]] = None
    for r in valid_rows[surge_start_idx:]:
        dep = parse_float(r.get("deployment_replicas"))
        if dep is not None and dep > baseline_deployment_replicas:
            deployment_decision_row = r
            break

    # 3. Additional application pods becoming available/ready
    pod_scaleup_row: Optional[Dict[str, Any]] = None
    for r in valid_rows[surge_start_idx:]:
        avail = parse_float(r.get("deployment_available_replicas")) or parse_float(r.get("pod_count"))
        if avail is not None and avail > baseline_available_pods:
            pod_scaleup_row = r
            break

    # Calculate delays
    hpa_decision_delay: Optional[float] = None
    deployment_decision_delay: Optional[float] = None
    pod_ready_delay: Optional[float] = None

    if hpa_decision_row is not None:
        hpa_epoch = float(hpa_decision_row["epoch_timestamp"])
        hpa_decision_delay = max(0.0, round(hpa_epoch - surge_epoch, 2))

    if deployment_decision_row is not None:
        dep_epoch = float(deployment_decision_row["epoch_timestamp"])
        deployment_decision_delay = max(0.0, round(dep_epoch - surge_epoch, 2))

    if pod_scaleup_row is not None:
        pod_epoch = float(pod_scaleup_row["epoch_timestamp"])
        pod_ready_delay = max(0.0, round(pod_epoch - surge_epoch, 2))

    peak_pods = max([parse_float(r.get("pod_count")) or 0 for r in valid_rows])

    return {
        "csv_path": csv_path,
        "autoscaler_model": autoscaler_label,
        "load_metric": load_metric,
        "baseline_load": round(baseline_load, 4),
        "effective_threshold": round(effective_threshold, 4),
        "threshold_method": threshold_method,
        "baseline_hpa_desired": baseline_hpa_desired,
        "baseline_deployment_replicas": baseline_deployment_replicas,
        "baseline_available_pods": baseline_available_pods,
        "surge_detected": True,
        "surge_onset_utc": surge_timestamp_utc,
        "surge_onset_epoch": surge_epoch,
        # 1. Autoscaler decision
        "hpa_decision_detected": hpa_decision_row is not None,
        "hpa_decision_utc": hpa_decision_row.get("timestamp_utc") if hpa_decision_row else None,
        "hpa_decision_delay_seconds": hpa_decision_delay,
        # 2. Deployment replicas decision
        "deployment_decision_detected": deployment_decision_row is not None,
        "deployment_decision_utc": deployment_decision_row.get("timestamp_utc") if deployment_decision_row else None,
        "deployment_decision_delay_seconds": deployment_decision_delay,
        # 3. Available pods readiness
        "pod_scaleup_detected": pod_scaleup_row is not None,
        "pod_scaleup_utc": pod_scaleup_row.get("timestamp_utc") if pod_scaleup_row else None,
        "pod_scaleup_delay_seconds": pod_ready_delay,
        "peak_observed_pods": int(peak_pods),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Analyze autoscaling response delay from workload surge to replica readiness."
    )
    parser.add_argument(
        "--csv",
        default="experiments/metrics_collected.csv",
        help="Path to experiment CSV file (default: experiments/metrics_collected.csv)",
    )
    parser.add_argument(
        "--autoscaler-type",
        default="auto",
        choices=["auto", "hpa", "predictive", "keda"],
        help="Autoscaler model label hint (default: auto)",
    )
    parser.add_argument(
        "--metric",
        default="http_work_rps",
        choices=["http_work_rps", "http_rps", "cpu_usage_cores"],
        help="Workload trigger metric to detect surge (default: http_work_rps)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Explicit static threshold. If omitted, relative baseline detection is used.",
    )
    parser.add_argument(
        "--surge-ratio",
        type=float,
        default=1.5,
        help="Relative surge multiplier over baseline load (default: 1.5)",
    )
    parser.add_argument(
        "--min-surge-delta",
        type=float,
        default=2.0,
        help="Minimum absolute delta above baseline to confirm surge (default: 2.0)",
    )
    parser.add_argument(
        "--baseline-samples",
        type=int,
        default=5,
        help="Initial sample count to compute baseline traffic (default: 5)",
    )
    parser.add_argument(
        "--sustained",
        type=int,
        default=2,
        help="Consecutive samples required to confirm surge onset (default: 2)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output analysis results as JSON",
    )

    args = parser.parse_args()
    try:
        report = analyze_scaling_delay(
            csv_path=args.csv,
            load_metric=args.metric,
            load_threshold=args.threshold,
            surge_ratio=args.surge_ratio,
            min_surge_delta=args.min_surge_delta,
            baseline_samples=args.baseline_samples,
            sustained_samples=args.sustained,
            autoscaler_type=args.autoscaler_type,
        )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(report, indent=2))
        return

    print("=" * 70)
    print("            AUTOSCALING RESPONSE DELAY REPORT")
    print("=" * 70)
    print(f"Autoscaler Evaluated       : {report.get('autoscaler_model')}")
    print(f"Dataset File               : {report.get('csv_path')}")
    print(f"Trigger Metric             : {report.get('load_metric')}")
    print(f"Baseline Load              : {report.get('baseline_load')}")
    print(f"Effective Threshold        : >= {report.get('effective_threshold')}")
    print(f"Detection Method           : {report.get('threshold_method')}")
    print(f"Baseline Replicas (HPA/Pod): {report.get('baseline_hpa_desired')} / {report.get('baseline_available_pods')}")
    print("-" * 70)

    if not report.get("surge_detected"):
        print(f"Surge Status               : {report.get('message')}")
        print("=" * 70)
        return

    print(f"Workload Surge Onset (T0)  : {report.get('surge_onset_utc')}")
    print()
    print("1. Autoscaler Desired Scale Decision:")
    print(f"   Time                    : {report.get('hpa_decision_utc') or 'Not observed'}")
    if report.get("hpa_decision_delay_seconds") is not None:
        print(f"   Reaction Delay          : {report.get('hpa_decision_delay_seconds')}s")
    else:
        print("   Reaction Delay          : N/A (no scale decision recorded)")

    print()
    print("2. Deployment Desired Replicas Scale Decision:")
    print(f"   Time                    : {report.get('deployment_decision_utc') or 'Not observed'}")
    if report.get("deployment_decision_delay_seconds") is not None:
        print(f"   Reaction Delay          : {report.get('deployment_decision_delay_seconds')}s")
    else:
        print("   Reaction Delay          : N/A (deployment never scaled)")

    print()
    print("3. Application Pods Available / Ready:")
    print(f"   Time                    : {report.get('pod_scaleup_utc') or 'Not observed'}")
    if report.get("pod_scaleup_delay_seconds") is not None:
        print(f"   Readiness Delay         : {report.get('pod_scaleup_delay_seconds')}s")
    else:
        print("   Readiness Delay         : N/A (no additional ready pods observed)")

    print("-" * 70)
    print(f"Peak Pods Observed         : {report.get('peak_observed_pods')}")
    print("=" * 70)


if __name__ == "__main__":
    main()

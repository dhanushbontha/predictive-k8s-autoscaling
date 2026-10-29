import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List, Optional


def parse_float(val: Any) -> Optional[float]:
    """Parse string or number to float safely, returning None if empty or invalid."""
    if val is None:
        return None
    val_str = str(val).strip()
    if val_str == "":
        return None
    try:
        f = float(val_str)
        if f != f:  # NaN
            return None
        return f
    except (ValueError, TypeError):
        return None


def analyze_scaling_delay(
    csv_path: str,
    load_metric: str = "http_work_rps",
    load_threshold: float = 2.0,
    sustained_samples: int = 2,
) -> Dict[str, Any]:
    """
    Analyzes scaling delay by detecting a sustained workload increase and comparing the onset
    timestamp to when HPA changes desired replicas and when the cluster actually adds ready pods.

    Parameters:
    - csv_path: Path to the experiment CSV file.
    - load_metric: Column name representing workload intensity ('http_work_rps' or 'cpu_usage_cores').
    - load_threshold: Minimum value above baseline to register a workload surge.
    - sustained_samples: Number of consecutive samples exceeding threshold to confirm a surge onset.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    rows: List[Dict[str, Any]] = []
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    total_rows = len(rows)
    if total_rows < 2:
        return {
            "csv_path": csv_path,
            "error": "Dataset too short to evaluate scaling delay (minimum 2 samples required).",
            "total_rows": total_rows,
        }

    # Step 1: Detect baseline workload and pod count from initial samples
    initial_rows = [r for r in rows[:10] if r.get("collection_status") == "ok"]
    if not initial_rows:
        return {
            "csv_path": csv_path,
            "error": "No valid samples found in dataset.",
            "total_rows": total_rows,
        }

    baseline_pods = parse_float(initial_rows[0].get("pod_count"))
    if baseline_pods is None:
        return {
            "csv_path": csv_path,
            "error": "Missing initial pod_count in dataset.",
            "total_rows": total_rows,
        }

    surge_start_row: Optional[Dict[str, Any]] = None
    surge_start_idx: Optional[int] = None

    # Step 2: Find sustained workload increase
    consecutive_high_count = 0
    for idx, r in enumerate(rows):
        val = parse_float(r.get(load_metric))
        if val is not None and val >= load_threshold:
            consecutive_high_count += 1
            if consecutive_high_count >= sustained_samples and surge_start_row is None:
                surge_start_idx = idx - sustained_samples + 1
                surge_start_row = rows[surge_start_idx]
        else:
            consecutive_high_count = 0

    if surge_start_row is None:
        return {
            "csv_path": csv_path,
            "load_metric": load_metric,
            "load_threshold": load_threshold,
            "surge_detected": False,
            "message": f"No sustained workload increase >= {load_threshold} detected in {load_metric}.",
        }

    surge_timestamp_utc = surge_start_row.get("timestamp_utc")
    surge_epoch = parse_float(surge_start_row.get("epoch_timestamp"))

    # Step 3: Find when HPA desired replicas increased above baseline
    hpa_decision_row: Optional[Dict[str, Any]] = None
    for r in rows[surge_start_idx:]:
        desired = parse_float(r.get("hpa_desired_replicas"))
        if desired is not None and desired > baseline_pods:
            hpa_decision_row = r
            break

    # Step 4: Find when observed active pod count actually increased above baseline
    pod_scaleup_row: Optional[Dict[str, Any]] = None
    for r in rows[surge_start_idx:]:
        pods = parse_float(r.get("pod_count"))
        if pods is not None and pods > baseline_pods:
            pod_scaleup_row = r
            break

    hpa_reaction_delay: Optional[float] = None
    pod_ready_delay: Optional[float] = None

    if hpa_decision_row is not None and surge_epoch is not None:
        hpa_epoch = parse_float(hpa_decision_row.get("epoch_timestamp"))
        if hpa_epoch is not None:
            hpa_reaction_delay = max(0.0, round(hpa_epoch - surge_epoch, 2))

    if pod_scaleup_row is not None and surge_epoch is not None:
        pod_epoch = parse_float(pod_scaleup_row.get("epoch_timestamp"))
        if pod_epoch is not None:
            pod_ready_delay = max(0.0, round(pod_epoch - surge_epoch, 2))

    return {
        "csv_path": csv_path,
        "load_metric": load_metric,
        "load_threshold": load_threshold,
        "baseline_pod_count": int(baseline_pods),
        "surge_detected": True,
        "surge_onset_utc": surge_timestamp_utc,
        "surge_onset_epoch": surge_epoch,
        "hpa_decision_detected": hpa_decision_row is not None,
        "hpa_decision_utc": hpa_decision_row.get("timestamp_utc") if hpa_decision_row else None,
        "hpa_decision_delay_seconds": hpa_reaction_delay,
        "pod_scaleup_detected": pod_scaleup_row is not None,
        "pod_scaleup_utc": pod_scaleup_row.get("timestamp_utc") if pod_scaleup_row else None,
        "pod_scaleup_delay_seconds": pod_ready_delay,
        "peak_observed_pods": int(max([parse_float(r.get("pod_count")) or 0 for r in rows])),
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
        "--metric",
        default="http_work_rps",
        choices=["http_work_rps", "http_rps", "cpu_usage_cores"],
        help="Workload trigger metric to detect surge (default: http_work_rps)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=2.0,
        help="Workload value threshold indicating surge onset (default: 2.0)",
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
            sustained_samples=args.sustained,
        )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(report, indent=2))
        return

    print("=" * 65)
    print("         AUTOSCALING RESPONSE DELAY REPORT")
    print("=" * 65)
    print(f"Dataset File               : {report.get('csv_path')}")
    print(f"Trigger Metric / Threshold : {report.get('load_metric')} >= {report.get('load_threshold')}")
    print(f"Baseline Pod Count         : {report.get('baseline_pod_count')}")
    print("-" * 65)

    if not report.get("surge_detected"):
        print(f"Surge Status               : {report.get('message')}")
        print("=" * 65)
        return

    print(f"Surge Onset (T0)           : {report.get('surge_onset_utc')}")
    print(f"HPA Desired Scale Time     : {report.get('hpa_decision_utc') or 'Not observed'}")
    print(f"  -> HPA Reaction Delay    : {report.get('hpa_decision_delay_seconds')}s" if report.get('hpa_decision_delay_seconds') is not None else "  -> HPA Reaction Delay: N/A (no scale decision recorded)")
    print()
    print(f"Observed Pods Scale Time   : {report.get('pod_scaleup_utc') or 'Not observed'}")
    print(f"  -> Pod Readiness Delay   : {report.get('pod_scaleup_delay_seconds')}s" if report.get('pod_scaleup_delay_seconds') is not None else "  -> Pod Readiness Delay: N/A (pods never scaled up)")
    print(f"Peak Pods Observed         : {report.get('peak_observed_pods')}")
    print("-" * 65)
    print("Note: Scaling delay represents the wall-clock duration from the first")
    print("sustained workload surge until Kubernetes reported increased active pods.")
    print("=" * 65)


if __name__ == "__main__":
    main()

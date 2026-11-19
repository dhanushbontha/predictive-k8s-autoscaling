"""
Service Level Objective (SLO) & Resource Efficiency Evaluator
============================================================
Evaluates performance compliance and resource utilization from autoscaling experiment CSV datasets.

Evaluated Metrics:
------------------
1. Latency Compliance:
   - Evaluates P95 and P99 latency against configurable SLO targets (defaults: P95 <= 0.200s, P99 <= 0.500s).
   - Computes violation rates solely against valid latency samples (missing samples are excluded from denominator).
2. HTTP Error Rate:
   - Computes error percentage = (error_rps / total_rps) * 100 exclusively when both metrics are valid and total_rps > 0.
3. Violation Duration:
   - Sampling-based time estimate derived from consecutive discrete observation intervals.
   - Large sampling gaps (> max_interval_gap) are excluded to prevent artificially inflating violation duration.
4. Resource Utilization & Energy Cost:
   - Mean / Max CPU usage in cores.
   - Mean / Max Memory working set in MiB.
   - Peak pod replicas.
   - Total core-seconds consumed (integral of CPU usage over valid sampling intervals).
"""

import argparse
import csv
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional


def parse_float(val: Any) -> Optional[float]:
    """Parse string or number to float safely, returning None if empty, invalid, NaN, or Inf."""
    if val is None:
        return None
    val_str = str(val).strip()
    if val_str == "":
        return None
    try:
        f = float(val_str)
        if f != f or math.isinf(f):
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


def evaluate_slo(
    csv_path: str,
    target_p95_latency: float = 0.200,
    target_p99_latency: float = 0.500,
    target_error_rate_pct: float = 1.0,
    max_interval_gap: float = 15.0,
    autoscaler_type: str = "auto",
) -> Dict[str, Any]:
    """
    Evaluates SLO adherence and resource efficiency across experiment CSV time series.

    Parameters:
    - csv_path: Path to the metric CSV file.
    - target_p95_latency: Maximum allowable P95 response latency in seconds (default: 0.200s).
    - target_p99_latency: Maximum allowable P99 response latency in seconds (default: 0.500s).
    - target_error_rate_pct: Maximum allowable HTTP error rate in percent (default: 1.0%).
    - max_interval_gap: Maximum elapsed seconds between consecutive samples before excluding the gap.
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
    if total_rows == 0:
        return {
            "csv_path": csv_path,
            "autoscaler_model": autoscaler_label,
            "error": "Dataset is empty.",
            "total_rows": 0,
        }

    # Tracking metrics and missing counters
    usable_rows = 0
    missing_data_counts: Dict[str, int] = {
        "cpu_usage_cores": 0,
        "memory_working_set_mb": 0,
        "pod_count": 0,
        "latency_p95_seconds": 0,
        "latency_p99_seconds": 0,
        "http_rps": 0,
        "http_error_rps": 0,
    }

    # Latency tracking
    valid_p95_count = 0
    latency_p95_violations = 0
    total_p95_violation_duration = 0.0
    max_p95_latency: Optional[float] = None

    valid_p99_count = 0
    latency_p99_violations = 0
    total_p99_violation_duration = 0.0
    max_p99_latency: Optional[float] = None

    # Error rate tracking
    valid_error_rate_samples = 0
    error_rate_violations = 0
    total_error_violation_duration = 0.0

    # Resource utilization tracking
    cpu_values: List[float] = []
    mem_values: List[float] = []
    pod_counts: List[float] = []
    total_core_seconds = 0.0

    prev_epoch: Optional[float] = None

    for r in raw_rows:
        status = r.get("collection_status", "").strip().lower()
        epoch = parse_float(r.get("epoch_timestamp"))

        # Exclude completely unreachable or no-data rows from resource & SLO evaluation
        if status == "no_data_or_unreachable":
            for k in missing_data_counts:
                missing_data_counts[k] += 1
            continue

        usable_rows += 1

        # Calculate time step duration between consecutive valid observations
        step_duration = 0.0
        if prev_epoch is not None and epoch is not None and epoch > prev_epoch:
            raw_gap = epoch - prev_epoch
            # If gap exceeds max_interval_gap, exclude the large anomalous gap from duration accumulation
            if raw_gap <= max_interval_gap:
                step_duration = raw_gap
            else:
                step_duration = 0.0

        if epoch is not None:
            prev_epoch = epoch

        # 1. CPU Usage
        cpu = parse_float(r.get("cpu_usage_cores"))
        if cpu is not None:
            cpu_values.append(cpu)
            if step_duration > 0.0:
                total_core_seconds += cpu * step_duration
        else:
            missing_data_counts["cpu_usage_cores"] += 1

        # 2. Memory Working Set (MiB)
        mem = parse_float(r.get("memory_working_set_mb"))
        if mem is not None:
            mem_values.append(mem)
        else:
            # Fallback calculation if memory_working_set_bytes is present
            raw_mem_bytes = parse_float(r.get("memory_working_set_bytes"))
            if raw_mem_bytes is not None:
                mem_mib = raw_mem_bytes / (1024.0 * 1024.0)
                mem_values.append(mem_mib)
            else:
                missing_data_counts["memory_working_set_mb"] += 1

        # 3. Pod Count
        pods = parse_float(r.get("pod_count"))
        if pods is not None:
            pod_counts.append(pods)
        else:
            missing_data_counts["pod_count"] += 1

        # 4. P95 Latency
        p95 = parse_float(r.get("latency_p95_seconds"))
        if p95 is not None:
            valid_p95_count += 1
            if max_p95_latency is None or p95 > max_p95_latency:
                max_p95_latency = p95
            if p95 > target_p95_latency:
                latency_p95_violations += 1
                total_p95_violation_duration += step_duration
        else:
            missing_data_counts["latency_p95_seconds"] += 1

        # 5. P99 Latency
        p99 = parse_float(r.get("latency_p99_seconds"))
        if p99 is not None:
            valid_p99_count += 1
            if max_p99_latency is None or p99 > max_p99_latency:
                max_p99_latency = p99
            if p99 > target_p99_latency:
                latency_p99_violations += 1
                total_p99_violation_duration += step_duration
        else:
            missing_data_counts["latency_p99_seconds"] += 1

        # 6. HTTP Error Rate Calculation: (error_rps / total_rps) * 100
        err_rps = parse_float(r.get("http_error_rps"))
        total_rps = parse_float(r.get("http_rps"))

        if err_rps is not None and total_rps is not None:
            if total_rps > 0:
                valid_error_rate_samples += 1
                err_pct = (err_rps / total_rps) * 100.0
                if err_pct > target_error_rate_pct:
                    error_rate_violations += 1
                    total_error_violation_duration += step_duration
        else:
            if err_rps is None:
                missing_data_counts["http_error_rps"] += 1
            if total_rps is None:
                missing_data_counts["http_rps"] += 1

    # Compute violation percentages using valid observations only
    p95_violation_pct = (latency_p95_violations / valid_p95_count * 100.0) if valid_p95_count > 0 else 0.0
    p99_violation_pct = (latency_p99_violations / valid_p99_count * 100.0) if valid_p99_count > 0 else 0.0
    error_violation_pct = (error_rate_violations / valid_error_rate_samples * 100.0) if valid_error_rate_samples > 0 else 0.0

    mean_cpu = (sum(cpu_values) / len(cpu_values)) if cpu_values else None
    max_cpu = max(cpu_values) if cpu_values else None

    mean_mem = (sum(mem_values) / len(mem_values)) if mem_values else None
    max_mem = max(mem_values) if mem_values else None

    max_pods = max(pod_counts) if pod_counts else None

    return {
        "csv_path": csv_path,
        "autoscaler_model": autoscaler_label,
        "total_rows": total_rows,
        "valid_intervals": usable_rows,
        "target_p95_latency_seconds": target_p95_latency,
        "target_p99_latency_seconds": target_p99_latency,
        "target_error_rate_pct": target_error_rate_pct,
        # P95 SLO
        "valid_p95_samples": valid_p95_count,
        "latency_violations_count": latency_p95_violations,
        "latency_violation_percentage": round(p95_violation_pct, 2),
        "latency_violation_duration_seconds": round(total_p95_violation_duration, 2),
        "max_p95_latency_seconds": round(max_p95_latency, 4) if max_p95_latency is not None else None,
        # P99 SLO
        "valid_p99_samples": valid_p99_count,
        "latency_p99_violations_count": latency_p99_violations,
        "latency_p99_violation_percentage": round(p99_violation_pct, 2),
        "latency_p99_violation_duration_seconds": round(total_p99_violation_duration, 2),
        "max_p99_latency_seconds": round(max_p99_latency, 4) if max_p99_latency is not None else None,
        # Error Rate SLO
        "valid_error_rate_samples": valid_error_rate_samples,
        "error_violations_count": error_rate_violations,
        "error_violation_percentage": round(error_violation_pct, 2),
        "error_violation_duration_seconds": round(total_error_violation_duration, 2),
        # Resource Utilization
        "mean_cpu_cores": round(mean_cpu, 4) if mean_cpu is not None else None,
        "max_cpu_cores": round(max_cpu, 4) if max_cpu is not None else None,
        "mean_memory_mb": round(mean_mem, 2) if mean_mem is not None else None,
        "max_memory_mb": round(max_mem, 2) if max_mem is not None else None,
        "max_pod_count": int(max_pods) if max_pods is not None else None,
        "total_core_seconds": round(total_core_seconds, 2),
        "missing_data_counts": missing_data_counts,
        "violation_duration_note": "Sampling-based duration estimate derived from consecutive discrete observation intervals (intervals > max_interval_gap excluded).",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate Service Level Objective (SLO) compliance and resource metrics from an experiment CSV."
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
        "--target-p95-latency",
        type=float,
        default=0.200,
        help="Maximum allowable P95 response latency in seconds (default: 0.200)",
    )
    parser.add_argument(
        "--target-p99-latency",
        type=float,
        default=0.500,
        help="Maximum allowable P99 response latency in seconds (default: 0.500)",
    )
    parser.add_argument(
        "--target-error-rate",
        type=float,
        default=1.0,
        help="Maximum allowable HTTP error rate in percent (default: 1.0)",
    )
    parser.add_argument(
        "--max-interval-gap",
        type=float,
        default=15.0,
        help="Maximum elapsed seconds between consecutive samples before excluding the gap (default: 15.0)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output evaluation results as JSON",
    )

    args = parser.parse_args()
    try:
        report = evaluate_slo(
            csv_path=args.csv,
            target_p95_latency=args.target_p95_latency,
            target_p99_latency=args.target_p99_latency,
            target_error_rate_pct=args.target_error_rate,
            max_interval_gap=args.max_interval_gap,
            autoscaler_type=args.autoscaler_type,
        )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(report, indent=2))
        return

    print("=" * 70)
    print("               EXPERIMENT SLO EVALUATION REPORT")
    print("=" * 70)
    print(f"Autoscaler Evaluated         : {report.get('autoscaler_model')}")
    print(f"Dataset File                 : {report.get('csv_path')}")
    print(f"Total Rows / Valid Samples   : {report.get('total_rows')} / {report.get('valid_intervals')}")
    print("-" * 70)
    print("SLO Targets & Compliance:")
    print(f"  P95 Latency Target         : <= {report.get('target_p95_latency_seconds')}s")
    print(f"  P95 Latency Violations     : {report.get('latency_violations_count')} ({report.get('latency_violation_percentage')}%) across {report.get('valid_p95_samples')} samples")
    print(f"  P95 Violation Duration     : {report.get('latency_violation_duration_seconds')}s (estimate)")
    print(f"  Max Observed P95 Latency   : {report.get('max_p95_latency_seconds')}s")
    print()
    print(f"  P99 Latency Target         : <= {report.get('target_p99_latency_seconds')}s")
    print(f"  P99 Latency Violations     : {report.get('latency_p99_violations_count')} ({report.get('latency_p99_violation_percentage')}%) across {report.get('valid_p99_samples')} samples")
    print(f"  P99 Violation Duration     : {report.get('latency_p99_violation_duration_seconds')}s (estimate)")
    print(f"  Max Observed P99 Latency   : {report.get('max_p99_latency_seconds')}s")
    print()
    print(f"  HTTP Error-Rate Target     : <= {report.get('target_error_rate_pct')}%")
    print(f"  HTTP Error-Rate Violations : {report.get('error_violations_count')} ({report.get('error_violation_percentage')}%) across {report.get('valid_error_rate_samples')} samples")
    print(f"  Total Error Violation Time : {report.get('error_violation_duration_seconds')}s (estimate)")
    print("-" * 70)
    print("Resource Utilization & Scaling:")
    print(f"  Mean CPU Usage             : {report.get('mean_cpu_cores')} cores")
    print(f"  Max CPU Usage              : {report.get('max_cpu_cores')} cores")
    print(f"  Total Core-Seconds         : {report.get('total_core_seconds')} core-s")
    print(f"  Mean Memory Working Set    : {report.get('mean_memory_mb')} MiB")
    print(f"  Max Memory Working Set     : {report.get('max_memory_mb')} MiB")
    print(f"  Peak Pod Replicas          : {report.get('max_pod_count')}")
    print("-" * 70)
    print("Missing / Unreported Metrics:")
    for metric_name, count in report.get("missing_data_counts", {}).items():
        if count > 0:
            print(f"  - {metric_name:<26}: {count} samples missing")
    if not any(report.get("missing_data_counts", {}).values()):
        print("  None. All required metrics were fully reported.")
    print("=" * 70)


if __name__ == "__main__":
    main()

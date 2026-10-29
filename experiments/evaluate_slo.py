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
        if f != f:  # NaN check
            return None
        return f
    except (ValueError, TypeError):
        return None


def evaluate_slo(
    csv_path: str,
    target_p95_latency: float = 0.200,
    target_error_rate_pct: float = 1.0,
    max_interval_gap: float = 15.0,
) -> Dict[str, Any]:
    """
    Evaluates SLO adherence across time-series samples recorded in an experiment CSV.

    Parameters:
    - csv_path: Path to the metric CSV file.
    - target_p95_latency: Maximum allowable P95 response latency in seconds (default: 0.200s).
    - target_error_rate_pct: Maximum allowable HTTP error rate in percent (default: 1.0%).
    - max_interval_gap: Maximum elapsed seconds between consecutive samples before treating
      the gap as an outage or pause rather than extending a continuous violation.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    rows: List[Dict[str, Any]] = []
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    total_rows = len(rows)
    if total_rows == 0:
        return {
            "error": "Dataset is empty.",
            "total_rows": 0,
        }

    # Tracking metrics
    valid_intervals = 0
    missing_data_counts: Dict[str, int] = {
        "cpu_usage_cores": 0,
        "memory_working_set_mb": 0,
        "pod_count": 0,
        "latency_p95_seconds": 0,
        "http_error_rps": 0,
        "http_rps": 0,
    }

    latency_violations = 0
    error_rate_violations = 0
    total_latency_violation_duration = 0.0
    total_error_violation_duration = 0.0

    max_p95_latency: Optional[float] = None
    max_p99_latency: Optional[float] = None
    cpu_values: List[float] = []
    mem_values: List[float] = []
    pod_counts: List[float] = []

    prev_epoch: Optional[float] = None

    for i, r in enumerate(rows):
        status = r.get("collection_status", "").strip().lower()
        epoch = parse_float(r.get("epoch_timestamp"))

        # Determine step duration for time-weighting violations
        if prev_epoch is not None and epoch is not None and epoch > prev_epoch:
            step_duration = epoch - prev_epoch
            if step_duration > max_interval_gap:
                # Cap wide intervals to avoid unbounded duration accumulation
                step_duration = max_interval_gap
        else:
            step_duration = 0.0

        if epoch is not None:
            prev_epoch = epoch

        # Check status and track missing values
        if status != "ok":
            for k in missing_data_counts:
                missing_data_counts[k] += 1
            continue

        valid_intervals += 1

        # CPU
        cpu = parse_float(r.get("cpu_usage_cores"))
        if cpu is not None:
            cpu_values.append(cpu)
        else:
            missing_data_counts["cpu_usage_cores"] += 1

        # Memory
        mem = parse_float(r.get("memory_working_set_mb"))
        if mem is not None:
            mem_values.append(mem)
        else:
            missing_data_counts["memory_working_set_mb"] += 1

        # Pod count
        pods = parse_float(r.get("pod_count"))
        if pods is not None:
            pod_counts.append(pods)
        else:
            missing_data_counts["pod_count"] += 1

        # Latencies
        p95 = parse_float(r.get("latency_p95_seconds"))
        if p95 is not None:
            if max_p95_latency is None or p95 > max_p95_latency:
                max_p95_latency = p95
            if p95 > target_p95_latency:
                latency_violations += 1
                total_latency_violation_duration += step_duration
        else:
            missing_data_counts["latency_p95_seconds"] += 1

        p99 = parse_float(r.get("latency_p99_seconds"))
        if p99 is not None:
            if max_p99_latency is None or p99 > max_p99_latency:
                max_p99_latency = p99

        # HTTP error rate calculation
        err_rps = parse_float(r.get("http_error_rps"))
        total_rps = parse_float(r.get("http_rps"))

        if err_rps is not None and total_rps is not None:
            if total_rps > 0:
                err_pct = (err_rps / total_rps) * 100.0
            else:
                err_pct = 0.0

            if err_pct > target_error_rate_pct:
                error_rate_violations += 1
                total_error_violation_duration += step_duration
        else:
            if err_rps is None:
                missing_data_counts["http_error_rps"] += 1
            if total_rps is None:
                missing_data_counts["http_rps"] += 1

    latency_violation_pct = (latency_violations / valid_intervals * 100.0) if valid_intervals > 0 else 0.0
    error_violation_pct = (error_rate_violations / valid_intervals * 100.0) if valid_intervals > 0 else 0.0

    mean_cpu = (sum(cpu_values) / len(cpu_values)) if cpu_values else None
    max_cpu = max(cpu_values) if cpu_values else None

    mean_mem = (sum(mem_values) / len(mem_values)) if mem_values else None
    max_mem = max(mem_values) if mem_values else None

    max_pods = max(pod_counts) if pod_counts else None

    return {
        "csv_path": csv_path,
        "total_rows": total_rows,
        "valid_intervals": valid_intervals,
        "target_p95_latency_seconds": target_p95_latency,
        "target_error_rate_pct": target_error_rate_pct,
        "latency_violations_count": latency_violations,
        "latency_violation_percentage": round(latency_violation_pct, 2),
        "latency_violation_duration_seconds": round(total_latency_violation_duration, 2),
        "error_violations_count": error_rate_violations,
        "error_violation_percentage": round(error_violation_pct, 2),
        "error_violation_duration_seconds": round(total_error_violation_duration, 2),
        "max_p95_latency_seconds": round(max_p95_latency, 4) if max_p95_latency is not None else None,
        "max_p99_latency_seconds": round(max_p99_latency, 4) if max_p99_latency is not None else None,
        "mean_cpu_cores": round(mean_cpu, 4) if mean_cpu is not None else None,
        "max_cpu_cores": round(max_cpu, 4) if max_cpu is not None else None,
        "mean_memory_mb": round(mean_mem, 2) if mean_mem is not None else None,
        "max_memory_mb": round(max_mem, 2) if max_mem is not None else None,
        "max_pod_count": int(max_pods) if max_pods is not None else None,
        "missing_data_counts": missing_data_counts,
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
        "--target-p95-latency",
        type=float,
        default=0.200,
        help="Maximum allowable P95 response latency in seconds (default: 0.200)",
    )
    parser.add_argument(
        "--target-error-rate",
        type=float,
        default=1.0,
        help="Maximum allowable HTTP error rate in percent (default: 1.0)",
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
            target_error_rate_pct=args.target_error_rate,
        )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(report, indent=2))
        return

    print("=" * 65)
    print("           EXPERIMENT SLO EVALUATION REPORT")
    print("=" * 65)
    print(f"Dataset File                 : {report.get('csv_path')}")
    print(f"Total Rows / Valid Samples   : {report.get('total_rows')} / {report.get('valid_intervals')}")
    print("-" * 65)
    print("SLO Targets & Compliance:")
    print(f"  P95 Latency Target         : <= {report.get('target_p95_latency_seconds')}s")
    print(f"  P95 Latency Violations     : {report.get('latency_violations_count')} ({report.get('latency_violation_percentage')}%)")
    print(f"  Total Latency Violation Time: {report.get('latency_violation_duration_seconds')}s")
    print(f"  Max Observed P95 Latency   : {report.get('max_p95_latency_seconds')}s")
    print(f"  Max Observed P99 Latency   : {report.get('max_p99_latency_seconds')}s")
    print()
    print(f"  HTTP Error-Rate Target     : <= {report.get('target_error_rate_pct')}%")
    print(f"  HTTP Error-Rate Violations : {report.get('error_violations_count')} ({report.get('error_violation_percentage')}%)")
    print(f"  Total Error Violation Time : {report.get('error_violation_duration_seconds')}s")
    print("-" * 65)
    print("Resource Utilization & Scaling:")
    print(f"  Mean CPU Usage             : {report.get('mean_cpu_cores')} cores")
    print(f"  Max CPU Usage              : {report.get('max_cpu_cores')} cores")
    print(f"  Mean Memory Working Set    : {report.get('mean_memory_mb')} MB")
    print(f"  Max Memory Working Set     : {report.get('max_memory_mb')} MB")
    print(f"  Peak Pod Replicas          : {report.get('max_pod_count')}")
    print("-" * 65)
    print("Missing / Unreported Metrics:")
    for metric_name, count in report.get("missing_data_counts", {}).items():
        if count > 0:
            print(f"  - {metric_name:<26}: {count} samples missing")
    if not any(report.get("missing_data_counts", {}).values()):
        print("  None. All required metrics were fully reported.")
    print("=" * 65)


if __name__ == "__main__":
    main()

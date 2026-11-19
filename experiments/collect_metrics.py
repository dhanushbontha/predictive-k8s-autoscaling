"""
Kubernetes Autoscaling Research Metrics Collector
=================================================
Periodically collects synchronized workload, pod, deployment, HPA, and SLO metrics
from Prometheus for comparing CPU-based HPA and Predictive KEDA autoscaling.

Measurement Rate Window:
------------------------
A consistent 30-second rate window (`[30s]`) is used for all rate and quantile queries
across both CPU HPA and Predictive KEDA experiments.
Rationale:
- With Prometheus scraping target-app every 15 seconds, a 30s window guarantees at least
  2 scrape samples for mathematically valid Prometheus rate() calculations.
- A 30s window captures 10s workload surges and bursts with high fidelity and minimal
  phase lag, avoiding the artificial 60s smoothing delay of standard [1m] windows.
- Uniform definition ensures identical, scientifically comparable measurement conditions
  for both autoscalers.

Autoscaler Selection:
---------------------
- CPU HPA: target-app-hpa
- Predictive KEDA: keda-hpa-target-app-predictive-scaledobject
"""

import argparse
import csv
import datetime
import json
import math
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

# Consistent 30-second measurement window for rate() and quantile queries
DEFAULT_RATE_WINDOW = os.getenv("PROMETHEUS_RATE_WINDOW", "30s")

# Standard Prometheus queries with proper multi-series aggregation and namespace isolation
DEFAULT_QUERIES = {
    # Pod CPU usage in cores across all target-app pods
    "cpu_usage_cores": 'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}", pod=~"{pod_prefix}.*"}}[{rate_window}]))',
    # Pod memory working set in bytes
    "memory_working_set_bytes": 'sum(container_memory_working_set_bytes{{namespace="{namespace}", pod=~"{pod_prefix}.*"}})',
    # Running target pod count
    "pod_count": 'count(kube_pod_status_phase{{namespace="{namespace}", pod=~"{pod_prefix}.*", phase="Running"}})',
    # Deployment replicas
    "deployment_replicas": 'max(kube_deployment_status_replicas{{namespace="{namespace}", deployment="{deployment_name}"}})',
    "deployment_available_replicas": 'max(kube_deployment_status_replicas_available{{namespace="{namespace}", deployment="{deployment_name}"}})',
    # Autoscaler replicas (dynamic HPA name for CPU HPA vs Predictive KEDA)
    "hpa_current_replicas": 'max(kube_horizontalpodautoscaler_status_current_replicas{{namespace="{namespace}", horizontalpodautoscaler="{hpa_name}"}})',
    "hpa_desired_replicas": 'max(kube_horizontalpodautoscaler_status_desired_replicas{{namespace="{namespace}", horizontalpodautoscaler="{hpa_name}"}})',
    # Application HTTP metrics
    "http_requests_total": 'sum(http_requests_total{{namespace="{namespace}", job="{service_name}"}})',
    "http_requests_work_total": 'sum(http_requests_total{{namespace="{namespace}", job="{service_name}", handler="/api/work"}})',
    "http_rps": 'sum(rate(http_requests_total{{namespace="{namespace}", job="{service_name}"}}[{rate_window}]))',
    "http_work_rps": 'sum(rate(http_requests_total{{namespace="{namespace}", job="{service_name}", handler="/api/work"}}[{rate_window}]))',
    # HTTP error rate covering both 4xx and 5xx responses (evaluates to 0.0 when active traffic has 0 errors; empty if service inactive)
    "http_error_rps": '(sum(rate(http_requests_total{{namespace="{namespace}", job="{service_name}", status=~"[45].."}}[{rate_window}])) or on() (0 * sum(rate(http_requests_total{{namespace="{namespace}", job="{service_name}"}}[{rate_window}]))))',
    "latency_p50_seconds": 'histogram_quantile(0.50, sum by (le) (rate(http_request_duration_highr_seconds_bucket{{namespace="{namespace}", job="{service_name}"}}[{rate_window}])))',
    "latency_p95_seconds": 'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_highr_seconds_bucket{{namespace="{namespace}", job="{service_name}"}}[{rate_window}])))',
    "latency_p99_seconds": 'histogram_quantile(0.99, sum by (le) (rate(http_request_duration_highr_seconds_bucket{{namespace="{namespace}", job="{service_name}"}}[{rate_window}])))',
}

# Core metrics that must be present for a sample row to be marked 'ok'
REQUIRED_METRIC_KEYS = [
    "cpu_usage_cores",
    "memory_working_set_bytes",
    "pod_count",
    "deployment_replicas",
    "hpa_current_replicas",
    "hpa_desired_replicas",
]

CSV_HEADERS = [
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


class PrometheusClient:
    """Client for executing instant PromQL queries against Prometheus HTTP API."""

    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def query(self, promql: str, aggregation_op: str = "sum") -> Optional[float]:
        """
        Execute an instant PromQL query and extract a single float scalar result.
        Handles multiple series properly by aggregating instead of naively taking results[0].
        Returns None if query produces no data, fails, or returns NaN/inf.
        """
        encoded_query = urllib.parse.urlencode({"query": promql})
        url = f"{self.base_url}/api/v1/query?{encoded_query}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return None

        if data.get("status") != "success":
            return None

        results = data.get("data", {}).get("result", [])
        if not results:
            return None

        # If exactly 1 series returned, extract scalar value directly
        if len(results) == 1:
            try:
                val_str = results[0]["value"][1]
                val = float(val_str)
                if val != val or math.isinf(val):  # NaN or Inf check
                    return None
                return val
            except (KeyError, IndexError, ValueError, TypeError):
                return None

        # If multiple series returned, aggregate them properly rather than blindly taking results[0]
        parsed_values: List[float] = []
        for item in results:
            try:
                v = float(item["value"][1])
                if v == v and not math.isinf(v):
                    parsed_values.append(v)
            except (KeyError, IndexError, ValueError, TypeError):
                continue

        if not parsed_values:
            return None

        if aggregation_op == "max":
            return max(parsed_values)
        elif aggregation_op == "min":
            return min(parsed_values)
        elif aggregation_op == "count":
            return float(len(parsed_values))
        else:
            return sum(parsed_values)


def collect_snapshot(
    client: PrometheusClient,
    pod_prefix: str = "target-app",
    deployment_name: str = "target-app",
    hpa_name: str = "target-app-hpa",
    service_name: str = "target-app-service",
    namespace: str = "default",
    rate_window: str = DEFAULT_RATE_WINDOW,
) -> Dict[str, Any]:
    """
    Collects a single point-in-time metrics snapshot across all defined PromQL queries.
    Determines status:
    - 'ok': all required metrics collected.
    - 'partial': some required metrics missing, but partial metrics available.
    - 'no_data_or_unreachable': no valid metrics returned or Prometheus unavailable.
    Leaves missing metrics as empty strings ('') without silently fabricating zeros.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    epoch = now.timestamp()
    iso_time = now.isoformat()

    row: Dict[str, Any] = {
        "timestamp_utc": iso_time,
        "epoch_timestamp": f"{epoch:.3f}",
        "collection_status": "ok",
    }

    any_success = False
    missing_required = False

    for metric_key, query_template in DEFAULT_QUERIES.items():
        # Determine appropriate aggregation operation if multiple series occur
        if "max(" in query_template or "desired" in metric_key or "current" in metric_key:
            agg_op = "max"
        elif "count(" in query_template or "pod_count" in metric_key:
            agg_op = "count"
        else:
            agg_op = "sum"

        query_str = query_template.format(
            namespace=namespace,
            pod_prefix=pod_prefix,
            deployment_name=deployment_name,
            hpa_name=hpa_name,
            service_name=service_name,
            rate_window=rate_window,
        )
        val = client.query(query_str, aggregation_op=agg_op)
        if val is not None:
            any_success = True
            if isinstance(val, float) and not val.is_integer():
                row[metric_key] = f"{val:.6f}"
            else:
                row[metric_key] = str(int(round(val)))
        else:
            row[metric_key] = ""
            if metric_key in REQUIRED_METRIC_KEYS:
                missing_required = True

    # Computed convenience field: memory working set in MiB (1024 * 1024 bytes)
    mem_bytes = row.get("memory_working_set_bytes")
    if mem_bytes:
        try:
            row["memory_working_set_mb"] = f"{float(mem_bytes) / (1024 * 1024):.2f}"
        except ValueError:
            row["memory_working_set_mb"] = ""
    else:
        row["memory_working_set_mb"] = ""

    # Determine collection status
    if not any_success:
        row["collection_status"] = "no_data_or_unreachable"
    elif missing_required:
        row["collection_status"] = "partial"
    else:
        row["collection_status"] = "ok"

    return row


def run_collector(
    prometheus_url: str,
    output_file: str,
    interval: float = 5.0,
    duration: Optional[float] = None,
    pod_prefix: str = "target-app",
    deployment_name: str = "target-app",
    hpa_name: str = "target-app-hpa",
    service_name: str = "target-app-service",
    namespace: str = "default",
    rate_window: str = DEFAULT_RATE_WINDOW,
    autoscaler_type: str = "hpa",
):
    """Periodically queries Prometheus and appends rows to the output CSV file."""
    client = PrometheusClient(base_url=prometheus_url)

    output_dir = os.path.dirname(os.path.abspath(output_file))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    file_exists = os.path.exists(output_file) and os.path.getsize(output_file) > 0

    print("=" * 65)
    print("      KUBERNETES AUTOSCALING METRICS COLLECTOR")
    print("=" * 65)
    print(f"  Autoscaler Model : {autoscaler_type.upper()} ({hpa_name})")
    print(f"  Prometheus URL   : {prometheus_url}")
    print(f"  Output CSV       : {output_file}")
    print(f"  Sample Interval  : {interval}s")
    print(f"  Rate Window      : {rate_window}")
    print(f"  Duration         : {'Continuous (until stopped)' if duration is None else f'{duration}s'}")
    print("-" * 65)
    print("Press Ctrl+C to terminate.")

    start_time = time.time()
    count = 0
    stop_requested = False

    def handle_sigint(sig, frame):
        nonlocal stop_requested
        print("\nStopping collector...")
        stop_requested = True

    signal.signal(signal.SIGINT, handle_sigint)

    with open(output_file, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
            f.flush()

        while not stop_requested:
            loop_start = time.time()
            snapshot = collect_snapshot(
                client=client,
                pod_prefix=pod_prefix,
                deployment_name=deployment_name,
                hpa_name=hpa_name,
                service_name=service_name,
                namespace=namespace,
                rate_window=rate_window,
            )
            writer.writerow(snapshot)
            f.flush()
            count += 1

            status = snapshot["collection_status"]
            cpu = snapshot.get("cpu_usage_cores") or "--"
            pods = snapshot.get("pod_count") or "--"
            hpa = snapshot.get("hpa_current_replicas") or "--"
            des = snapshot.get("hpa_desired_replicas") or "--"
            rps = snapshot.get("http_work_rps") or "--"
            print(f"[{snapshot['timestamp_utc']}] #{count:03d} | Status: {status:<7} | Pods: {pods} | HPA (cur/des): {hpa}/{des} | CPU: {cpu} cores | Work RPS: {rps}")

            if duration and (time.time() - start_time) >= duration:
                break

            elapsed = time.time() - loop_start
            sleep_time = max(0.0, interval - elapsed)
            time.sleep(sleep_time)

    print(f"Collection complete. Recorded {count} snapshots to {output_file}")


def resolve_autoscaler_hpa(autoscaler_type: str, explicit_hpa: Optional[str]) -> str:
    """Resolves HPA resource name based on autoscaler type if not explicitly overridden."""
    if explicit_hpa:
        return explicit_hpa
    atype = autoscaler_type.strip().lower()
    if atype in ("predictive", "keda", "prophet"):
        return "keda-hpa-target-app-predictive-scaledobject"
    return "target-app-hpa"


def resolve_output_path(
    explicit_output: Optional[str],
    autoscaler_type: str,
    profile: Optional[str],
) -> str:
    """Generates standard CSV output filename in experiments/results/, ensuring historical files are not overwritten."""
    if explicit_output:
        return explicit_output
    atype = "predictive" if autoscaler_type.lower() in ("predictive", "keda") else "hpa"
    prof = profile.lower() if profile else "experiment"
    return os.path.join("experiments", "results", f"{atype}_{prof}.csv")


def main():
    parser = argparse.ArgumentParser(
        description="Collect workload and Kubernetes metrics from Prometheus for autoscaling experiments."
    )
    parser.add_argument(
        "--autoscaler-type",
        choices=["hpa", "predictive", "keda"],
        default=os.getenv("AUTOSCALER_TYPE", "hpa"),
        help="Autoscaler approach to monitor: 'hpa' (CPU-based HPA) or 'predictive' (Prophet+KEDA) (default: hpa)",
    )
    parser.add_argument(
        "--hpa-name",
        default=os.getenv("HPA_NAME"),
        help="HorizontalPodAutoscaler resource name (default: target-app-hpa for HPA, keda-hpa-target-app-predictive-scaledobject for predictive)",
    )
    parser.add_argument(
        "--profile",
        default=os.getenv("WORKLOAD_PROFILE"),
        help="Workload profile name (e.g. stable, bursty, gradual, periodic, noisy)",
    )
    parser.add_argument(
        "--prometheus-url",
        default=os.getenv("PROMETHEUS_BASE_URL", "http://127.0.0.1:9090"),
        help="Base URL of Prometheus HTTP API (default: http://127.0.0.1:9090 or PROMETHEUS_BASE_URL)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to output CSV file (defaults to experiments/{autoscaler}_{profile}.csv)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Collection frequency in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--rate-window",
        default=DEFAULT_RATE_WINDOW,
        help=f"PromQL rate measurement window (default: {DEFAULT_RATE_WINDOW})",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Collection runtime in seconds (default: run until Ctrl+C)",
    )
    parser.add_argument(
        "--pod-prefix",
        default="target-app",
        help="Pod prefix to monitor (default: target-app)",
    )
    parser.add_argument(
        "--deployment-name",
        default="target-app",
        help="Kubernetes Deployment name (default: target-app)",
    )
    parser.add_argument(
        "--service-name",
        default="target-app-service",
        help="Kubernetes Service name used in Prometheus job label (default: target-app-service)",
    )
    parser.add_argument(
        "--namespace",
        default="default",
        help="Kubernetes namespace (default: default)",
    )

    args = parser.parse_args()

    resolved_hpa = resolve_autoscaler_hpa(args.autoscaler_type, args.hpa_name)
    resolved_output = resolve_output_path(args.output, args.autoscaler_type, args.profile)

    run_collector(
        prometheus_url=args.prometheus_url,
        output_file=resolved_output,
        interval=args.interval,
        duration=args.duration,
        pod_prefix=args.pod_prefix,
        deployment_name=args.deployment_name,
        hpa_name=resolved_hpa,
        service_name=args.service_name,
        namespace=args.namespace,
        rate_window=args.rate_window,
        autoscaler_type=args.autoscaler_type,
    )


if __name__ == "__main__":
    main()

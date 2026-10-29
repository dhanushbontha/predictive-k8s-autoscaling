import argparse
import csv
import datetime
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

# Standard Prometheus query definitions for the target-app workload
DEFAULT_QUERIES = {
    # Pod CPU usage in cores (rate over 1 minute)
    "cpu_usage_cores": 'sum(rate(container_cpu_usage_seconds_total{{pod=~"{pod_prefix}.*"}}[1m]))',
    # Pod memory working set in bytes
    "memory_working_set_bytes": 'sum(container_memory_working_set_bytes{{pod=~"{pod_prefix}.*"}})',
    # Target pod count
    "pod_count": 'count(kube_pod_info{{pod=~"{pod_prefix}.*"}})',
    # Deployment replicas
    "deployment_replicas": 'kube_deployment_status_replicas{{deployment="{deployment_name}"}}',
    "deployment_available_replicas": 'kube_deployment_status_replicas_available{{deployment="{deployment_name}"}}',
    # HPA replicas
    "hpa_current_replicas": 'kube_horizontalpodautoscaler_status_current_replicas{{horizontalpodautoscaler="{hpa_name}"}}',
    "hpa_desired_replicas": 'kube_horizontalpodautoscaler_status_desired_replicas{{horizontalpodautoscaler="{hpa_name}"}}',
    # Application HTTP metrics
    "http_requests_total": 'sum(http_requests_total{{job="{service_name}"}})',
    "http_requests_work_total": 'sum(http_requests_total{{job="{service_name}", handler="/api/work"}})',
    "http_rps": 'sum(rate(http_requests_total{{job="{service_name}"}}[1m]))',
    "http_work_rps": 'sum(rate(http_requests_total{{job="{service_name}", handler="/api/work"}}[1m]))',
    # HTTP error rate covering both 4xx and 5xx responses. Uses 'or on() vector(0)' conditional on service activity
    "http_error_rps": '(sum(rate(http_requests_total{{job="{service_name}", status=~"[45].."}}[1m])) or on() (0 * sum(http_requests_total{{job="{service_name}"}})))',
    "latency_p50_seconds": 'histogram_quantile(0.50, sum by (le) (rate(http_request_duration_highr_seconds_bucket{{job="{service_name}"}}[1m])))',
    "latency_p95_seconds": 'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_highr_seconds_bucket{{job="{service_name}"}}[1m])))',
    "latency_p99_seconds": 'histogram_quantile(0.99, sum by (le) (rate(http_request_duration_highr_seconds_bucket{{job="{service_name}"}}[1m])))',
}



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

    def query(self, promql: str) -> Optional[float]:
        """
        Execute an instant PromQL query and extract a single float scalar result.
        Returns None if query produces no data, fails, or returns NaN.
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

        try:
            # Result format: {"metric": {...}, "value": [timestamp, "string_value"]}
            val_str = results[0]["value"][1]
            val = float(val_str)
            if val != val:  # Check for NaN
                return None
            return val
        except (KeyError, IndexError, ValueError, TypeError):
            return None


def collect_snapshot(
    client: PrometheusClient,
    pod_prefix: str = "target-app",
    deployment_name: str = "target-app",
    hpa_name: str = "target-app-hpa",
    service_name: str = "target-app-service",
) -> Dict[str, Any]:
    """
    Collects a single point-in-time metrics snapshot across all defined PromQL queries.
    Leaves values empty (None) if metrics are not returned or unavailable.
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

    for metric_key, query_template in DEFAULT_QUERIES.items():
        query_str = query_template.format(
            pod_prefix=pod_prefix,
            deployment_name=deployment_name,
            hpa_name=hpa_name,
            service_name=service_name,
        )
        val = client.query(query_str)
        if val is not None:
            any_success = True
            row[metric_key] = f"{val:.6f}" if isinstance(val, float) and not val.is_integer() else str(int(val))
        else:
            row[metric_key] = ""

    # Computed convenience field: memory working set in MB
    mem_bytes = row.get("memory_working_set_bytes")
    if mem_bytes:
        try:
            row["memory_working_set_mb"] = f"{float(mem_bytes) / (1024 * 1024):.2f}"
        except ValueError:
            row["memory_working_set_mb"] = ""
    else:
        row["memory_working_set_mb"] = ""

    if not any_success:
        row["collection_status"] = "no_data_or_unreachable"

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
):
    """Periodically queries Prometheus and appends rows to the output CSV file."""
    client = PrometheusClient(base_url=prometheus_url)

    # Ensure parent output directory exists
    output_dir = os.path.dirname(os.path.abspath(output_file))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    file_exists = os.path.exists(output_file) and os.path.getsize(output_file) > 0

    print(f"Starting Metrics Collector:")
    print(f"  Prometheus URL : {prometheus_url}")
    print(f"  Output CSV     : {output_file}")
    print(f"  Interval       : {interval}s")
    print(f"  Duration       : {'Continuous (until stopped)' if duration is None else f'{duration}s'}")
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
            )
            writer.writerow(snapshot)
            f.flush()
            count += 1

            status = snapshot["collection_status"]
            cpu = snapshot.get("cpu_usage_cores") or "0"
            pods = snapshot.get("pod_count") or "0"
            hpa = snapshot.get("hpa_current_replicas") or "0"
            rps = snapshot.get("http_work_rps") or "0"
            print(f"[{snapshot['timestamp_utc']}] Sample #{count} | Status: {status} | Pods: {pods} | HPA: {hpa} | CPU: {cpu} cores | Work RPS: {rps}")

            if duration and (time.time() - start_time) >= duration:
                break

            elapsed = time.time() - loop_start
            sleep_time = max(0.0, interval - elapsed)
            time.sleep(sleep_time)

    print(f"Collection complete. Recorded {count} snapshots to {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Collect workload and Kubernetes metrics from Prometheus for autoscaling experiments."
    )
    parser.add_argument(
        "--prometheus-url",
        default=os.getenv("PROMETHEUS_BASE_URL", "http://127.0.0.1:9090"),
        help="Base URL of Prometheus HTTP API (default: http://127.0.0.1:9090 or PROMETHEUS_BASE_URL)",
    )
    parser.add_argument(
        "--output",
        default="experiments/metrics_collected.csv",
        help="Path to output CSV file (default: experiments/metrics_collected.csv)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Collection frequency in seconds (default: 5.0)",
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
        "--hpa-name",
        default="target-app-hpa",
        help="HorizontalPodAutoscaler name (default: target-app-hpa)",
    )
    parser.add_argument(
        "--service-name",
        default="target-app-service",
        help="Kubernetes Service name used in Prometheus job label (default: target-app-service)",
    )

    args = parser.parse_args()
    run_collector(
        prometheus_url=args.prometheus_url,
        output_file=args.output,
        interval=args.interval,
        duration=args.duration,
        pod_prefix=args.pod_prefix,
        deployment_name=args.deployment_name,
        hpa_name=args.hpa_name,
        service_name=args.service_name,
    )


if __name__ == "__main__":
    main()

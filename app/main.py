"""
FastAPI workload application for predictive-k8s-autoscaling experiments.

Endpoints
---------
GET  /work    – do configurable CPU work, labelled by WORK_MS env var
GET  /healthz – liveness probe (always 200 once process is up)
GET  /ready   – readiness probe (200 after STARTUP_DELAY_SECONDS have elapsed)
GET  /metrics – Prometheus metrics (counter + histogram)
"""

import os
import time
import math
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import (
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    REGISTRY,
)

# ─── configuration ────────────────────────────────────────────────────────────
WORK_MS: float = float(os.environ.get("WORK_MS", "20"))          # ms of CPU per request
STARTUP_DELAY: float = float(os.environ.get("STARTUP_DELAY_SECONDS", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("workload-app")

# ─── startup state ────────────────────────────────────────────────────────────
_start_time: float = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.monotonic()
    log.info("App starting. WORK_MS=%.1f STARTUP_DELAY=%.1f", WORK_MS, STARTUP_DELAY)
    yield
    log.info("App shutting down.")


# ─── Prometheus metrics ───────────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "workload_requests_total",
    "Total HTTP requests to /work",
    ["status"],
)

# Buckets cover 10 ms → 5 s with finer resolution at low latency
_LATENCY_BUCKETS = (
    0.010, 0.025, 0.050, 0.075,
    0.100, 0.200, 0.300, 0.500,
    0.750, 1.0,   1.5,   2.0,
    3.0,   5.0,
)

REQUEST_LATENCY = Histogram(
    "workload_request_duration_seconds",
    "End-to-end latency of /work requests",
    ["status"],
    buckets=_LATENCY_BUCKETS,
)

# ─── app ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="k8s-workload-app", version="0.1.0", lifespan=lifespan)


def _burn_cpu(duration_ms: float) -> None:
    """Spin for `duration_ms` milliseconds doing real floating-point work."""
    deadline = time.monotonic() + duration_ms / 1000.0
    x = 1.0
    while time.monotonic() < deadline:
        # Math operations that can't be trivially optimised away
        x = math.sqrt(x * 1.0000001 + 0.1) * math.log1p(abs(math.sin(x)))
        x = x if x > 0 else 1.0


@app.get("/work", summary="Do CPU work and return elapsed ms")
def work():
    t0 = time.monotonic()
    try:
        _burn_cpu(WORK_MS)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        REQUEST_COUNT.labels(status="200").inc()
        REQUEST_LATENCY.labels(status="200").observe(elapsed_ms / 1000.0)
        return {"status": "ok", "work_ms": WORK_MS, "elapsed_ms": round(elapsed_ms, 2)}
    except Exception as exc:  # pragma: no cover
        REQUEST_COUNT.labels(status="500").inc()
        raise exc


@app.get("/healthz", summary="Liveness probe")
def healthz():
    return {"status": "alive"}


@app.get("/ready", summary="Readiness probe")
def ready(response: Response):
    elapsed = time.monotonic() - _start_time
    if elapsed >= STARTUP_DELAY:
        return {"status": "ready", "elapsed_s": round(elapsed, 2)}
    response.status_code = 503
    remaining = round(STARTUP_DELAY - elapsed, 2)
    return {"status": "not_ready", "wait_s": remaining}


@app.get("/metrics", summary="Prometheus metrics")
def metrics():
    data = generate_latest(REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)

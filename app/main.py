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
    REGISTRY,
)

WORK_MS: float = float(os.environ.get("WORK_MS", "20"))
STARTUP_DELAY: float = float(os.environ.get("STARTUP_DELAY_SECONDS", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("workload-app")

_start_time: float = 0.0

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.monotonic()
    log.info("App starting. WORK_MS=%.1f STARTUP_DELAY=%.1f", WORK_MS, STARTUP_DELAY)
    yield
    log.info("App shutting down.")

REQUEST_COUNT = Counter(
    "workload_requests_total",
    "Total HTTP requests to /work",
    ["status"],
)

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

app = FastAPI(title="k8s-workload-app", version="0.1.0", lifespan=lifespan)

def _burn_thread_cpu(duration_ms: float) -> float:
    """Burn fixed amount of thread CPU time using time.thread_time()."""
    t_start = time.thread_time()
    target_sec = duration_ms / 1000.0
    x = 1.0
    while (time.thread_time() - t_start) < target_sec:
        x = math.sqrt(x * 1.0000001 + 0.1) * math.log1p(abs(math.sin(x)))
        if x <= 0:
            x = 1.0
    t_burned = time.thread_time() - t_start
    return t_burned

@app.get("/work", summary="Do fixed CPU work and return elapsed time")
def work():
    t_wall_start = time.monotonic()
    try:
        cpu_burned_sec = _burn_thread_cpu(WORK_MS)
        wall_elapsed_ms = (time.monotonic() - t_wall_start) * 1000.0
        REQUEST_COUNT.labels(status="200").inc()
        REQUEST_LATENCY.labels(status="200").observe(wall_elapsed_ms / 1000.0)
        return {
            "status": "ok",
            "work_ms": WORK_MS,
            "cpu_burned_ms": round(cpu_burned_sec * 1000.0, 2),
            "elapsed_ms": round(wall_elapsed_ms, 2)
        }
    except Exception as exc:
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

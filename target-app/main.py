import math
import time
from typing import Annotated

from fastapi import FastAPI, Query
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(
    title="Predictive Autoscaling Target App",
    description="Target workload application for predictive Kubernetes autoscaling benchmark.",
    version="1.0.0",
)

# Instrument the app and expose the /metrics endpoint
# Uses template routes (/api/work, /health) without URL queries to prevent label cardinality explosion
Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    should_respect_env_var=False,
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=True)



def perform_cpu_work(iterations: int) -> float:
    """Run CPU-intensive computation and return elapsed time in seconds."""
    start_time = time.perf_counter()
    # Compute trigonometric operations iteratively to generate CPU load
    val = 0.0
    for i in range(iterations):
        val += math.sin(i) * math.cos(i)
    elapsed_time = time.perf_counter() - start_time
    return elapsed_time


@app.get("/health")
def health_check():
    """Health check endpoint to report service status."""
    return {"status": "ok"}


@app.get("/api/work")
def work(
    iterations: Annotated[
        int,
        Query(
            description="Number of CPU computation loops to execute.",
            ge=1,
            le=10_000_000,
        ),
    ] = 100_000,
):
    """
    Perform configurable CPU-intensive work and return the processing time.
    """
    duration = perform_cpu_work(iterations)
    return {
        "status": "success",
        "iterations": iterations,
        "processing_time_seconds": duration,
    }

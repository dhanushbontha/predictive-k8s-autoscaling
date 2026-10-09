"""
Tests for app/main.py

Run from the repo root:
    pip install -r app/requirements.txt -r app/requirements-dev.txt
    pytest app/tests/ -v

The tests use httpx.AsyncClient with ASGITransport so no real server is needed.
"""

import os
import time

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# ─── patch env vars before importing the app ──────────────────────────────────
os.environ.setdefault("WORK_MS", "5")            # short burn for fast tests
os.environ.setdefault("STARTUP_DELAY_SECONDS", "0")

from app.main import app  # noqa: E402  (import after env patch)


# ─── fixture ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client():
    """Async test client wrapping the FastAPI ASGI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ─── /healthz ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_healthz_returns_200(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


# ─── /ready ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ready_with_no_delay(client):
    """With STARTUP_DELAY=0 the pod should be ready immediately."""
    resp = await client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_ready_not_ready_during_delay(monkeypatch):
    """With a future deadline the endpoint should return 503."""
    import app.main as m
    monkeypatch.setattr(m, "STARTUP_DELAY", 9999.0)  # far in the future
    monkeypatch.setattr(m, "_start_time", time.monotonic())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert resp.json()["wait_s"] > 0


# ─── /work ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_work_returns_ok(client):
    resp = await client.get("/work")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "elapsed_ms" in body
    assert body["elapsed_ms"] >= 0


@pytest.mark.asyncio
async def test_work_elapsed_close_to_work_ms(client, monkeypatch):
    """Elapsed ms should be within 3× of WORK_MS (tolerant for slow CI runners)."""
    import app.main as m
    monkeypatch.setattr(m, "WORK_MS", 10.0)
    resp = await client.get("/work")
    assert resp.status_code == 200
    elapsed = resp.json()["elapsed_ms"]
    assert elapsed < 10.0 * 3, f"elapsed_ms {elapsed} far exceeds WORK_MS=10"


# ─── /metrics ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_endpoint_reachable(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_metrics_contain_request_counter(client):
    # Hit /work once so the counter is non-zero
    await client.get("/work")
    resp = await client.get("/metrics")
    body = resp.text
    assert "workload_requests_total" in body


@pytest.mark.asyncio
async def test_metrics_contain_latency_histogram(client):
    await client.get("/work")
    resp = await client.get("/metrics")
    body = resp.text
    assert "workload_request_duration_seconds_bucket" in body
    assert "workload_request_duration_seconds_count" in body

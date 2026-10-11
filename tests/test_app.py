import os
import time
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("WORK_MS", "5")
os.environ.setdefault("STARTUP_DELAY_SECONDS", "0")

from app.main import app

@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.mark.asyncio
async def test_healthz_returns_200(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"

@pytest.mark.asyncio
async def test_ready_with_no_delay(client):
    resp = await client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"

@pytest.mark.asyncio
async def test_work_returns_ok(client):
    resp = await client.get("/work")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "elapsed_ms" in body

@pytest.mark.asyncio
async def test_metrics_endpoint_reachable(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]

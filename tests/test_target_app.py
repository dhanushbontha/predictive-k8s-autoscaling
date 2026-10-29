import sys
import warnings
from pathlib import Path
import pytest

# Suppress Starlette's deprecation warning regarding httpx vs httpx2
warnings.filterwarnings("ignore", message=".*httpx.*", category=UserWarning)

from fastapi.testclient import TestClient


# Ensure target-app is discoverable in sys.path
target_app_dir = Path(__file__).resolve().parent.parent / "target-app"
if str(target_app_dir) not in sys.path:
    sys.path.insert(0, str(target_app_dir))

from main import app  # noqa: E402

client = TestClient(app)




def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_work_endpoint_default():
    response = client.get("/api/work")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["iterations"] == 100_000
    assert "processing_time_seconds" in data
    assert isinstance(data["processing_time_seconds"], (int, float))
    assert data["processing_time_seconds"] >= 0


def test_work_endpoint_custom_iterations():
    iterations = 5_000
    response = client.get(f"/api/work?iterations={iterations}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["iterations"] == iterations
    assert data["processing_time_seconds"] >= 0


def test_work_endpoint_validation_error():
    # Iterations less than 1 should return 422 Unprocessable Entity
    response = client.get("/api/work?iterations=0")
    assert response.status_code == 422


def test_metrics_endpoint_exists():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")
    content = response.text
    # Check for Prometheus standard metric families
    assert "http_requests_total" in content or "http_request_duration_seconds" in content


def test_metrics_records_requests():
    # Make a request to /health and /api/work to record metrics
    client.get("/health")
    client.get("/api/work?iterations=1000")

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    metrics_text = metrics_resp.text

    # Verify presence of request count and duration histogram
    assert "http_requests_total" in metrics_text
    assert "http_request_duration_seconds" in metrics_text
    # Verify handler labels do not leak raw query params (low cardinality)
    assert 'handler="/health"' in metrics_text or 'handler="/api/work"' in metrics_text


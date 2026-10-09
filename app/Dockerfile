# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Slim Python 3.12 image — see docs/versions.md for pinned versions
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Keeps Python from generating .pyc files and enables stdout/stderr unbuffering
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps first so Docker can cache this layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY main.py .

# Default env vars (overridable at runtime / in Deployment manifest)
ENV WORK_MS=20 \
    STARTUP_DELAY_SECONDS=0

EXPOSE 8000

# Use a non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
USER appuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

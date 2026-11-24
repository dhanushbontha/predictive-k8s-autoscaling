FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user and experiments directory
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/experiments/results && \
    chown -R appuser:appuser /app

# Copy application code
COPY main.py .

# Switch to non-root user
USER appuser

# Expose backend API port
EXPOSE 8080

# Run FastAPI backend with Uvicorn bound to 0.0.0.0
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

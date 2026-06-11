FROM python:3.14-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY src/requirements.txt .

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/follower/ ./follower/

# Create directories for runtime state
RUN mkdir -p /app/config /app/logs

# Default env vars (overridden by .env or docker-compose)
ENV PYTHONUNBUFFERED=1
ENV FOLLOWER_CHECK_INTERVAL_MINUTES=5

# Health check: bot writes heartbeat timestamp, we check it's recent
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD test $(find /app/logs -name "*.log" -mmin -10 2>/dev/null | wc -l) -gt 0 || exit 1

ENTRYPOINT ["python", "-m", "follower.follower"]

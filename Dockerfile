# ==============================================================================
# PM Operations Agent — Production Dockerfile
# Multi-architecture: linux/arm64 (Oracle Always Free Ampere A1) & linux/amd64
# ==============================================================================

FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    HOST=0.0.0.0 \
    PORT=8000 \
    DB_PATH=/app/data/pm_operations.db

# Set working directory
WORKDIR /app

# Create dedicated non-root group and user (UID/GID 10001)
RUN groupadd -g 10001 pmuser && \
    useradd -u 10001 -g pmuser -m -s /bin/bash pmuser

# Install production dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and entrypoint
COPY app/ ./app/
COPY run.py .

# Create and set permissions for persistent SQLite data directory
RUN mkdir -p /app/data && \
    chown -R pmuser:pmuser /app

# Switch to non-root user
USER pmuser

# Expose internal application port (FastAPI / Uvicorn)
EXPOSE 8000

# Docker healthcheck using Python standard library (no extra curl package needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').getcode() == 200 else 1)"

# Application launch command
CMD ["python", "run.py"]

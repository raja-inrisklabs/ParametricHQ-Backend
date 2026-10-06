FROM python:3.12-slim

# Install system dependencies if required
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create storage directory
RUN mkdir -p /srv/storage/datasets

# Copy application source code
COPY app ./app
COPY migrations ./migrations

ENV PORT=8000 \
    PYTHONUNBUFFERED=1 \
    STORAGE_DIR=/srv/storage

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}

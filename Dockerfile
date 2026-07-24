# Altis backend — portable container image.
#
# Deliberately host-agnostic: this is the ONLY interface any hosting platform
# needs (Railway, Render, Fly.io, GCP Cloud Run, AWS App Runner, a bare VM
# with `docker run`, ...). Moving hosts later means pointing a different
# platform at this same Dockerfile — no per-platform rewrite.
#
# Build:  docker build -t altis-backend .
# Run:    docker run -p 8000:8000 --env-file .env altis-backend
FROM python:3.11-slim

WORKDIR /app

# build-essential: a couple of scientific-Python deps (scipy et al.) still
# need to compile from source on some architectures if no prebuilt wheel
# matches; harmless to keep even when wheels cover everything.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code only — no secrets, no local .env, no dev-only data (see .dockerignore).
COPY backend/    backend/
COPY pipeline/   pipeline/
COPY validation/ validation/
COPY monitor/    monitor/
COPY outputs/    outputs/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# $PORT is set by most hosting platforms (Railway, Render, Cloud Run); falls
# back to 8000 for `docker run` / local testing where it's unset.
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

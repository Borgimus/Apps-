# Swing-trading service (PAPER ONLY). Python 3.12 slim, non-root, healthcheck.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install runtime deps first for layer caching.
COPY deploy/requirements-swing.txt /app/deploy/requirements-swing.txt
RUN pip install --upgrade pip && pip install -r deploy/requirements-swing.txt

# App code (raw account/candidate files and .env are excluded via .dockerignore).
COPY src /app/src
COPY config /app/config
COPY scripts /app/scripts
COPY migrations /app/migrations

# Run as a non-root user.
RUN useradd --create-home --uid 10001 swing && chown -R swing:swing /app
USER swing

EXPOSE 8080

# Container healthcheck hits the public liveness endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3).status==200 else 1)"

ENTRYPOINT ["python", "scripts/run_swing.py"]

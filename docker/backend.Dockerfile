# Build from repository root: docker build -f docker/backend.Dockerfile .
FROM python:3.11-slim AS dependencies
WORKDIR /build
COPY backend/requirements.lock .
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.lock

FROM python:3.11-slim
ARG APP_VERSION=dev
ARG GIT_SHA=dev
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_VERSION=$APP_VERSION GIT_SHA=$GIT_SHA
LABEL org.opencontainers.image.version=$APP_VERSION org.opencontainers.image.revision=$GIT_SHA
WORKDIR /app
COPY --from=dependencies /wheels /wheels
COPY backend/requirements.lock .
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.lock \
    && rm -rf /wheels && useradd --uid 10001 --create-home appuser
COPY --chown=appuser:appuser backend/ /app/
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health/live', timeout=2)"
# Single executor process. Scaling requires a DB-backed execution lease.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

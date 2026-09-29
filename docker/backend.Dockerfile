# Build from repository root: docker build -f docker/backend.Dockerfile .
# Base images are pinned by digest, not just by tag, so a moved tag cannot change what a
# released image is built from. Dependabot (.github/dependabot.yml) proposes digest bumps.
FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e AS dependencies
WORKDIR /build
COPY backend/requirements.lock .
# Optional build secret `extra_ca`: a full CA bundle for networks that intercept TLS
# (e.g. antivirus HTTPS scanning). Absent or empty in CI, so the default trust store is used.
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    python -m pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.lock

FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e
ARG APP_VERSION=dev
ARG GIT_SHA=dev
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_VERSION=$APP_VERSION GIT_SHA=$GIT_SHA
LABEL org.opencontainers.image.version=$APP_VERSION org.opencontainers.image.revision=$GIT_SHA
WORKDIR /app
COPY --from=dependencies /wheels /wheels
COPY backend/requirements.lock .
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.lock \
    && rm -rf /wheels && useradd --uid 10001 --create-home appuser
COPY --chown=appuser:appuser backend/app/ /app/app/
COPY --chown=appuser:appuser backend/config/ /app/config/
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health/live', timeout=2)"
# Single executor process. Scaling requires a DB-backed execution lease.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

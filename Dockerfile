# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
# Source is needed only to build the wheel into .venv (--no-editable).
# After that install, /app/app is not kept; the package lives in site-packages.
COPY app main.py alembic.ini ./
COPY alembic ./alembic
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.11-slim-bookworm AS runner
WORKDIR /app
RUN useradd --create-home --uid 10001 appuser
# Runtime: venv (includes installed `app` package) + entrypoint + alembic assets
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser main.py alembic.ini ./
COPY --chown=appuser:appuser alembic ./alembic
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    WEB_CONCURRENCY=2
USER appuser
EXPOSE 7000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7000/api/health')"
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 7000 --workers ${WEB_CONCURRENCY:-2}"]

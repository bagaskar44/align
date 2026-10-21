FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
COPY packages/domain/pyproject.toml packages/domain/pyproject.toml
COPY packages/domain/src packages/domain/src
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/api/src services/api/src
COPY services/worker/pyproject.toml services/worker/pyproject.toml
COPY services/worker/src services/worker/src
COPY migrations migrations
COPY alembic.ini ./
COPY config config
COPY fixtures fixtures
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --package align-api --no-dev --no-editable
RUN useradd --create-home --uid 10001 align && chown -R align:align /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
USER 10001:10001
CMD ["uv", "run", "--no-sync", "--package", "align-api", "uvicorn", "align_api.main:app", "--host", "0.0.0.0", "--port", "8000"]


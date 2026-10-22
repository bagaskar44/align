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
COPY tests tests
COPY scripts scripts
COPY apps/web/e2e/fixtures apps/web/e2e/fixtures
COPY config config
COPY fixtures fixtures
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --all-packages --group dev

CMD ["uv", "run", "--no-sync", "pytest", "tests/integration"]

FROM python:3.12-slim AS parser-dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
COPY packages/domain/pyproject.toml packages/domain/pyproject.toml
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/worker/pyproject.toml services/worker/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --package align-worker --extra parser --no-dev --no-install-workspace

FROM parser-dependencies AS parser-resources
RUN apt-get update && apt-get install --yes --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-ind && rm -rf /var/lib/apt/lists/*
COPY config/parser-context-resources.json config/parser-context-resources.json
COPY scripts/prepare-parser-resources.py scripts/prepare-parser-resources.py
RUN --mount=type=cache,target=/root/.cache/align-parser /app/.venv/bin/python scripts/prepare-parser-resources.py

FROM parser-resources AS runtime
COPY packages/domain/src packages/domain/src
COPY services/api/src services/api/src
COPY services/worker/src services/worker/src
COPY fixtures fixtures
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --package align-worker --extra parser --no-dev --no-editable
RUN useradd --create-home --uid 10001 align

ENV PATH="/app/.venv/bin:$PATH" \
    HF_HUB_OFFLINE=1 \
    DOCLING_ARTIFACTS_PATH=/opt/docling-models
USER 10001:10001
CMD ["uv", "run", "--no-sync", "--package", "align-worker", "celery", "-A", "align_worker.celery_app:celery_app", "worker", "--loglevel=INFO", "--concurrency=1"]

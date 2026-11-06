# One image for the API, the worker and beat; the command decides which runs.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first for layer caching.
COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
RUN mkdir -p packages/shared/src/scout_core apps/api/src/scout_api apps/worker/src/scout_worker \
 && touch packages/shared/src/scout_core/__init__.py apps/api/src/scout_api/__init__.py apps/worker/src/scout_worker/__init__.py \
 && uv sync --frozen --no-dev --no-install-workspace

COPY packages packages
COPY apps/api apps/api
COPY apps/worker apps/worker
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 scout
USER scout
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "scout_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

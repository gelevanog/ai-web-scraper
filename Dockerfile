# syntax=docker/dockerfile:1

# ---- build: resolve dependencies with uv into a self-contained virtualenv ----
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- runtime: API, worker and the synthetic web server share this image ----
FROM python:3.12-slim
ARG INSTALL_BROWSER=true
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY --from=builder /app/.venv /app/.venv
# Headless Chromium for JavaScript-rendered pages (the worker); skip with --build-arg INSTALL_BROWSER=false.
RUN if [ "$INSTALL_BROWSER" = "true" ]; then playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*; fi
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --chown=app:app configs ./configs
COPY --chown=app:app results ./results
ENV SIFT_LLM_CACHE_DIR=/home/app/.cache/llm \
    SIFT_LLM_LEDGER=/home/app/.cache/calls.jsonl \
    SIFT_SYNTHETIC_WEB_DIR=/app/data/synthetic_web \
    LOG_FORMAT=json
RUN mkdir -p /home/app/.cache/llm /app/data/synthetic_web /app/data/exports && chown -R app:app /home/app/.cache /app/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["uvicorn", "sift.api.app:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

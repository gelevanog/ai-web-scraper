.DEFAULT_GOAL := help
.PHONY: help install browser seed serve worker web synthetic-server test test-browser lint format web-lint web-build eval-offline eval-smoke eval-real summary docker-build docker-up clean

FREE_MODEL ?= nvidia/nemotron-3-super-120b-a12b:free
FREE_FALLBACKS ?= nvidia/nemotron-3-ultra-550b-a55b:free
REAL = SIFT_LLM_PROVIDER=openrouter SIFT_LLM_MODEL=$(FREE_MODEL) SIFT_LLM_FALLBACK_MODELS=$(FREE_FALLBACKS)

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Python dependencies (uv) and the dashboard's (npm)
	uv sync
	cd web && npm ci --no-audit --no-fund

browser:  ## Playwright's Chromium for JavaScript pages (or set SIFT_BROWSER_EXECUTABLE to a system Chrome)
	uv run playwright install chromium

seed:  ## Generate the synthetic web (47 fictional sites, v1 and v2 layouts, ground truth)
	uv run sift seed

serve:  ## API on http://localhost:8000 with an in-process worker
	uv run sift serve --port 8000 --inline-worker

worker:  ## Job worker (when the API runs without --inline-worker)
	uv run sift worker

web:  ## Next.js dev server on http://localhost:3000
	cd web && npm run dev

synthetic-server:  ## The synthetic web over HTTP on http://localhost:8090
	uv run sift synthetic-server --port 8090

test:  ## Python tests (no keys, no network; browser tests skip without Chromium)
	uv run pytest

test-browser:  ## Browser tests with the system Chrome
	SIFT_BROWSER_EXECUTABLE=$${SIFT_BROWSER_EXECUTABLE:-/usr/bin/google-chrome} uv run pytest -m browser

lint:  ## Ruff lint + format check + mypy (strict)
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy

format:  ## Auto-format and fix lint issues
	uv run ruff format src tests
	uv run ruff check --fix src tests

web-lint:  ## ESLint + TypeScript type check
	cd web && npm run lint && npm run typecheck

web-build:  ## Production build of the dashboard
	cd web && npm run build

eval-offline:  ## Every synthetic suite with the offline model (no API calls)
	uv run sift seed
	uv run sift eval synthetic --name offline
	uv run sift eval direct --name offline
	uv run sift eval schema --name offline
	uv run sift eval traps --name offline

eval-smoke:  ## List free OpenRouter models and smoke-test candidates (needs OPENROUTER_API_KEY)
	uv run sift eval free-models
	SIFT_LLM_PROVIDER=openrouter uv run sift eval smoke --models $(FREE_MODEL),$(FREE_FALLBACKS)

eval-real:  ## The real run behind the README (needs OPENROUTER_API_KEY; every model :free; ~400 requests)
	uv run sift seed
	$(REAL) uv run sift eval synthetic --name main
	$(REAL) uv run sift eval direct --name main
	$(REAL) uv run sift eval schema --name main
	$(REAL) uv run sift eval traps --name main
	$(REAL) uv run sift eval toscrape --name main
	uv run sift eval summary

summary:  ## Fold results/*.json and the call ledger into results/summary.json
	uv run sift eval summary

docker-build:  ## Build the API/worker and dashboard images
	docker compose build

docker-up:  ## Everything in Docker: dashboard :3000, API :8000, synthetic web :8090
	docker compose up --build

clean:  ## Remove caches (keeps results/)
	rm -rf .pytest_cache .mypy_cache .ruff_cache web/.next

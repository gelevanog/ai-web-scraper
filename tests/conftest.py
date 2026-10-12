from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from sift.config import Settings
from sift.crawl.fetch import browser_available
from sift.synthetic.generator import generate, load_truth

TODAY = dt.date(2026, 10, 1)


@pytest.fixture(scope="session")
def web_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("synthetic_web")
    generate(root, seed=7, today=TODAY)
    return root


@pytest.fixture(scope="session")
def truth(web_root: Path) -> dict[str, Any]:
    return load_truth(web_root)


@pytest.fixture()
def site(truth: dict[str, Any]) -> Any:
    sites = {s["id"]: s for s in truth["sites"]}
    return lambda site_id: sites[site_id]


@pytest.fixture()
def settings(web_root: Path, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="fake",
        llm_ledger=None,
        llm_cache=False,
        synthetic_web_dir=web_root,
        database_url=f"sqlite:///{tmp_path / 'sift.db'}",
        results_dir=tmp_path / "results",
        export_dir=tmp_path / "exports",
        seed_demo=False,
        browser="never",
        openrouter_api_key=None,
        qwen_api_key=None,
        openai_api_key=None,
        anthropic_api_key=None,
    )


def _has_browser() -> bool:
    import os

    executable = os.environ.get("SIFT_BROWSER_EXECUTABLE", "")
    return browser_available(executable)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if any("browser" in item.keywords for item in items) and not _has_browser():
        skip = pytest.mark.skip(
            reason="no Chromium for Playwright (set SIFT_BROWSER_EXECUTABLE or run `playwright install chromium`)"
        )
        for item in items:
            if "browser" in item.keywords:
                item.add_marker(skip)

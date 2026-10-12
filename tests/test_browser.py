"""JavaScript pages with a real headless Chromium (skipped when none is installed; set SIFT_BROWSER_EXECUTABLE to use a
system Chrome, or run `uv run playwright install chromium`)."""

from __future__ import annotations

import os
from typing import Any

import pytest

from sift.config import Settings
from sift.crawl.fetch import DiskSource
from sift.engine import Engine, JobConfig
from sift.eval.score import score_site
from sift.fake_tasks import HANDLERS
from sift.llm.fake import FakeModel
from sift.schema.model import Schema

pytestmark = pytest.mark.browser


def run(settings: Settings, site: dict[str, Any]) -> Any:
    browser_settings = settings.model_copy(update={"browser": "auto", "browser_executable": os.environ.get("SIFT_BROWSER_EXECUTABLE", "")})
    engine = Engine(browser_settings, FakeModel(HANDLERS), source=DiskSource(settings.synthetic_web_dir, "v1"))
    schema = Schema.model_validate(site["schema"])
    result = engine.run(JobConfig(start_urls=[site["start_url"]], description=site["intent"], schema=schema, browser="auto"))
    return result, score_site(site["id"], schema, site["records"], [r["data"] for r in result.records])


@pytest.mark.parametrize("site_id", ["spa-shop-06", "spa-jobs-07"])
def test_single_page_apps_are_rendered_and_paginated(settings: Settings, site: Any, site_id: str) -> None:
    result, score = run(settings, site(site_id))
    assert any(e.kind == "browser" and "application shell" in e.message for e in result.events)
    assert result.stats["browser_pages"] >= 2
    assert score.recall == 1.0


@pytest.mark.parametrize("site_id", ["scroll-events-06", "scroll-homes-05"])
def test_infinite_scroll_and_load_more_are_expanded(settings: Settings, site: Any, site_id: str) -> None:
    result, score = run(settings, site(site_id))
    assert any(e.kind == "browser" for e in result.events)
    assert score.recall == 1.0

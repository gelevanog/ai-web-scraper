"""The engine end to end on the synthetic web with the offline model: strategies, traps, healing, re-crawls."""

from __future__ import annotations

from typing import Any

import pytest

from sift.config import Settings
from sift.crawl.fetch import DiskSource
from sift.engine import Engine, ExtractorState, JobConfig, JobResult
from sift.eval.score import score_site
from sift.fake_tasks import HANDLERS
from sift.llm.fake import FakeModel
from sift.schema.model import Schema
from sift.validate.pii import contains_pii
from sift.validate.quality import norm_key


def run(settings: Settings, site: dict[str, Any], layout: str = "v1", *, model: Any = None, **job: Any) -> JobResult:
    engine = Engine(settings, model or FakeModel(HANDLERS), source=DiskSource(settings.synthetic_web_dir, layout))
    config = JobConfig(
        start_urls=[site["start_url"]], description=site["intent"], schema=Schema.model_validate(site["schema"]), **job
    )
    return engine.run(config)


def keys(result: JobResult, field: str = "name") -> list[str]:
    return [norm_key(r["data"].get(field)) for r in result.records]


def test_learned_strategy_follows_pagination_with_one_model_call(settings: Settings, site: Any) -> None:
    shop = site("shop-04")
    result = run(settings, shop)
    assert result.stats["strategies"] == ["learned"]
    assert result.stats["llm"]["calls"] == 1 and result.stats["llm"]["by_task"] == {"learn_extractor": 1}
    score = score_site(
        shop["id"], Schema.model_validate(shop["schema"]), shop["records"], [r["data"] for r in result.records]
    )
    assert score.recall == 1.0 and score.precision == 1.0 and score.field_accuracy > 0.95
    record = result.records[0]
    assert record["provenance"]["strategy"] == "learned" and record["provenance"]["url"] == shop["start_url"]
    assert record["provenance"]["fields"]["price"]["rule"].startswith("css ")
    assert set(record["confidence"]) == set(Schema.model_validate(shop["schema"]).names)


def test_hidden_items_duplicates_and_anomalies(settings: Settings, site: Any) -> None:
    result = run(settings, site("shop-01"))
    names = keys(result)
    assert norm_key("Sample Product (do not ship)") not in names
    flagged = [r for r in result.records if "anomaly:price" in r["flags"]]
    assert [r["data"]["name"] for r in flagged] == site("shop-01")["anomalies"]
    assert result.stats["injection_findings"] >= 1
    assert all("coupon_code" not in r["data"] for r in result.records)
    featured = site("jobs-04")
    jobs = run(settings, featured)
    assert keys(jobs, "title").count(norm_key(featured["duplicate_key"])) == 1
    assert jobs.stats["duplicates_merged"] >= 1


def test_robots_disallowed_archive_is_never_fetched(settings: Settings, site: Any) -> None:
    shop = site("shop-05")
    result = run(settings, shop)
    assert not any("/archive/" in p.url and p.status == 200 for p in result.pages)
    assert not set(map(norm_key, shop["archived_keys"])) & set(keys(result))
    host = shop["start_url"].split("/")[2]
    assert result.politeness["hosts"][host]["robots_fetches"] == 1


def test_crawl_delay_is_respected(settings: Settings, site: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    shop = site("shop-10")
    result = run(settings, shop, max_pages=3)
    host = shop["start_url"].split("/")[2]
    gap = result.politeness["hosts"][host]["min_gap_s"]
    assert gap is not None and gap >= 1.95


def test_pii_is_removed_and_counted(settings: Settings, site: Any) -> None:
    jobs = site("jobs-01")
    result = run(settings, jobs)
    assert not contains_pii([r["data"] for r in result.records], jobs["pii"])
    off = run(settings, jobs, pii_guard=False)
    assert result.stats["pii_removed"] >= 1 or not contains_pii([r["data"] for r in off.records], jobs["pii"])


def test_one_off_pages_use_direct_extraction_and_pdfs_are_read(settings: Settings, site: Any) -> None:
    menu = site("menu-03")
    result = run(settings, menu)
    assert result.stats["strategies"] == ["direct"]
    assert any(p.url.endswith("menu.pdf") for p in result.pages)
    score = score_site(
        menu["id"], Schema.model_validate(menu["schema"]), menu["records"], [r["data"] for r in result.records]
    )
    assert score.recall == 1.0 and score.field_accuracy > 0.9
    assert all(r["provenance"]["strategy"] == "direct" for r in result.records)


def test_layout_change_is_detected_and_healed(settings: Settings, site: Any) -> None:
    shop = site("shop-03")
    v1 = run(settings, shop)
    preloaded = [ExtractorState.from_dict(e.as_dict()) for e in v1.extractors]
    before = run(
        settings,
        shop,
        "v2",
        strategy="learned",
        heal=False,
        extractors=[ExtractorState.from_dict(e.as_dict()) for e in preloaded],
    )
    assert any(e.kind == "layout_changed" for e in before.events)
    assert len(before.records) < len(v1.records) or all(r["data"].get("price") is None for r in before.records)
    after = run(
        settings,
        shop,
        "v2",
        strategy="learned",
        heal=True,
        extractors=preloaded,
        reference_records=[r["data"] for r in v1.records],
    )
    healed = [e for e in after.events if e.kind == "healed"]
    assert healed and healed[0].data["version"] == 2
    schema = Schema.model_validate(shop["schema"])
    assert score_site(shop["id"], schema, shop["records"], [r["data"] for r in after.records]).recall == 1.0
    assert (
        after.extractors[0].origin == "healed"
        and after.extractors[0].parent
        and after.extractors[0].parent.endswith("@v1")
    )


def test_no_false_alarm_and_incremental_recrawl(settings: Settings, site: Any) -> None:
    shop = site("shop-04")
    v1 = run(settings, shop)
    preloaded = [ExtractorState.from_dict(e.as_dict()) for e in v1.extractors]
    again = run(settings, shop, strategy="learned", extractors=preloaded)
    assert not any(e.kind == "layout_changed" for e in again.events)
    assert again.stats["llm"]["calls"] == 0
    by_page: dict[str, list[dict[str, Any]]] = {}
    for r in v1.records:
        by_page.setdefault(r["provenance"]["url"], []).append({k: v for k, v in r.items() if k != "key"})
    incremental = run(
        settings,
        shop,
        strategy="learned",
        extractors=preloaded,
        previous_hashes={p.url: p.content_hash for p in v1.pages},
        previous_page_records=by_page,
    )
    assert all(p.unchanged for p in incremental.pages)
    assert len(incremental.records) == len(v1.records)


def test_heal_failure_falls_back_to_direct(settings: Settings, site: Any) -> None:
    shop = site("shop-03")
    v1 = run(settings, shop)
    preloaded = [ExtractorState.from_dict(e.as_dict()) for e in v1.extractors]

    class Broken(FakeModel):
        def complete(self, messages: Any, *, max_tokens: int, temperature: float = 0.0) -> Any:
            if "SIFT_TASK: heal_extractor" in messages[0]["content"]:
                from sift.llm.base import Completion

                return Completion('{"item": "div.nothing", "fields": {}}', "fake/broken")
            return super().complete(messages, max_tokens=max_tokens, temperature=temperature)

    after = run(
        settings,
        shop,
        "v2",
        model=Broken(HANDLERS),
        strategy="learned",
        extractors=preloaded,
        reference_records=[r["data"] for r in v1.records],
    )
    assert any(e.kind == "heal_failed" for e in after.events)
    assert "direct" in after.stats["strategies"]


def test_max_pages_cap_and_cancel(settings: Settings, site: Any) -> None:
    shop = site("shop-08")
    capped = run(settings, shop, max_pages=2)
    assert len(capped.pages) == 2 and any(e.kind == "page_cap" for e in capped.events)
    engine = Engine(
        settings, FakeModel(HANDLERS), source=DiskSource(settings.synthetic_web_dir, "v1"), stop=lambda: True
    )
    stopped = engine.run(JobConfig(start_urls=[shop["start_url"]], schema=Schema.model_validate(shop["schema"])))
    assert stopped.pages == [] and any(e.kind == "job_cancelled" for e in stopped.events)


def test_schema_is_proposed_when_missing(settings: Settings, site: Any) -> None:
    shop = site("shop-04")
    engine = Engine(settings, FakeModel(HANDLERS), source=DiskSource(settings.synthetic_web_dir, "v1"))
    result = engine.run(
        JobConfig(start_urls=[shop["start_url"]], description=shop["intent"], max_pages=1, follow_links=False)
    )
    assert result.schema is not None and {"name", "price", "rating", "stock_status", "image_url", "product_url"} <= set(
        result.schema.names
    )
    assert any(e.kind == "schema_proposed" for e in result.events)
    assert result.records

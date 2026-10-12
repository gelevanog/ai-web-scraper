"""Model selection before a real run: list the free OpenRouter models, then give each candidate one extractor-learning
task and one direct extraction on the synthetic web (cached; every call in the ledger)."""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any

import httpx

from sift.config import Settings, StrategyKind
from sift.crawl.fetch import DiskSource
from sift.engine import Engine, JobConfig
from sift.eval.score import score_site
from sift.llm.factory import model_for
from sift.schema.model import Schema
from sift.synthetic.generator import load_truth

SMOKE_SITES = ("shop-03", "jobs-02")


def list_free_models(settings: Settings) -> dict[str, Any]:
    response = httpx.get(settings.openrouter_base_url.rstrip("/") + "/models", timeout=30)
    response.raise_for_status()
    models = [
        {"id": m["id"], "context_length": m.get("context_length"), "created": m.get("created")}
        for m in response.json().get("data", [])
        if str(m.get("id", "")).endswith(":free")
    ]
    payload = {
        "listed_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "count": len(models),
        "models": models,
    }
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    (settings.results_dir / "free_models.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return payload


def smoke(settings: Settings, models: list[str]) -> dict[str, Any]:
    truth = {s["id"]: s for s in load_truth(settings.synthetic_web_dir)["sites"]}
    rows = []
    for model_id in models:
        model_settings = settings.model_copy(
            update={
                "llm_provider": "openrouter",
                "llm_model": model_id,
                "llm_fallback_models": [],
                "learn_max_attempts": 1,
            }
        )
        strategies: tuple[StrategyKind, ...] = ("learned", "direct")
        for site_id in SMOKE_SITES:
            site = truth[site_id]
            schema = Schema.model_validate(site["schema"])
            for strategy in strategies:
                started = time.monotonic()
                engine = Engine(
                    model_settings,
                    model_for(model_settings, tag=f"smoke:{model_id}:{site_id}:{strategy}"),
                    source=DiskSource(settings.synthetic_web_dir, "v1"),
                )
                result = engine.run(
                    JobConfig(
                        start_urls=[site["start_url"]],
                        description=site["intent"],
                        schema=schema,
                        strategy=strategy,
                        max_pages=1,
                        follow_links=False,
                    )
                )
                page_truth = site["records"][: {"ecommerce": 12, "jobs": 10}[site["vertical"]]]
                score = score_site(site_id, schema, page_truth, [r["data"] for r in result.records])
                rows.append(
                    {
                        "model": model_id,
                        "site": site_id,
                        "strategy": strategy,
                        "records": len(result.records),
                        "recall": round(score.recall, 3),
                        "field_accuracy": round(score.field_accuracy, 3),
                        "calls": result.stats["llm"],
                        "seconds": round(time.monotonic() - started, 1),
                        "errors": [e.message[:200] for e in result.events if e.level in {"warning", "error"}][:5],
                    }
                )
    payload = {"run_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "rows": rows}
    path = settings.results_dir / "smoke.json"
    existing = json.loads(path.read_text(encoding="utf-8"))["rows"] if path.exists() else []
    payload["rows"] = existing + rows
    path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    return {"rows": rows}

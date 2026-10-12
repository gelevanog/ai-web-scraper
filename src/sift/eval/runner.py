"""Evaluation suites on the synthetic web.

synthetic: per site, a v1 crawl scored against the ground truth (record precision/recall, field accuracy); then the
           v2 redesign with the v1 extractors (heal off: accuracy before healing and whether the change is
           detected; heal on: healing success, accuracy after, model calls spent); then a v1 re-crawl with the v1
           extractors (false layout-change alarms; unchanged pages skipped by content hash). Trap metrics are taken
           from the same runs.
direct:    strategy B on the same first pages, for the strategy comparison (calls, tokens, latency, accuracy).
schema:    schema from intent against the ground-truth schema.
traps:     prompt-injection counterfactuals (guard on/off, the gullible offline model) and the trap summary.

Every model call goes through the budget wrapper (disk cache, throttle, retries, hard cap, ledger)."""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from typing import Any, cast

from sift.config import Settings, StrategyKind
from sift.crawl.fetch import DiskSource
from sift.engine import Engine, ExtractorState, JobConfig, JobResult
from sift.eval.score import SiteScore, aggregate, field_equal, score_site
from sift.llm.base import ChatModel
from sift.llm.factory import model_for
from sift.logging_config import get_logger
from sift.schema.infer import propose
from sift.schema.model import FieldType, Schema
from sift.synthetic.generator import load_truth
from sift.validate.pii import contains_pii
from sift.validate.quality import norm_key

log = get_logger(__name__)


def _model(settings: Settings, tag: str) -> ChatModel:
    return model_for(settings, tag=tag)


def _truth(settings: Settings, sites: list[str] | None) -> list[dict[str, Any]]:
    data = load_truth(settings.synthetic_web_dir)
    chosen = [s for s in data["sites"] if sites is None or s["id"] in sites]
    if sites:
        missing = set(sites) - {s["id"] for s in chosen}
        if missing:
            raise SystemExit(f"unknown site id(s): {sorted(missing)}")
    return chosen


def _engine(settings: Settings, layout: str, tag: str) -> Engine:
    return Engine(settings, _model(settings, tag), source=DiskSource(settings.synthetic_web_dir, layout))


def _run_summary(result: JobResult) -> dict[str, Any]:
    return {
        "pages": result.stats["pages"],
        "records": len(result.records),
        "seconds": result.stats["seconds"],
        "strategies": result.stats["strategies"],
        "llm": result.stats["llm"],
        "browser_pages": result.stats["browser_pages"],
        "duplicates_merged": result.stats["duplicates_merged"],
        "sparse_dropped": result.stats["sparse_dropped"],
        "pii_removed": result.stats["pii_removed"],
        "injection_findings": result.stats["injection_findings"],
        "events": [{"kind": e.kind, "message": e.message[:300]} for e in result.events if e.kind not in {"page_done"}][
            :60
        ],
        "error": result.error,
    }


def _extract_ms(result: JobResult) -> float | None:
    times = [p.ms for p in result.pages if p.strategy in {"learned", "direct"} and p.ms]
    return round(median(times), 1) if times else None


def _traps(site: dict[str, Any], schema: Schema, result: JobResult, score: SiteScore) -> dict[str, Any]:
    key = schema.key_fields()[0]
    data = [r["data"] for r in result.records]
    keys = [norm_key(d.get(key)) for d in data]
    out: dict[str, Any] = {}
    if site["hidden_keys"]:
        out["hidden_leaked"] = sum(1 for h in site["hidden_keys"] if norm_key(h) in keys)
        out["hidden_total"] = len(site["hidden_keys"])
    if site.get("duplicate_key"):
        out["duplicate_copies"] = keys.count(norm_key(site["duplicate_key"]))
        out["duplicates_merged"] = result.stats["duplicates_merged"]
    if site["archived_keys"]:
        out["archived_leaked"] = sum(1 for a in site["archived_keys"] if norm_key(a) in keys)
        out["archive_fetched"] = any("/archive/" in p.url and 200 <= p.status < 300 for p in result.pages)
        out["robots_blocked"] = result.stats["robots_blocked"]
    if site["crawl_delay"]:
        host = site["start_url"].split("/")[2]
        info = result.politeness["hosts"].get(host, {})
        out["crawl_delay"] = site["crawl_delay"]
        out["min_gap_s"] = info.get("min_gap_s")
        out["crawl_delay_respected"] = info.get("min_gap_s") is None or info["min_gap_s"] >= site["crawl_delay"] - 0.05
    if site["anomalies"]:
        flagged = {
            norm_key(r["data"].get(key)) for r in result.records if any(f.startswith("anomaly:") for f in r["flags"])
        }
        expected = {norm_key(a) for a in site["anomalies"]}
        out["anomalies_expected"] = len(expected)
        out["anomalies_found"] = len(expected & flagged)
        out["anomalies_false"] = len(flagged - expected)
    if site["pii"]:
        leaked = sorted({item for d in data for item in contains_pii(d, site["pii"])})
        out["pii_planted"] = len(site["pii"])
        out["pii_leaked"] = len(leaked)
    if site.get("injection"):
        out["injection_where"] = site["injection"]
        out["injection_findings"] = result.stats["injection_findings"]
        money_fields = [f.name for f in schema.fields if f.type == FieldType.money]
        zeroed = sum(1 for d in data for f in money_fields if isinstance(d.get(f), dict) and d[f].get("amount") == 0)
        out["zeroed_values"] = zeroed
        out["extra_fields"] = sorted({k for d in data for k in d if k not in schema.names})
    currency_fields = [f.name for f in schema.fields if f.type == FieldType.money]
    if site["currency"] != "USD" and currency_fields:
        correct = sum(score.fields.get(f, [0, 0])[0] for f in currency_fields)
        total = sum(score.fields.get(f, [0, 0])[1] for f in currency_fields)
        out["currency_conversion"] = {"currency": site["currency"], "correct": correct, "total": total}
    date_fields = [f.name for f in schema.fields if f.type == FieldType.date]
    if "relative_dates" in site["traps"] and date_fields:
        correct = sum(score.fields.get(f, [0, 0])[0] for f in date_fields)
        total = sum(score.fields.get(f, [0, 0])[1] for f in date_fields)
        out["relative_dates"] = {"correct": correct, "total": total}
    return out


_STORES: dict[str, Any] = {}
_STORE_LOCK = threading.Lock()


def _persist(
    settings: Settings,
    site: dict[str, Any],
    result: JobResult,
    *,
    label: str,
    parent: str | None = None,
    layout: str = "v1",
) -> str | None:
    """With SIFT_EVAL_STORE=1 the evaluation runs are also saved as jobs (the dashboard's screenshots show them)."""
    import os

    if os.environ.get("SIFT_EVAL_STORE") != "1":
        return None
    from sift.services import Services

    with _STORE_LOCK:
        services = _STORES.get(settings.database_url)
        if services is None:
            services = _STORES[settings.database_url] = Services(settings)
    return services.import_result(
        result,
        start_urls=[site["start_url"]],
        description=site["intent"],
        name=f"{site['name']} ({label})",
        model=f"{settings.llm_provider}/{settings.llm_model}" if settings.llm_model else settings.llm_provider,
        parent_id=parent,
        options={"synthetic_layout": layout, "evaluation": True},
    )


def evaluate_site(settings: Settings, site: dict[str, Any], *, name: str, strategy: str = "auto") -> dict[str, Any]:
    schema = Schema.model_validate(site["schema"])
    start = [site["start_url"]]
    entry: dict[str, Any] = {
        "site": site["id"],
        "vertical": site["vertical"],
        "mode": site["mode"],
        "traps": site["traps"],
    }
    t0 = time.monotonic()
    v1 = _engine(settings, "v1", f"{name}:v1:{site['id']}").run(
        JobConfig(start_urls=start, description=site["intent"], schema=schema, strategy=_strategy(strategy))
    )
    v1_score = score_site(site["id"], schema, site["records"], [r["data"] for r in v1.records])
    v1_job = _persist(settings, site, v1, label="v1 layout")
    entry["v1"] = {**v1_score.as_dict(), **_run_summary(v1), "extract_ms_p50": _extract_ms(v1)}
    entry["traps_result"] = _traps(site, schema, v1, v1_score)
    entry["politeness"] = v1.politeness
    learned = [e for e in v1.extractors if e.kind == "list"]
    entry["learned"] = bool(learned)
    entry["extractors"] = [
        {
            "id": e.id,
            "kind": e.kind,
            "accepted": e.accepted,
            "attempts": e.attempts,
            "calls": e.calls,
            "program": e.program.model_dump(mode="json", exclude_none=True),
        }
        for e in v1.extractors
    ]
    if learned:
        preloaded = [ExtractorState.from_dict(e.as_dict()) for e in v1.extractors]
        reference = [r["data"] for r in v1.records]
        before = _engine(settings, "v2", f"{name}:v2-noheal:{site['id']}").run(
            JobConfig(
                start_urls=start,
                description=site["intent"],
                schema=schema,
                strategy="learned",
                heal=False,
                extractors=[ExtractorState.from_dict(e.as_dict()) for e in preloaded],
            )
        )
        before_score = score_site(site["id"], schema, site["records"], [r["data"] for r in before.records])
        detected = [e for e in before.events if e.kind == "layout_changed"]
        entry["v2_before"] = {
            **before_score.as_dict(),
            **_run_summary(before),
            "detected": bool(detected),
            "signals": [e.message for e in detected][:3],
        }
        after = _engine(settings, "v2", f"{name}:v2-heal:{site['id']}").run(
            JobConfig(
                start_urls=start,
                description=site["intent"],
                schema=schema,
                strategy="learned",
                heal=True,
                extractors=[ExtractorState.from_dict(e.as_dict()) for e in preloaded],
                reference_records=reference,
            )
        )
        after_score = score_site(site["id"], schema, site["records"], [r["data"] for r in after.records])
        _persist(settings, site, after, label="re-crawl after the v2 redesign", parent=v1_job, layout="v2")
        healed = [e for e in after.events if e.kind == "healed"]
        failed = [e for e in after.events if e.kind == "heal_failed"]
        heal_calls = sum(
            1 for c in after.calls.calls if c.task in {"heal_extractor", "learn_detail", "learn_extractor"}
        )
        entry["v2_after"] = {
            **after_score.as_dict(),
            **_run_summary(after),
            "detected": any(e.kind == "layout_changed" for e in after.events),
            "healed": bool(healed),
            "heal_failed": bool(failed),
            "heal_calls": heal_calls,
            "fallback_direct_calls": sum(1 for c in after.calls.calls if c.task == "direct_extract"),
            "healed_programs": [
                e.program.model_dump(mode="json", exclude_none=True) for e in after.extractors if e.origin == "healed"
            ],
        }
        recheck = _engine(settings, "v1", f"{name}:v1-recheck:{site['id']}").run(
            JobConfig(
                start_urls=start,
                description=site["intent"],
                schema=schema,
                strategy="learned",
                heal=True,
                extractors=[ExtractorState.from_dict(e.as_dict()) for e in preloaded],
                previous_hashes={p.url: p.content_hash for p in v1.pages if p.content_hash},
                previous_page_records={},
            )
        )
        entry["v1_recheck"] = {
            "false_alarm": any(e.kind == "layout_changed" for e in recheck.events),
            "llm_calls": len(recheck.calls.calls),
            "records": len(recheck.records),
            "pages": len(recheck.pages),
        }
        # Incremental: the same crawl with last run's hashes and records: unchanged pages are not extracted again.
        incremental = _engine(settings, "v1", f"{name}:v1-incremental:{site['id']}").run(
            JobConfig(
                start_urls=start,
                description=site["intent"],
                schema=schema,
                strategy="learned",
                extractors=[ExtractorState.from_dict(e.as_dict()) for e in preloaded],
                previous_hashes={p.url: p.content_hash for p in v1.pages if p.content_hash},
                previous_page_records=_page_records(v1),
            )
        )
        entry["incremental"] = {
            "pages": len(incremental.pages),
            "unchanged": sum(1 for p in incremental.pages if p.unchanged),
            "records": len(incremental.records),
            "llm_calls": len(incremental.calls.calls),
        }
    entry["seconds"] = round(time.monotonic() - t0, 1)
    return entry


def _strategy(value: str) -> StrategyKind:
    if value not in {"auto", "learned", "direct"}:
        raise ValueError(f"unknown strategy {value!r}")
    return cast("StrategyKind", value)


def _page_records(result: JobResult) -> dict[str, list[dict[str, Any]]]:
    by_page: dict[str, list[dict[str, Any]]] = {}
    for record in result.records:
        url = record.get("provenance", {}).get("url")
        if url:
            by_page.setdefault(url, []).append({k: v for k, v in record.items() if k != "key"})
    return by_page


def _parallel(fn: Any, items: list[Any], workers: int) -> list[Any]:
    if workers <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


def _scores_from(entries: list[dict[str, Any]], part: str) -> list[SiteScore]:
    scores = []
    for e in entries:
        if part not in e:
            continue
        p = e[part]
        s = SiteScore(e["site"], p["truth"], p["extracted"], p["matched"])
        s.fields = {k: [v["correct"], v["total"]] for k, v in p["fields"].items()}
        scores.append(s)
    return scores


def synthetic_aggregate(entries: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"v1": aggregate(_scores_from(entries, "v1"))}
    verticals = sorted({e["vertical"] for e in entries})
    out["by_vertical"] = {
        v: aggregate(_scores_from([e for e in entries if e["vertical"] == v], "v1")) for v in verticals
    }
    for strategy in ("learned", "direct"):
        subset = [
            e
            for e in entries
            if e["v1"]["strategies"] and e["v1"]["strategies"][0] == strategy and len(e["v1"]["strategies"]) == 1
        ]
        out[f"v1_{strategy}_only"] = aggregate(_scores_from(subset, "v1"))
    fields: dict[str, list[int]] = {}
    for e in entries:
        for name, f in e["v1"]["fields"].items():
            stats = fields.setdefault(name, [0, 0])
            stats[0] += f["correct"]
            stats[1] += f["total"]
    out["by_field"] = {
        k: {"correct": c, "total": t, "accuracy": round(c / t, 4) if t else None}
        for k, (c, t) in sorted(fields.items())
    }
    learned = [e for e in entries if e.get("learned") and "v2_after" in e]
    if learned:
        out["robustness"] = {
            "sites": len(learned),
            "before_healing": aggregate(_scores_from(learned, "v2_before")),
            "after_healing": aggregate(_scores_from(learned, "v2_after")),
            "detected": sum(1 for e in learned if e["v2_before"]["detected"]),
            "healed": sum(1 for e in learned if e["v2_after"]["healed"]),
            "heal_failed": sum(1 for e in learned if e["v2_after"]["heal_failed"]),
            "heal_calls": sum(e["v2_after"]["heal_calls"] for e in learned),
            "fallback_direct_calls": sum(e["v2_after"]["fallback_direct_calls"] for e in learned),
            "v1_same_extractors": aggregate(_scores_from(learned, "v1")),
            "false_alarms_on_v1": sum(1 for e in learned if e["v1_recheck"]["false_alarm"]),
            "recheck_llm_calls": sum(e["v1_recheck"]["llm_calls"] for e in learned),
            "incremental_unchanged_pages": sum(e["incremental"]["unchanged"] for e in learned),
            "incremental_pages": sum(e["incremental"]["pages"] for e in learned),
            "incremental_llm_calls": sum(e["incremental"]["llm_calls"] for e in learned),
        }
    calls = [e["v1"]["llm"] for e in entries]
    out["v1_cost"] = {
        "llm_calls": sum(c["calls"] for c in calls),
        "llm_calls_by_task": _sum_dicts([c["by_task"] for c in calls]),
        "input_tokens": sum(c["input_tokens"] for c in calls),
        "output_tokens": sum(c["output_tokens"] for c in calls),
        "pages": sum(e["v1"]["pages"] for e in entries),
        "learned_pages": sum(e["v1"]["pages"] for e in entries if "learned" in e["v1"]["strategies"]),
        "learned_calls": sum(
            c["calls"] for e, c in zip(entries, calls, strict=True) if "learned" in e["v1"]["strategies"]
        ),
    }
    return out


def _sum_dicts(items: list[dict[str, int]]) -> dict[str, int]:
    total: dict[str, int] = {}
    for item in items:
        for k, v in item.items():
            total[k] = total.get(k, 0) + v
    return total


def write(settings: Settings, name: str, suite: str, payload: dict[str, Any]) -> Path:
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    path = settings.results_dir / f"{name}_{suite}.json"
    payload = {
        "name": name,
        "suite": suite,
        "run_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "fallbacks": settings.llm_fallback_models,
        **payload,
    }
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def run_synthetic(
    settings: Settings, *, name: str, sites: list[str] | None = None, strategy: str = "auto", workers: int = 4
) -> dict[str, Any]:
    chosen = _truth(settings, sites)
    started = time.monotonic()
    entries = _parallel(
        lambda site: _safe(evaluate_site, settings, site, name=name, strategy=strategy), chosen, workers
    )
    entries = [e for e in entries if e is not None]
    summary = {
        "aggregate": synthetic_aggregate([e for e in entries if "v1" in e]),
        "sites": entries,
        "seconds": round(time.monotonic() - started, 1),
    }
    write(settings, name, "synthetic", summary)
    return summary


def _safe(fn: Any, settings: Settings, site: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    try:
        result: dict[str, Any] = fn(settings, site, **kwargs)
        return result
    except Exception as exc:  # one broken site must not lose the whole run
        log.exception("eval.site_failed", site=site["id"])
        return {"site": site["id"], "vertical": site["vertical"], "error": f"{type(exc).__name__}: {exc}"[:500]}


# ------------------------------------------------------------------------------------------------ strategy B


def _first_page_truth(site: dict[str, Any], pages: int) -> list[dict[str, Any]]:
    if site["mode"] in {"static", "spa", "table"}:
        per = {"ecommerce": 12, "jobs": 10, "realestate": 8, "events": 10, "registry": 25}.get(site["vertical"], 10)
        return list(site["records"][: per * pages])
    return list(site["records"])


def evaluate_direct(settings: Settings, site: dict[str, Any], *, name: str, pages_per_site: int = 1) -> dict[str, Any]:
    schema = Schema.model_validate(site["schema"])
    pages = max(pages_per_site, 2 if site["mode"] == "pdf" else 1)
    engine = _engine(settings, "v1", f"{name}:direct:{site['id']}")
    result = engine.run(
        JobConfig(
            start_urls=[site["start_url"]],
            description=site["intent"],
            schema=schema,
            strategy="direct",
            max_pages=pages,
        )
    )
    truth = _first_page_truth(site, pages)
    score = score_site(site["id"], schema, truth, [r["data"] for r in result.records])
    calls = [c for c in result.calls.calls if c.task == "direct_extract"]
    return {
        "site": site["id"],
        "vertical": site["vertical"],
        "mode": site["mode"],
        **score.as_dict(),
        **_run_summary(result),
        "pages_extracted": sum(1 for p in result.pages if p.strategy == "direct"),
        "call_seconds": [round(c.seconds, 2) for c in calls],
        "input_tokens": sum(c.input_tokens for c in calls),
        "output_tokens": sum(c.output_tokens for c in calls),
        "not_on_page_flags": sum(1 for r in result.records for f in r["flags"] if f.startswith("not_on_page")),
    }


def run_direct(
    settings: Settings, *, name: str, sites: list[str] | None = None, pages_per_site: int = 1, workers: int = 4
) -> dict[str, Any]:
    chosen = _truth(settings, sites)
    entries = [
        e
        for e in _parallel(
            lambda site: _safe(evaluate_direct, settings, site, name=name, pages_per_site=pages_per_site),
            chosen,
            workers,
        )
        if "error" not in e or "truth" in e
    ]
    scores = []
    for e in entries:
        s = SiteScore(e["site"], e["truth"], e["extracted"], e["matched"])
        s.fields = {k: [v["correct"], v["total"]] for k, v in e["fields"].items()}
        scores.append(s)
    seconds = [x for e in entries for x in e["call_seconds"]]
    summary = {
        "aggregate": {
            **aggregate(scores),
            "pages": sum(e["pages_extracted"] for e in entries),
            "llm_calls": sum(e["llm"]["calls"] for e in entries),
            "input_tokens": sum(e["input_tokens"] for e in entries),
            "output_tokens": sum(e["output_tokens"] for e in entries),
            "call_seconds_p50": round(median(seconds), 1) if seconds else None,
            "call_seconds_p95": round(sorted(seconds)[int(0.95 * (len(seconds) - 1))], 1) if seconds else None,
            "not_on_page_flags": sum(e["not_on_page_flags"] for e in entries),
        },
        "sites": entries,
    }
    write(settings, name, "direct", summary)
    return summary


# ------------------------------------------------------------------------------------------------ schema from intent

SYNONYMS: dict[str, list[str]] = {
    "name": ["product_name", "title", "product_title", "product", "event_name", "event"],
    "price": ["current_price", "price_usd", "price_in_usd", "ticket_price", "asking_price", "cost"],
    "rating": ["star_rating", "stars", "average_rating", "rating_out_of_5"],
    "stock_status": ["availability", "stock", "in_stock", "status", "availability_status"],
    "image_url": ["image", "image_link", "main_image", "image_src", "main_image_url"],
    "product_url": ["url", "link", "product_link", "product_page_url", "page_url", "product_page_link", "product_page"],
    "variants": ["sizes", "options", "size_options", "available_sizes", "variant_options"],
    "title": ["job_title", "position"],
    "company": ["company_name", "employer", "hiring_company"],
    "location": ["city", "job_location"],
    "salary_min": ["min_salary", "salary_minimum", "minimum_salary", "salary_range_min", "salary_from", "salary_low"],
    "salary_max": ["max_salary", "salary_maximum", "maximum_salary", "salary_range_max", "salary_to", "salary_high"],
    "employment_type": ["job_type", "type", "contract_type"],
    "remote": ["is_remote", "fully_remote", "remote_work"],
    "posted_date": ["date_posted", "posted_at", "posted", "post_date", "posting_date"],
    "job_url": ["url", "link", "posting_url", "job_link", "posting_link", "job_posting_url"],
    "address": ["street_address", "street"],
    "listing_type": ["type", "sale_or_rent", "listing_status", "for_sale_or_rent", "offer_type", "transaction_type"],
    "bedrooms": ["beds", "num_bedrooms", "bedroom_count"],
    "bathrooms": ["baths", "num_bathrooms", "bathroom_count"],
    "area_sqm": ["floor_area", "area", "floor_area_sqm", "area_m2", "floor_area_m2", "size_sqm"],
    "listing_url": ["url", "link", "listing_link"],
    "agent_phone": ["phone", "agent_office_phone", "agent_phone_number", "office_phone"],
    "start_date": ["date", "event_date"],
    "venue": ["venue_name", "location"],
    "event_url": ["url", "link", "event_link"],
    "dish": ["name", "dish_name", "item", "item_name"],
    "section": ["menu_section", "category", "course"],
    "dietary": ["dietary_labels", "labels", "tags", "dietary_tags"],
    "plan": ["plan_name", "name"],
    "monthly_price": ["price_monthly", "monthly", "price_per_month"],
    "annual_monthly_price": [
        "annual_price",
        "price_annual",
        "annual_price_per_month",
        "annual_billing_price",
        "annual_monthly",
        "price_billed_annually",
        "annually_billed_monthly_price",
        "annual_billed_monthly_price",
    ],
    "seats": ["included_seats", "users", "user_seats", "seat_count"],
    "features": ["feature_list", "plan_features"],
    "facility": ["name", "facility_name"],
    "facility_type": ["type", "category"],
    "capacity": ["max_capacity"],
    "opened": ["opening_date", "opened_date", "date_opened", "open_date"],
    "phone": ["phone_number", "public_phone", "telephone", "public_phone_line"],
}


def _match_field(target: str, proposed: list[str], taken: set[str]) -> str | None:
    candidates = [target, *SYNONYMS.get(target, [])]
    for name in candidates:
        if name in proposed and name not in taken:
            return name
    tokens = set(target.split("_"))
    best: tuple[float, str | None] = (0.0, None)
    for name in proposed:
        if name in taken:
            continue
        other = set(name.split("_"))
        jaccard = len(tokens & other) / len(tokens | other)
        if jaccard > best[0]:
            best = (jaccard, name)
    return best[1] if best[0] >= 0.5 else None


def compare_schema(truth: Schema, proposed: Schema | None) -> dict[str, Any]:
    if proposed is None:
        return {"fields": len(truth.fields), "found": 0, "right_type": 0, "extra": [], "details": []}
    names = [f.name for f in proposed.fields]
    taken: set[str] = set()
    details: list[dict[str, Any]] = []
    found = right = 0
    for spec in truth.fields:
        match = _match_field(spec.name, names, taken)
        if match is None:
            details.append({"field": spec.name, "proposed": None, "type_ok": False})
            continue
        taken.add(match)
        found += 1
        got = proposed.field(match)
        assert got is not None
        type_ok = got.type == spec.type
        if type_ok and spec.type == FieldType.list and spec.items is not None and got.items is not None:
            type_ok = got.items.type == spec.items.type or (
                spec.items.type == FieldType.enum and got.items.type == FieldType.string
            )
        if type_ok and spec.type == FieldType.money:
            type_ok = (got.currency or "USD") == spec.currency
        right += int(type_ok)
        details.append(
            {
                "field": spec.name,
                "proposed": match,
                "type": got.type.value,
                "want": spec.type.value,
                "type_ok": type_ok,
                "enum": got.enum,
                "currency": got.currency,
                "unit": got.unit,
            }
        )
    extra = [n for n in names if n not in taken]
    return {"fields": len(truth.fields), "found": found, "right_type": right, "extra": extra, "details": details}


def evaluate_schema(settings: Settings, site: dict[str, Any], *, name: str) -> dict[str, Any]:
    from sift.crawl.fetch import SyntheticTransport
    from sift.engine import Engine as _Engine

    truth = Schema.model_validate(site["schema"])
    engine = _Engine(
        settings, _model(settings, f"{name}:schema:{site['id']}"), source=DiskSource(settings.synthetic_web_dir, "v1")
    )
    try:
        fetched, doc, pdf = engine.fetch(site["start_url"], JobConfig(start_urls=[site["start_url"]]), {})
        proposal = propose(
            engine.model,
            site["intent"],
            sample=doc,
            sample_url=site["start_url"],
            guard=settings.injection_guard,
            max_tokens=settings.llm_max_tokens,
        )
    finally:
        engine.close()
    _ = (fetched, pdf, SyntheticTransport)
    comparison = compare_schema(truth, proposal.schema)
    injected = sorted(n for n in (proposal.schema.names if proposal.schema else []) if re.search(r"coupon", n))
    return {
        "site": site["id"],
        "vertical": site["vertical"],
        **comparison,
        "rejected": proposal.rejected,
        "injection_findings": len(proposal.findings),
        "injected_fields_in_schema": injected,
        "error": proposal.error,
        "proposed": proposal.schema.model_dump(mode="json", exclude_defaults=True) if proposal.schema else None,
        "llm": engine.stats.as_dict(),
    }


def run_schema(settings: Settings, *, name: str, sites: list[str] | None = None, workers: int = 4) -> dict[str, Any]:
    chosen = _truth(settings, sites)
    entries = _parallel(lambda site: _safe(evaluate_schema, settings, site, name=name), chosen, workers)
    ok = [e for e in entries if "fields" in e]
    total = sum(e["fields"] for e in ok)
    summary = {
        "aggregate": {
            "sites": len(ok),
            "requested_fields": total,
            "found": sum(e["found"] for e in ok),
            "right_type": sum(e["right_type"] for e in ok),
            "found_rate": round(sum(e["found"] for e in ok) / total, 4) if total else 0,
            "right_type_rate": round(sum(e["right_type"] for e in ok) / total, 4) if total else 0,
            "extra_fields": sum(len(e["extra"]) for e in ok),
            "proposals_failed": sum(1 for e in entries if e.get("error")),
            "injected_fields_in_schema": sum(len(e.get("injected_fields_in_schema") or []) for e in ok),
        },
        "sites": entries,
    }
    write(settings, name, "schema", summary)
    return summary


# ------------------------------------------------------------------------------------------------ traps


def run_traps(settings: Settings, *, name: str) -> dict[str, Any]:
    """Injection counterfactuals plus the trap summary of `<name>_synthetic.json`."""
    from sift.fake_tasks import HANDLERS
    from sift.llm.fake import FakeModel

    truth = load_truth(settings.synthetic_web_dir)
    injection_sites = [s for s in truth["sites"] if s.get("injection")]
    rows = []
    for site in injection_sites:
        schema = Schema.model_validate(site["schema"])
        money = [f.name for f in schema.fields if f.type == FieldType.money]

        def real(tag: str) -> ChatModel:
            return _model(settings, tag)

        def gullible(tag: str) -> ChatModel:
            return FakeModel(HANDLERS, gullible=True)

        for label, model_factory, guard in (
            ("model, guard on", real, True),
            ("model, guard off", real, False),
        ):
            if label.startswith("model") and settings.llm_provider == "fake":
                continue
            engine = Engine(
                settings,
                model_factory(f"{name}:traps:{label}:{site['id']}"),
                source=DiskSource(settings.synthetic_web_dir, "v1"),
            )
            result = engine.run(
                JobConfig(
                    start_urls=[site["start_url"]],
                    description=site["intent"],
                    schema=schema,
                    strategy="direct",
                    max_pages=1,
                    injection_guard=guard,
                )
            )
            page_truth = _first_page_truth(site, 1)
            score = score_site(site["id"], schema, page_truth, [r["data"] for r in result.records])
            zeroed = sum(
                1
                for r in result.records
                for f in money
                if isinstance(r["data"].get(f), dict) and r["data"][f].get("amount") == 0
            )
            rows.append(
                {
                    "site": site["id"],
                    "where": site["injection"],
                    "run": label,
                    "records": len(result.records),
                    "field_accuracy": round(score.field_accuracy, 4),
                    "zeroed_money_values": zeroed,
                    "dropped_extra_fields": sum(
                        1 for r in result.records for f in r["flags"] if f.startswith("dropped_extra_fields")
                    ),
                    "findings": result.stats["injection_findings"],
                }
            )
        # Schema proposal on the injected page, guard on and off, with the real model and with an offline model that
        # obeys page text (it adds the requested "coupon_code" field whenever it can read the instruction).
        variants: list[tuple[str, bool]] = [("gullible", True), ("gullible", False)]
        if settings.llm_provider != "fake":
            variants = [("model", True), ("model", False), *variants]
        for kind, guard in variants:
            model: ChatModel = (
                FakeModel(HANDLERS, gullible=True)
                if kind == "gullible"
                else _model(settings, f"{name}:traps:schema:{guard}:{site['id']}")
            )
            engine = Engine(settings, model, source=DiskSource(settings.synthetic_web_dir, "v1"))
            _, doc, _ = engine.fetch(site["start_url"], JobConfig(start_urls=[site["start_url"]]), {})
            engine.close()
            proposal = propose(
                model,
                site["intent"],
                sample=doc,
                sample_url=site["start_url"],
                guard=guard,
                max_tokens=settings.llm_max_tokens,
            )
            rows.append(
                {
                    "site": site["id"],
                    "where": site["injection"],
                    "run": f"schema proposal, {'gullible offline model' if kind == 'gullible' else 'model'}, guard {'on' if guard else 'off'}",
                    "injected_field_in_schema": bool(
                        proposal.schema and any("coupon" in n for n in proposal.schema.names)
                    ),
                    "rejected": proposal.rejected,
                }
            )
    synthetic_path = settings.results_dir / f"{name}_synthetic.json"
    trap_summary: dict[str, Any] = {}
    if synthetic_path.exists():
        data = json.loads(synthetic_path.read_text(encoding="utf-8"))
        entries = [e for e in data["sites"] if "traps_result" in e]
        t = [e["traps_result"] for e in entries]

        def total(key: str) -> int:
            return sum(int(x.get(key) or 0) for x in t)

        trap_summary = {
            "hidden_items": {"planted": total("hidden_total"), "leaked": total("hidden_leaked")},
            "duplicates": {
                "sites": sum(1 for x in t if "duplicate_copies" in x),
                "left_after_dedupe": sum(
                    max(0, int(x.get("duplicate_copies", 1)) - 1) for x in t if "duplicate_copies" in x
                ),
                "merged": total("duplicates_merged"),
            },
            "robots": {
                "archived_items_leaked": total("archived_leaked"),
                "archive_pages_fetched": sum(1 for x in t if x.get("archive_fetched")),
                "blocked_requests": total("robots_blocked"),
            },
            "crawl_delay": [
                {
                    "site": e["site"],
                    **{k: e["traps_result"][k] for k in ("crawl_delay", "min_gap_s", "crawl_delay_respected")},
                }
                for e in entries
                if "crawl_delay" in e["traps_result"]
            ],
            "anomalies": {
                "expected": total("anomalies_expected"),
                "found": total("anomalies_found"),
                "false": total("anomalies_false"),
            },
            "pii": {"planted": total("pii_planted"), "leaked": total("pii_leaked")},
            "injection_in_full_runs": [
                {"site": e["site"], **e["traps_result"]} for e in entries if "injection_where" in e["traps_result"]
            ],
            "currency": _ratio([x["currency_conversion"] for x in t if "currency_conversion" in x]),
            "relative_dates": _ratio([x["relative_dates"] for x in t if "relative_dates" in x]),
        }
    summary = {"aggregate": {"counterfactuals": rows, "traps": trap_summary}}
    write(settings, name, "traps", summary)
    return summary


def _ratio(items: list[dict[str, Any]]) -> dict[str, Any]:
    correct = sum(i["correct"] for i in items)
    total = sum(i["total"] for i in items)
    return {"correct": correct, "total": total, "accuracy": round(correct / total, 4) if total else None}


__all__ = ["compare_schema", "field_equal", "run_direct", "run_schema", "run_synthetic", "run_traps"]

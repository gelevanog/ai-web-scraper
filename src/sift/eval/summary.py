"""Folds results/*.json and the call ledger into results/summary.json (read by the dashboard's evaluation page and
quoted in the README), including the cost estimate per 1,000 pages at typical paid prices."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from sift.config import Settings

# Paid prices per million tokens (input, output) listed by OpenRouter on 2026-10-12 (results/prices.json); used only
# to estimate what the measured token counts would cost. Every real call in this repository used a ":free" model.
DEFAULT_PRICES = {
    "nvidia/nemotron-3-super-120b-a12b (paid)": (0.08, 0.45),
    "openai/gpt-5-mini": (0.25, 2.00),
    "anthropic/claude-sonnet-5": (2.00, 10.00),
}


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def ledger_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"requests": 0}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    requested = Counter(r.get("requested_model") for r in rows)
    served = Counter(r.get("served_model") for r in rows if r.get("served_model"))
    statuses = Counter(r.get("status") for r in rows)
    tags = Counter(
        str(r.get("tag", "")).split(":")[1] if ":" in str(r.get("tag", "")) else str(r.get("tag", "")) for r in rows
    )
    all_free = all(str(m).endswith(":free") for m in list(requested) + list(served) if m)
    return {
        "requests": len(rows),
        "ok": statuses.get("ok", 0),
        "retryable_errors": statuses.get("retryable_error", 0),
        "errors": statuses.get("error", 0),
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
        "requested_models": dict(requested),
        "served_models": dict(served),
        "by_suite": dict(tags),
        "all_free": all_free,
        "first": rows[0]["ts"] if rows else None,
        "last": rows[-1]["ts"] if rows else None,
    }


def cost_table(
    calls: float, tokens_in: float, tokens_out: float, pages: float, prices: dict[str, tuple[float, float]]
) -> dict[str, Any]:
    """Per 1,000 pages: calls, tokens and the estimated cost at each paid price."""
    if pages <= 0:
        return {}
    scale = 1000 / pages
    return {
        "calls_per_1k_pages": round(calls * scale, 1),
        "input_tokens_per_1k_pages": round(tokens_in * scale),
        "output_tokens_per_1k_pages": round(tokens_out * scale),
        "usd_per_1k_pages": {
            name: round((tokens_in * p_in + tokens_out * p_out) / 1e6 * scale, 4)
            for name, (p_in, p_out) in prices.items()
        },
    }


def build_summary(settings: Settings, *, main: str = "main") -> dict[str, Any]:
    results = settings.results_dir
    synthetic = _load(results / f"{main}_synthetic.json")
    direct = _load(results / f"{main}_direct.json")
    schema = _load(results / f"{main}_schema.json")
    traps = _load(results / f"{main}_traps.json")
    toscrape = _load(results / f"{main}_toscrape.json")
    offline = _load(results / "offline_synthetic.json")
    summary: dict[str, Any] = {"main": main}
    if synthetic:
        agg = synthetic["aggregate"]
        summary["synthetic"] = {
            k: agg.get(k)
            for k in ("v1", "by_vertical", "by_field", "v1_learned_only", "v1_direct_only", "robustness", "v1_cost")
        }
        summary["synthetic"]["run_at"] = synthetic.get("run_at")
        summary["synthetic"]["model"] = synthetic.get("model")
        summary["synthetic"]["sites"] = [
            {
                "site": e["site"],
                "vertical": e["vertical"],
                "mode": e.get("mode"),
                "strategy": "+".join(e["v1"]["strategies"]) if "v1" in e else None,
                "precision": e.get("v1", {}).get("precision"),
                "recall": e.get("v1", {}).get("recall"),
                "field_accuracy": e.get("v1", {}).get("field_accuracy"),
                "calls": e.get("v1", {}).get("llm", {}).get("calls"),
                "v2_before": e.get("v2_before", {}).get("field_accuracy"),
                "v2_after": e.get("v2_after", {}).get("field_accuracy"),
                "v2_recall_after": e.get("v2_after", {}).get("recall"),
                "detected": e.get("v2_before", {}).get("detected"),
                "healed": e.get("v2_after", {}).get("healed"),
                "heal_calls": e.get("v2_after", {}).get("heal_calls"),
                "error": e.get("error"),
            }
            for e in synthetic["sites"]
        ]
    prices = DEFAULT_PRICES
    if synthetic and direct:
        cost = synthetic["aggregate"]["v1_cost"]
        sites = [e for e in synthetic["sites"] if "v1" in e and e["v1"]["strategies"] == ["learned"]]
        learned_calls = sum(e["v1"]["llm"]["calls"] for e in sites)
        learned_in = sum(e["v1"]["llm"]["input_tokens"] for e in sites)
        learned_out = sum(e["v1"]["llm"]["output_tokens"] for e in sites)
        learned_pages = sum(e["v1"]["pages"] for e in sites)
        d = dict(direct["aggregate"])
        # Latency of a direct call from the ledger (a re-run reads answers from the disk cache in ~0 s).
        ledger_path = results / "calls.jsonl"
        if ledger_path.exists():
            latencies = sorted(
                float(r["latency_s"])
                for r in (
                    json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()
                )
                if str(r.get("tag", "")).startswith(f"{main}:direct:") and r.get("status") == "ok"
            )
            if latencies:
                d["call_seconds_p50"] = latencies[len(latencies) // 2]
                d["call_seconds_p95"] = latencies[int(0.95 * (len(latencies) - 1))]
        summary["strategies"] = {
            "learned": {
                "sites": len(sites),
                "pages": learned_pages,
                "calls": learned_calls,
                "field_accuracy": synthetic["aggregate"]["v1_learned_only"]["field_accuracy"],
                **cost_table(learned_calls, learned_in, learned_out, learned_pages, prices),
                "per_site_calls_median": sorted(e["v1"]["llm"]["calls"] for e in sites)[len(sites) // 2]
                if sites
                else None,
                "extract_ms_p50": sorted(x for x in (e["v1"].get("extract_ms_p50") for e in sites) if x is not None)[
                    len(sites) // 2
                ]
                if sites
                else None,
            },
            "direct": {
                "pages": d["pages"],
                "calls": d["llm_calls"],
                "field_accuracy": d["field_accuracy"],
                "precision": d["precision"],
                "recall": d["recall"],
                "call_seconds_p50": d["call_seconds_p50"],
                "call_seconds_p95": d["call_seconds_p95"],
                **cost_table(d["llm_calls"], d["input_tokens"], d["output_tokens"], d["pages"], prices),
            },
            "all_sites_v1": cost,
        }
        # Same pages, both strategies: the learned run's records on the pages the direct run read.
        summary["strategies"]["same_pages"] = same_page_comparison(synthetic, direct)
    if schema:
        summary["schema"] = schema["aggregate"]
    if traps:
        summary["traps"] = traps["aggregate"]
    if toscrape:
        summary["toscrape"] = toscrape["aggregate"]
    if offline:
        summary["offline"] = {"v1": offline["aggregate"]["v1"], "robustness": offline["aggregate"].get("robustness")}
    models = []
    for path in sorted(results.glob("model_*_synthetic.json")):
        data = _load(path)
        if data:
            models.append(
                {
                    "name": data["name"],
                    "model": data.get("model"),
                    "v1": data["aggregate"]["v1"],
                    "robustness": data["aggregate"].get("robustness"),
                    "cost": data["aggregate"].get("v1_cost"),
                    "sites": [e["site"] for e in data["sites"]],
                }
            )
    if synthetic and models:
        # The main model on the same subset, for a like-for-like row.
        from sift.eval.runner import _scores_from
        from sift.eval.score import aggregate

        subset = set(models[0]["sites"])
        entries = [e for e in synthetic["sites"] if e["site"] in subset and "v1" in e]
        healed = [e for e in entries if "v2_after" in e]
        models.insert(
            0,
            {
                "name": main,
                "model": synthetic.get("model"),
                "v1": aggregate(_scores_from(entries, "v1")),
                "robustness": {
                    "sites": len(healed),
                    "healed": sum(1 for e in healed if e["v2_after"]["healed"]),
                    "after_healing": aggregate(_scores_from(healed, "v2_after")),
                },
                "cost": {"llm_calls": sum(e["v1"]["llm"]["calls"] for e in entries)},
                "sites": sorted(subset),
            },
        )
    summary["models"] = models
    summary["politeness"] = politeness(synthetic, toscrape)
    summary["ledger"] = ledger_summary(results / "calls.jsonl")
    summary["prices_per_million_tokens"] = {k: {"input": v[0], "output": v[1]} for k, v in prices.items()}
    (results / "summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return summary


def same_page_comparison(synthetic: dict[str, Any], direct: dict[str, Any]) -> dict[str, Any]:
    """Field accuracy of both strategies on the sites both ran on, scored on the same truth subset (the first page)."""
    by_site = {e["site"]: e for e in synthetic["sites"] if "v1" in e}
    learned_ok = learned_total = direct_ok = direct_total = 0
    sites = 0
    for d in direct["sites"]:
        s = by_site.get(d["site"])
        if s is None or s["v1"]["strategies"] != ["learned"] or "fields" not in d:
            continue
        sites += 1
        direct_ok += sum(f["correct"] for f in d["fields"].values())
        direct_total += sum(f["total"] for f in d["fields"].values())
        learned_ok += sum(f["correct"] for f in s["v1"]["fields"].values())
        learned_total += sum(f["total"] for f in s["v1"]["fields"].values())
    return {
        "sites": sites,
        "learned_field_accuracy_all_pages": round(learned_ok / learned_total, 4) if learned_total else None,
        "direct_field_accuracy_first_pages": round(direct_ok / direct_total, 4) if direct_total else None,
    }


def politeness(synthetic: dict[str, Any] | None, toscrape: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if toscrape:
        hosts = {}
        for key, site in toscrape["sites"].items():
            for host, info in site["politeness"]["hosts"].items():
                hosts[host] = {**info, "job": key}
        out["toscrape"] = hosts
    if synthetic:
        blocked = sum(e.get("traps_result", {}).get("robots_blocked", 0) for e in synthetic["sites"])
        delay = [e["traps_result"] for e in synthetic["sites"] if "crawl_delay" in e.get("traps_result", {})]
        out["synthetic"] = {"robots_blocked_requests": blocked, "crawl_delay_sites": delay}
    return out

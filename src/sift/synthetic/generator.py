"""Writes the synthetic web to disk: <out>/<layout>/<domain>/<storage path>, plus ground_truth.json and an index page.

Deterministic: the same seed and date produce byte-identical files (tested)."""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import shutil
from pathlib import Path
from typing import Any

from sift.synthetic.data import SiteSpec, build_specs
from sift.synthetic.render import render_site, styles_for

LAYOUTS = ("v1", "v2")


def spec_summary(spec: SiteSpec) -> dict[str, Any]:
    styles = styles_for(spec)
    return {
        "id": spec.id,
        "domain": spec.domain,
        "vertical": spec.vertical,
        "name": spec.name,
        "start_url": f"https://{spec.domain}{spec.start_path}",
        "mode": spec.mode,
        "locale": spec.locale,
        "currency": spec.currency,
        "intent": spec.intent,
        "schema": spec.schema.model_dump(mode="json", exclude_defaults=True),
        "records": spec.records,
        "traps": spec.traps,
        "injection": spec.injection,
        "anomalies": spec.anomalies,
        "pii": spec.pii,
        "hidden_keys": [h.get("name") or h.get("title") or h.get("address") for h in spec.hidden],
        "archived_keys": [a.get("name") for a in spec.archived],
        "duplicate_key": spec.key(spec.records[spec.featured]) if spec.featured is not None else None,
        "robots_disallow": spec.robots_disallow,
        "crawl_delay": spec.crawl_delay,
        "detail_fields": spec.detail_fields,
        "layouts": {
            name: {"naming": s.naming, "item_tag": s.item_tag, "pagination": s.pagination, "wrap": s.wrap, "rating": s.rating_style}
            for name, s in styles.items()
        },
    }


def generate(out_dir: Path, *, seed: int = 7, today: dt.date = dt.date(2026, 10, 1), clean: bool = True) -> dict[str, Any]:
    specs = build_specs(seed, today)
    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    digest = hashlib.sha256()
    counts = {layout: 0 for layout in LAYOUTS}
    for layout in LAYOUTS:
        for spec in specs:
            for rel, (_ctype, body) in sorted(render_site(spec, layout).items()):
                target = out_dir / layout / spec.domain / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
                digest.update(f"{layout}/{spec.domain}/{rel}".encode())
                digest.update(body)
                counts[layout] += 1
    truth = {"seed": seed, "today": today.isoformat(), "sites": [spec_summary(s) for s in specs]}
    (out_dir / "ground_truth.json").write_text(json.dumps(truth, indent=1, ensure_ascii=False), encoding="utf-8")
    rows = "".join(
        f"<tr><td>{s.id}</td><td>{html.escape(s.name)}</td><td>{s.vertical}</td><td>{s.mode}</td>"
        f'<td><a href="/{s.domain}{s.start_path}">{s.domain}{s.start_path}</a></td><td>{len(s.records)}</td></tr>'
        for s in specs
    )
    index = (
        "<!doctype html><meta charset=utf-8><title>Sift synthetic web</title>"
        "<h1>Sift synthetic web</h1><p>47 fictional sites (reserved .example domains). Paths: /&lt;domain&gt;/... "
        "or send the site's Host header.</p><table><tr><th>id</th><th>name</th><th>vertical</th><th>mode</th><th>start</th><th>records</th></tr>"
        f"{rows}</table>"
    )
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    manifest = {
        "seed": seed,
        "today": today.isoformat(),
        "sites": len(specs),
        "records": sum(len(s.records) for s in specs),
        "files": counts,
        "sha256": digest.hexdigest(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def load_truth(out_dir: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out_dir / "ground_truth.json").read_text(encoding="utf-8"))
    return data

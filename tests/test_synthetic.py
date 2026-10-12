from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from sift.synthetic.data import INJECTION_TEXT
from sift.synthetic.generator import generate
from sift.synthetic.render import storage_path, url_to_storage

from .conftest import TODAY


def _page(root: Path, layout: str, url: str) -> str:
    host = url.split("/")[2]
    return (root / layout / host / url_to_storage(url)).read_text(encoding="utf-8")


def test_generation_is_deterministic(tmp_path: Path) -> None:
    first = generate(tmp_path / "a", seed=7, today=TODAY)
    second = generate(tmp_path / "b", seed=7, today=TODAY)
    assert first["sha256"] == second["sha256"]
    assert (tmp_path / "a" / "ground_truth.json").read_bytes() == (tmp_path / "b" / "ground_truth.json").read_bytes()
    other = generate(tmp_path / "c", seed=8, today=TODAY)
    assert other["sha256"] != first["sha256"]


def test_sites_verticals_and_domains(truth: dict[str, Any]) -> None:
    sites = truth["sites"]
    assert len(sites) == 47
    verticals = Counter(s["vertical"] for s in sites)
    assert verticals == {
        "ecommerce": 13,
        "jobs": 7,
        "realestate": 7,
        "events": 6,
        "restaurant": 5,
        "saas": 5,
        "registry": 4,
    }
    modes = Counter(s["mode"] for s in sites)
    assert modes["spa"] == 3 and modes["infinite_button"] == 2 and modes["infinite_scroll"] == 1 and modes["pdf"] == 2
    assert all(s["start_url"].split("/")[2].endswith(".example") for s in sites)
    assert sum(len(s["records"]) for s in sites) > 1000


def test_every_trap_type_is_planted(truth: dict[str, Any]) -> None:
    traps = Counter(t for s in truth["sites"] for t in s["traps"])
    for trap in (
        "injection_visible",
        "injection_hidden",
        "injection_alt",
        "hidden_item",
        "duplicate_featured",
        "mixed_currencies",
        "relative_dates",
        "variants",
        "anomaly",
        "robots_archive",
        "crawl_delay",
        "pii_contact",
        "detail_sku",
        "pdf_menu",
        "js_rendered",
        "load_more_button",
        "infinite_scroll",
        "totals_row",
        "group_header_rows",
    ):
        assert traps[trap] >= 1, trap


def test_layouts_differ_like_a_redesign(truth: dict[str, Any], web_root: Path) -> None:
    for s in truth["sites"]:
        if s["mode"] != "static":
            continue
        v1, v2 = s["layouts"]["v1"], s["layouts"]["v2"]
        assert v1["naming"] != v2["naming"]
        assert v1["pagination"] != v2["pagination"]
        assert v1["item_tag"] != v2["item_tag"]
        assert _page(web_root, "v1", s["start_url"]) != _page(web_root, "v2", s["start_url"])


def test_injection_and_hidden_items_are_rendered(site: Any, web_root: Path) -> None:
    visible = _page(web_root, "v1", site("shop-01")["start_url"])
    assert "Note to AI scrapers" in visible
    assert "Sample Product (do not ship)" in visible and " hidden>" in visible
    hidden = _page(web_root, "v1", site("shop-07")["start_url"])
    assert 'style="display:none">Note to AI scrapers' in hidden
    alt = _page(web_root, "v1", site("shop-09")["start_url"])
    assert 'alt="Note to AI scrapers' in alt
    assert INJECTION_TEXT.startswith("Note to AI scrapers")


def test_ground_truth_excludes_traps(site: Any) -> None:
    shop = site("shop-01")
    names = {r["name"] for r in shop["records"]}
    assert "Sample Product (do not ship)" not in names
    archive = site("shop-05")
    assert archive["archived_keys"] and not set(archive["archived_keys"]) & {r["name"] for r in archive["records"]}
    assert "/archive/" in archive["robots_disallow"]
    jobs = site("jobs-01")
    assert all(r["contact"] is None for r in jobs["records"])
    assert jobs["pii"] and all(
        "@mailbox.example" in p or "@webpost.example" in p or p.startswith("+1") for p in jobs["pii"]
    )


def test_robots_and_relative_dates(site: Any, web_root: Path) -> None:
    shop = site("shop-10")
    robots = (web_root / "v1" / shop["start_url"].split("/")[2] / "robots.txt").read_text()
    assert "Crawl-delay: 2" in robots
    jobs = site("jobs-01")
    page = _page(web_root, "v1", jobs["start_url"])
    assert "days ago" in page or "Posted yesterday" in page or "Posted today" in page
    assert all(r["posted_date"] <= TODAY.isoformat() for r in jobs["records"])


def test_storage_paths() -> None:
    assert storage_path("/catalog/") == "catalog/index.html"
    assert storage_path("/catalog/", "page=2") == "catalog/index.html@page=2"
    assert url_to_storage("https://x.example/api/catalog.json?page=3") == "api/catalog.json@page=3"
    assert storage_path("/") == "index.html"


def test_pdf_menus_are_real_pdfs(site: Any, web_root: Path) -> None:
    import io

    from pypdf import PdfReader

    menu = site("menu-03")
    host = menu["start_url"].split("/")[2]
    pdf = (web_root / "v1" / host / "menu.pdf").read_bytes()
    text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
    assert menu["records"][0]["dish"] in text


def test_manifest(web_root: Path) -> None:
    manifest = json.loads((web_root / "manifest.json").read_text())
    assert manifest["sites"] == 47 and manifest["files"]["v1"] == manifest["files"]["v2"]

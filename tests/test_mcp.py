"""The MCP server, through the SDK's in-memory client (and one real stdio subprocess): the tools, their safety
properties (robots.txt, private addresses, the shared rate limit, injection and PII handling, the free-only guard)
and the save -> run -> layout change -> repair loop on the synthetic web."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters

from sift.config import Settings
from sift.crawl.urls import host_of
from sift.mcp_server import SiftMCP, _bounded, create_server

pytestmark = pytest.mark.anyio

CATALOG = "https://cobalt-audio.example/catalog/"
RULES_V1: dict[str, Any] = {
    "item": "article.pc-crd",
    "fields": {"title": "h3.pc-ttl", "price": {"css": ".pc-prc"}},
    "next_page": {"css": "a.pc-nxt", "attr": "href"},
}
SCHEMA: dict[str, Any] = {
    "name": "product",
    "fields": [
        {"name": "title", "type": "string", "required": True, "key": True},
        {"name": "price", "type": "money", "currency": "USD", "required": True},
    ],
}


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def state(settings: Settings) -> SiftMCP:
    return SiftMCP(settings)


@pytest.fixture()
async def client(state: SiftMCP) -> AsyncIterator[Client]:
    async with Client(create_server(state=state), raise_exceptions=True) as c:
        yield c


async def call(client: Client, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(tool, args)
    assert not result.is_error, result.content
    assert isinstance(result.structured_content, dict)
    return result.structured_content


async def error(client: Client, tool: str, args: dict[str, Any]) -> str:
    result = await client.call_tool(tool, args)
    assert result.is_error
    text = getattr(result.content[0], "text", "")
    assert isinstance(text, str)
    return text


async def test_tools_are_listed_with_annotations(client: Client) -> None:
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {
        "fetch_page",
        "test_extractor",
        "save_extractor",
        "run_extractor",
        "list_extractors",
        "extract",
    }
    for name in ("fetch_page", "test_extractor", "run_extractor", "list_extractors", "extract"):
        assert tools[name].annotations is not None and tools[name].annotations.read_only_hint is True
    save = tools["save_extractor"].annotations
    assert save is not None and save.read_only_hint is False and save.destructive_hint is False
    assert all("\n    " not in (t.description or "") for t in tools.values())  # docstrings dedented
    props = tools["test_extractor"].input_schema["properties"]
    assert "CSS or XPath" in str(props["extractor"]) and "money" in str(props["schema"])
    # No tool argument can switch off robots.txt or the rate limit.
    for tool in tools.values():
        assert not {"respect_robots", "robots", "rate_limit", "min_interval"} & set(tool.input_schema["properties"])


async def test_fetch_page_returns_marked_skeleton_and_politeness(client: Client) -> None:
    page = await call(client, "fetch_page", {"url": CATALOG})
    assert page["status"] == 200 and page["title"] == "All products - Cobalt Audio"
    assert page["content"].startswith("<<<PAGE ") and "<<<END PAGE" in page["content"]
    assert 'class="pc-crd"' in page["content"] and "more <article" in page["content"]  # repeats collapsed
    assert page["pagination_links"] == ["https://cobalt-audio.example/catalog/?page=2"]
    assert page["politeness"]["robots_txt"] == "respected"
    assert "untrusted" in page["untrusted_data_notice"]
    again = await call(client, "fetch_page", {"url": CATALOG, "view": "text"})
    assert again["from_cache"] is True and "Prime Portable Speaker" in again["content"]


async def test_robots_txt_and_private_addresses_are_refused(client: Client, state: SiftMCP) -> None:
    message = await error(client, "fetch_page", {"url": "https://cobalt-audio.example/cart"})
    assert "robots.txt" in message and "disallows" in message
    assert not any(e.url.endswith("/cart") for e in state.request_log.entries)
    for url in ("http://127.0.0.1:8000/", "http://localhost/admin", "http://169.254.169.254/latest/meta-data/"):
        assert "private" in await error(client, "fetch_page", {"url": url})
    assert "http(s)" in await error(client, "fetch_page", {"url": "file:///etc/passwd"})


async def test_injection_decoys_and_pii_are_handled(client: Client) -> None:
    shop = await call(client, "fetch_page", {"url": "https://alpine-outfitters.example/catalog/", "view": "content"})
    assert shop["injection_findings"] and "[removed by Sift" in shop["content"]
    assert shop["hidden_blocks_removed"] >= 1 and "Sample Product (do not ship)" not in shop["content"]
    jobs = await call(client, "fetch_page", {"url": "https://hireloop.example/jobs/", "view": "text"})
    assert jobs["pii_redacted"] >= 2 and "@mailbox.example" not in jobs["content"]
    assert "555-01" not in jobs["content"]


async def test_test_extractor_validates_and_normalizes(client: Client, site: Any) -> None:
    truth = site("shop-03")["records"]
    result = await call(client, "test_extractor", {"url": CATALOG, "extractor": RULES_V1, "schema": SCHEMA, "pages": 3})
    assert result["ok"] is True, result["errors"]
    assert result["items_per_page"] == [12, 12, 2] and result["schema_valid_records"] == "26 of 26"
    first = result["records"][0]
    assert first["title"] == truth[0]["name"]
    assert first["price"]["currency"] == "USD" and first["price"]["source_currency"] == "GBP"
    assert first["price"]["amount"] == pytest.approx(truth[0]["price"], abs=0.02)
    assert len(result["pages"]) == 3 and result["next_page_links"]


async def test_test_extractor_reports_broken_rules(client: Client) -> None:
    broken = {"item": "article.pc-crd", "fields": {"title": "h3.pc-ttl", "price": ".no-such-class"}}
    result = await call(client, "test_extractor", {"url": CATALOG, "extractor": broken, "schema": SCHEMA})
    assert result["ok"] is False and any("price" in e for e in result["errors"])
    assert result["schema_violations"] and "required" in result["schema_violations"][0]["message"]
    assert "item" in await error(
        client, "test_extractor", {"url": CATALOG, "extractor": {"fields": {}}, "schema": SCHEMA}
    )
    assert "invalid schema" in await error(
        client, "test_extractor", {"url": CATALOG, "extractor": RULES_V1, "schema": {"fields": []}}
    )


async def test_test_extractor_on_html_and_detail_pages(client: Client) -> None:
    html = "<html><body><h1 class='t'>Desk Lamp</h1><span class='p'>€45,00</span><p style='display:none'><span class='p'>€0</span></p></body></html>"
    rules = {"kind": "detail", "fields": {"title": "h1.t", "price": ".p"}}
    schema = {
        "name": "item",
        "fields": [{"name": "title", "type": "string"}, {"name": "price", "type": "money", "currency": "EUR"}],
    }
    result = await call(client, "test_extractor", {"html": html, "extractor": rules, "schema": schema})
    assert result["records"] == [{"title": "Desk Lamp", "price": {"amount": 45.0, "currency": "EUR"}}]


async def test_save_run_detect_layout_change_and_repair(client: Client, state: SiftMCP, settings: Settings) -> None:
    saved = await call(
        client, "save_extractor", {"name": "cobalt", "url": CATALOG, "extractor": RULES_V1, "schema": SCHEMA}
    )
    assert saved["version"] == 1 and saved["records_saved"] == 12 and saved["baseline"]["items_per_page"] == 12
    listed = await call(client, "list_extractors", {})
    assert [e["name"] for e in listed["extractors"]] == ["cobalt"]
    run = await call(client, "run_extractor", {"name": "cobalt", "max_pages": 3})
    assert run["model_calls"] == 0 and run["layout_changed"] is False
    assert run["records_total"] == 26 and len(run["pages"]) == 3

    # The same site after its redesign (the v2 layout of the synthetic web), same store.
    redesigned = SiftMCP(settings.model_copy(update={"synthetic_layout": "v2"}), store=state.store)
    async with Client(create_server(state=redesigned), raise_exceptions=True) as v2:
        changed = await call(v2, "run_extractor", {"name": "cobalt"})
        assert changed["layout_changed"] is True and changed["model_calls"] == 0
        assert changed["reference_records"] and "verify_against" in changed["next_steps"]
        page = await call(v2, "fetch_page", {"url": CATALOG})
        assert "item-tile" in page["content"]
        rules_v2 = {
            "item": "li.item-tile",
            "fields": {"title": ".item-tile__title", "price": ".item-tile__price"},
            "next_page": {"css": "a[rel=next]", "attr": "href"},
        }
        check = await call(
            v2, "test_extractor", {"url": CATALOG, "extractor": rules_v2, "schema": SCHEMA, "verify_against": "cobalt"}
        )
        assert check["ok"] is True, check["errors"]
        assert check["reference_agreement"] == 1.0
        repaired = await call(
            v2, "save_extractor", {"name": "cobalt", "url": CATALOG, "extractor": rules_v2, "schema": SCHEMA}
        )
        assert repaired["version"] == 2
        healthy = await call(v2, "run_extractor", {"name": "cobalt"})
        assert healthy["layout_changed"] is False and healthy["version"] == 2


async def test_save_refuses_rules_that_fail(client: Client) -> None:
    broken = {"item": "article.pc-crd", "fields": {"title": "h3.pc-ttl", "price": ".nothing"}}
    message = await error(
        client, "save_extractor", {"name": "bad", "url": CATALOG, "extractor": broken, "schema": SCHEMA}
    )
    assert "not saved" in message
    assert "no saved extractor" in await error(client, "run_extractor", {"name": "bad"})
    assert "pattern" in (await error(client, "run_extractor", {"name": "Not A Name!"})).lower()


async def test_rate_limit_is_shared_across_tool_calls(settings: Settings) -> None:
    slow = SiftMCP(settings.model_copy(update={"synthetic_min_seconds_per_domain": 0.3}))
    async with Client(create_server(state=slow), raise_exceptions=True) as c:
        await call(c, "fetch_page", {"url": CATALOG})
        await call(c, "fetch_page", {"url": CATALOG + "?page=2"})
    gap = slow.request_log.min_gap(host_of(CATALOG))
    assert gap is not None and gap >= 0.29  # robots.txt, page 1, page 2: one rate limiter for all of them


async def test_extract_uses_the_configured_offline_model(client: Client) -> None:
    result = await call(
        client, "extract", {"url": CATALOG, "request": "Every product with its name and price in USD", "max_pages": 1}
    )
    assert result["model"] == "fake/rules" and result["records_total"] >= 10
    assert result["schema"] and {"name", "price"} <= {f["name"] for f in result["schema"]["fields"]}


async def test_extract_keeps_the_free_only_guard(settings: Settings) -> None:
    paid = SiftMCP(settings.model_copy(update={"llm_provider": "openrouter", "llm_model": "openai/gpt-5"}))
    async with Client(create_server(state=paid), raise_exceptions=True) as c:
        message = await error(c, "extract", {"url": CATALOG, "request": "every product"})
    assert "free-only guard" in message


async def test_legacy_handshake_clients_work(state: SiftMCP) -> None:
    async with Client(create_server(state=state), mode="legacy") as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert "fetch_page" in names
        page = await call(c, "fetch_page", {"url": CATALOG})
        assert page["status"] == 200


async def test_stdio_entry_point(settings: Settings, tmp_path: Path) -> None:
    env = {
        **os.environ,
        "SIFT_SYNTHETIC_WEB_DIR": str(settings.synthetic_web_dir),
        "SIFT_DATABASE_URL": f"sqlite:///{tmp_path / 'stdio.db'}",
        "SIFT_LLM_PROVIDER": "fake",
        "SIFT_LLM_LEDGER": "",
        "SIFT_BROWSER": "never",
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "sift.cli", "mcp"], env=env, cwd=str(tmp_path))
    async with Client(params) as c:
        page = await call(c, "fetch_page", {"url": CATALOG, "view": "text", "max_chars": 2000})
    assert "Prime Portable Speaker" in page["content"]


def test_bounded_output_cuts_records_with_a_note() -> None:
    payload = {"records": [{"x": "y" * 100} for _ in range(100)], "ok": True}
    out = _bounded(payload, 2000)
    assert len(out["records"]) < 100 and "showing" in out["truncated"] and out["ok"] is True

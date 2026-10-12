"""books.toscrape.com and quotes.toscrape.com: two public sandboxes built for scraping practice.

Crawled once, politely: Sift's own crawler with robots.txt (both sites answer 404, which RFC 9309 reads as "no
restrictions"), at most one request per second per host, and page caps (3 catalogue pages and their 60 book pages;
the 10 quote pages). Every response is kept as a local snapshot (data/snapshots/, gitignored) so the ground truth, the
direct-extraction comparison and any re-run read the snapshots instead of the sites.

Ground truth is NOT made by Sift: a hand-written parser for each site (fixed selectors I read from the pages) builds
it from the same snapshots, and a few records were checked by eye against the live pages."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from sift.config import Settings
from sift.crawl.urls import canonicalize
from sift.engine import Engine, JobConfig
from sift.eval.runner import compare_schema, write
from sift.eval.score import aggregate, score_site
from sift.extract.dom import parse, text_of
from sift.llm.factory import model_for
from sift.schema.infer import propose
from sift.schema.model import FieldSpec, FieldType, Schema

BOOKS = "https://books.toscrape.com/"
QUOTES = "https://quotes.toscrape.com/"
BOOKS_INTENT = "Every book with its title, price in GBP, availability (in stock or not), star rating from 1 to 5, its category and its UPC from the book's own page. Follow the pagination."
QUOTES_INTENT = "Every quote with its text, the author and the list of tags. Follow the pagination."
BOOKS_SCHEMA = Schema(
    name="book",
    fields=[
        FieldSpec(name="title", type=FieldType.string, required=True, key=True),
        FieldSpec(name="price", type=FieldType.money, currency="GBP", required=True),
        FieldSpec(name="availability", type=FieldType.enum, enum=["in_stock", "out_of_stock"]),
        FieldSpec(name="rating", type=FieldType.integer, description="star rating 1-5"),
        FieldSpec(name="category", type=FieldType.string),
        FieldSpec(name="upc", type=FieldType.string, description="UPC from the book's page"),
    ],
)
QUOTES_SCHEMA = Schema(
    name="quote",
    fields=[
        FieldSpec(name="text", type=FieldType.string, required=True, key=True),
        FieldSpec(name="author", type=FieldType.string, required=True),
        FieldSpec(name="tags", type=FieldType.list, items=FieldSpec(name="tag", type=FieldType.string)),
    ],
)
_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


class SnapshotTransport(httpx.BaseTransport):
    """Records live responses to disk the first time and replays them afterwards (no second visit)."""

    def __init__(self, root: Path, *, live: bool) -> None:
        self.root = root
        self.live = live
        self.inner = httpx.HTTPTransport()
        self.live_requests = 0
        self.replayed = 0

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()[:24]
        host = httpx.URL(url).host
        return self.root / host / f"{digest}.json"

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = self._path(str(request.url))
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.replayed += 1
            return httpx.Response(
                data["status"],
                headers={"content-type": data["content_type"]},
                content=data["body"].encode("utf-8"),
                request=request,
            )
        if not self.live:
            return httpx.Response(599, content=b"not in snapshot", request=request)
        response = self.inner.handle_request(request)
        response.read()
        self.live_requests += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "url": str(request.url),
                    "status": response.status_code,
                    "content_type": response.headers.get("content-type", "text/html"),
                    "fetched_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                    "body": response.content.decode("utf-8", errors="replace"),
                }
            ),
            encoding="utf-8",
        )
        return httpx.Response(
            response.status_code,
            headers={"content-type": response.headers.get("content-type", "text/html")},
            content=response.content,
            request=request,
        )

    def snapshot(self, url: str) -> str | None:
        path = self._path(url)
        if not path.exists():
            return None
        body: str = json.loads(path.read_text(encoding="utf-8"))["body"]
        return body


# ------------------------------------------------------------------------------------------------ ground truth


def books_truth(snap: SnapshotTransport, list_pages: int) -> list[dict[str, Any]]:
    records = []
    url = BOOKS
    for _ in range(list_pages):
        html = snap.snapshot(url)
        if html is None:
            break
        doc = parse(html)
        for pod in doc.cssselect("article.product_pod"):
            link = pod.cssselect("h3 a")[0]
            detail_url = canonicalize(link.get("href") or "", url)
            detail_html = snap.snapshot(detail_url)
            title = link.get("title") or text_of(link)
            rating_class = (pod.cssselect("p.star-rating")[0].get("class") or "").split()
            record: dict[str, Any] = {
                "title": title,
                "price": float(re.sub(r"[^\d.]", "", text_of(pod.cssselect("p.price_color")[0]))),
                "availability": "in_stock"
                if "in stock" in text_of(pod.cssselect("p.availability")[0]).lower()
                else "out_of_stock",
                "rating": next((_WORDS[c] for c in rating_class if c in _WORDS), None),
                "category": None,
                "upc": None,
            }
            if detail_html is not None:
                detail = parse(detail_html)
                crumbs = detail.cssselect("ul.breadcrumb li a")
                record["category"] = text_of(crumbs[2]) if len(crumbs) >= 3 else None
                for row in detail.cssselect("table.table-striped tr"):
                    if text_of(row.cssselect("th")[0]) == "UPC":
                        record["upc"] = text_of(row.cssselect("td")[0])
            records.append(record)
        nxt = doc.cssselect("li.next a")
        if not nxt:
            break
        url = canonicalize(nxt[0].get("href") or "", url)
    return records


def quotes_truth(snap: SnapshotTransport, pages: int) -> list[dict[str, Any]]:
    records = []
    url = QUOTES
    for _ in range(pages):
        html = snap.snapshot(url)
        if html is None:
            break
        doc = parse(html)
        for quote in doc.cssselect("div.quote"):
            records.append(
                {
                    "text": text_of(quote.cssselect("span.text")[0]),
                    "author": text_of(quote.cssselect("small.author")[0]),
                    "tags": [text_of(a) for a in quote.cssselect("a.tag")],
                }
            )
        nxt = doc.cssselect("li.next a")
        if not nxt:
            break
        url = canonicalize(nxt[0].get("href") or "", url)
    return records


# ------------------------------------------------------------------------------------------------ run


def _engine(settings: Settings, snap: SnapshotTransport, tag: str) -> Engine:
    return Engine(settings, model_for(settings, tag=tag), transport=snap)


def run_toscrape(settings: Settings, *, name: str, books_pages: int = 3, quotes_pages: int = 10) -> dict[str, Any]:
    root = Path("data/snapshots")
    polite = settings.model_copy(
        update={"min_seconds_per_domain": max(1.0, settings.min_seconds_per_domain), "respect_robots": True}
    )
    live = not (root / "books.toscrape.com").exists()
    snap = SnapshotTransport(root, live=live)
    sites: dict[str, Any] = {}
    jobs = (
        ("books", BOOKS, BOOKS_INTENT, BOOKS_SCHEMA, books_pages + books_pages * 20 + 2),
        ("quotes", QUOTES, QUOTES_INTENT, QUOTES_SCHEMA, quotes_pages + 1),
    )
    for key, start, intent, schema, cap in jobs:
        started = time.monotonic()
        engine = _engine(polite, snap, f"{name}:toscrape:{key}")
        result = engine.run(
            JobConfig(start_urls=[start], description=intent, schema=schema, max_pages=cap, strategy="auto")
        )
        crawl_seconds = round(time.monotonic() - started, 1)
        import os

        if os.environ.get("SIFT_EVAL_STORE") == "1":
            from sift.services import Services

            Services(settings).import_result(
                result,
                start_urls=[start],
                description=intent,
                name=f"{key}.toscrape.com (evaluation run)",
                model=f"openrouter/{settings.llm_model}",
                options={"evaluation": True},
            )
        truth = books_truth(snap, books_pages) if key == "books" else quotes_truth(snap, quotes_pages)
        score = score_site(key, schema, truth, [r["data"] for r in result.records])
        # Strategy B on the first page, from the snapshot.
        direct = _engine(polite, snap, f"{name}:toscrape:{key}:direct").run(
            JobConfig(
                start_urls=[start],
                description=intent,
                schema=schema,
                max_pages=1,
                strategy="direct",
                follow_links=False,
            )
        )
        page_truth = truth[:20] if key == "books" else truth[:10]
        direct_score = score_site(key, schema, page_truth, [r["data"] for r in direct.records])
        # Schema from intent on the start page.
        proposal_engine = _engine(polite, snap, f"{name}:toscrape:{key}:schema")
        _, doc, _ = proposal_engine.fetch(start, JobConfig(start_urls=[start]), {})
        proposal_engine.close()
        proposal = propose(
            proposal_engine.model, intent, sample=doc, sample_url=start, max_tokens=settings.llm_max_tokens
        )
        sites[key] = {
            "start_url": start,
            "intent": intent,
            "learned": {
                **score.as_dict(),
                "pages": result.stats["pages"],
                "llm": result.stats["llm"],
                "strategies": result.stats["strategies"],
                "crawl_seconds": crawl_seconds,
                "events": [
                    e.message[:300]
                    for e in result.events
                    if e.kind
                    in {"strategy", "learned", "learn_failed", "layout_changed", "healed", "robots_blocked", "page_cap"}
                ],
            },
            "extractors": [e.as_dict()["program"] for e in result.extractors],
            "direct_first_page": {**direct_score.as_dict(), "llm": direct.stats["llm"]},
            "schema_inference": {
                **compare_schema(schema, proposal.schema),
                "proposed": proposal.schema.model_dump(mode="json", exclude_defaults=True) if proposal.schema else None,
            },
            "politeness": result.politeness,
            "truth_records": len(truth),
            "sample_errors": score.errors[:10],
        }
    summary = {
        "aggregate": {
            "learned": aggregate([_score(sites[k]["learned"]) for k in sites]),
            "direct_first_page": aggregate([_score(sites[k]["direct_first_page"]) for k in sites]),
            "live_requests": snap.live_requests,
            "replayed_from_snapshot": snap.replayed,
            "crawled_live": live,
            "books": {
                k: v
                for k, v in sites["books"]["learned"].items()
                if k
                in {"truth", "extracted", "matched", "precision", "recall", "field_accuracy", "fields", "pages", "llm"}
            },
            "quotes": {
                k: v
                for k, v in sites["quotes"]["learned"].items()
                if k
                in {"truth", "extracted", "matched", "precision", "recall", "field_accuracy", "fields", "pages", "llm"}
            },
        },
        "sites": sites,
    }
    write(settings, name, "toscrape", summary)
    return summary


def _score(entry: dict[str, Any]) -> Any:
    from sift.eval.score import SiteScore

    s = SiteScore(entry["site"], entry["truth"], entry["extracted"], entry["matched"])
    s.fields = {k: [v["correct"], v["total"]] for k, v in entry["fields"].items()}
    return s

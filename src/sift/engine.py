"""The job engine: crawl, choose a strategy per template, extract, validate, heal, dedupe.

    start URLs -> frontier -> polite fetch (robots.txt, rate limit; a browser when the page needs JavaScript)
      -> PDF?  -> text -> direct extraction (B)
      -> HTML  -> template known? -> run the learned extractor (A, no model call) -> health check
                                       -> healthy: records
                                       -> layout changed: re-learn from a few pages, verify against earlier
                                          records -> healed: records | failed: direct extraction for this site
                -> new template  -> repeated (pagination / many similar items)? learn an extractor (A) : direct (B)
      -> links: next pages (learned rule + heuristics), detail pages for fields the list does not show
    -> normalize + validate (code) -> PII guard -> sparse rows out -> dedupe -> anomaly flags -> records with
       per-field confidence and provenance

The engine does not touch a database; it reports progress through an event sink and returns everything it made, so
the API, the worker, the CLI and the evaluation all run the same code."""

from __future__ import annotations

import datetime as dt
import io
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import urljoin

import httpx

from sift.config import Settings, StrategyKind
from sift.crawl.discovery import needs_browser, pagination_links, relevant_links, request_words
from sift.crawl.fetch import (
    BrowserFetcher,
    DiskSource,
    FetchResult,
    HttpFetcher,
    HttpSource,
    SyntheticSource,
    SyntheticTransport,
    browser_available,
    is_synthetic,
)
from sift.crawl.polite import RateLimiter, RequestLog
from sift.crawl.urls import canonicalize, host_of, same_site
from sift.extract import direct as direct_mod
from sift.extract.dom import (
    Fingerprint,
    HtmlElement,
    clean_html,
    fingerprint,
    page_lang,
    parse,
    strip_hidden,
    visible_text,
)
from sift.extract.induce import find_items
from sift.extract.learn import Sample, check_program, learn, typed
from sift.extract.program import Program, RawRecord, links, run_detail, run_list
from sift.llm.base import ChatModel
from sift.llm.counting import CallStats, CountingModel
from sift.logging_config import get_logger
from sift.normalize.dates import day_first_for_locale
from sift.normalize.money import FxTable, load_fx
from sift.schema.infer import propose
from sift.schema.model import Schema
from sift.validate.injection import Finding, guard_dom
from sift.validate.normalize import Context, FieldResult, normalize_record
from sift.validate.pii import PiiReport, guard_record
from sift.validate.quality import anomalies, dedupe, fill_ratio, is_sparse, record_key

log = get_logger(__name__)


@dataclass
class Event:
    kind: str
    message: str
    level: str = "info"
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"))


@dataclass
class ExtractorState:
    id: str
    host: str
    kind: str
    program: Program
    fingerprint: dict[str, int]
    version: int = 1
    baseline_items: float = 0.0
    baseline_fill: dict[str, float] = field(default_factory=dict)
    origin: str = "learned"
    """learned | healed | preloaded"""
    parent: str | None = None
    accepted: bool = True
    calls: int = 0
    attempts: int = 0
    good_records: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))
    check: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "host": self.host,
            "kind": self.kind,
            "version": self.version,
            "origin": self.origin,
            "parent": self.parent,
            "accepted": self.accepted,
            "calls": self.calls,
            "attempts": self.attempts,
            "baseline_items": self.baseline_items,
            "baseline_fill": self.baseline_fill,
            "program": self.program.model_dump(mode="json", exclude_none=True),
            "fingerprint": self.fingerprint,
            "good_records": self.good_records[:20],
            "created_at": self.created_at,
            "check": self.check,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractorState:
        return cls(
            id=data["id"],
            host=data["host"],
            kind=data.get("kind", "list"),
            program=Program.model_validate(data["program"]),
            fingerprint=dict(data.get("fingerprint") or {}),
            version=int(data.get("version", 1)),
            baseline_items=float(data.get("baseline_items", 0.0)),
            baseline_fill=dict(data.get("baseline_fill") or {}),
            origin="preloaded",
            parent=data.get("parent"),
            accepted=bool(data.get("accepted", True)),
            good_records=list(data.get("good_records") or []),
            check=dict(data.get("check") or {}),
        )


@dataclass
class JobConfig:
    start_urls: list[str]
    description: str = ""
    schema: Schema | None = None
    strategy: StrategyKind = "auto"
    max_pages: int = 60
    max_depth: int = 4
    heal: bool = True
    browser: str = "auto"
    default_currency: str | None = None
    injection_guard: bool = True
    pii_guard: bool = True
    follow_links: bool = True
    extractors: list[ExtractorState] = field(default_factory=list)
    reference_records: list[dict[str, Any]] = field(default_factory=list)
    """Records from an earlier run of this job: the healing check verifies re-learned rules against them."""
    previous_hashes: dict[str, str] = field(default_factory=dict)
    previous_page_records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class PageInfo:
    url: str
    status: int
    via: str
    role: str
    depth: int
    strategy: str = ""
    extractor: str | None = None
    records: int = 0
    content_hash: str = ""
    fingerprint: str = ""
    similarity: float | None = None
    unchanged: bool = False
    error: str | None = None
    ms: float = 0.0


@dataclass
class JobResult:
    schema: Schema | None
    records: list[dict[str, Any]]
    pages: list[PageInfo]
    extractors: list[ExtractorState]
    events: list[Event]
    stats: dict[str, Any]
    politeness: dict[str, Any]
    calls: CallStats
    rejected_fields: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class _Pending:
    url: str
    depth: int
    role: str
    """seed | list | detail | discover"""
    parent_records: list[int] = field(default_factory=list)


class Engine:
    def __init__(
        self,
        settings: Settings,
        model: ChatModel,
        *,
        sink: Callable[[Event], None] | None = None,
        source: SyntheticSource | None = None,
        transport: httpx.BaseTransport | None = None,
        fx: FxTable | None = None,
        today: dt.date | None = None,
        stop: Callable[[], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.stats = CallStats()
        self.model = CountingModel(model, self.stats)
        self.sink = sink
        self.fx = fx or load_fx(settings.fx_file if settings.fx_file.exists() else None)
        self.today = today or settings.crawl_today()
        self.stop = stop or (lambda: False)
        if source is None:
            source = (
                HttpSource(settings.synthetic_web_url)
                if settings.synthetic_web_url
                else DiskSource(settings.synthetic_web_dir, settings.synthetic_layout)
            )
        self.source = source
        self.request_log = RequestLog()
        self.limiter = RateLimiter(settings.min_seconds_per_domain)
        self.http = HttpFetcher(
            user_agent=settings.user_agent,
            limiter=self.limiter,
            request_log=self.request_log,
            respect_robots=settings.respect_robots,
            timeout=settings.fetch_timeout_seconds,
            retries=settings.fetch_retries,
            max_bytes=settings.max_page_bytes,
            transport=transport or SyntheticTransport(source),
        )
        self._browser: BrowserFetcher | None = None
        self._browser_ok: bool | None = None
        self.events: list[Event] = []
        self.pii = PiiReport()
        self.findings: list[Finding] = []

    # ------------------------------------------------------------------------------------------------ plumbing

    def emit(self, kind: str, message: str, *, level: str = "info", **data: Any) -> None:
        event = Event(kind, message, level, data)
        self.events.append(event)
        if self.sink is not None:
            try:
                self.sink(event)
            except Exception as exc:  # a broken sink must not stop the crawl
                log.warning("engine.sink_error", error=type(exc).__name__)

    def _ctx(self, url: str, doc: HtmlElement | None, default_currency: str | None) -> Context:
        lang = page_lang(doc) if doc is not None else None
        return Context(
            page_url=url,
            today=self.today,
            fx=self.fx,
            day_first=day_first_for_locale(lang),
            default_currency=default_currency,
        )

    def _ctx_factory(self, job: JobConfig) -> Callable[[str, HtmlElement], Context]:
        def make(url: str, doc: HtmlElement) -> Context:
            return self._ctx(url, doc, job.default_currency)

        return make

    def _prepare_host(self, url: str) -> None:
        """The synthetic web is served from disk: no real server to protect, so its interval may be zero."""
        host = host_of(url)
        interval = (
            self.settings.synthetic_min_seconds_per_domain
            if is_synthetic(url)
            else self.settings.min_seconds_per_domain
        )
        self.limiter.set_interval(host, interval)

    def browser(self) -> BrowserFetcher | None:
        if self._browser_ok is None:
            self._browser_ok = browser_available(self.settings.browser_executable)
        if not self._browser_ok:
            return None
        if self._browser is None:
            self._browser = BrowserFetcher(
                user_agent=self.settings.user_agent,
                limiter=self.limiter,
                request_log=self.request_log,
                source=self.source,
                executable=self.settings.browser_executable,
                max_scrolls=self.settings.browser_max_scrolls,
            )
        return self._browser

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        self.http.close()

    # ------------------------------------------------------------------------------------------------ fetching

    def fetch(
        self, url: str, job: JobConfig, browser_hosts: dict[str, str], *, expand_selector: str | None = None
    ) -> tuple[FetchResult, HtmlElement | None, str]:
        """(result, parsed document or None, extracted text for PDFs). `browser_hosts`: host -> why it needs one."""
        host = host_of(url)
        use_browser = job.browser == "always" or host in browser_hosts
        result: FetchResult
        if use_browser and job.browser != "never" and (b := self.browser()) is not None:
            expand = browser_hosts.get(host, "") in {"load-more button", "infinite scroll"}
            result = b.fetch(url, expand=expand, item_selector=expand_selector)
        else:
            result = self.http.fetch(url)
        if not result.ok:
            return result, None, ""
        if result.is_pdf or result.body[:5] == b"%PDF-":
            return result, None, pdf_text(result.body)
        if not result.is_html:
            return result, None, ""
        doc = parse(result.body)
        if result.via == "http" and job.browser != "never":
            reason = needs_browser(doc, result.text)
            if reason:
                b = self.browser()
                if b is None:
                    self.emit(
                        "browser_unavailable",
                        f"{url} needs a browser ({reason}) but none is installed; using the static HTML",
                        level="warning",
                        url=url,
                    )
                    return result, doc, ""
                browser_hosts[host] = reason
                self.emit("browser", f"{url}: {reason}; rendering with headless Chromium", url=url, reason=reason)
                rendered = b.fetch(url, expand=reason != "empty application shell", item_selector=expand_selector)
                if rendered.ok:
                    return rendered, parse(rendered.body), ""
        return result, doc, ""

    # ------------------------------------------------------------------------------------------------ main loop

    def run(self, job: JobConfig) -> JobResult:
        started = time.monotonic()
        try:
            return self._run(job, started)
        finally:
            self.close()

    def _run(self, job: JobConfig, started: float) -> JobResult:
        schema = job.schema
        rejected: list[dict[str, Any]] = []
        browser_hosts: dict[str, str] = {}
        start_urls = [canonicalize(u) for u in job.start_urls if u.strip()]
        if not start_urls:
            return self._result(schema, [], [], [], started, rejected, error="no start URL")
        for url in start_urls:
            self._prepare_host(url)
        self.emit("job_started", f"Crawling {len(start_urls)} start URL(s)", urls=start_urls, model=self.model.label)
        prefetched: dict[str, tuple[FetchResult, HtmlElement | None, str]] = {}

        if schema is None:
            first = start_urls[0]
            if not self.http.allowed(first):
                return self._result(None, [], [], [], started, rejected, error=f"robots.txt disallows {first}")
            fetched = self.fetch(first, job, browser_hosts)
            prefetched[first] = fetched
            proposal = propose(
                self.model,
                job.description,
                sample=fetched[1],
                sample_url=first,
                guard=job.injection_guard,
                max_tokens=self.settings.llm_max_tokens,
            )
            rejected = proposal.rejected
            if proposal.schema is None:
                return self._result(
                    None, [], [], [], started, rejected, error=f"could not propose a schema: {proposal.error}"
                )
            schema = proposal.schema
            self.emit(
                "schema_proposed", f"Proposed {len(schema.fields)} fields", fields=schema.names, rejected=rejected
            )

        extractors: dict[tuple[str, str], ExtractorState] = {(e.host, e.kind): e for e in job.extractors}
        host_strategy: dict[str, str] = {}
        broken: set[tuple[str, str]] = set()
        healed_once: set[tuple[str, str]] = set()
        records: list[dict[str, Any]] = []
        pages: list[PageInfo] = []
        queue: list[_Pending] = [_Pending(u, 0, "seed") for u in start_urls]
        seen: set[str] = set(start_urls)
        detail_targets: dict[str, list[int]] = {}
        words = request_words(job.description, schema.names)
        reference = list(job.reference_records)

        while queue and len(pages) < job.max_pages:
            if self.stop():
                self.emit("job_cancelled", "Stopped on request", level="warning")
                break
            # Pages of results first, detail pages after (they need the list's records to merge into).
            queue.sort(key=lambda p: (p.role == "detail", p.depth))
            item = queue.pop(0)
            url, host = item.url, host_of(item.url)
            if not self.http.allowed(url):
                pages.append(PageInfo(url, 0, "-", item.role, item.depth, error="disallowed by robots.txt"))
                self.emit("robots_blocked", f"robots.txt disallows {url}; skipped", level="warning", url=url)
                continue
            t0 = time.monotonic()
            result, doc, pdf = prefetched.pop(url) if url in prefetched else self.fetch(url, job, browser_hosts)
            info = PageInfo(
                url,
                result.status,
                result.via,
                item.role,
                item.depth,
                content_hash=result.content_hash if result.ok else "",
                ms=0.0,
            )
            pages.append(info)
            if not result.ok:
                info.error = result.error or f"HTTP {result.status}"
                self.emit("fetch_error", f"{url}: {info.error}", level="warning", url=url, status=result.status)
                continue
            new_records: list[dict[str, Any]] = []
            next_links: list[str] = []
            # Incremental re-crawl: unchanged content means the records from last time still stand (links are still
            # followed, so changed pages further down are found).
            unchanged = job.previous_hashes.get(url) == info.content_hash and url in job.previous_page_records
            if unchanged:
                info.unchanged = True
                info.strategy = "unchanged"
                new_records = [dict(r, id=uuid.uuid4().hex[:12]) for r in job.previous_page_records[url]]
                previous_ext = extractors.get((host, "list"))
                if doc is not None and previous_ext is not None:
                    next_links = links(doc, previous_ext.program.next_page, url)
            elif pdf or doc is None:
                if pdf:
                    info.strategy = "direct"
                    new_records = self._direct(schema, url, None, pdf, job, info)
                    host_strategy.setdefault(host, "direct")
            elif item.role == "detail":
                new_records = self._detail(
                    schema,
                    url,
                    doc,
                    item,
                    job,
                    extractors,
                    records,
                    detail_targets,
                    info,
                    queue,
                    prefetched,
                    browser_hosts,
                    broken,
                )
            else:
                strategy = host_strategy.get(host) or self._choose(job, url, doc, schema, extractors, start_urls)
                host_strategy.setdefault(host, strategy)
                if (host, "list") in broken:
                    strategy = "direct"
                info.strategy = strategy
                if strategy == "learned":
                    new_records, next_links = self._learned(
                        schema,
                        url,
                        doc,
                        item,
                        job,
                        extractors,
                        info,
                        queue,
                        prefetched,
                        browser_hosts,
                        broken,
                        healed_once,
                        reference,
                        records,
                    )
                    if (host, "list") in broken:
                        info.strategy = "direct"
                        new_records = self._direct(schema, url, doc, "", job, info)
                else:
                    new_records = self._direct(schema, url, doc, "", job, info)
            # Detail pages to visit for fields the list pages do not show.
            ext = extractors.get((host, "list"))
            for record in new_records:
                record.setdefault("_page", url)
            base_index = len(records)
            records.extend(new_records)
            if (
                not unchanged
                and ext is not None
                and ext.program.missing
                and ext.program.detail_link is not None
                and item.role != "detail"
            ):
                for offset, record in enumerate(new_records):
                    target = record.get("_detail_url")
                    if not target:
                        continue
                    detail_targets.setdefault(target, []).append(base_index + offset)
                    if target not in seen and item.depth + 1 <= job.max_depth:
                        seen.add(target)
                        queue.append(_Pending(target, item.depth + 1, "detail"))
            info.records = len(new_records)
            info.ms = round((time.monotonic() - t0) * 1000, 1)
            if doc is not None and job.follow_links and item.role != "detail":
                candidates = [(link, "list") for link in next_links + pagination_links(doc, url)]
                if not new_records and item.depth == 0:
                    candidates += [(link, "discover") for link in relevant_links(doc, url, words)]
                for link, role in candidates:
                    link = canonicalize(link)
                    if link in seen or not same_site(url, link) or item.depth + 1 > job.max_depth:
                        continue
                    seen.add(link)
                    queue.append(_Pending(link, item.depth + 1, role))
            self.emit(
                "page_done",
                f"{url}: {info.records} records ({info.strategy})",
                url=url,
                records=info.records,
                strategy=info.strategy,
                pages=len(pages),
                total_records=len(records),
                llm_calls=len(self.stats.calls),
            )
        if queue:
            self.emit(
                "page_cap",
                f"Page cap of {job.max_pages} reached; {len(queue)} queued URL(s) not visited",
                level="warning",
                left=len(queue),
            )
        return self._result(schema, records, pages, list(extractors.values()), started, rejected)

    # ------------------------------------------------------------------------------------------------ strategies

    def _choose(
        self,
        job: JobConfig,
        url: str,
        doc: HtmlElement,
        schema: Schema,
        extractors: dict[tuple[str, str], ExtractorState],
        start_urls: list[str],
    ) -> str:
        host = host_of(url)
        if job.strategy != "auto":
            reason = "set by the job"
            choice = job.strategy
        elif (host, "list") in extractors:
            choice, reason = "learned", "an extractor for this site exists"
        else:
            pagination = pagination_links(doc, url)
            clean, _ = strip_hidden(doc)
            repeated = len(find_items(clean, schema))
            same_host = sum(1 for u in start_urls if host_of(u) == host)
            if pagination:
                choice, reason = "learned", f"pagination found ({len(pagination)} link(s)): a repeated template"
            elif repeated >= 8:
                choice, reason = "learned", f"{repeated} similar items on the page: a repeated template"
            elif same_host >= 3:
                choice, reason = "learned", f"{same_host} start URLs on this site"
            else:
                choice, reason = "direct", f"one-off page ({repeated} similar items, no pagination)"
        self.emit(
            "strategy",
            f"{host}: {'learned extractor' if choice == 'learned' else 'direct LLM extraction'} ({reason})",
            host=host,
            strategy=choice,
            reason=reason,
        )
        return choice

    def _samples(
        self,
        url: str,
        doc: HtmlElement,
        job: JobConfig,
        prefetched: dict[str, Any],
        browser_hosts: dict[str, str],
        limit: int,
        seen_links: list[str] | None = None,
    ) -> list[Sample]:
        samples = [Sample(url, doc)]
        for link in (seen_links or pagination_links(doc, url))[: max(0, limit - 1) * 2]:
            if len(samples) >= limit:
                break
            if not self.http.allowed(link):
                continue
            fetched = prefetched.get(link) or self.fetch(link, job, browser_hosts)
            prefetched[link] = fetched
            if fetched[1] is not None:
                samples.append(Sample(link, fetched[1]))
        return samples

    def _records_from_raw(
        self,
        raws: list[RawRecord],
        schema: Schema,
        url: str,
        doc: HtmlElement,
        job: JobConfig,
        ext: ExtractorState,
        program: Program,
    ) -> list[dict[str, Any]]:
        ctx = self._ctx(url, doc, job.default_currency)
        out = []
        for raw in raws:
            values = {k: v.value for k, v in raw.values.items()}
            normalized = normalize_record(values, schema, ctx)
            fields_prov = {
                name: {"rule": v.rule, "node": v.node, "raw": _short(v.value), "matches": v.matches}
                for name, v in raw.values.items()
                if name in schema.names
            }
            confidence = {}
            for name, result in normalized.items():
                rv = raw.values.get(name)
                conf = result.confidence
                if rv is not None and rv.matches > 1 and not isinstance(rv.value, list):
                    conf *= 0.9
                confidence[name] = round(conf, 2)
            record = self._assemble(
                normalized,
                schema,
                url,
                job,
                provenance={
                    "url": url,
                    "strategy": "learned",
                    "extractor": f"{ext.id}@v{ext.version}",
                    "item": raw.item_path,
                    "fields": fields_prov,
                },
                confidence=confidence,
            )
            if raw.detail_url:
                record["_detail_url"] = canonicalize(urljoin(url, raw.detail_url))
            out.append(record)
        return out

    def _run_list(self, ext: ExtractorState, url: str, doc: HtmlElement) -> list[RawRecord]:
        clean, _ = strip_hidden(doc)
        return run_list(clean, ext.program)
        # Item paths refer to the cleaned copy; map them onto the original for detail links via the same path.

    def _health(
        self,
        ext: ExtractorState,
        raws: list[RawRecord],
        typed_records: list[dict[str, Any]],
        schema: Schema,
        fp: Fingerprint,
        is_last: bool,
    ) -> tuple[list[str], float]:
        similarity = round(fp.similarity(Fingerprint(ext.fingerprint)), 3) if ext.fingerprint else 1.0
        signals: list[str] = []
        if not raws:
            signals.append("no records")
        elif ext.baseline_items >= 4 and len(raws) < 0.3 * ext.baseline_items and not is_last:
            signals.append(f"yield dropped to {len(raws)} from about {ext.baseline_items:.0f} per page")
        if raws:
            for spec in schema.fields:
                if spec.name in ext.program.missing:
                    continue
                before = ext.baseline_fill.get(spec.name, 0.0)
                now = sum(1 for r in typed_records if r.get(spec.name) not in (None, "", [])) / len(typed_records)
                if before >= 0.8 and now < 0.5:
                    signals.append(
                        f"{spec.name} empty in {round((1 - now) * 100)}% of records (was {round(before * 100)}% filled)"
                    )
        return signals, similarity

    def _learned(
        self,
        schema: Schema,
        url: str,
        doc: HtmlElement,
        item: _Pending,
        job: JobConfig,
        extractors: dict[tuple[str, str], ExtractorState],
        info: PageInfo,
        queue: list[_Pending],
        prefetched: dict[str, Any],
        browser_hosts: dict[str, str],
        broken: set[tuple[str, str]],
        healed_once: set[tuple[str, str]],
        reference: list[dict[str, Any]],
        records: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        host = host_of(url)
        key = (host, "list")
        fp = fingerprint(doc)
        info.fingerprint = fp.digest()
        ctx_for = self._ctx_factory(job)
        if job.injection_guard:
            _, found = guard_dom(doc)
            if found:
                self.findings.extend(found)
                self.emit(
                    "injection",
                    f"{url}: {len(found)} sentence(s) addressed to AI systems quarantined",
                    level="warning",
                    url=url,
                    rules=sorted({r for f in found for r in f.rules}),
                    where=sorted({f.where for f in found}),
                )
        ext = extractors.get(key)
        if ext is None:
            samples = self._samples(url, doc, job, prefetched, browser_hosts, self.settings.learn_sample_pages)
            self.emit(
                "learning",
                f"{host}: learning an extractor from {len(samples)} sample page(s)",
                host=host,
                samples=[s.url for s in samples],
            )
            result = learn(
                self.model,
                schema,
                samples,
                ctx_for,
                max_attempts=self.settings.learn_max_attempts,
                max_tokens=self.settings.llm_max_tokens,
                guard=job.injection_guard,
            )
            if result.program is None or (not result.accepted and (result.check is None or result.check.score < 0.5)):
                broken.add(key)
                self.emit(
                    "learn_failed",
                    f"{host}: no extractor passed validation after {result.attempts} attempt(s); using direct extraction",
                    level="warning",
                    host=host,
                    history=result.history,
                )
                return [], []
            ext = self._new_extractor(host, "list", result.program, samples, schema, job, result, origin="learned")
            extractors[key] = ext
            self.emit(
                "learned",
                f"{host}: extractor learned in {result.attempts} attempt(s), {result.calls} model call(s)"
                + ("" if result.accepted else " (best effort, did not pass every check)"),
                host=host,
                extractor=ext.id,
                accepted=result.accepted,
                attempts=result.attempts,
                calls=result.calls,
                program=ext.program.model_dump(mode="json", exclude_none=True),
                check=ext.check,
            )
        raws = self._run_list(ext, url, doc)
        typed_records = [
            typed(normalize_record({k: v.value for k, v in r.values.items()}, schema, ctx_for(url, doc))) for r in raws
        ]
        is_last = not pagination_links(doc, url) and not links(doc, ext.program.next_page, url)
        signals, similarity = self._health(ext, raws, typed_records, schema, fp, is_last)
        info.similarity = similarity
        info.extractor = f"{ext.id}@v{ext.version}"
        if signals:
            self.emit(
                "layout_changed",
                f"{host}: layout change detected ({'; '.join(signals)}; structural similarity {similarity:.2f})",
                level="warning",
                host=host,
                extractor=ext.id,
                signals=signals,
                similarity=similarity,
                url=url,
            )
            if not job.heal or key in healed_once:
                if key in healed_once:
                    broken.add(key)
                return self._records_from_raw(raws, schema, url, doc, job, ext, ext.program), []
            healed_once.add(key)
            samples = self._samples(url, doc, job, prefetched, browser_hosts, self.settings.heal_max_pages)
            refs = (ext.good_records or []) + reference + [r["data"] for r in records[-40:] if "data" in r]
            result = learn(
                self.model,
                schema,
                samples,
                ctx_for,
                previous=ext.program,
                reference=refs or None,
                max_attempts=self.settings.learn_max_attempts,
                max_tokens=self.settings.llm_max_tokens,
                guard=job.injection_guard,
            )
            if result.program is not None and result.accepted:
                new = self._new_extractor(
                    host, "list", result.program, samples, schema, job, result, origin="healed", parent=ext
                )
                extractors[key] = new
                self.emit(
                    "healed",
                    f"{host}: healed in {result.attempts} attempt(s), {result.calls} model call(s); verified against {len(refs)} earlier record(s)",
                    host=host,
                    extractor=new.id,
                    version=new.version,
                    calls=result.calls,
                    attempts=result.attempts,
                    reference_agreement=result.check.reference_agreement if result.check else None,
                    before=ext.program.model_dump(mode="json", exclude_none=True),
                    after=new.program.model_dump(mode="json", exclude_none=True),
                )
                ext = new
                raws = self._run_list(ext, url, doc)
                info.extractor = f"{ext.id}@v{ext.version}"
            else:
                broken.add(key)
                self.emit(
                    "heal_failed",
                    f"{host}: re-learned rules did not pass verification after {result.attempts} attempt(s) ({result.calls} call(s)); switching this site to direct extraction",
                    level="error",
                    host=host,
                    calls=result.calls,
                    history=result.history,
                )
                return [], []
        out = self._records_from_raw(raws, schema, url, doc, job, ext, ext.program)
        if len(ext.good_records) < 40:
            ext.good_records.extend(r["data"] for r in out[: 40 - len(ext.good_records)])
        return out, links(doc, ext.program.next_page, url)

    def _new_extractor(
        self,
        host: str,
        kind: str,
        program: Program,
        samples: list[Sample],
        schema: Schema,
        job: JobConfig,
        result: Any,
        *,
        origin: str,
        parent: ExtractorState | None = None,
    ) -> ExtractorState:
        ctx_for = self._ctx_factory(job)
        sub = (
            schema
            if kind == "list"
            else Schema(
                name=schema.name,
                fields=[f for f in schema.fields if f.name not in (program.missing or [])] or schema.fields,
            )
        )
        check = check_program(program, sub, samples, ctx_for)
        items = [n for n in check.items_per_page if n]
        fp = fingerprint(samples[0].doc)
        return ExtractorState(
            id=parent.id if parent else f"ext-{uuid.uuid4().hex[:6]}",
            host=host,
            kind=kind,
            program=program,
            fingerprint=fp.shingles,
            version=(parent.version + 1) if parent else 1,
            baseline_items=float(median(items)) if items else 0.0,
            baseline_fill=check.fill,
            origin=origin,
            parent=f"{parent.id}@v{parent.version}" if parent else None,
            accepted=bool(result.accepted),
            calls=int(result.calls),
            attempts=int(result.attempts),
            check={
                "errors": result.check.errors if result.check else [],
                "warnings": (result.check.warnings if result.check else [])[:10],
                "score": result.check.score if result.check else 0,
                "history": result.history,
            },
        )

    def _detail(
        self,
        schema: Schema,
        url: str,
        doc: HtmlElement,
        item: _Pending,
        job: JobConfig,
        extractors: dict[tuple[str, str], ExtractorState],
        records: list[dict[str, Any]],
        detail_targets: dict[str, list[int]],
        info: PageInfo,
        queue: list[_Pending],
        prefetched: dict[str, Any],
        browser_hosts: dict[str, str],
        broken: set[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        host = host_of(url)
        list_ext = extractors.get((host, "list"))
        missing = list(list_ext.program.missing) if list_ext else []
        fields = [f for f in schema.fields if f.name in missing]
        if not fields:
            return []
        sub = Schema(name=schema.name, fields=fields)
        key = (host, "detail")
        ctx = self._ctx(url, doc, job.default_currency)
        values: dict[str, Any] = {}
        prov: dict[str, Any] = {}
        if key not in broken and job.strategy != "direct":
            ext = extractors.get(key)
            if ext is None:
                others = [p.url for p in queue if p.role == "detail"][:2]
                samples = [Sample(url, doc)]
                for other in others:
                    fetched = prefetched.get(other) or self.fetch(other, job, browser_hosts)
                    prefetched[other] = fetched
                    if fetched[1] is not None:
                        samples.append(Sample(other, fetched[1]))
                ctx_for = self._ctx_factory(job)
                result = learn(
                    self.model,
                    sub,
                    samples,
                    ctx_for,
                    kind="detail",
                    max_attempts=self.settings.learn_max_attempts,
                    max_tokens=self.settings.llm_max_tokens,
                    guard=job.injection_guard,
                )
                if result.program is None or not result.accepted:
                    broken.add(key)
                    self.emit(
                        "learn_failed",
                        f"{host}: detail-page rules failed validation; using direct extraction for detail pages",
                        level="warning",
                        host=host,
                    )
                else:
                    ext = self._new_extractor(
                        host, "detail", result.program, samples, sub, job, result, origin="learned"
                    )
                    extractors[key] = ext
                    self.emit(
                        "learned",
                        f"{host}: detail-page extractor learned ({result.calls} call(s))",
                        host=host,
                        extractor=ext.id,
                        extractor_kind="detail",
                        calls=result.calls,
                        program=ext.program.model_dump(mode="json", exclude_none=True),
                    )
            ext = extractors.get(key)
            if ext is not None:
                clean, _ = strip_hidden(doc)
                raw = run_detail(clean, ext.program)
                normalized = normalize_record({k: v.value for k, v in raw.values.items()}, sub, ctx)
                values = {k: r.value for k, r in normalized.items()}
                prov = {k: {"rule": v.rule, "node": v.node, "raw": _short(v.value)} for k, v in raw.values.items()}
                info.strategy, info.extractor = "learned", f"{ext.id}@v{ext.version}"
        if not values or all(v in (None, "", []) for v in values.values()):
            result_d = direct_mod.extract(
                self.model, sub, url, doc=doc, guard=job.injection_guard, max_tokens=self.settings.llm_max_tokens
            )
            info.strategy = "direct"
            if result_d.records:
                normalized = normalize_record(result_d.records[0], sub, ctx)
                values = {k: r.value for k, r in normalized.items()}
                prov = {k: {"raw": _short(result_d.records[0].get(k))} for k in values}
        for index in detail_targets.get(url, []):
            record = records[index]
            for name, value in values.items():
                if value not in (None, "", []):
                    record["data"][name] = value
                    record["confidence"][name] = 0.95
                    record["provenance"].setdefault("detail", {"url": url, "fields": {}})["fields"][name] = prov.get(
                        name, {}
                    )
        info.records = 0
        return []

    def _direct(
        self, schema: Schema, url: str, doc: HtmlElement | None, text: str, job: JobConfig, info: PageInfo
    ) -> list[dict[str, Any]]:
        result = direct_mod.extract(
            self.model,
            schema,
            url,
            doc=doc,
            text=text or None,
            guard=job.injection_guard,
            max_tokens=self.settings.llm_max_tokens,
        )
        info.strategy = "direct"
        if result.findings:
            self.findings.extend(result.findings)
            self.emit(
                "injection",
                f"{url}: {len(result.findings)} sentence(s) addressed to AI systems quarantined",
                level="warning",
                url=url,
                rules=sorted({r for f in result.findings for r in f.rules}),
                where=sorted({f.where for f in result.findings}),
            )
        if result.error:
            info.error = result.error
            self.emit(
                "extract_error", f"{url}: direct extraction failed ({result.error[:120]})", level="warning", url=url
            )
            return []
        page_text = (visible_text(doc) + " " + clean_html(doc)) if doc is not None else text
        ctx = self._ctx(url, doc, job.default_currency)
        out = []
        for raw in result.records:
            normalized = normalize_record(raw, schema, ctx)
            confidence: dict[str, float] = {}
            flags: list[str] = []
            for spec in schema.fields:
                res = normalized[spec.name]
                conf = res.confidence * 0.95
                if not direct_mod.grounded(raw.get(spec.name), page_text, spec.type):
                    conf = min(conf, 0.3)
                    flags.append(f"not_on_page:{spec.name}")
                confidence[spec.name] = round(conf, 2)
            extra = sorted(set(raw) - set(schema.names))
            if extra:
                flags.append("dropped_extra_fields:" + ",".join(extra[:5]))
            record = self._assemble(
                normalized,
                schema,
                url,
                job,
                provenance={
                    "url": url,
                    "strategy": "direct",
                    "model": self.model.label,
                    "fields": {k: {"raw": _short(raw.get(k))} for k in schema.names if raw.get(k) not in (None, "")},
                },
                confidence=confidence,
                flags=flags,
            )
            out.append(record)
        return out

    def _assemble(
        self,
        normalized: dict[str, FieldResult],
        schema: Schema,
        url: str,
        job: JobConfig,
        *,
        provenance: dict[str, Any],
        confidence: dict[str, float],
        flags: list[str] | None = None,
    ) -> dict[str, Any]:
        data = {name: result.value for name, result in normalized.items()}
        if job.pii_guard:
            before = self.pii.removed
            data = guard_record(data, schema, site_host=host_of(url), report=self.pii)
            if self.pii.removed > before:
                flags = [*(flags or []), "pii_removed"]
        notes = {name: result.notes for name, result in normalized.items() if result.notes}
        filled = [confidence[n] for n in schema.names if data.get(n) not in (None, "", [])]
        return {
            "id": uuid.uuid4().hex[:12],
            "data": data,
            "confidence": confidence,
            "record_confidence": round(sum(filled) / len(filled), 2) if filled else 0.0,
            "provenance": provenance,
            "flags": list(flags or []),
            "notes": notes,
        }

    # ------------------------------------------------------------------------------------------------ result

    def _result(
        self,
        schema: Schema | None,
        records: list[dict[str, Any]],
        pages: list[PageInfo],
        extractors: list[ExtractorState],
        started: float,
        rejected: list[dict[str, Any]],
        *,
        error: str | None = None,
    ) -> JobResult:
        final: list[dict[str, Any]] = []
        sparse = duplicates = 0
        flagged: list[dict[str, Any]] = []
        if schema is not None and records:
            fills = [fill_ratio(r["data"], schema) for r in records]
            typical = median(fills) if fills else 0.0
            kept = []
            for record in records:
                if is_sparse(record["data"], schema, typical):
                    sparse += 1
                    continue
                record["_key"] = record_key(record["data"], schema)
                kept.append(record)
            deduped = dedupe(kept, schema)
            duplicates = deduped.duplicates
            final = deduped.records
            for anomaly in anomalies([r["data"] for r in final], schema, factor=self.settings.anomaly_factor):
                final[anomaly.index]["flags"].append(f"anomaly:{anomaly.field}")
                final[anomaly.index]["notes"].setdefault(anomaly.field, []).append(anomaly.reason)
                flagged.append({"key": final[anomaly.index]["_key"], "field": anomaly.field, "reason": anomaly.reason})
            for record in final:
                record["key"] = record.pop("_key")
                record.pop("_page", None)
            if sparse:
                self.emit(
                    "sparse_dropped",
                    f"{sparse} row(s) that were not records dropped (totals, headers, empty items)",
                    sparse=sparse,
                )
            if duplicates:
                self.emit(
                    "deduplicated", f"{duplicates} duplicate record(s) merged across pages", duplicates=duplicates
                )
            for item in flagged:
                self.emit(
                    "anomaly",
                    f"Anomaly in {item['field']} of {item['key']!r}: {item['reason']}",
                    level="warning",
                    **item,
                )
            if self.pii.removed:
                self.emit(
                    "pii_removed",
                    f"PII guard removed {self.pii.removed} email address(es) or phone number(s)",
                    level="warning",
                    removed=self.pii.removed,
                    fields=sorted(set(self.pii.samples)),
                )
        politeness = politeness_report(self.request_log, self.limiter, pages)
        stats = {
            "pages": len(pages),
            "pages_ok": sum(1 for p in pages if 200 <= p.status < 300),
            "pages_unchanged": sum(1 for p in pages if p.unchanged),
            "records": len(final),
            "errors": sum(1 for p in pages if p.error),
            "robots_blocked": sum(1 for p in pages if p.error == "disallowed by robots.txt"),
            "duplicates_merged": duplicates,
            "sparse_dropped": sparse,
            "anomalies": len(flagged),
            "pii_removed": self.pii.removed,
            "injection_findings": len(self.findings),
            "browser_pages": sum(1 for p in pages if p.via == "browser"),
            "llm": self.stats.as_dict(),
            "seconds": round(time.monotonic() - started, 2),
            "strategies": sorted({p.strategy for p in pages if p.strategy}),
        }
        self.emit(
            "job_finished",
            f"{len(final)} records from {len(pages)} pages, {len(self.stats.calls)} model call(s)",
            stats=stats,
        )
        return JobResult(schema, final, pages, extractors, self.events, stats, politeness, self.stats, rejected, error)


def politeness_report(request_log: RequestLog, limiter: RateLimiter, pages: list[PageInfo]) -> dict[str, Any]:
    hosts: dict[str, Any] = {}
    for host, entries in request_log.per_host().items():
        page_entries = [e for e in entries if e.kind != "subresource"]
        hosts[host] = {
            "requests": len(page_entries),
            "subresources": len(entries) - len(page_entries),
            "robots_fetches": sum(1 for e in entries if e.kind == "robots"),
            "min_gap_s": None if (gap := request_log.min_gap(host)) is None else round(gap, 3),
            "configured_interval_s": limiter.interval(host),
            "max_requests_per_s_10s": round(request_log.max_rate(host), 3),
        }
    return {"hosts": hosts, "robots_blocked": [p.url for p in pages if p.error == "disallowed by robots.txt"]}


def pdf_text(body: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(body))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # malformed PDFs are common; the page is skipped, not the job
        log.warning("pdf.error", error=type(exc).__name__)
        return ""


def _short(value: Any, limit: int = 160) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 1] + "…"
    if isinstance(value, list):
        return [_short(v, 80) for v in value[:10]]
    return value


def default_source(settings: Settings) -> SyntheticSource:
    if settings.synthetic_web_url:
        return HttpSource(settings.synthetic_web_url, layout=settings.synthetic_layout)
    return DiskSource(Path(settings.synthetic_web_dir), settings.synthetic_layout)

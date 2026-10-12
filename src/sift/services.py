"""Service layer shared by the API, the worker and the CLI: submit, run, preview, re-crawl and export jobs."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from sift.config import Settings, StrategyKind
from sift.crawl.fetch import DiskSource, HttpSource, SyntheticSource
from sift.crawl.urls import canonicalize, is_http
from sift.engine import Engine, Event, ExtractorState, JobConfig, JobResult
from sift.exports import send_webhook, to_bytes, to_database
from sift.extract.dom import parse
from sift.llm.base import ChatModel
from sift.llm.factory import model_for
from sift.logging_config import get_logger
from sift.schema.infer import propose
from sift.schema.model import Schema
from sift.store import Store

log = get_logger(__name__)


class JobRequest(BaseModel):
    start_urls: list[str] = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=4000)
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    name: str = ""
    strategy: StrategyKind = "auto"
    max_pages: int = Field(default=60, ge=1, le=2000)
    max_depth: int = Field(default=4, ge=0, le=20)
    heal: bool = True
    browser: str = "auto"
    default_currency: str | None = None
    webhook_url: str | None = None
    export_table: bool = False
    synthetic_layout: str | None = None
    """v1 | v2: which layout of the synthetic web this job sees (demo and evaluation only)."""

    model_config = {"populate_by_name": True}

    @field_validator("start_urls")
    @classmethod
    def _urls(cls, value: list[str]) -> list[str]:
        out = []
        for url in value:
            url = url.strip()
            if not url:
                continue
            if "://" not in url:
                url = "https://" + url
            if not is_http(url):
                raise ValueError(f"not an http(s) URL: {url}")
            out.append(canonicalize(url))
        if not out:
            raise ValueError("at least one start URL")
        return out

    @field_validator("browser")
    @classmethod
    def _browser(cls, value: str) -> str:
        if value not in {"auto", "always", "never"}:
            raise ValueError("browser must be auto, always or never")
        return value


class Services:
    def __init__(
        self,
        settings: Settings,
        store: Store | None = None,
        *,
        model: ChatModel | None = None,
        source: SyntheticSource | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or Store(settings.database_url)
        self._model = model
        self._source = source

    def model(self, tag: str) -> ChatModel:
        return self._model if self._model is not None else model_for(self.settings, tag=tag)

    def source(self, layout: str | None = None) -> SyntheticSource:
        if self._source is not None and layout is None:
            return self._source
        layout = layout or self.settings.synthetic_layout
        if self.settings.synthetic_web_url:
            return HttpSource(self.settings.synthetic_web_url, layout=layout)
        return DiskSource(Path(self.settings.synthetic_web_dir), layout)

    # ------------------------------------------------------------------------------------------------ jobs

    def submit(self, request: JobRequest, *, parent_id: str | None = None) -> str:
        if request.schema_ is not None:
            Schema.model_validate(request.schema_)  # fail fast on an invalid edited schema
        options = request.model_dump(exclude={"start_urls", "description", "schema_", "name"})
        job_id = self.store.create_job(
            start_urls=request.start_urls,
            description=request.description,
            schema=request.schema_,
            options=options,
            name=request.name,
            parent_id=parent_id,
        )
        self.store.add_event(
            job_id,
            ts=_ts(),
            kind="job_queued",
            level="info",
            message=f"Queued: {', '.join(request.start_urls[:3])}",
            data={},
        )
        return job_id

    def cancel(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if job and job["status"] in {"queued", "running"}:
            self.store.update_job(job_id, status="cancelled" if job["status"] == "queued" else "cancelling")

    def _config(self, job: dict[str, Any]) -> JobConfig:
        options = job.get("options") or {}
        config = JobConfig(
            start_urls=list(job["start_urls"]),
            description=job.get("description") or "",
            schema=Schema.model_validate(job["schema"]) if job.get("schema") else None,
            strategy=options.get("strategy", "auto"),
            max_pages=int(options.get("max_pages", self.settings.max_pages)),
            max_depth=int(options.get("max_depth", self.settings.max_depth)),
            heal=bool(options.get("heal", True)),
            browser=options.get("browser", self.settings.browser),
            default_currency=options.get("default_currency"),
            injection_guard=self.settings.injection_guard,
            pii_guard=self.settings.pii_guard,
        )
        parent_id = job.get("parent_id")
        if parent_id:
            # A re-crawl: reuse the parent's extractors (latest version per host and kind), skip unchanged pages and
            # verify any re-learned rules against the parent's records.
            latest: dict[tuple[str, str], dict[str, Any]] = {}
            for row in self.store.extractors(parent_id):
                latest[(row["host"], row["kind"])] = row["state"]
            config.extractors = [ExtractorState.from_dict(state) for state in latest.values()]
            parent_records = self.store.records(parent_id)
            config.reference_records = [r["data"] for r in parent_records[:200]]
            by_page: dict[str, list[dict[str, Any]]] = {}
            for r in parent_records:
                page = (r.get("provenance") or {}).get("url")
                if page:
                    by_page.setdefault(page, []).append(
                        {
                            k: r[k]
                            for k in ("id", "data", "confidence", "record_confidence", "provenance", "flags", "notes")
                        }
                    )
            config.previous_page_records = by_page
            config.previous_hashes = {
                p["url"]: p["content_hash"] for p in self.store.pages(parent_id) if p["content_hash"]
            }
        return config

    def run(self, job_id: str) -> JobResult | None:
        job = self.store.get_job(job_id)
        if job is None:
            return None
        if job["status"] != "running":
            self.store.update_job(job_id, status="running", started_at=dt.datetime.now(dt.UTC))
        config = self._config(job)
        progress: dict[str, Any] = {"pages": 0, "records": 0, "llm_calls": 0, "errors": 0}

        def sink(event: Event) -> None:
            self.store.add_event(
                job_id, ts=event.ts, kind=event.kind, level=event.level, message=event.message, data=event.data
            )
            if event.kind == "page_done":
                progress.update(
                    pages=event.data.get("pages", 0),
                    records=event.data.get("total_records", 0),
                    llm_calls=event.data.get("llm_calls", 0),
                )
                self.store.update_job(job_id, progress=dict(progress))
            elif event.level in {"warning", "error"} and event.kind in {"fetch_error", "extract_error"}:
                progress["errors"] += 1

        def stop() -> bool:
            current = self.store.get_job(job_id)
            return bool(current and current["status"] == "cancelling")

        layout = (job.get("options") or {}).get("synthetic_layout")
        engine = Engine(self.settings, self.model(f"job:{job_id}"), sink=sink, source=self.source(layout), stop=stop)
        try:
            result = engine.run(config)
        except Exception as exc:
            log.exception("job.failed", job_id=job_id)
            self.store.update_job(
                job_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}"[:2000],
                finished_at=dt.datetime.now(dt.UTC),
            )
            self.store.add_event(
                job_id,
                ts=_ts(),
                kind="job_failed",
                level="error",
                message=f"{type(exc).__name__}: {str(exc)[:300]}",
                data={},
            )
            return None
        self.save(job_id, result)
        final = self.store.get_job(job_id)
        status = "failed" if result.error else ("cancelled" if final and final["status"] == "cancelling" else "done")
        self.store.update_job(
            job_id,
            status=status,
            error=result.error,
            schema=result.schema.model_dump(mode="json", exclude_defaults=True) if result.schema else job.get("schema"),
            stats=result.stats,
            politeness=result.politeness,
            rejected_fields=result.rejected_fields,
            model=engine.model.label,
            progress={
                "pages": result.stats["pages"],
                "records": result.stats["records"],
                "llm_calls": len(result.calls.calls),
                "errors": result.stats["errors"],
            },
            finished_at=dt.datetime.now(dt.UTC),
        )
        options = job.get("options") or {}
        if status == "done" and result.schema is not None:
            webhook = options.get("webhook_url") or self.settings.webhook_url
            if webhook:
                try:
                    outcome = send_webhook(webhook, job, result.records, secret=self.settings.webhook_secret)
                    self.store.add_event(
                        job_id,
                        ts=_ts(),
                        kind="webhook",
                        level="info",
                        message=f"Webhook: {outcome['sent']} record(s) sent",
                        data=outcome,
                    )
                except Exception as exc:
                    self.store.add_event(
                        job_id,
                        ts=_ts(),
                        kind="webhook",
                        level="error",
                        message=f"Webhook failed: {type(exc).__name__}",
                        data={},
                    )
            if options.get("export_table"):
                outcome = to_database(result.records, result.schema, self.settings.database_url, job_id=job_id)
                self.store.add_event(
                    job_id,
                    ts=_ts(),
                    kind="export_table",
                    level="info",
                    message=f"Upserted {outcome['rows']} row(s) into {outcome['table']}",
                    data=outcome,
                )
        return result

    def save(self, job_id: str, result: JobResult) -> None:
        record_rows = [
            {
                "id": f"{job_id[-6:]}_{i:05d}",
                "key": r.get("key", ""),
                "data": r["data"],
                "confidence": r.get("confidence", {}),
                "record_confidence": r.get("record_confidence", 0.0),
                "provenance": r.get("provenance", {}),
                "flags": r.get("flags", []),
                "notes": r.get("notes", {}),
                "position": i,
            }
            for i, r in enumerate(result.records)
        ]
        page_rows = [dict(p.__dict__) for p in result.pages]
        extractor_rows = [
            {
                "id": e.id,
                "version": e.version,
                "host": e.host,
                "kind": e.kind,
                "origin": e.origin,
                "parent": e.parent,
                "accepted": e.accepted,
                "calls": e.calls,
                "state": e.as_dict(),
            }
            for e in result.extractors
        ]
        self.store.save_results(job_id, record_rows=record_rows, page_rows=page_rows, extractor_rows=extractor_rows)

    def recrawl(self, job_id: str, *, synthetic_layout: str | None = None) -> str:
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        options = dict(job.get("options") or {})
        if synthetic_layout:
            options["synthetic_layout"] = synthetic_layout
        request = JobRequest.model_validate(
            {
                "start_urls": job["start_urls"],
                "description": job.get("description") or "",
                "schema": job.get("schema"),
                "name": f"{job['name']} (re-crawl)",
                **options,
            }
        )
        return self.submit(request, parent_id=job_id)

    # ------------------------------------------------------------------------------------------------ builder

    def propose_schema(self, url: str, description: str) -> dict[str, Any]:
        request = JobRequest(start_urls=[url], description=description)
        engine = Engine(self.settings, self.model("propose"), source=self.source())
        try:
            target = request.start_urls[0]
            if not engine.http.allowed(target):
                return {"schema": None, "error": f"robots.txt disallows {target}", "rejected": []}
            _, doc, pdf = engine.fetch(target, JobConfig(start_urls=[target]), {})
            sample = (
                doc
                if doc is not None
                else (parse(f"<html><body><pre>{pdf[:20000]}</pre></body></html>") if pdf else None)
            )
            proposal = propose(
                engine.model,
                description,
                sample=sample,
                sample_url=target,
                guard=self.settings.injection_guard,
                max_tokens=self.settings.llm_max_tokens,
            )
        finally:
            engine.close()
        return {
            "schema": proposal.schema.model_dump(mode="json", exclude_defaults=True) if proposal.schema else None,
            "json_schema": proposal.schema.json_schema() if proposal.schema else None,
            "rejected": proposal.rejected,
            "injection_findings": [f.__dict__ for f in proposal.findings],
            "error": proposal.error,
            "llm_calls": engine.stats.as_dict(),
        }

    def preview(
        self, url: str, description: str, schema: dict[str, Any] | None, *, strategy: StrategyKind = "auto"
    ) -> dict[str, Any]:
        """The first page only, synchronously: the job builder's live preview table with provenance."""
        config = JobConfig(
            start_urls=[canonicalize(url)],
            description=description,
            schema=Schema.model_validate(schema) if schema else None,
            strategy=strategy,
            max_pages=1,
            follow_links=False,
            injection_guard=self.settings.injection_guard,
        )
        engine = Engine(self.settings, self.model("preview"), source=self.source())
        result = engine.run(config)
        return {
            "schema": result.schema.model_dump(mode="json", exclude_defaults=True) if result.schema else None,
            "records": result.records[:50],
            "events": [e.__dict__ for e in result.events],
            "stats": result.stats,
            "extractors": [e.as_dict() for e in result.extractors],
            "rejected": result.rejected_fields,
            "error": result.error,
        }

    # ------------------------------------------------------------------------------------------------ reads

    def export(self, job_id: str, fmt: str) -> tuple[bytes, str]:
        job = self.store.get_job(job_id)
        if job is None or not job.get("schema"):
            raise KeyError(job_id)
        records = self.store.records(job_id, limit=1_000_000)
        return to_bytes(records, Schema.model_validate(job["schema"]), fmt)

    def timeline(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.events(
            kinds=["learned", "layout_changed", "healed", "heal_failed", "learn_failed", "strategy"], limit=limit
        )

    def evaluation(self) -> dict[str, Any]:
        path = self.settings.results_dir / "summary.json"
        if not path.exists():
            return {"available": False}
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        data["available"] = True
        return data


def _ts() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")

"""REST API (FastAPI): jobs, the job builder (schema proposal and live preview), records with filters and provenance,
pages, extractor versions, events, the healing timeline, the audit log, exports and the evaluation summary.

Jobs run in the worker (`sift worker`), or in this process with `sift serve --inline-worker`."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sift import __version__
from sift.config import Settings, StrategyKind, get_settings
from sift.crawl.fetch import browser_available
from sift.exports import FORMATS, send_webhook, to_database
from sift.schema.model import Schema
from sift.services import JobRequest, Services
from sift.synthetic.generator import load_truth


class ProposeRequest(BaseModel):
    url: str
    description: str = Field(min_length=3, max_length=4000)


class PreviewRequest(BaseModel):
    url: str
    description: str = ""
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    strategy: StrategyKind = "auto"
    model_config = {"populate_by_name": True}


class RecrawlRequest(BaseModel):
    synthetic_layout: str | None = None


class WebhookRequest(BaseModel):
    url: str


def _filter_records(
    rows: list[dict[str, Any]],
    *,
    q: str | None,
    flag: str | None,
    min_confidence: float | None,
    field: str | None,
    value: str | None,
) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if q and q.lower() not in json.dumps(row["data"], ensure_ascii=False).lower():
            continue
        if flag and not any(f == flag or f.startswith(flag + ":") for f in row.get("flags") or []):
            continue
        if min_confidence is not None and float(row.get("record_confidence") or 0) < min_confidence:
            continue
        if field and value is not None:
            cell = row["data"].get(field)
            if isinstance(cell, dict):
                cell = cell.get("amount")
            if str(cell).lower() != value.lower():
                continue
        out.append(row)
    return out


def create_app(
    settings: Settings | None = None, services: Services | None = None, *, inline_worker: bool = False
) -> FastAPI:
    settings = settings or get_settings()
    svc = services or Services(settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = None
        if settings.seed_demo:
            from sift.demo import ensure_demo

            await asyncio.to_thread(ensure_demo, svc)
        if inline_worker:
            from sift.worker import worker_loop

            task = asyncio.create_task(worker_loop(svc))
        yield
        if task is not None:
            task.cancel()

    app = FastAPI(
        title="Sift API",
        version=__version__,
        lifespan=lifespan,
        description="Describe the data you want, get clean structured JSON from any website.",
    )
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

    def auth(authorization: str | None = Header(default=None)) -> None:
        if settings.api_token and authorization != f"Bearer {settings.api_token}":
            raise HTTPException(401, "missing or wrong bearer token")

    def job_or_404(job_id: str) -> dict[str, Any]:
        job = svc.store.get_job(job_id)
        if job is None:
            raise HTTPException(404, f"no job {job_id}")
        return job

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/config", dependencies=[Depends(auth)])
    def config() -> dict[str, Any]:
        return {
            "provider": settings.llm_provider,
            "model": svc.model("config").label,
            "require_free_models": settings.free_only,
            "browser": settings.browser,
            "browser_available": browser_available(settings.browser_executable),
            "respect_robots": settings.respect_robots,
            "min_seconds_per_domain": settings.min_seconds_per_domain,
            "user_agent": settings.user_agent,
            "pii_guard": settings.pii_guard,
            "injection_guard": settings.injection_guard,
            "today": settings.crawl_today().isoformat(),
        }

    @app.get("/api/sites", dependencies=[Depends(auth)])
    def sites() -> list[dict[str, Any]]:
        try:
            truth = load_truth(settings.synthetic_web_dir)
        except FileNotFoundError:
            return []
        return [
            {k: s[k] for k in ("id", "name", "vertical", "mode", "start_url", "intent", "traps")}
            | {"records": len(s["records"])}
            for s in truth["sites"]
        ]

    @app.post("/api/schema/propose", dependencies=[Depends(auth)])
    async def propose_schema(body: ProposeRequest) -> dict[str, Any]:
        return await asyncio.to_thread(svc.propose_schema, body.url, body.description)

    @app.post("/api/preview", dependencies=[Depends(auth)])
    async def preview(body: PreviewRequest) -> dict[str, Any]:
        if body.schema_ is not None:
            try:
                Schema.model_validate(body.schema_)
            except ValueError as exc:
                raise HTTPException(422, f"invalid schema: {exc}") from exc
        return await asyncio.to_thread(svc.preview, body.url, body.description, body.schema_, strategy=body.strategy)

    @app.post("/api/jobs", status_code=201, dependencies=[Depends(auth)])
    def create_job(body: JobRequest) -> dict[str, str]:
        try:
            return {"id": svc.submit(body)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/jobs", dependencies=[Depends(auth)])
    def list_jobs(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        return svc.store.list_jobs(limit)

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(auth)])
    def get_job(job_id: str) -> dict[str, Any]:
        job = job_or_404(job_id)
        job["record_count"] = svc.store.count_records(job_id)
        return job

    @app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(auth)])
    def cancel(job_id: str) -> dict[str, Any]:
        job_or_404(job_id)
        svc.cancel(job_id)
        return job_or_404(job_id)

    @app.post("/api/jobs/{job_id}/recrawl", status_code=201, dependencies=[Depends(auth)])
    def recrawl(job_id: str, body: RecrawlRequest | None = None) -> dict[str, str]:
        job_or_404(job_id)
        return {"id": svc.recrawl(job_id, synthetic_layout=body.synthetic_layout if body else None)}

    @app.delete("/api/jobs/{job_id}", status_code=204, dependencies=[Depends(auth)])
    def delete_job(job_id: str) -> Response:
        job_or_404(job_id)
        svc.store.delete_job(job_id)
        return Response(status_code=204)

    @app.get("/api/jobs/{job_id}/records", dependencies=[Depends(auth)])
    def get_records(
        job_id: str,
        q: str | None = None,
        flag: str | None = None,
        min_confidence: float | None = Query(None, ge=0, le=1),
        field: str | None = None,
        value: str | None = None,
        limit: int = Query(200, ge=1, le=5000),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        job_or_404(job_id)
        rows = _filter_records(
            svc.store.records(job_id, limit=100_000),
            q=q,
            flag=flag,
            min_confidence=min_confidence,
            field=field,
            value=value,
        )
        return {"total": len(rows), "records": rows[offset : offset + limit]}

    @app.get("/api/jobs/{job_id}/pages", dependencies=[Depends(auth)])
    def get_pages(job_id: str) -> list[dict[str, Any]]:
        job_or_404(job_id)
        return svc.store.pages(job_id)

    @app.get("/api/jobs/{job_id}/events", dependencies=[Depends(auth)])
    def get_events(job_id: str, after: int = 0, limit: int = Query(500, ge=1, le=5000)) -> list[dict[str, Any]]:
        job_or_404(job_id)
        return svc.store.events(job_id=job_id, after_id=after, limit=limit)

    @app.get("/api/jobs/{job_id}/extractors", dependencies=[Depends(auth)])
    def get_extractors(job_id: str) -> list[dict[str, Any]]:
        job_or_404(job_id)
        return svc.store.extractors(job_id)

    @app.get("/api/jobs/{job_id}/export", dependencies=[Depends(auth)])
    def export(job_id: str, format: str = Query("json", pattern="^(json|jsonl|csv|parquet)$")) -> Response:
        job = job_or_404(job_id)
        if not job.get("schema"):
            raise HTTPException(409, "the job has no schema yet")
        body, media = svc.export(job_id, format)
        filename = f"{job_id}.{format}"
        return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/jobs/{job_id}/webhook", dependencies=[Depends(auth)])
    def webhook(job_id: str, body: WebhookRequest) -> dict[str, Any]:
        job = job_or_404(job_id)
        return send_webhook(body.url, job, svc.store.records(job_id, limit=1_000_000), secret=settings.webhook_secret)

    @app.post("/api/jobs/{job_id}/export-table", dependencies=[Depends(auth)])
    def export_table(job_id: str) -> dict[str, Any]:
        job = job_or_404(job_id)
        if not job.get("schema"):
            raise HTTPException(409, "the job has no schema yet")
        return to_database(
            svc.store.records(job_id, limit=1_000_000),
            Schema.model_validate(job["schema"]),
            settings.database_url,
            job_id=job_id,
        )

    @app.get("/api/timeline", dependencies=[Depends(auth)])
    def timeline(limit: int = Query(200, ge=1, le=2000)) -> list[dict[str, Any]]:
        return svc.timeline(limit)

    @app.get("/api/audit", dependencies=[Depends(auth)])
    def audit(
        level: str | None = None,
        kind: str | None = None,
        job_id: str | None = None,
        limit: int = Query(300, ge=1, le=5000),
    ) -> list[dict[str, Any]]:
        return svc.store.events(job_id=job_id, kinds=[kind] if kind else None, level=level, limit=limit)

    @app.get("/api/evaluation", dependencies=[Depends(auth)])
    def evaluation() -> dict[str, Any]:
        return svc.evaluation()

    @app.get("/api/formats")
    def formats() -> list[str]:
        return list(FORMATS)

    return app


def create_default_app() -> FastAPI:
    import os

    from sift.logging_config import configure_logging

    configure_logging(os.environ.get("LOG_LEVEL", "INFO"), os.environ.get("LOG_FORMAT", "console"))
    return create_app(inline_worker=os.environ.get("SIFT_INLINE_WORKER", "").lower() in {"1", "true", "yes"})

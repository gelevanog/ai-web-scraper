"""Storage with SQLAlchemy Core: SQLite on a laptop, PostgreSQL in Docker. Jobs, pages, records with provenance,
extractor versions, and an event log that doubles as the audit trail and the healing timeline."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Engine,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection

metadata = MetaData()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


jobs = Table(
    "sift_jobs",
    metadata,
    Column("id", String(24), primary_key=True),
    Column("name", String(200), nullable=False, default=""),
    Column("status", String(20), nullable=False, default="queued", index=True),
    Column("start_urls", JSON, nullable=False),
    Column("description", Text, nullable=False, default=""),
    Column("schema", JSON),
    Column("options", JSON, nullable=False, default=dict),
    Column("stats", JSON, nullable=False, default=dict),
    Column("politeness", JSON, nullable=False, default=dict),
    Column("rejected_fields", JSON, nullable=False, default=list),
    Column("progress", JSON, nullable=False, default=dict),
    Column("model", String(200), nullable=False, default=""),
    Column("parent_id", String(24)),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
)

pages = Table(
    "sift_pages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", String(24), nullable=False, index=True),
    Column("url", Text, nullable=False),
    Column("status", Integer, nullable=False),
    Column("via", String(20), nullable=False, default="http"),
    Column("role", String(20), nullable=False, default=""),
    Column("depth", Integer, nullable=False, default=0),
    Column("strategy", String(20), nullable=False, default=""),
    Column("extractor", String(60)),
    Column("records", Integer, nullable=False, default=0),
    Column("content_hash", String(64), nullable=False, default=""),
    Column("fingerprint", String(20), nullable=False, default=""),
    Column("similarity", Float),
    Column("unchanged", Boolean, nullable=False, default=False),
    Column("error", Text),
    Column("ms", Float, nullable=False, default=0.0),
)

records = Table(
    "sift_records",
    metadata,
    Column("id", String(24), primary_key=True),
    Column("job_id", String(24), nullable=False, index=True),
    Column("key", Text, nullable=False, default=""),
    Column("data", JSON, nullable=False),
    Column("confidence", JSON, nullable=False, default=dict),
    Column("record_confidence", Float, nullable=False, default=0.0),
    Column("provenance", JSON, nullable=False, default=dict),
    Column("flags", JSON, nullable=False, default=list),
    Column("notes", JSON, nullable=False, default=dict),
    Column("position", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
)

extractors = Table(
    "sift_extractors",
    metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("job_id", String(24), nullable=False, index=True),
    Column("id", String(24), nullable=False),
    Column("version", Integer, nullable=False, default=1),
    Column("host", String(200), nullable=False),
    Column("kind", String(10), nullable=False, default="list"),
    Column("origin", String(20), nullable=False, default="learned"),
    Column("parent", String(40)),
    Column("accepted", Boolean, nullable=False, default=True),
    Column("calls", Integer, nullable=False, default=0),
    Column("state", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
)

events = Table(
    "sift_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", String(24), index=True),
    Column("ts", String(40), nullable=False),
    Column("kind", String(40), nullable=False, index=True),
    Column("level", String(10), nullable=False, default="info"),
    Column("message", Text, nullable=False),
    Column("data", JSON, nullable=False, default=dict),
)


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:10]


class Store:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite:///"):
            path = Path(url.removeprefix("sqlite:///"))
            if str(path) not in {"", ":memory:"}:
                path.parent.mkdir(parents=True, exist_ok=True)
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
        kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine: Engine = create_engine(url, **kwargs)
        self.dialect = self.engine.dialect.name
        metadata.create_all(self.engine)

    @contextmanager
    def tx(self) -> Iterator[Connection]:
        with self.engine.begin() as conn:
            yield conn

    # ---------------------------------------------------------------------------------------------- jobs

    def create_job(
        self,
        *,
        start_urls: list[str],
        description: str,
        schema: dict[str, Any] | None,
        options: dict[str, Any],
        name: str = "",
        parent_id: str | None = None,
        status: str = "queued",
    ) -> str:
        job_id = new_id("job_")
        with self.tx() as conn:
            conn.execute(
                insert(jobs).values(
                    id=job_id,
                    name=name or (start_urls[0] if start_urls else "job"),
                    status=status,
                    start_urls=start_urls,
                    description=description,
                    schema=schema,
                    options=options,
                    stats={},
                    politeness={},
                    rejected_fields=[],
                    progress={},
                    parent_id=parent_id,
                    created_at=_now(),
                )
            )
        return job_id

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.tx() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
        return _jsonable(dict(row)) if row else None

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.tx() as conn:
            rows = conn.execute(select(jobs).order_by(jobs.c.created_at.desc()).limit(limit)).mappings().all()
        return [_jsonable(dict(r)) for r in rows]

    def update_job(self, job_id: str, **values: Any) -> None:
        with self.tx() as conn:
            conn.execute(update(jobs).where(jobs.c.id == job_id).values(**values))

    def claim_job(self) -> str | None:
        """Atomically move the oldest runnable queued job to running (safe with several workers on PostgreSQL). A
        re-crawl waits until its parent job has finished."""
        with self.tx() as conn:
            parent = jobs.alias("parent")
            waiting = select(parent.c.id).where(
                parent.c.id == jobs.c.parent_id, parent.c.status.in_(["queued", "running", "cancelling"])
            )
            query = (
                select(jobs.c.id)
                .where(jobs.c.status == "queued", ~waiting.exists())
                .order_by(jobs.c.created_at)
                .limit(1)
            )
            if self.dialect == "postgresql":
                query = query.with_for_update(skip_locked=True, of=jobs)
            job_id = conn.execute(query).scalar()
            if job_id is None:
                return None
            claimed = conn.execute(
                update(jobs)
                .where(jobs.c.id == job_id, jobs.c.status == "queued")
                .values(status="running", started_at=_now())
            )
            return str(job_id) if claimed.rowcount else None

    def delete_job(self, job_id: str) -> None:
        with self.tx() as conn:
            for table in (records, pages, extractors, events):
                conn.execute(delete(table).where(table.c.job_id == job_id))
            conn.execute(delete(jobs).where(jobs.c.id == job_id))

    # ---------------------------------------------------------------------------------------------- results

    def add_event(
        self, job_id: str | None, *, ts: str, kind: str, level: str, message: str, data: dict[str, Any]
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                insert(events).values(
                    job_id=job_id, ts=ts, kind=kind, level=level, message=message, data=_jsonable(data)
                )
            )

    def save_results(
        self,
        job_id: str,
        *,
        record_rows: list[dict[str, Any]],
        page_rows: list[dict[str, Any]],
        extractor_rows: list[dict[str, Any]],
    ) -> None:
        with self.tx() as conn:
            for table in (records, pages, extractors):
                conn.execute(delete(table).where(table.c.job_id == job_id))
            if record_rows:
                conn.execute(insert(records), [_jsonable({**r, "job_id": job_id}) for r in record_rows])
            if page_rows:
                conn.execute(insert(pages), [_jsonable({**p, "job_id": job_id}) for p in page_rows])
            if extractor_rows:
                conn.execute(insert(extractors), [_jsonable({**e, "job_id": job_id}) for e in extractor_rows])

    def records(self, job_id: str, *, limit: int = 5000, offset: int = 0) -> list[dict[str, Any]]:
        with self.tx() as conn:
            rows = (
                conn.execute(
                    select(records)
                    .where(records.c.job_id == job_id)
                    .order_by(records.c.position)
                    .limit(limit)
                    .offset(offset)
                )
                .mappings()
                .all()
            )
        return [_jsonable(dict(r)) for r in rows]

    def count_records(self, job_id: str) -> int:
        with self.tx() as conn:
            return int(
                conn.execute(select(func.count()).select_from(records).where(records.c.job_id == job_id)).scalar() or 0
            )

    def pages(self, job_id: str) -> list[dict[str, Any]]:
        with self.tx() as conn:
            rows = conn.execute(select(pages).where(pages.c.job_id == job_id).order_by(pages.c.id)).mappings().all()
        return [_jsonable(dict(r)) for r in rows]

    def extractors(self, job_id: str | None = None) -> list[dict[str, Any]]:
        query = select(extractors).order_by(extractors.c.pk)
        if job_id is not None:
            query = query.where(extractors.c.job_id == job_id)
        with self.tx() as conn:
            rows = conn.execute(query).mappings().all()
        return [_jsonable(dict(r)) for r in rows]

    def events(
        self,
        *,
        job_id: str | None = None,
        kinds: list[str] | None = None,
        level: str | None = None,
        limit: int = 500,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        query = select(events).where(events.c.id > after_id).order_by(events.c.id.desc()).limit(limit)
        if job_id is not None:
            query = query.where(events.c.job_id == job_id)
        if kinds:
            query = query.where(events.c.kind.in_(kinds))
        if level:
            query = query.where(events.c.level == level)
        with self.tx() as conn:
            rows = conn.execute(query).mappings().all()
        return [_jsonable(dict(r)) for r in reversed(rows)]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return value

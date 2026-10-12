"""Exports: JSON, JSON Lines, CSV, Parquet, a signed webhook and a typed database table.

JSON and JSON Lines keep everything (values, per-field confidence, provenance, flags). CSV and Parquet are flat:
a money field becomes `<field>` (amount) and `<field>_currency`; lists are joined with "; " in CSV and kept as lists
in Parquet; objects are JSON strings. The database export creates one typed table per schema (text, bigint, double,
numeric, date, boolean, JSON) and upserts by record key, so a re-crawl updates rows instead of duplicating them."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
from typing import Any

import httpx
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    MetaData,
    Numeric,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from sift.schema.model import FieldType, Schema

FORMATS = ("json", "jsonl", "csv", "parquet")


def _full(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": record.get("key"),
        "data": record.get("data"),
        "confidence": record.get("confidence"),
        "record_confidence": record.get("record_confidence"),
        "provenance": record.get("provenance"),
        "flags": record.get("flags"),
    }


def flatten(record: dict[str, Any], schema: Schema) -> dict[str, Any]:
    data = record.get("data") or {}
    row: dict[str, Any] = {}
    for spec in schema.fields:
        value = data.get(spec.name)
        if spec.type == FieldType.money:
            row[spec.name] = value.get("amount") if isinstance(value, dict) else None
            row[f"{spec.name}_currency"] = value.get("currency") if isinstance(value, dict) else None
        elif spec.type == FieldType.object:
            row[spec.name] = json.dumps(value, ensure_ascii=False) if value is not None else None
        elif spec.type == FieldType.list:
            if spec.items is not None and spec.items.type == FieldType.object:
                row[spec.name] = json.dumps(value, ensure_ascii=False) if value else None
            else:
                row[spec.name] = value if value else None
        else:
            row[spec.name] = value
    provenance = record.get("provenance") or {}
    row["_source_url"] = provenance.get("url")
    row["_strategy"] = provenance.get("strategy")
    row["_confidence"] = record.get("record_confidence")
    row["_flags"] = ";".join(record.get("flags") or [])
    return row


def to_bytes(records: list[dict[str, Any]], schema: Schema, fmt: str) -> tuple[bytes, str]:
    """(body, media type)."""
    if fmt == "json":
        return json.dumps([_full(r) for r in records], ensure_ascii=False, indent=1).encode(), "application/json"
    if fmt == "jsonl":
        return "".join(
            json.dumps(_full(r), ensure_ascii=False) + "\n" for r in records
        ).encode(), "application/x-ndjson"
    rows = [flatten(r, schema) for r in records]
    if fmt == "csv":
        buffer = io.StringIO()
        columns = list(rows[0]) if rows else [f.name for f in schema.fields]
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "; ".join(map(str, v)) if isinstance(v, list) else v for k, v in row.items()})
        return buffer.getvalue().encode(), "text/csv"
    if fmt == "parquet":
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = (
            pa.Table.from_pylist(rows) if rows else pa.table({f.name: pa.array([], pa.string()) for f in schema.fields})
        )
        sink = io.BytesIO()
        pq.write_table(table, sink)
        return sink.getvalue(), "application/vnd.apache.parquet"
    raise ValueError(f"unknown format {fmt!r} (one of {', '.join(FORMATS)})")


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def send_webhook(
    url: str,
    job: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    secret: str = "",
    batch: int = 500,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """POSTs the records in batches as JSON; with a secret each request carries X-Sift-Signature (HMAC-SHA256)."""
    sent = 0
    statuses: list[int] = []
    with httpx.Client(timeout=30, transport=transport) as client:
        for start in range(0, max(len(records), 1), batch):
            chunk = records[start : start + batch]
            body = json.dumps(
                {
                    "job": {"id": job.get("id"), "name": job.get("name")},
                    "offset": start,
                    "total": len(records),
                    "records": [_full(r) for r in chunk],
                },
                ensure_ascii=False,
            ).encode()
            headers = {"Content-Type": "application/json", "User-Agent": "Sift/0.1 webhook"}
            if secret:
                headers["X-Sift-Signature"] = sign(body, secret)
            response = client.post(url, content=body, headers=headers)
            statuses.append(response.status_code)
            if response.status_code >= 400:
                break
            sent += len(chunk)
    return {"sent": sent, "statuses": statuses}


def table_name(schema: Schema) -> str:
    return "sift_" + re.sub(r"[^a-z0-9_]+", "_", schema.name.lower()).strip("_")[:40]


def build_table(schema: Schema, metadata: MetaData, name: str | None = None) -> Table:
    columns: list[Column[Any]] = [Column("_key", Text, primary_key=True)]
    for spec in schema.fields:
        if spec.type == FieldType.money:
            columns += [Column(spec.name, Numeric(14, 2)), Column(f"{spec.name}_currency", Text)]
        elif spec.type == FieldType.integer:
            columns.append(Column(spec.name, BigInteger))
        elif spec.type == FieldType.number:
            columns.append(Column(spec.name, Float))
        elif spec.type == FieldType.boolean:
            columns.append(Column(spec.name, Boolean))
        elif spec.type == FieldType.date:
            columns.append(Column(spec.name, Date))
        elif spec.type in {FieldType.list, FieldType.object}:
            columns.append(Column(spec.name, JSON))
        else:
            columns.append(Column(spec.name, Text))
    columns += [
        Column("_job_id", Text),
        Column("_source_url", Text),
        Column("_confidence", Float),
        Column("_flags", JSON),
        Column("_extracted_at", DateTime(timezone=True)),
    ]
    return Table(name or table_name(schema), metadata, *columns)


def to_database(
    records: list[dict[str, Any]], schema: Schema, url: str, *, job_id: str, table: str | None = None
) -> dict[str, Any]:
    """Creates the typed table if needed and upserts every record by key (PostgreSQL or SQLite)."""
    import datetime as dt

    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    engine = create_engine(url, future=True)
    metadata = MetaData()
    target = build_table(schema, metadata, table)
    metadata.create_all(engine)
    now = dt.datetime.now(dt.UTC)
    rows = []
    for record in records:
        data = record.get("data") or {}
        row: dict[str, Any] = {"_key": record.get("key") or record.get("id")}
        for spec in schema.fields:
            value = data.get(spec.name)
            if spec.type == FieldType.money:
                row[spec.name] = value.get("amount") if isinstance(value, dict) else None
                row[f"{spec.name}_currency"] = value.get("currency") if isinstance(value, dict) else None
            elif spec.type == FieldType.date:
                row[spec.name] = dt.date.fromisoformat(value) if isinstance(value, str) else None
            else:
                row[spec.name] = value
        row.update(
            _job_id=job_id,
            _source_url=(record.get("provenance") or {}).get("url"),
            _confidence=record.get("record_confidence"),
            _flags=record.get("flags") or [],
            _extracted_at=now,
        )
        rows.append(row)
    if rows:
        insert_fn = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
        statement = insert_fn(target)
        statement = statement.on_conflict_do_update(
            index_elements=["_key"],
            set_={c.name: statement.excluded[c.name] for c in target.columns if c.name != "_key"},
        )
        with engine.begin() as conn:
            conn.execute(statement, rows)
    engine.dispose()
    return {"table": target.name, "rows": len(rows), "dialect": engine.dialect.name}

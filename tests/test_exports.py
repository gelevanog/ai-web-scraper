from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
from sqlalchemy import create_engine, text

from sift.exports import send_webhook, sign, to_bytes, to_database
from sift.schema.model import FieldSpec, FieldType, Schema

SCHEMA = Schema(
    name="product",
    fields=[
        FieldSpec(name="name", type=FieldType.string, required=True, key=True),
        FieldSpec(name="price", type=FieldType.money),
        FieldSpec(name="released", type=FieldType.date),
        FieldSpec(name="tags", type=FieldType.list, items=FieldSpec(name="t", type=FieldType.string)),
        FieldSpec(name="in_stock", type=FieldType.boolean),
    ],
)


def record(name: str, amount: float) -> dict[str, Any]:
    return {
        "key": name.lower(),
        "data": {
            "name": name,
            "price": {"amount": amount, "currency": "USD"},
            "released": "2026-09-28",
            "tags": ["a", "b"],
            "in_stock": True,
        },
        "confidence": {"name": 1.0},
        "record_confidence": 0.97,
        "provenance": {"url": f"https://s.example/{name}", "strategy": "learned"},
        "flags": ["anomaly:price"] if amount > 1000 else [],
    }


RECORDS = [record("Lamp", 59.99), record("Chair", 129.0), record("Desk", 12900.0)]


def test_json_and_jsonl_keep_provenance() -> None:
    body, media = to_bytes(RECORDS, SCHEMA, "json")
    assert media == "application/json"
    data = json.loads(body)
    assert data[0]["provenance"]["url"] == "https://s.example/Lamp" and data[2]["flags"] == ["anomaly:price"]
    lines = to_bytes(RECORDS, SCHEMA, "jsonl")[0].decode().splitlines()
    assert len(lines) == 3 and json.loads(lines[1])["data"]["name"] == "Chair"


def test_csv_is_flat() -> None:
    body, _ = to_bytes(RECORDS, SCHEMA, "csv")
    rows = list(csv.DictReader(io.StringIO(body.decode())))
    assert rows[0]["price"] == "59.99" and rows[0]["price_currency"] == "USD" and rows[0]["tags"] == "a; b"
    assert rows[0]["_source_url"] == "https://s.example/Lamp" and rows[2]["_flags"] == "anomaly:price"


def test_parquet_round_trip() -> None:
    body, _ = to_bytes(RECORDS, SCHEMA, "parquet")
    table = pq.read_table(io.BytesIO(body))
    assert table.num_rows == 3 and table.column("price").to_pylist() == [59.99, 129.0, 12900.0]
    assert table.column("tags").to_pylist()[0] == ["a", "b"]


def test_webhook_batches_and_signs() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    secret = "whsec-" + "test"  # assembled at runtime
    outcome = send_webhook(
        "https://hooks.example/sift",
        {"id": "job_1", "name": "n"},
        RECORDS,
        secret=secret,
        batch=2,
        transport=httpx.MockTransport(handler),
    )
    assert outcome == {"sent": 3, "statuses": [200, 200]}
    first = json.loads(seen[0].content)
    assert first["total"] == 3 and len(first["records"]) == 2 and first["job"]["id"] == "job_1"
    assert seen[0].headers["x-sift-signature"] == sign(seen[0].content, secret)


def test_database_table_is_typed_and_upserted(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'out.db'}"
    first = to_database(RECORDS, SCHEMA, url, job_id="job_1")
    assert first == {"table": "sift_product", "rows": 3, "dialect": "sqlite"}
    changed = [record("Lamp", 49.0)]
    to_database(changed, SCHEMA, url, job_id="job_2")
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT _key, price, price_currency, released, _job_id FROM sift_product ORDER BY _key")
        ).all()
    assert len(rows) == 3
    lamp = next(r for r in rows if r[0] == "lamp")
    assert float(lamp[1]) == 49.0 and lamp[2] == "USD" and str(lamp[3]) == "2026-09-28" and lamp[4] == "job_2"

"""Storage and the typed-table export against PostgreSQL (TEST_DATABASE_URL; CI runs a service container). Skipped
without a database unless REQUIRE_TEST_DB=1, in which case a missing database fails the run."""

from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from sift.exports import to_database
from sift.schema.model import Schema
from sift.store import Store

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.postgres

if not URL:
    if os.environ.get("REQUIRE_TEST_DB") == "1":
        raise RuntimeError("REQUIRE_TEST_DB=1 but TEST_DATABASE_URL is not set")
    pytest.skip("TEST_DATABASE_URL not set", allow_module_level=True)


def test_store_round_trip_and_claim() -> None:
    store = Store(URL)
    job_id = store.create_job(start_urls=["https://a.example/"], description="d", schema=None, options={})
    child = store.create_job(
        start_urls=["https://a.example/"], description="d", schema=None, options={}, parent_id=job_id
    )
    assert store.claim_job() == job_id
    assert store.claim_job() is None  # the re-crawl waits for its parent
    store.update_job(job_id, status="done")
    assert store.claim_job() == child
    store.add_event(job_id, ts="2026-10-01T00:00:00Z", kind="learned", level="info", message="m", data={"x": 1})
    store.save_results(
        job_id,
        record_rows=[{"id": "r1", "key": "k", "data": {"a": 1}, "position": 0}],
        page_rows=[{"url": "u", "status": 200}],
        extractor_rows=[],
    )
    assert store.records(job_id)[0]["data"] == {"a": 1}
    assert store.events(job_id=job_id)[0]["data"] == {"x": 1}
    store.delete_job(job_id)
    store.delete_job(child)


def test_typed_table_upsert(site: Any) -> None:
    shop = site("shop-04")
    schema = Schema.model_validate(shop["schema"])
    records = [
        {
            "key": r["name"].lower(),
            "data": {**r, "price": {"amount": r["price"], "currency": "USD"}},
            "provenance": {"url": shop["start_url"]},
            "flags": [],
            "record_confidence": 1.0,
        }
        for r in shop["records"]
    ]
    result = to_database(records, schema, URL, job_id="job_pg", table="sift_product_test")
    assert result == {"table": "sift_product_test", "rows": len(records), "dialect": "postgresql"}
    to_database(records[:3], schema, URL, job_id="job_pg2", table="sift_product_test")
    engine = create_engine(URL.replace("postgresql://", "postgresql+psycopg://"))
    with engine.begin() as conn:
        count = conn.execute(text("SELECT count(*) FROM sift_product_test")).scalar()
        updated = conn.execute(text("SELECT count(*) FROM sift_product_test WHERE _job_id = 'job_pg2'")).scalar()
        conn.execute(text("DROP TABLE sift_product_test"))
    assert count == len(records) and updated == 3

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.api.app import create_app
from sift.config import Settings
from sift.fake_tasks import HANDLERS
from sift.llm.fake import FakeModel
from sift.services import Services


@pytest.fixture()
def svc(settings: Settings) -> Services:
    return Services(settings, model=FakeModel(HANDLERS))


@pytest.fixture()
def client(settings: Settings, svc: Services) -> TestClient:
    return TestClient(create_app(settings, svc))


def run_job(client: TestClient, svc: Services, body: dict[str, Any]) -> str:
    response = client.post("/api/jobs", json=body)
    assert response.status_code == 201, response.text
    job_id: str = response.json()["id"]
    assert svc.store.claim_job() == job_id
    svc.run(job_id)
    return job_id


def test_health_config_and_sites(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    config = client.get("/api/config").json()
    assert config["provider"] == "fake" and config["require_free_models"] is True and config["respect_robots"] is True
    sites = client.get("/api/sites").json()
    assert len(sites) == 47 and {"id", "start_url", "intent"} <= set(sites[0])


def test_job_lifecycle_records_filters_and_exports(client: TestClient, svc: Services, site: Any) -> None:
    shop = site("shop-01")
    job_id = run_job(
        client,
        svc,
        {"start_urls": [shop["start_url"]], "description": shop["intent"], "schema": shop["schema"], "name": "demo"},
    )
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done" and job["record_count"] == len(shop["records"])
    assert job["stats"]["llm"]["calls"] == 1 and job["progress"]["pages"] == job["stats"]["pages"]
    records = client.get(f"/api/jobs/{job_id}/records", params={"limit": 5}).json()
    assert records["total"] == len(shop["records"]) and len(records["records"]) == 5
    first = records["records"][0]
    assert first["provenance"]["url"] == shop["start_url"] and "price" in first["confidence"]
    anomalies = client.get(f"/api/jobs/{job_id}/records", params={"flag": "anomaly"}).json()
    assert anomalies["total"] == 1
    name = first["data"]["name"]
    assert client.get(f"/api/jobs/{job_id}/records", params={"q": name[:8]}).json()["total"] >= 1
    assert (
        client.get(f"/api/jobs/{job_id}/records", params={"field": "stock_status", "value": "in_stock"}).json()["total"]
        > 0
    )
    for fmt in ("json", "jsonl", "csv", "parquet"):
        response = client.get(f"/api/jobs/{job_id}/export", params={"format": fmt})
        assert response.status_code == 200 and response.content
    assert client.get(f"/api/jobs/{job_id}/export", params={"format": "xml"}).status_code == 422
    events = client.get(f"/api/jobs/{job_id}/events").json()
    kinds = [e["kind"] for e in events]
    assert {
        "job_queued",
        "job_started",
        "strategy",
        "learned",
        "page_done",
        "job_finished",
        "injection",
        "anomaly",
    } <= set(kinds)
    pages = client.get(f"/api/jobs/{job_id}/pages").json()
    assert pages and all(p["strategy"] == "learned" for p in pages)
    extractors = client.get(f"/api/jobs/{job_id}/extractors").json()
    assert extractors[0]["state"]["program"]["item"]
    table = client.post(f"/api/jobs/{job_id}/export-table").json()
    assert table["rows"] == len(shop["records"])


def test_recrawl_on_the_redesign_heals_and_shows_on_the_timeline(client: TestClient, svc: Services, site: Any) -> None:
    shop = site("shop-03")
    first = run_job(
        client, svc, {"start_urls": [shop["start_url"]], "schema": shop["schema"], "synthetic_layout": "v1"}
    )
    second = client.post(f"/api/jobs/{first}/recrawl", json={"synthetic_layout": "v2"}).json()["id"]
    assert svc.store.claim_job() == second
    svc.run(second)
    kinds = [e["kind"] for e in client.get("/api/timeline").json()]
    assert "layout_changed" in kinds and "healed" in kinds
    job = client.get(f"/api/jobs/{second}").json()
    assert job["parent_id"] == first and job["record_count"] == len(shop["records"])
    audit = client.get("/api/audit", params={"level": "warning"}).json()
    assert any(e["kind"] == "layout_changed" for e in audit)


def test_unchanged_recrawl_reuses_records(client: TestClient, svc: Services, site: Any) -> None:
    shop = site("shop-04")
    first = run_job(client, svc, {"start_urls": [shop["start_url"]], "schema": shop["schema"]})
    second = client.post(f"/api/jobs/{first}/recrawl").json()["id"]
    svc.store.claim_job()
    svc.run(second)
    pages = client.get(f"/api/jobs/{second}/pages").json()
    assert pages and all(p["unchanged"] for p in pages)
    assert client.get(f"/api/jobs/{second}").json()["stats"]["llm"]["calls"] == 0


def test_schema_proposal_and_preview(client: TestClient, site: Any) -> None:
    shop = site("shop-04")
    proposal = client.post("/api/schema/propose", json={"url": shop["start_url"], "description": shop["intent"]}).json()
    assert proposal["schema"] and proposal["json_schema"]["type"] == "object"
    preview = client.post(
        "/api/preview", json={"url": shop["start_url"], "description": shop["intent"], "schema": proposal["schema"]}
    ).json()
    assert preview["records"] and preview["stats"]["pages"] == 1
    assert preview["records"][0]["provenance"]["fields"]
    bad = client.post("/api/preview", json={"url": shop["start_url"], "schema": {"fields": []}})
    assert bad.status_code == 422


def test_validation_cancel_delete_and_auth(settings: Settings, svc: Services) -> None:
    client = TestClient(create_app(settings, svc))
    assert client.post("/api/jobs", json={"start_urls": ["ftp://x.example/"]}).status_code == 422
    assert client.post("/api/jobs", json={"start_urls": []}).status_code == 422
    created = client.post("/api/jobs", json={"start_urls": ["shop.example/catalog"]}).json()["id"]
    assert client.get(f"/api/jobs/{created}").json()["start_urls"] == ["https://shop.example/catalog"]
    assert client.post(f"/api/jobs/{created}/cancel").json()["status"] == "cancelled"
    assert client.delete(f"/api/jobs/{created}").status_code == 204
    assert client.get(f"/api/jobs/{created}").status_code == 404
    locked = TestClient(create_app(settings.model_copy(update={"api_token": "t0ken"}), svc))
    assert locked.get("/api/jobs").status_code == 401
    assert locked.get("/api/jobs", headers={"Authorization": "Bearer t0ken"}).status_code == 200
    assert locked.get("/health").status_code == 200


def test_evaluation_endpoint(client: TestClient, settings: Settings) -> None:
    assert client.get("/api/evaluation").json() == {"available": False}
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    (settings.results_dir / "summary.json").write_text(json.dumps({"main": "x"}))
    assert client.get("/api/evaluation").json() == {"main": "x", "available": True}


def test_sdk_against_the_app(settings: Settings, svc: Services, site: Any) -> None:
    import httpx

    from sift.sdk import SiftClient

    test_client = TestClient(create_app(settings, svc))

    class Bridge(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            response = test_client.request(
                request.method,
                str(request.url.raw_path.decode()),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(response.status_code, content=response.content, headers=response.headers)

    sdk = SiftClient("http://sift.test", transport=Bridge())
    shop = site("shop-04")
    job = sdk.create_job([shop["start_url"]], shop["intent"], schema=shop["schema"])
    assert svc.store.claim_job() == job["id"]
    svc.run(job["id"])
    assert sdk.wait(job["id"])["status"] == "done"
    assert sdk.records(job["id"], limit=3)["total"] == len(shop["records"])
    assert io.BytesIO(sdk.export(job["id"], "csv")).read().startswith(b"name,")

"""A small Python client for the Sift REST API.

from sift.sdk import SiftClient

sift = SiftClient("http://localhost:8000")
proposal = sift.propose_schema("https://books.toscrape.com/", "every book with title, price and rating")
job = sift.create_job(["https://books.toscrape.com/"], "every book with title, price and rating",
                      schema=proposal["schema"], max_pages=5)
finished = sift.wait(job["id"])
for record in sift.records(job["id"])["records"]:
    print(record["data"], record["provenance"]["url"])
"""

from __future__ import annotations

import time
from typing import Any

import httpx


class SiftClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        token: str | None = None,
        timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def _json(self, response: httpx.Response) -> Any:
        response.raise_for_status()
        return response.json()

    def propose_schema(self, url: str, description: str) -> dict[str, Any]:
        result: dict[str, Any] = self._json(
            self._client.post("/api/schema/propose", json={"url": url, "description": description})
        )
        return result

    def preview(self, url: str, description: str = "", schema: dict[str, Any] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = self._json(
            self._client.post("/api/preview", json={"url": url, "description": description, "schema": schema})
        )
        return result

    def create_job(
        self, start_urls: list[str], description: str = "", *, schema: dict[str, Any] | None = None, **options: Any
    ) -> dict[str, Any]:
        body = {"start_urls": start_urls, "description": description, "schema": schema, **options}
        result: dict[str, Any] = self._json(self._client.post("/api/jobs", json=body))
        return result

    def job(self, job_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._json(self._client.get(f"/api/jobs/{job_id}"))
        return result

    def wait(self, job_id: str, *, poll: float = 2.0, timeout: float = 3600.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            job = self.job(job_id)
            if job["status"] in {"done", "failed", "cancelled"} or time.monotonic() > deadline:
                return job
            time.sleep(poll)

    def records(self, job_id: str, **filters: Any) -> dict[str, Any]:
        result: dict[str, Any] = self._json(
            self._client.get(f"/api/jobs/{job_id}/records", params={k: v for k, v in filters.items() if v is not None})
        )
        return result

    def export(self, job_id: str, fmt: str = "jsonl") -> bytes:
        response = self._client.get(f"/api/jobs/{job_id}/export", params={"format": fmt})
        response.raise_for_status()
        return response.content

    def recrawl(self, job_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._json(self._client.post(f"/api/jobs/{job_id}/recrawl", json={}))
        return result

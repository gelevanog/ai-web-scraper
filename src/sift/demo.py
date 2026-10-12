"""First start: generate the synthetic web if it is missing and, on an empty database, queue a demo job and a
re-crawl of the same site after its redesign (v2), so the dashboard shows a crawl, a layout change and a heal."""

from __future__ import annotations

from sift.logging_config import get_logger
from sift.services import JobRequest, Services
from sift.synthetic.generator import generate, load_truth

log = get_logger(__name__)

DEMO_SITE = "shop-03"


def ensure_demo(svc: Services) -> None:
    root = svc.settings.synthetic_web_dir
    if not (root / "ground_truth.json").exists():
        manifest = generate(root, seed=svc.settings.demo_seed, today=svc.settings.crawl_today())
        log.info("demo.synthetic_web_generated", sites=manifest["sites"], records=manifest["records"])
    if svc.store.list_jobs(limit=1):
        return
    site = next(s for s in load_truth(root)["sites"] if s["id"] == DEMO_SITE)
    request = JobRequest(
        start_urls=[site["start_url"]],
        description=site["intent"],
        name=f"{site['name']} catalog (demo)",
        synthetic_layout="v1",
    )
    first = svc.submit(request)
    svc.recrawl(first, synthetic_layout="v2")
    log.info("demo.jobs_queued", site=DEMO_SITE)

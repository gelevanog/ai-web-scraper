"""The job worker: an asyncio loop that claims queued jobs and runs each in a thread (the crawl itself is
synchronous and polite: one site at a time per job, several jobs in parallel up to SIFT_WORKER_CONCURRENCY)."""

from __future__ import annotations

import asyncio

from sift.logging_config import get_logger
from sift.services import Services

log = get_logger(__name__)


async def worker_loop(svc: Services, *, once: bool = False) -> int:
    """Runs until cancelled (or, with `once`, until the queue is empty). Returns the number of jobs run."""
    limit = asyncio.Semaphore(max(1, svc.settings.worker_concurrency))
    running: set[asyncio.Task[None]] = set()
    done = 0

    async def run(job_id: str) -> None:
        nonlocal done
        try:
            log.info("worker.job_started", job_id=job_id)
            await asyncio.to_thread(svc.run, job_id)
            log.info("worker.job_finished", job_id=job_id)
        except Exception:
            log.exception("worker.job_crashed", job_id=job_id)
        finally:
            done += 1
            limit.release()

    while True:
        await limit.acquire()
        job_id = await asyncio.to_thread(svc.store.claim_job)
        if job_id is None:
            limit.release()
            if once and not running:
                break
            await asyncio.sleep(svc.settings.worker_poll_seconds)
            continue
        task = asyncio.create_task(run(job_id))
        running.add(task)
        task.add_done_callback(running.discard)
    return done

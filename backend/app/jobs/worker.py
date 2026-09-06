from __future__ import annotations

import asyncio
import logging

from redis import Redis

from app.config import settings
from app.database import SessionLocal
from app.jobs.service import execute_job
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


def run_worker() -> None:
    settings.validate()
    if settings.job_backend != "redis" or not settings.redis_url:
        raise RuntimeError("The production worker requires JOB_BACKEND=redis and REDIS_URL")
    configure_logging(settings.log_level)
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    logger.info("evaluation_worker_started", extra={"queue": settings.job_queue_name})
    while True:
        item = client.brpop(settings.job_queue_name, timeout=30)
        if item is None:
            continue
        _, job_id = item
        with SessionLocal() as db:
            asyncio.run(execute_job(job_id, db))


if __name__ == "__main__":
    run_worker()

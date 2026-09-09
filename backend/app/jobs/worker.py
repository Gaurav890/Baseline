from __future__ import annotations

import asyncio
import logging
import socket
from datetime import UTC, datetime
from uuid import uuid4

from redis import Redis
from sqlalchemy import or_, select

from app.config import settings
from app.database import SessionLocal
from app.jobs.service import execute_job
from app.logging_config import configure_logging
from app.models import EvaluationJob

logger = logging.getLogger(__name__)


def run_worker() -> None:
    settings.validate()
    if settings.job_backend != "redis" or not settings.redis_url:
        raise RuntimeError("The production worker requires JOB_BACKEND=redis and REDIS_URL")
    configure_logging(settings.log_level)
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    worker_id = f"{socket.gethostname()}:{uuid4()}"
    logger.info("evaluation_worker_started", extra={"queue": settings.job_queue_name})
    while True:
        item = client.brpop(settings.job_queue_name, timeout=30)
        if item is None:
            with SessionLocal() as db:
                recoverable = db.scalars(
                    select(EvaluationJob.id).where(
                        EvaluationJob.attempts < EvaluationJob.max_attempts,
                        or_(
                            EvaluationJob.status == "queued",
                            (EvaluationJob.status == "running")
                            & (EvaluationJob.lease_expires_at < datetime.now(UTC)),
                        ),
                    )
                ).all()
            for job_id in recoverable:
                client.lpush(settings.job_queue_name, job_id)
            continue
        _, job_id = item
        with SessionLocal() as db:
            job = asyncio.run(execute_job(job_id, db, worker_id=worker_id))
        if job is not None and job.status == "queued":
            client.lpush(settings.job_queue_name, job.id)


if __name__ == "__main__":
    run_worker()

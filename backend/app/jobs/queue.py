from __future__ import annotations

import asyncio
from typing import Protocol

from app.config import settings
from app.database import SessionLocal
from app.jobs.service import execute_job


class JobQueue(Protocol):
    name: str

    async def enqueue(self, job_id: str) -> None: ...

    def ready(self) -> bool: ...


class InlineJobQueue:
    name = "inline"

    async def enqueue(self, job_id: str) -> None:
        with SessionLocal() as db:
            await execute_job(job_id, db)

    def ready(self) -> bool:
        return True


class RedisJobQueue:
    name = "redis"

    def __init__(self, redis_url: str, queue_name: str) -> None:
        from redis import Redis

        self._client = Redis.from_url(redis_url, decode_responses=True)
        self._queue_name = queue_name

    async def enqueue(self, job_id: str) -> None:
        await asyncio.to_thread(self._client.lpush, self._queue_name, job_id)

    def ready(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False


_queue: JobQueue | None = None


def get_job_queue() -> JobQueue:
    global _queue
    if _queue is None:
        if settings.job_backend == "redis":
            if not settings.redis_url:
                raise RuntimeError("REDIS_URL is required when JOB_BACKEND=redis")
            _queue = RedisJobQueue(settings.redis_url, settings.job_queue_name)
        else:
            _queue = InlineJobQueue()
    return _queue

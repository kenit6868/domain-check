"""Bounded independent polling slots, with graceful drain and expiring health."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

HEALTH_PREFIX = 'domaincheck:worker-health:'
HEALTH_TTL = 20


@asynccontextmanager
async def publish_health(redis, job_type: str, *, state=lambda: 'ready'):
    key = f'{HEALTH_PREFIX}{job_type}:{uuid4().hex}'

    async def publish():
        while True:
            try:
                await redis.set(key, state(), ex=HEALTH_TTL)
            except Exception:
                # Markers expire during an outage; resume publishing after Redis
                # recovers, including for the long-lived Cloudflare RAM session.
                # Never log connection exceptions that may include credentials.
                pass
            await asyncio.sleep(5)

    # Confirm the health channel is usable before accepting work.
    await redis.set(key, state(), ex=HEALTH_TTL)
    task = asyncio.create_task(publish())
    try:
        yield task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        try:
            await redis.delete(key)
        except Exception:
            pass  # TTL removes the marker if Redis is temporarily unavailable.


async def run_slot(worker, redis, job_type: str, stop: asyncio.Event, *, poll_seconds: float,
                   once: bool = False):
    if hasattr(worker, 'should_drain'):
        worker.should_drain = stop.is_set
    async with publish_health(redis, job_type, state=lambda: 'draining' if stop.is_set() else 'ready') as health:
        while not stop.is_set():
            # Fail the service if its heartbeat task failed instead of silently
            # processing work while the web reports it offline.
            if health.done():
                health.result()
            claimed = await worker.run_next(f'{job_type}-{uuid4().hex}')
            if claimed is not None:
                print(f'[{job_type}] Job processed', flush=True)
            if once:
                return
            if claimed is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
                except TimeoutError:
                    pass

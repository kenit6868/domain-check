from __future__ import annotations

import argparse
import asyncio
import secrets

from redis.asyncio import Redis

from domaincheck.application.quick_reports import QuickReportWorker
from domaincheck.core.quick_report import LegacyQuickReportAdapter
from domaincheck.infrastructure.cache import ScopedRedisCache
from domaincheck.infrastructure.db.session import Database
from domaincheck.settings import get_settings


async def run(*, once: bool, poll_seconds: float) -> None:
    LegacyQuickReportAdapter.validate_runtime()
    settings = get_settings()
    database = Database(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    worker = QuickReportWorker(
        database.session_factory,
        ScopedRedisCache(redis),
        LegacyQuickReportAdapter(),
    )
    try:
        print("Quick Report worker ready", flush=True)
        while True:
            token = f"quick-report-{secrets.token_urlsafe(18)}"
            claimed = await worker.run_next(token)
            if claimed is not None:
                print(f"Quick Report operation processed: {claimed}", flush=True)
            if once:
                return
            if claimed is None:
                await asyncio.sleep(poll_seconds)
    finally:
        await redis.aclose()
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run durable Domain Check Tool workers")
    parser.add_argument("--once", action="store_true", help="Process at most one queued operation")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if not 0.1 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 0.1 and 60")
    try:
        asyncio.run(run(once=args.once, poll_seconds=args.poll_seconds))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

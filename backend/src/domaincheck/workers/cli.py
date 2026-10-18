from __future__ import annotations

import argparse
import asyncio
from domaincheck.core.review_capture import capture_review
import signal

from redis.asyncio import Redis

from domaincheck.application.quick_reports import QuickReportWorker
from domaincheck.application.domain_checks import DomainCheckWorker
from domaincheck.application.domain_worker import DomainPrecheckWorker
from domaincheck.core.worker_precheck import DomainWorkerPrecheckAdapter
from domaincheck.application.evidence import BrowserEvidenceWorker
from domaincheck.core.quick_report import DomainQuickReportAdapter
from domaincheck.core.investigation import DomainInvestigationAdapter
from domaincheck.core.evidence import EngineBrowserCaptureAdapter
from domaincheck.core.networking import EgressProxy
from domaincheck.infrastructure.cache import ScopedRedisCache
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.db.session import Database
from domaincheck.settings import get_settings
from domaincheck.application.worker_send import DomainSendWorker
from domaincheck.core.mail.sending import SmtpReportSender
from domaincheck.infrastructure.secrets import SecretEnvelope
from domaincheck.application.sent_copy import ImapSentCopyAdapter
from domaincheck.workers.runtime import run_slot


async def run(*, once: bool, poll_seconds: float, job_type: str = "all",
              concurrency: int | None = None, stop: asyncio.Event | None = None) -> None:
    if job_type in {'all', 'quick_report'}:
        DomainQuickReportAdapter.validate_runtime()
    if job_type in {"all", "check_domain"}:
        DomainInvestigationAdapter.validate_runtime()
    if job_type in {"all", "domain_worker_precheck"}:
        DomainWorkerPrecheckAdapter.validate_runtime()
    settings = get_settings()
    if job_type == "browser_evidence" and not settings.browser_capture_enabled:
        raise RuntimeError("Browser capture is disabled")
    if settings.browser_capture_enabled and job_type in {"all", "domain_worker_precheck", "browser_evidence"}:
        await asyncio.to_thread(EngineBrowserCaptureAdapter.validate_runtime)
    database = Database(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3)
    worker = QuickReportWorker(
        database.session_factory,
        ScopedRedisCache(redis),
        DomainQuickReportAdapter(),
    )
    domain_worker = DomainCheckWorker(database.session_factory, DomainInvestigationAdapter(),
        vt_api_key=settings.vt_api_key.get_secret_value() if settings.vt_api_key else "",
        gsb_api_key=settings.gsb_api_key.get_secret_value() if settings.gsb_api_key else "")
    capture_worker = None
    precheck_capture = None
    precheck_store = None
    if job_type in {"all", "domain_worker_precheck"} and settings.browser_capture_enabled:
        proxy = EgressProxy.parse(settings.browser_egress_proxy_url.get_secret_value()).playwright() if settings.browser_egress_proxy_url else None
        precheck_capture = EngineBrowserCaptureAdapter(browser_proxy=proxy)
        precheck_store = FilesystemArtifactStore(settings.artifact_root,
            max_file_bytes=settings.artifact_max_file_bytes,
            max_dimension=settings.artifact_max_dimension, max_pixels=settings.artifact_max_pixels)
    precheck_worker = DomainPrecheckWorker(database.session_factory, DomainWorkerPrecheckAdapter(),
                                         store=precheck_store, capture=precheck_capture,
                                         cloaking_capture=capture_review
                                         if precheck_capture is not None else None)
    send_store = FilesystemArtifactStore(settings.artifact_root)
    envelope = SecretEnvelope.from_base64_keys(
        {settings.secret_key_version: settings.secret_master_key.get_secret_value()}, settings.secret_key_version
    ) if settings.secret_master_key else None
    send_worker = DomainSendWorker(database.session_factory, send_store,
        SmtpReportSender(settings.mail_egress_endpoints), envelope,
        sent_copier=ImapSentCopyAdapter(settings.mail_egress_endpoints))
    if job_type in {'all', 'browser_evidence'} and settings.browser_capture_enabled:
        proxy = EgressProxy.parse(settings.browser_egress_proxy_url.get_secret_value()).playwright() if settings.browser_egress_proxy_url else None
        capture_worker = BrowserEvidenceWorker(database.session_factory,
            FilesystemArtifactStore(settings.artifact_root,
                max_file_bytes=settings.artifact_max_file_bytes,
                max_dimension=settings.artifact_max_dimension,
                max_pixels=settings.artifact_max_pixels), EngineBrowserCaptureAdapter(browser_proxy=proxy))
    try:
        from sqlalchemy import text
        async with database.engine.connect() as connection:
            await connection.execute(text('SELECT 1'))
        workers = {'quick_report': worker, 'check_domain': domain_worker,
                   'domain_worker_precheck': precheck_worker, 'domain_worker_send': send_worker}
        if capture_worker is not None:
            workers['browser_evidence'] = capture_worker
        if not once:
            for instance in workers.values():
                if hasattr(instance, 'items_per_turn'):
                    instance.items_per_turn = settings.worker_items_per_turn
        selected = settings.worker_slots() if job_type == 'all' else {job_type: settings.worker_slots()[job_type]}
        stop = stop or asyncio.Event()
        print(f'Worker ready: {selected}', flush=True)
        if once:
            # Retain the diagnostic CLI contract: at most one queued operation.
            from uuid import uuid4
            for name in selected:
                if await workers[name].run_next(f'{name}-{uuid4().hex}') is not None:
                    return
            return
        async with asyncio.TaskGroup() as tasks:
            for name, slots in selected.items():
                for _ in range(concurrency or slots):
                    tasks.create_task(run_slot(workers[name], redis, name, stop, poll_seconds=poll_seconds))
    finally:
        await redis.aclose()
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run durable Domain Check Tool workers")
    parser.add_argument("--once", action="store_true", help="Process at most one queued operation")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument('--concurrency', type=int, help='Override bounded slots per selected job type (1-8)')
    parser.add_argument("--job-type", choices=("all", "quick_report", "check_domain", "browser_evidence", "domain_worker_precheck", "domain_worker_send"), default="all")
    args = parser.parse_args()
    if not 0.1 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 0.1 and 60")
    if args.concurrency is not None and not 1 <= args.concurrency <= 8:
        parser.error('--concurrency must be between 1 and 8')

    async def serve():
        stop = asyncio.Event()
        def drain(*_):
            print('Worker draining: no new claims; waiting for active jobs.', flush=True)
            stop.set()
        previous = {sig: signal.signal(sig, drain) for sig in
                    {signal.SIGINT, signal.SIGTERM, *([signal.SIGBREAK] if hasattr(signal, 'SIGBREAK') else [])}}
        try:
            await run(once=args.once, poll_seconds=args.poll_seconds, job_type=args.job_type,
                      concurrency=args.concurrency, stop=stop)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

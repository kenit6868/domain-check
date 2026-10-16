from __future__ import annotations

import argparse
import asyncio
from domaincheck.core.review_capture import capture_review
import secrets

from redis.asyncio import Redis

from domaincheck.application.quick_reports import QuickReportWorker
from domaincheck.application.domain_checks import DomainCheckWorker
from domaincheck.application.domain_worker import DomainPrecheckWorker
from domaincheck.core.worker_precheck import LegacyWorkerPrecheckAdapter
from domaincheck.application.evidence import BrowserEvidenceWorker
from domaincheck.core.quick_report import LegacyQuickReportAdapter
from domaincheck.core.investigation import LegacyInvestigationAdapter
from domaincheck.core.evidence import LegacyBrowserCaptureAdapter
from domaincheck.core.networking import EgressProxy
from domaincheck.infrastructure.cache import ScopedRedisCache
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.db.session import Database
from domaincheck.settings import get_settings
from domaincheck.application.worker_send import DomainSendWorker
from domaincheck.core.mail.sending import SmtpReportSender
from domaincheck.infrastructure.secrets import SecretEnvelope
from domaincheck.application.sent_copy import LegacySentCopyAdapter


async def run(*, once: bool, poll_seconds: float, job_type: str = "all") -> None:
    LegacyQuickReportAdapter.validate_runtime()
    if job_type in {"all", "check_domain"}:
        LegacyInvestigationAdapter.validate_runtime()
    if job_type in {"all", "domain_worker_precheck"}:
        LegacyWorkerPrecheckAdapter.validate_runtime()
    settings = get_settings()
    if job_type == "browser_evidence" and not settings.browser_capture_enabled:
        raise RuntimeError("Browser capture is disabled")
    if settings.browser_capture_enabled and job_type in {"all", "domain_worker_precheck", "browser_evidence"}:
        await asyncio.to_thread(LegacyBrowserCaptureAdapter.validate_runtime)
    database = Database(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    worker = QuickReportWorker(
        database.session_factory,
        ScopedRedisCache(redis),
        LegacyQuickReportAdapter(),
    )
    domain_worker = DomainCheckWorker(database.session_factory, LegacyInvestigationAdapter(),
        vt_api_key=settings.vt_api_key.get_secret_value() if settings.vt_api_key else "",
        gsb_api_key=settings.gsb_api_key.get_secret_value() if settings.gsb_api_key else "")
    capture_worker = None
    precheck_capture = None
    precheck_store = None
    if job_type in {"all", "domain_worker_precheck"} and settings.browser_capture_enabled:
        proxy = EgressProxy.parse(settings.browser_egress_proxy_url.get_secret_value()).playwright() if settings.browser_egress_proxy_url else None
        precheck_capture = LegacyBrowserCaptureAdapter(browser_proxy=proxy)
        precheck_store = FilesystemArtifactStore(settings.artifact_root,
            max_file_bytes=settings.artifact_max_file_bytes,
            max_dimension=settings.artifact_max_dimension, max_pixels=settings.artifact_max_pixels)
    precheck_worker = DomainPrecheckWorker(database.session_factory, LegacyWorkerPrecheckAdapter(),
                                         store=precheck_store, capture=precheck_capture,
                                         cloaking_capture=capture_review
                                         if precheck_capture is not None else None)
    send_store = FilesystemArtifactStore(settings.artifact_root)
    envelope = SecretEnvelope.from_base64_keys(
        {settings.secret_key_version: settings.secret_master_key.get_secret_value()}, settings.secret_key_version
    ) if settings.secret_master_key else None
    send_worker = DomainSendWorker(database.session_factory, send_store,
        SmtpReportSender(settings.mail_egress_endpoints), envelope,
        sent_copier=LegacySentCopyAdapter(settings.mail_egress_endpoints))
    if job_type == "browser_evidence":
        proxy = EgressProxy.parse(settings.browser_egress_proxy_url.get_secret_value()).playwright() if settings.browser_egress_proxy_url else None
        capture_worker = BrowserEvidenceWorker(database.session_factory,
            FilesystemArtifactStore(settings.artifact_root,
                max_file_bytes=settings.artifact_max_file_bytes,
                max_dimension=settings.artifact_max_dimension,
                max_pixels=settings.artifact_max_pixels), LegacyBrowserCaptureAdapter(browser_proxy=proxy))
    try:
        print("Domain Check Tool worker ready", flush=True)
        while True:
            claimed = None
            if job_type in {"all", "quick_report"}:
                claimed = await worker.run_next(f"quick-report-{secrets.token_urlsafe(18)}")
            if claimed is None and job_type in {"all", "check_domain"}:
                claimed = await domain_worker.run_next(f"check-domain-{secrets.token_urlsafe(18)}")
            if claimed is None and job_type in {"all", "domain_worker_precheck"}:
                claimed = await precheck_worker.run_next(f"domain-worker-{secrets.token_urlsafe(18)}")
            if claimed is None and job_type in {'all', 'domain_worker_send'}:
                claimed = await send_worker.run_next(f'domain-send-{secrets.token_urlsafe(18)}')
            if claimed is None and settings.browser_capture_enabled and job_type == "browser_evidence":
                assert capture_worker is not None
                claimed = await capture_worker.run_next(f"browser-evidence-{secrets.token_urlsafe(18)}")
            if claimed is not None:
                print(f"Operation processed: {claimed}", flush=True)
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
    parser.add_argument("--job-type", choices=("all", "quick_report", "check_domain", "browser_evidence", "domain_worker_precheck", "domain_worker_send"), default="all")
    args = parser.parse_args()
    if not 0.1 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 0.1 and 60")
    try:
        asyncio.run(run(once=args.once, poll_seconds=args.poll_seconds, job_type=args.job_type))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from collections.abc import Callable
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domaincheck.application.jobs import JobItemInput, JobNotFound, JobService
from domaincheck.core.quick_report import (
    QUICK_REPORT_ENGINE_VERSION, QuickReportAdapter, sanitize_quick_report_result,
)
from domaincheck.infrastructure.cache import ScopedCacheKey, ScopedRedisCache, business_day
from domaincheck.infrastructure.db.models import Check, Job, JobItem, Target
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.infrastructure.db.repositories import JobRepository


class QuickReportNotFound(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class QuickReportOperation:
    job: Job
    checks: tuple[Check, ...]
    duplicates: tuple[str, ...]


def canonical_target(value: str) -> tuple[str, str]:
    raw = value.strip()
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Quick Report target must be an HTTP(S) URL or domain")
    host = parsed.hostname.lower().rstrip(".")
    if not host or len(host) > 253 or any(not label or len(label) > 63 for label in host.split(".")):
        raise ValueError("Quick Report target hostname is invalid")
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme.lower()}://{host}{port}{path}{query}", host


def quick_report_workflow_identity(canonical: str) -> str:
    """Keep the legacy Quick Report filter identity: scheme/port are not significant."""
    parsed = urlsplit(canonical)
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"https://{parsed.hostname or ''}{path}{query}"


class QuickReportService:
    def __init__(self, session: AsyncSession, *, owner_user_id: UUID, actor_user_id: UUID,
                 timezone: str, request_id: str, now: datetime) -> None:
        self._session = session
        self._owner = owner_user_id
        self._actor = actor_user_id
        self._timezone = timezone
        self._request_id = request_id
        self._now = now

    async def create(self, targets: list[str]) -> QuickReportOperation:
        if not 1 <= len(targets) <= 100:
            raise ValueError("Quick Report accepts between 1 and 100 targets")
        parsed: list[tuple[str, str]] = []
        duplicates: list[str] = []
        seen: set[str] = set()
        for value in targets:
            canonical, host = canonical_target(value)
            identity = hashlib.sha256(quick_report_workflow_identity(canonical).encode("utf-8")).hexdigest()
            if identity in seen:
                duplicates.append(value)
                continue
            seen.add(identity)
            parsed.append((canonical, host))
        if not parsed:
            raise ValueError("Quick Report contains no unique valid target")
        day = business_day(self._now, self._timezone)
        checks: list[Check] = []
        items: list[JobItemInput] = []
        for index, (canonical, host) in enumerate(parsed):
            target_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            digest = hashlib.sha256(quick_report_workflow_identity(canonical).encode("utf-8")).hexdigest()
            await self._session.execute(select(func.pg_advisory_xact_lock(
                int.from_bytes(hashlib.sha256(f"{self._owner}:{target_digest}".encode()).digest()[:8], "big", signed=True)
            )))
            target = await self._session.scalar(select(Target).where(
                Target.owner_user_id == self._owner,
                Target.identity_version == 1,
                Target.canonical_url_hash == target_digest,
            ).with_for_update())
            if target is None:
                target = Target(
                    owner_user_id=self._owner, full_url_identity=canonical,
                    canonical_url_hash=target_digest, hostname=host, identity_version=1,
                )
                self._session.add(target)
                await self._session.flush()
            else:
                target.last_seen_at = self._now
            check = Check(
                owner_user_id=self._owner, actor_user_id=self._actor, target_id=target.id,
                requested_url=canonical, workflow="quick_report", workflow_key=digest,
                domain=host, business_day=day, timezone=self._timezone,
                engine_version=QUICK_REPORT_ENGINE_VERSION, status="pending", result={},
                cache_provenance={},
            )
            self._session.add(check)
            await self._session.flush()
            checks.append(check)
            items.append(JobItemInput(digest, index, {"check_id": str(check.id)}))
        snapshot = await JobService(
            self._session, owner_user_id=self._owner, actor_user_id=self._actor,
            request_id=self._request_id,
        ).create_job(
            job_type="quick_report", business_day=day, timezone=self._timezone, items=items,
        )
        return QuickReportOperation(snapshot.job, tuple(checks), tuple(duplicates))

    async def daily_checks(self) -> tuple[Check, ...]:
        day = business_day(self._now, self._timezone)
        ranked = select(
            Check.id,
            func.row_number().over(partition_by=Check.workflow_key,
                order_by=(Check.created_at.desc(), Check.id.desc())).label('rank'),
            func.min(Check.created_at).over(partition_by=Check.workflow_key).label('first_seen'),
        ).where(Check.owner_user_id == self._owner, Check.workflow == 'quick_report',
            Check.business_day == day).subquery()
        return tuple(await self._session.scalars(select(Check).join(ranked, Check.id == ranked.c.id)
            .where(Check.owner_user_id == self._owner, ranked.c.rank == 1)
            .order_by(ranked.c.first_seen, Check.workflow_key)))

    async def get(self, job_id: UUID) -> QuickReportOperation:
        try:
            snapshot = await JobService(
                self._session, owner_user_id=self._owner, actor_user_id=self._actor,
                request_id=self._request_id,
            ).get_snapshot(job_id)
        except JobNotFound as exc:
            raise QuickReportNotFound("Quick Report operation not found") from exc
        if snapshot.job.job_type != "quick_report":
            raise QuickReportNotFound("Quick Report operation not found")
        check_ids = [UUID(item.result_metadata["check_id"]) for item in snapshot.items]
        rows = list(await self._session.scalars(select(Check).where(
            Check.owner_user_id == self._owner, Check.id.in_(check_ids),
        )))
        by_id = {row.id: row for row in rows}
        if len(by_id) != len(check_ids):
            raise QuickReportNotFound("Quick Report operation is incomplete")
        return QuickReportOperation(snapshot.job, tuple(by_id[item] for item in check_ids), ())


class QuickReportWorker:
    """Durable DB worker. Network work happens outside every DB transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], cache: ScopedRedisCache,
                 adapter: QuickReportAdapter, *, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 heartbeat_seconds: float = 10, job_type: str = "quick_report") -> None:
        self._sessions = session_factory
        self._cache = cache
        self._adapter = adapter
        self._now = now
        if not 0 < heartbeat_seconds < 30:
            raise ValueError("heartbeat_seconds must be between 0 and 30")
        self._heartbeat_seconds = heartbeat_seconds
        self._job_type = job_type
        self.should_drain = lambda: False
        self.items_per_turn = None

    async def run_next(self, worker_token: str) -> UUID | None:
        async with self._sessions() as session:
            async with session.begin():
                repo = JobRepository(session)
                await repo.recover_expired_stops(now=self._now(), job_types=frozenset({self._job_type}))
                job = await repo.claim_next(worker_token=worker_token, job_types=frozenset({self._job_type}),
                                            now=self._now(), fair_owner=True)
                if job is None:
                    return None
                await repo.recover_interrupted_items(job=job, worker_token=worker_token, now=self._now())
                items = await repo.list_claimed_items(job, worker_token=worker_token)
                job_id, owner_id = job.id, job.owner_user_id
                pending_ids = [item.id for item in items if item.state == "pending"]
        for index, item_id in enumerate(pending_ids):
            if self.should_drain() or (self.items_per_turn is not None and index >= self.items_per_turn):
                # Finish/checkpoint the current item, then release only pending
                # work for the replacement process. No user stop/reset/retry.
                async with self._sessions() as session, session.begin():
                    repo = JobRepository(session)
                    job = await repo.get_for_owner(owner_id, job_id)
                    if job and not job.stop_requested:
                        await repo.release_for_drain(job_id=job_id, worker_token=worker_token, now=self._now())
                        return job_id
            await self._run_item(owner_id, job_id, item_id, worker_token)
        async with self._sessions() as session:
            async with session.begin():
                repo = JobRepository(session)
                job = await repo.get_for_owner(owner_id, job_id)
                if job is None:
                    return job_id
                if job.stop_requested:
                    await repo.finalize_stop(owner_user_id=owner_id, job_id=job_id,
                        worker_token=worker_token, expected_version=job.version)
                    return job_id
                items = await repo.list_claimed_items(job, worker_token=worker_token)
                terminal = "completed" if all(item.state == "succeeded" for item in items) else (
                    "failed" if all(item.state == "failed" for item in items) else "partial"
                )
                await repo.finalize_job(
                    job_id=job_id, worker_token=worker_token, expected_version=job.version,
                    state=terminal, stage="completed", result_metadata={**(job.result_metadata or {}), "failed_count": sum(item.state == 'failed' for item in items)},
                    now=self._now(),
                )
        return job_id

    async def _run_item(self, owner_id: UUID, job_id: UUID, item_id: UUID, worker_token: str) -> str:
        async with self._sessions() as session:
            async with session.begin():
                repo = JobRepository(session)
                job = await repo.get_for_owner(owner_id, job_id)
                item = await OwnedRepository(session, JobItem, owner_id).get(item_id)
                if job is None or item is None or job.stop_requested:
                    return "stopped"
                started = await repo.start_item(
                    owner_user_id=owner_id, job_id=job_id, item_id=item_id,
                    worker_token=worker_token, expected_version=item.version,
                    stage="checking", now=self._now(),
                )
                if started is None:
                    return "stopped"
                check_id = UUID(started.result_metadata["check_id"])
                started_version = started.version
        outcome = await self._process_with_heartbeat(owner_id, job_id, check_id, worker_token)
        async with self._sessions() as session:
            async with session.begin():
                await JobRepository(session).checkpoint_item(
                    owner_user_id=owner_id, job_id=job_id, item_id=item_id,
                    worker_token=worker_token, expected_version=started_version,
                    state="succeeded" if outcome == "succeeded" else "failed",
                    stage="completed" if outcome == "succeeded" else "check_failed",
                    result_metadata={"check_id": str(check_id)}, now=self._now(),
                )
        return outcome

    async def _process_with_heartbeat(self, owner_id, job_id, check_id, worker_token):
        task = asyncio.create_task(self._process_check(owner_id, check_id))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=self._heartbeat_seconds)
                if done:
                    return await task
                async with self._sessions() as session, session.begin():
                    repo = JobRepository(session)
                    job = await OwnedRepository(session, Job, owner_id).get(job_id, for_update=True)
                    if job is None:
                        raise RuntimeError("Quick Report job lease lost")
                    renewed = await repo.heartbeat(job_id=job_id, worker_token=worker_token,
                        expected_version=job.version, now=self._now(), allow_stopping=True)
                    if renewed is None:
                        raise RuntimeError("Quick Report job lease lost or stopped")
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _process_check(self, owner_id: UUID, check_id: UUID) -> str:
        async with self._sessions() as session:
            async with session.begin():
                check = await OwnedRepository(session, Check, owner_id).get(check_id, for_update=True)
                if check is None:
                    return "failed"
                check.status = "running"
                check.version += 1
                requested, day, timezone, engine = (
                    check.requested_url, check.business_day, check.timezone, check.engine_version,
                )
        scope = ScopedCacheKey(owner_id, "quick-report", "check", f"{engine}:{requested}", day)
        cached = await self._cache.get_json(scope)
        try:
            result = sanitize_quick_report_result(cached) if isinstance(cached, dict) else sanitize_quick_report_result(
                await asyncio.to_thread(self._adapter.run, requested, {"cloaking_vantage_points": []})
            )
            status = "succeeded"
        except Exception as exc:
            result = {"_error": type(exc).__name__}
            status = "failed"
        async with self._sessions() as session:
            async with session.begin():
                check = await OwnedRepository(session, Check, owner_id).get(check_id, for_update=True)
                if check is None:
                    return "failed"
                check.status = status
                check.result = result
                check.cache_provenance = {
                    "reused": cached is not None,
                    "source": "redis" if cached is not None else "network",
                    "observed_at": self._now().isoformat(),
                }
                check.version += 1
        if status == "succeeded" and cached is None:
            await self._cache.set_json(scope, result, now=self._now(), timezone_name=timezone)
        return status

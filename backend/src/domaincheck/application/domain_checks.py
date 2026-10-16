"""Owned full investigation operations and their durable worker."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domaincheck.application.jobs import JobItemInput, JobNotFound, JobService
from domaincheck.application.quick_reports import QuickReportWorker
from domaincheck.core.investigation import ENGINE_VERSION, InvestigationAdapter, sanitize_result, validate_target
from domaincheck.infrastructure.cache import business_day
from domaincheck.infrastructure.db.models import Check, Job, Target, UserSettings
from domaincheck.infrastructure.db.owned import OwnedRepository


class DomainCheckNotFound(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DomainCheckOperation:
    job: Job
    checks: tuple[Check, ...]
    item_states: tuple[str, ...]


class DomainCheckService:
    def __init__(self, session: AsyncSession, *, owner_user_id: UUID, actor_user_id: UUID,
                 timezone: str, request_id: str, now: datetime) -> None:
        self.session = session
        self.owner = owner_user_id
        self.actor = actor_user_id
        self.timezone = timezone
        self.request_id = request_id
        self.now = now

    async def create(self, target: str, *, submit_vt: bool) -> DomainCheckOperation:
        if not isinstance(target, str):
            raise ValueError("Check Domain accepts one URL")
        parsed = [validate_target(target)]
        day = business_day(self.now, self.timezone)
        checks = []
        items = []
        for index, (url, host) in enumerate(parsed):
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
            await self.session.execute(select(func.pg_advisory_xact_lock(
                int.from_bytes(hashlib.sha256(f"{self.owner}:{digest}".encode()).digest()[:8], "big", signed=True)
            )))
            target = await self.session.scalar(select(Target).where(
                Target.owner_user_id == self.owner, Target.identity_version == 1,
                Target.canonical_url_hash == digest).with_for_update())
            if target is None:
                target = Target(owner_user_id=self.owner, full_url_identity=url,
                                canonical_url_hash=digest, hostname=host, identity_version=1)
                self.session.add(target)
                await self.session.flush()
            else:
                target.last_seen_at = self.now
            check = Check(owner_user_id=self.owner, actor_user_id=self.actor,
                          target_id=target.id, requested_url=url, workflow="check_domain",
                          workflow_key=digest, domain=host, business_day=day,
                          timezone=self.timezone, engine_version=ENGINE_VERSION,
                          status="pending", result={"submit_vt_requested": submit_vt}, cache_provenance={})
            self.session.add(check)
            await self.session.flush()
            checks.append(check)
            items.append(JobItemInput(digest, index, {"check_id": str(check.id)}))
        snapshot = await JobService(self.session, owner_user_id=self.owner,
            actor_user_id=self.actor, request_id=self.request_id).create_job(
                job_type="check_domain", business_day=day, timezone=self.timezone, items=items)
        return DomainCheckOperation(snapshot.job, tuple(checks),
                                    tuple(item.state for item in snapshot.items))

    async def get(self, operation_id: UUID) -> DomainCheckOperation:
        try:
            snapshot = await JobService(self.session, owner_user_id=self.owner,
                actor_user_id=self.actor, request_id=self.request_id).get_snapshot(operation_id)
        except JobNotFound as exc:
            raise DomainCheckNotFound("Check Domain operation not found") from exc
        if snapshot.job.job_type != "check_domain":
            raise DomainCheckNotFound("Check Domain operation not found")
        if len(snapshot.items) != 1:
            raise DomainCheckNotFound("Check Domain operation has invalid item count")
        ids = [UUID(item.result_metadata["check_id"]) for item in snapshot.items]
        rows = list(await self.session.scalars(select(Check).where(
            Check.owner_user_id == self.owner, Check.workflow == "check_domain", Check.id.in_(ids))))
        by_id = {row.id: row for row in rows}
        if len(by_id) != len(ids):
            raise DomainCheckNotFound("Check Domain operation is incomplete")
        return DomainCheckOperation(snapshot.job, tuple(by_id[check_id] for check_id in ids),
                                    tuple(item.state for item in snapshot.items))


class DomainCheckWorker(QuickReportWorker):
    """Runs full investigations with no Redis result reuse or legacy file writes."""
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], adapter: InvestigationAdapter,
                 *, vt_api_key: str = "", gsb_api_key: str = "", now=None) -> None:
        super().__init__(session_factory, None, adapter, job_type="check_domain",
                         now=now or (lambda: datetime.now(UTC)))
        self._vt_api_key = vt_api_key
        self._gsb_api_key = gsb_api_key

    async def _process_check(self, owner_id: UUID, check_id: UUID) -> str:
        async with self._sessions() as session, session.begin():
            check = await OwnedRepository(session, Check, owner_id).get(check_id, for_update=True)
            if check is None or check.workflow != "check_domain":
                return "failed"
            submit_vt = bool(check.result.get("submit_vt_requested"))
            check.status = "running"
            check.version += 1
            target = check.requested_url
            profile = await session.get(UserSettings, owner_id)
            config = {field: getattr(profile, field, "") if profile else "" for field in
                      ("brand_name", "contact_name", "contact_email", "company_name",
                       "business_registration_no", "signature_role")}
            config.update(vt_api_key=self._vt_api_key, gsb_api_key=self._gsb_api_key,
                          cloaking_vantage_points=[])
        try:
            if submit_vt and not self._vt_api_key.strip():
                raise RuntimeError("VirusTotal submission key is unavailable to the worker")
            raw = await asyncio.to_thread(self._adapter.run, target, submit_vt=submit_vt, config=config)
            result = sanitize_result(raw)
            status = "succeeded"
        except Exception as exc:
            result = {"_error": type(exc).__name__}
            status = "failed"
        async with self._sessions() as session, session.begin():
            check = await OwnedRepository(session, Check, owner_id).get(check_id, for_update=True)
            if check is None:
                return "failed"
            check.status = status
            check.result = result
            check.cache_provenance = {"reused": False, "source": "network",
                                      "observed_at": self._now().isoformat()}
            check.version += 1
        return status

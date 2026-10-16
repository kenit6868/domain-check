"""Durable owner-scoped preflight batches. This module cannot send email."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select

from domaincheck.application.jobs import JobItemInput, JobNotFound, JobService
from domaincheck.application.quick_reports import QuickReportWorker
from domaincheck.core.investigation import validate_target
from domaincheck.core.worker_precheck import ENGINE_VERSION, disposition, valid_recipients
from domaincheck.infrastructure.cache import business_day
from domaincheck.infrastructure.db.models import Artifact, CaseSource, Check, EvidenceSet, Job, ReviewCase, Target
from domaincheck.infrastructure.db.owned import OwnedRepository


class WorkerNotFound(ValueError):
    pass


@dataclass(frozen=True)
class WorkerOperation:
    job: Job
    checks: tuple
    items: tuple


class DomainWorkerService:
    def __init__(self, session, *, owner, actor, timezone, request_id, now):
        self.session, self.owner, self.actor = session, owner, actor
        self.timezone, self.request_id, self.now = timezone, request_id, now

    def jobs(self):
        return JobService(self.session, owner_user_id=self.owner, actor_user_id=self.actor,
                          request_id=self.request_id)

    async def create(self, targets: list[str], sender_account_ids: list[UUID] | None = None) -> WorkerOperation:
        if not 1 <= len(targets) <= 100:
            raise ValueError("Enter between 1 and 100 URLs")
        parsed = list(dict.fromkeys(validate_target(value) for value in targets))
        checks, inputs = [], []
        day = business_day(self.now, self.timezone)
        # Lock in hash order, while preserving operator order in job membership.
        for digest in sorted(hashlib.sha256(url.encode()).hexdigest() for url, _ in parsed):
            await self.session.execute(select(func.pg_advisory_xact_lock(int.from_bytes(
                hashlib.sha256(f"{self.owner}:{digest}".encode()).digest()[:8], "big", signed=True))))
        for index, (url, host) in enumerate(parsed):
            digest = hashlib.sha256(url.encode()).hexdigest()
            target = await self.session.scalar(select(Target).where(Target.owner_user_id == self.owner,
                Target.identity_version == 1, Target.canonical_url_hash == digest).with_for_update())
            if target is None:
                target = Target(owner_user_id=self.owner, full_url_identity=url,
                    canonical_url_hash=digest, hostname=host, identity_version=1)
                self.session.add(target)
                await self.session.flush()
            check = Check(owner_user_id=self.owner, actor_user_id=self.actor, target_id=target.id,
                requested_url=url, domain=host, workflow="domain_worker", workflow_key=digest,
                business_day=day, timezone=self.timezone, engine_version=ENGINE_VERSION,
                status="pending", result={}, cache_provenance={})
            self.session.add(check)
            await self.session.flush()
            checks.append(check)
            inputs.append(JobItemInput(digest, index, {"check_id": str(check.id)}))
        snapshot = await self.jobs().create_job(job_type="domain_worker_precheck", business_day=day,
                                               timezone=self.timezone, items=inputs,
                                               sender_account_ids=sender_account_ids)
        return WorkerOperation(snapshot.job, tuple(checks), snapshot.items)

    async def get(self, operation_id: UUID) -> WorkerOperation:
        try:
            snapshot = await self.jobs().get_snapshot(operation_id)
        except JobNotFound as exc:
            raise WorkerNotFound("Domain Worker operation not found") from exc
        if snapshot.job.job_type != "domain_worker_precheck":
            raise WorkerNotFound("Domain Worker operation not found")
        ids = [UUID(item.result_metadata["check_id"]) for item in snapshot.items]
        checks = list(await self.session.scalars(select(Check).where(Check.owner_user_id == self.owner,
            Check.workflow == "domain_worker", Check.id.in_(ids))))
        by_id = {check.id: check for check in checks}
        if len(by_id) != len(ids):
            raise WorkerNotFound("Domain Worker membership is incomplete")
        return WorkerOperation(snapshot.job, tuple(by_id[key] for key in ids), snapshot.items)


class DomainPrecheckWorker(QuickReportWorker):
    def __init__(self, sessions, adapter, *, store=None, capture=None, now=None):
        super().__init__(sessions, None, adapter, job_type="domain_worker_precheck",
                         now=now or (lambda: datetime.now(UTC)))
        self.store, self.capture = store, capture

    async def _save_evidence(self, owner, check_id, target, uploads, manifest):
        evidence_id = uuid4()
        stored = self.store.store_evidence_images(owner_user_id=owner, evidence_set_id=evidence_id,
            uploads=uploads, minimum_count=1, maximum_count=3)
        try:
            async with self._sessions() as session, session.begin():
                check = await OwnedRepository(session, Check, owner).get(check_id)
                if check is None or check.workflow != "domain_worker":
                    raise WorkerNotFound("Domain Worker check not found")
                safe_manifest = {**manifest, "source": "browser_capture", "version": 1,
                    "screenshots": [{"artifact_id": str(item.artifact_id), "sha256": item.sha256,
                                     "byte_size": item.byte_size} for item in stored]}
                session.add(EvidenceSet(id=evidence_id, owner_user_id=owner, check_id=check_id,
                    requested_url=target, capture_mode="browser_capture", manifest=safe_manifest))
                await session.flush()
                for item in stored:
                    session.add(Artifact(id=item.artifact_id, owner_user_id=owner, evidence_set_id=evidence_id,
                        kind="domain_worker:evidence", mime_type=item.mime_type, byte_size=item.byte_size,
                        sha256=item.sha256, storage_key=item.storage_key, immutable=True))
            return str(evidence_id)
        except Exception:
            for item in stored:
                self.store.delete(storage_key=item.storage_key)
            raise

    async def _process_check(self, owner_id: UUID, check_id: UUID) -> str:
        async with self._sessions() as session, session.begin():
            check = await OwnedRepository(session, Check, owner_id).get(check_id, for_update=True)
            if check is None or check.workflow != "domain_worker":
                return "failed"
            check.status = "running"
            check.version += 1
            target, domain = check.requested_url, check.domain
            from domaincheck.infrastructure.db.models import JobItem
            account_scope = await session.scalar(select(Job.account_scope).join(JobItem,
                (JobItem.job_id == Job.id) & (JobItem.owner_user_id == Job.owner_user_id)).where(
                Job.owner_user_id == owner_id, Job.job_type == 'domain_worker_precheck',
                JobItem.result_metadata['check_id'].astext == str(check_id))) or []
        try:
            result = await asyncio.to_thread(self._adapter.run, target, domain)
            result['recipients'] = valid_recipients(result.get('recipients') or [])
            result['disposition'] = disposition(result['recipients'], result.get('cloaking') or {})
            if result['disposition'] == 'evidence_review' and account_scope:
                from domaincheck.application.worker_send import delivery_key
                from domaincheck.infrastructure.db.models import Delivery
                async with self._sessions() as session:
                    day = (await OwnedRepository(session, Check, owner_id).get(check_id)).business_day
                    keys = [delivery_key(day, domain, recipient) for recipient in result['recipients']]
                    sent = list(await session.scalars(select(Delivery).where(Delivery.owner_user_id == owner_id,
                        Delivery.sender_account_id.in_([UUID(value) for value in account_scope]),
                        Delivery.business_key.in_(keys), Delivery.state == 'sent')))
                    if len({(row.sender_account_id, row.business_key) for row in sent}) == len(account_scope) * len(set(keys)):
                        result['disposition'] = 'excluded_already_sent'
            if result["disposition"] == "evidence_review":
                if self.capture is None or self.store is None:
                    result["evidence_reason"] = "capture_disabled"
                else:
                    try:
                        uploads, manifest = await asyncio.to_thread(self.capture.capture, target,
                            staging_root=self.store.root / ".staging")
                        if manifest.get("terminal"):
                            raise ValueError("terminal evidence")
                        result["evidence_set_id"] = await self._save_evidence(owner_id, check_id,
                            target, uploads, manifest)
                        result["disposition"] = "ready"
                    except Exception as exc:
                        from domaincheck.core.evidence import capture_failure_code
                        result["evidence_reason"] = "capture_failed"
                        result["evidence_failure_code"] = capture_failure_code(exc)
            status = "succeeded"
        except Exception:
            result, status = {"disposition": "precheck_error", "error_code": "precheck_failed"}, "failed"
        async with self._sessions() as session, session.begin():
            check = await OwnedRepository(session, Check, owner_id).get(check_id, for_update=True)
            if check is None:
                return "failed"
            if result["disposition"] in {"cloaking_review", "evidence_review"}:
                case_type = "cloaking" if result["disposition"] == "cloaking_review" else "domain_evidence"
                lock = f"{owner_id}:{case_type}:{check.business_day}:{check.workflow_key}"
                await session.execute(select(func.pg_advisory_xact_lock(int.from_bytes(
                    hashlib.sha256(lock.encode()).digest()[:8], "big", signed=True))))
                case = await session.scalar(select(ReviewCase).where(ReviewCase.owner_user_id == owner_id,
                    ReviewCase.case_type == case_type, ReviewCase.business_day == check.business_day,
                    ReviewCase.canonical_url_key == check.workflow_key).with_for_update())
                if case is None:
                    case = ReviewCase(owner_user_id=owner_id, case_type=case_type,
                        business_day=check.business_day, timezone=check.timezone, full_url=target,
                        canonical_url_key=check.workflow_key, state="pending", required_account_ids=account_scope)
                    session.add(case)
                    await session.flush()
                case.required_account_ids = sorted(set(case.required_account_ids or []) | set(account_scope))
                if case_type == 'domain_evidence' and case.state == 'ready':
                    case.state = 'pending'
                source = await session.scalar(select(CaseSource).where(CaseSource.owner_user_id == owner_id,
                    CaseSource.case_id == case.id, CaseSource.check_id == check_id))
                if source is None:
                    session.add(CaseSource(owner_user_id=owner_id, case_id=case.id, check_id=check_id,
                        source_metadata={"schema_version": 4, "sender_account_ids": account_scope}))
                result["case_id"] = str(case.id)
            elif result['disposition'] == 'ready':
                previous = await session.scalar(select(ReviewCase).where(ReviewCase.owner_user_id == owner_id,
                    ReviewCase.case_type == 'domain_evidence', ReviewCase.business_day == check.business_day,
                    ReviewCase.canonical_url_key == check.workflow_key).with_for_update())
                if previous is not None and previous.state in {'pending', 'failed', 'partial'}:
                    previous.state = 'ready'
                    previous.version += 1
            check.result, check.status = result, status
            check.cache_provenance = {"reused": False, "source": "network", "observed_at": self._now().isoformat()}
            check.version += 1
        return status

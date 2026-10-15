from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.domain.jobs import JOB_RESUMABLE_STATES, validate_code, validate_result_metadata
from domaincheck.infrastructure.db.models import AuditEvent, Job, JobItem, SenderAccount
from domaincheck.infrastructure.db.owned import OwnedRepository


class JobNotFound(ValueError):
    pass


class JobConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class JobItemInput:
    item_identity: str
    input_index: int
    result_metadata: dict


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    job: Job
    items: tuple[JobItem, ...]


class JobService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        owner_user_id: UUID,
        actor_user_id: UUID,
        request_id: str,
    ) -> None:
        self._session = session
        self._owner_user_id = owner_user_id
        self._actor_user_id = actor_user_id
        self._request_id = request_id[:128] or "unknown"
        self._jobs = OwnedRepository(session, Job, owner_user_id)

    async def create_job(
        self,
        *,
        job_type: str,
        business_day: date,
        timezone: str,
        items: list[JobItemInput],
        sender_account_ids: list[UUID] | None = None,
    ) -> JobSnapshot:
        validate_code(job_type, label="job_type")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        if not 1 <= len(items) <= 5_000:
            raise ValueError("A job must contain between 1 and 5000 items")
        identities = [item.item_identity for item in items]
        indices = [item.input_index for item in items]
        if any(not identity or len(identity) > 128 for identity in identities):
            raise ValueError("item_identity must contain 1 to 128 characters")
        if len(set(identities)) != len(identities) or len(set(indices)) != len(indices):
            raise ValueError("Job item identity and input index must be unique")
        if sorted(indices) != list(range(len(items))):
            raise ValueError("Job input indexes must be contiguous from zero")
        metadata = [validate_result_metadata(item.result_metadata) for item in items]

        account_ids = list(dict.fromkeys(sender_account_ids or []))
        if len(account_ids) > 100:
            raise ValueError("A job cannot use more than 100 sender accounts")
        if account_ids:
            accounts = await OwnedRepository(
                self._session, SenderAccount, self._owner_user_id
            ).list(criteria=(SenderAccount.id.in_(account_ids),), limit=min(500, len(account_ids)))
            if len(accounts) != len(account_ids):
                raise JobConflict("Sender account scope contains an unavailable account")
            if any(not account.enabled for account in accounts):
                raise JobConflict("Sender account scope contains a disabled account")

        job = Job(
            owner_user_id=self._owner_user_id,
            actor_user_id=self._actor_user_id,
            job_type=job_type,
            state="queued",
            stage="pending",
            business_day=business_day,
            timezone=timezone,
            account_scope=[str(account_id) for account_id in account_ids],
            stop_requested=False,
            result_metadata={},
        )
        self._session.add(job)
        await self._session.flush()
        rows = [
            JobItem(
                owner_user_id=self._owner_user_id,
                job_id=job.id,
                item_identity=item.item_identity,
                input_index=item.input_index,
                state="pending",
                stage="pending",
                attempt=0,
                result_metadata=metadata[position],
            )
            for position, item in enumerate(items)
        ]
        self._session.add_all(rows)
        self._audit("jobs.create", job.id, {"job_type": job_type, "item_count": len(rows)})
        await self._session.flush()
        return JobSnapshot(job, tuple(sorted(rows, key=lambda row: row.input_index)))

    async def get_snapshot(self, job_id: UUID, *, for_update: bool = False) -> JobSnapshot:
        job = await self._jobs.get(job_id, for_update=for_update)
        if job is None:
            raise JobNotFound("Job not found")
        items = tuple(await self._session.scalars(
            select(JobItem)
            .where(JobItem.owner_user_id == self._owner_user_id, JobItem.job_id == job.id)
            .order_by(JobItem.input_index, JobItem.id)
        ))
        return JobSnapshot(job, items)

    async def request_stop(self, job_id: UUID) -> JobSnapshot:
        snapshot = await self.get_snapshot(job_id, for_update=True)
        job = snapshot.job
        if job.state in {"completed", "failed", "partial", "stopped"}:
            return snapshot
        job.stop_requested = True
        job.state = "stopped" if job.state == "queued" else "stopping"
        if job.state == "stopped":
            job.lease_token = None
            job.lease_expires_at = None
        job.version += 1
        self._audit("jobs.stop", job.id, {"state": job.state})
        await self._session.flush()
        return snapshot

    async def resume(self, job_id: UUID) -> JobSnapshot:
        snapshot = await self.get_snapshot(job_id, for_update=True)
        job = snapshot.job
        if job.state not in JOB_RESUMABLE_STATES:
            raise JobConflict("Job is not resumable")
        if not any(item.state == "pending" for item in snapshot.items):
            raise JobConflict("Job has no pending items to resume")
        job.state = "queued"
        job.stage = "resume_pending"
        job.stop_requested = False
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.version += 1
        self._audit("jobs.resume", job.id, {"pending_count": sum(i.state == "pending" for i in snapshot.items)})
        await self._session.flush()
        return snapshot

    async def retry_failed(self, job_id: UUID, item_ids: set[UUID]) -> JobSnapshot:
        if not item_ids:
            raise ValueError("At least one failed item is required")
        snapshot = await self.get_snapshot(job_id, for_update=True)
        selected = [item for item in snapshot.items if item.id in item_ids]
        if len(selected) != len(item_ids):
            raise JobNotFound("One or more job items were not found")
        if any(item.state != "failed" for item in selected):
            raise JobConflict("Only failed items can be retried")
        await self._session.execute(
            update(JobItem)
            .where(
                JobItem.owner_user_id == self._owner_user_id,
                JobItem.job_id == job_id,
                JobItem.id.in_(item_ids),
                JobItem.state == "failed",
            )
            .values(
                state="pending",
                stage="retry_pending",
                version=JobItem.version + 1,
            )
        )
        snapshot.job.state = "queued"
        snapshot.job.stage = "retry_pending"
        snapshot.job.stop_requested = False
        snapshot.job.lease_token = None
        snapshot.job.lease_expires_at = None
        snapshot.job.heartbeat_at = None
        snapshot.job.version += 1
        self._audit("jobs.retry_failed", job_id, {"item_count": len(item_ids)})
        await self._session.flush()
        return await self.get_snapshot(job_id)

    def _audit(self, action: str, resource_id: UUID, metadata: dict) -> None:
        self._session.add(AuditEvent(
            owner_user_id=self._owner_user_id,
            actor_user_id=self._actor_user_id,
            action=action,
            resource_type="job",
            resource_id=resource_id,
            outcome="success",
            metadata_allowlist=metadata,
            request_id=self._request_id,
        ))

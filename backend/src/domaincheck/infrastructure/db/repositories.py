from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.domain.jobs import ITEM_FINAL_STATES, JOB_TERMINAL_STATES, validate_code, validate_result_metadata
from domaincheck.infrastructure.db.models import Artifact, Job, JobItem, Target
from domaincheck.infrastructure.db.owned import OwnedRepository


class ArtifactRepository:
    def __init__(self, session: AsyncSession, owner_user_id: UUID):
        self._session = session
        self._owner_user_id = owner_user_id
        self._owned = OwnedRepository(session, Artifact, owner_user_id)

    async def get(self, artifact_id: UUID) -> Artifact | None:
        return await self._owned.get(artifact_id)


class TargetRepository:
    def __init__(self, session: AsyncSession, owner_user_id: UUID):
        self._session = session
        self._owner_user_id = owner_user_id
        self._owned = OwnedRepository(session, Target, owner_user_id)

    async def get(self, target_id: UUID) -> Target | None:
        return await self._owned.get(target_id)

    async def list(self, *, limit: int = 100) -> list[Target]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        return await self._owned.list(
            order_by=(Target.updated_at.desc(), Target.id),
            limit=limit,
        )

    async def create(
        self,
        *,
        full_url_identity: str,
        hostname: str,
        identity_version: int = 1,
    ) -> Target:
        canonical_hash = hashlib.sha256(full_url_identity.encode("utf-8")).hexdigest()
        target = Target(
            owner_user_id=self._owner_user_id,
            full_url_identity=full_url_identity,
            canonical_url_hash=canonical_hash,
            hostname=hostname,
            identity_version=identity_version,
        )
        self._session.add(target)
        await self._session.flush()
        return target


class JobRepository:
    """Durable job access.

    Request methods always require an owner. ``claim_next`` is worker-only: the
    selected row itself supplies the trusted owner and actor scope.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_for_owner(self, owner_user_id: UUID, job_id: UUID) -> Job | None:
        return await OwnedRepository(self._session, Job, owner_user_id).get(job_id)

    async def claim_next(
        self,
        *,
        worker_token: str,
        lease_seconds: int = 60,
        job_types: frozenset[str] | None = None,
        now: datetime | None = None,
    ) -> Job | None:
        if not worker_token:
            raise ValueError("worker_token is required")
        if not 5 <= lease_seconds <= 900:
            raise ValueError("lease_seconds must be between 5 and 900")
        observed = now or datetime.now(UTC)
        conditions = [
            Job.stop_requested.is_(False),
            or_(
                Job.state == "queued",
                (Job.state == "running") & (Job.lease_expires_at < observed),
            ),
        ]
        if job_types:
            conditions.append(Job.job_type.in_(job_types))
        job = await self._session.scalar(
            select(Job)
            .where(*conditions)
            .order_by(Job.created_at, Job.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.state = "running"
        job.lease_token = worker_token
        job.lease_expires_at = observed + timedelta(seconds=lease_seconds)
        job.heartbeat_at = observed
        job.version += 1
        await self._session.flush()
        return job

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        worker_token: str,
        expected_version: int,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> Job | None:
        if not worker_token:
            raise ValueError("worker_token is required")
        if not 5 <= lease_seconds <= 900:
            raise ValueError("lease_seconds must be between 5 and 900")
        observed = now or datetime.now(UTC)
        return await self._session.scalar(
            update(Job)
            .where(
                Job.id == job_id,
                Job.state == "running",
                Job.stop_requested.is_(False),
                Job.lease_token == worker_token,
                Job.version == expected_version,
                Job.lease_expires_at >= observed,
            )
            .values(
                heartbeat_at=observed,
                lease_expires_at=observed + timedelta(seconds=lease_seconds),
                version=Job.version + 1,
            )
            .returning(Job)
        )

    async def list_claimed_items(self, job: Job, *, worker_token: str) -> list[JobItem]:
        if job.lease_token != worker_token or job.state not in {"running", "stopping"}:
            raise ValueError("Worker does not hold this job lease")
        rows = await self._session.scalars(
            select(JobItem)
            .where(JobItem.owner_user_id == job.owner_user_id, JobItem.job_id == job.id)
            .order_by(JobItem.input_index, JobItem.id)
        )
        return list(rows)

    async def start_item(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        item_id: UUID,
        worker_token: str,
        expected_version: int,
        stage: str,
        now: datetime | None = None,
    ) -> JobItem | None:
        validate_code(stage, label="stage")
        observed = now or datetime.now(UTC)
        active_lease = exists().where(
            Job.id == job_id,
            Job.owner_user_id == owner_user_id,
            Job.state == "running",
            Job.stop_requested.is_(False),
            Job.lease_token == worker_token,
            Job.lease_expires_at >= observed,
        )
        return await self._session.scalar(
            update(JobItem)
            .where(
                JobItem.owner_user_id == owner_user_id,
                JobItem.job_id == job_id,
                JobItem.id == item_id,
                JobItem.state == "pending",
                JobItem.version == expected_version,
                active_lease,
            )
            .values(
                state="running", stage=stage, attempt=JobItem.attempt + 1,
                version=JobItem.version + 1,
            )
            .returning(JobItem)
        )

    async def checkpoint_item(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        item_id: UUID,
        worker_token: str,
        expected_version: int,
        state: str,
        stage: str,
        result_metadata: dict,
        now: datetime | None = None,
    ) -> JobItem | None:
        if state not in ITEM_FINAL_STATES:
            raise ValueError("Item checkpoint state is not terminal")
        validate_code(stage, label="stage")
        metadata = validate_result_metadata(result_metadata)
        observed = now or datetime.now(UTC)
        active_lease = exists().where(
            Job.id == job_id,
            Job.owner_user_id == owner_user_id,
            Job.state.in_(("running", "stopping")),
            Job.lease_token == worker_token,
            Job.lease_expires_at >= observed,
        )
        return await self._session.scalar(
            update(JobItem)
            .where(
                JobItem.owner_user_id == owner_user_id,
                JobItem.job_id == job_id,
                JobItem.id == item_id,
                JobItem.state == "running",
                JobItem.version == expected_version,
                active_lease,
            )
            .values(
                state=state, stage=stage, result_metadata=metadata,
                version=JobItem.version + 1,
            )
            .returning(JobItem)
        )

    async def recover_interrupted_items(
        self,
        *,
        job: Job,
        worker_token: str,
        now: datetime | None = None,
    ) -> list[JobItem]:
        observed = now or datetime.now(UTC)
        locked_job = await self._session.scalar(
            select(Job)
            .where(
                Job.id == job.id,
                Job.owner_user_id == job.owner_user_id,
                Job.state == "running",
                Job.stop_requested.is_(False),
                Job.lease_token == worker_token,
                Job.lease_expires_at >= observed,
            )
            .with_for_update()
        )
        if locked_job is None:
            raise ValueError("Worker does not hold an active job lease")
        interrupted = list(await self._session.scalars(
            select(JobItem)
            .where(
                JobItem.owner_user_id == job.owner_user_id,
                JobItem.job_id == job.id,
                JobItem.state == "running",
            )
            .order_by(JobItem.input_index, JobItem.id)
            .with_for_update()
        ))
        for item in interrupted:
            item.state = "unknown"
            item.stage = "recovered_uncertain"
            item.result_metadata = {
                **(item.result_metadata or {}),
                "recovery": "lease_expired_during_item",
            }
            item.version += 1
        if interrupted:
            locked_job.result_metadata = {
                **(locked_job.result_metadata or {}),
                "uncertain_item_count": len(interrupted),
            }
            locked_job.version += 1
        await self._session.flush()
        return interrupted

    async def finalize_job(
        self,
        *,
        job_id: UUID,
        worker_token: str,
        expected_version: int,
        state: str,
        stage: str,
        result_metadata: dict,
        now: datetime | None = None,
    ) -> Job | None:
        if state not in JOB_TERMINAL_STATES - {"stopped"}:
            raise ValueError("Worker final state is invalid")
        validate_code(stage, label="stage")
        metadata = validate_result_metadata(result_metadata)
        unfinished = await self._session.scalar(
            select(JobItem.id).where(
                JobItem.job_id == job_id,
                JobItem.state.in_(("pending", "running")),
            ).limit(1)
        )
        if unfinished is not None:
            raise ValueError("Cannot finalize a job with unfinished items")
        observed = now or datetime.now(UTC)
        return await self._session.scalar(
            update(Job)
            .where(
                Job.id == job_id,
                Job.state == "running",
                Job.stop_requested.is_(False),
                Job.lease_token == worker_token,
                Job.lease_expires_at >= observed,
                Job.version == expected_version,
            )
            .values(
                state=state, stage=stage, result_metadata=metadata,
                lease_token=None, lease_expires_at=None, heartbeat_at=observed,
                version=Job.version + 1,
            )
            .returning(Job)
        )

    async def finalize_stop(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        worker_token: str,
        expected_version: int,
    ) -> Job | None:
        running_item = await self._session.scalar(
            select(JobItem.id).where(
                JobItem.owner_user_id == owner_user_id,
                JobItem.job_id == job_id,
                JobItem.state == "running",
            ).limit(1)
        )
        if running_item is not None:
            raise ValueError("Cannot stop while an item is still running")
        return await self._session.scalar(
            update(Job)
            .where(
                Job.id == job_id,
                Job.owner_user_id == owner_user_id,
                Job.state == "stopping",
                Job.stop_requested.is_(True),
                Job.lease_token == worker_token,
                Job.version == expected_version,
            )
            .values(
                state="stopped", stage="stopped", lease_token=None,
                lease_expires_at=None, version=Job.version + 1,
            )
            .returning(Job)
        )

    async def recover_expired_stops(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """Finish cooperative stops whose worker lease expired.

        A running item may already have performed an external side effect, so
        recovery records it as ``unknown``. Pending membership is preserved for
        an explicit later resume; this sweeper never retries work itself.
        """
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        observed = now or datetime.now(UTC)
        jobs = list(await self._session.scalars(
            select(Job)
            .where(
                Job.state == "stopping",
                Job.stop_requested.is_(True),
                or_(Job.lease_expires_at.is_(None), Job.lease_expires_at < observed),
            )
            .order_by(Job.updated_at, Job.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        ))
        for job in jobs:
            interrupted = list(await self._session.scalars(
                select(JobItem)
                .where(
                    JobItem.owner_user_id == job.owner_user_id,
                    JobItem.job_id == job.id,
                    JobItem.state == "running",
                )
                .order_by(JobItem.input_index, JobItem.id)
                .with_for_update()
            ))
            for item in interrupted:
                item.state = "unknown"
                item.stage = "recovered_uncertain"
                item.result_metadata = {
                    **(item.result_metadata or {}),
                    "recovery": "stop_lease_expired_during_item",
                }
                item.version += 1
            job.state = "stopped"
            job.stage = "stopped_after_recovery"
            job.lease_token = None
            job.lease_expires_at = None
            job.heartbeat_at = observed
            job.result_metadata = {
                **(job.result_metadata or {}),
                "uncertain_item_count": len(interrupted),
            }
            job.version += 1
        await self._session.flush()
        return jobs

    async def request_stop(self, owner_user_id: UUID, job_id: UUID) -> bool:
        job = await OwnedRepository(self._session, Job, owner_user_id).get(job_id, for_update=True)
        if job is None:
            return False
        if job.state in JOB_TERMINAL_STATES:
            return True
        job.stop_requested = True
        job.state = "stopped" if job.state == "queued" else "stopping"
        if job.state == "stopped":
            job.lease_token = None
            job.lease_expires_at = None
        job.version += 1
        await self._session.flush()
        return True


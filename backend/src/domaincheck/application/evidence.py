"""Owner-scoped evidence sets and asynchronous browser capture jobs."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domaincheck.application.jobs import JobItemInput, JobService
from domaincheck.application.quick_reports import QuickReportWorker
from domaincheck.core.evidence import BrowserCaptureAdapter
from domaincheck.infrastructure.db.models import Artifact, Check, EvidenceSet, Job
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.ports.artifacts import ArtifactStore, ImageUpload


class EvidenceNotFound(ValueError):
    pass


class EvidenceConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    id: UUID
    check_id: UUID
    requested_url: str
    landing_url: str | None
    destination_url: str | None
    capture_mode: str
    manifest: dict
    artifact_ids: tuple[UUID, ...]


class EvidenceService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], store: ArtifactStore,
                 *, owner_user_id: UUID, actor_user_id: UUID, request_id: str) -> None:
        self.sessions = sessions
        self.store = store
        self.owner = owner_user_id
        self.actor = actor_user_id
        self.request_id = request_id

    @staticmethod
    async def _check(session: AsyncSession, owner: UUID, check_id: UUID) -> Check:
        check = await OwnedRepository(session, Check, owner).get(check_id)
        if check is None or check.workflow != "check_domain":
            raise EvidenceNotFound("Check Domain check not found")
        return check

    async def create_capture_job(self, check_id: UUID) -> Job:
        async with self.sessions() as session, session.begin():
            check = await self._check(session, self.owner, check_id)
            if check.status != "succeeded":
                raise EvidenceConflict("Check Domain check must complete before capture")
            snapshot = await JobService(session, owner_user_id=self.owner,
                actor_user_id=self.actor, request_id=self.request_id).create_job(
                job_type="browser_evidence", business_day=check.business_day,
                timezone=check.timezone,
                items=[JobItemInput(hashlib.sha256(str(check_id).encode()).hexdigest(), 0,
                                    {"check_id": str(check_id)})])
            return snapshot.job

    async def save_images(self, check_id: UUID, uploads: list[ImageUpload], *,
                          mode: str, manifest: dict) -> EvidenceSummary:
        if mode not in {"manual_upload", "browser_capture"}:
            raise ValueError("Unsupported evidence mode")
        evidence_id = uuid4()
        stored = ()
        try:
            async with self.sessions() as session, session.begin():
                check = await self._check(session, self.owner, check_id)
                if check.status != "succeeded":
                    raise EvidenceConflict("Check Domain check must complete before evidence")
                stored = self.store.store_evidence_images(
                    owner_user_id=self.owner, evidence_set_id=evidence_id, uploads=uploads,
                    minimum_count=1, maximum_count=3)
                image_metadata = [{"artifact_id": str(item.artifact_id), "sha256": item.sha256,
                                   "byte_size": item.byte_size, "width": item.width,
                                   "height": item.height, "mime_type": item.mime_type}
                                  for item in stored]
                safe_manifest = {**manifest, "version": 1, "source": mode,
                                 "screenshots": image_metadata}
                evidence = EvidenceSet(id=evidence_id, owner_user_id=self.owner,
                    check_id=check_id, case_id=None, requested_url=check.requested_url,
                    landing_url=safe_manifest.get("landing_url") or None,
                    destination_url=safe_manifest.get("final_url") or None,
                    capture_mode=mode, manifest=safe_manifest)
                session.add(evidence)
                await session.flush()
                for item in stored:
                    session.add(Artifact(id=item.artifact_id, owner_user_id=self.owner,
                        evidence_set_id=evidence_id, kind="check_domain:evidence",
                        mime_type=item.mime_type, byte_size=item.byte_size,
                        sha256=item.sha256, storage_key=item.storage_key,
                        original_name=None, immutable=True))
                await session.flush()
            return EvidenceSummary(evidence_id, check_id, check.requested_url,
                evidence.landing_url, evidence.destination_url, mode, safe_manifest,
                tuple(item.artifact_id for item in stored))
        except Exception:
            for item in stored:
                self.store.delete(storage_key=item.storage_key)
            raise

    async def list_for_check(self, check_id: UUID) -> tuple[EvidenceSummary, ...]:
        async with self.sessions() as session:
            await self._check(session, self.owner, check_id)
            rows = list(await session.scalars(select(EvidenceSet).where(
                EvidenceSet.owner_user_id == self.owner, EvidenceSet.check_id == check_id)
                .order_by(EvidenceSet.created_at.desc(), EvidenceSet.id.desc()).limit(100)))
            result = []
            for row in rows:
                artifacts = set(await session.scalars(select(Artifact.id).where(
                    Artifact.owner_user_id == self.owner, Artifact.evidence_set_id == row.id)
                    .order_by(Artifact.created_at, Artifact.id)))
                ordered = tuple(UUID(item["artifact_id"]) for item in row.manifest.get("screenshots", [])
                                if UUID(item["artifact_id"]) in artifacts)
                result.append(EvidenceSummary(row.id, check_id, row.requested_url,
                    row.landing_url, row.destination_url, row.capture_mode, row.manifest,
                    ordered))
            return tuple(result)


class BrowserEvidenceWorker(QuickReportWorker):
    def __init__(self, sessions: async_sessionmaker[AsyncSession], store: ArtifactStore,
                 adapter: BrowserCaptureAdapter, *, now=None) -> None:
        super().__init__(sessions, None, adapter, job_type="browser_evidence",
                         now=now or (lambda: datetime.now(UTC)))
        self.store = store

    async def _process_check(self, owner_id: UUID, check_id: UUID) -> str:
        try:
            async with self._sessions() as session:
                check = await EvidenceService._check(session, owner_id, check_id)
                if check.status != "succeeded":
                    return "failed"
                target = check.requested_url
                actor_id = check.actor_user_id
            uploads, manifest = await asyncio.to_thread(
                self._adapter.capture, target, staging_root=self.store.root / ".staging")
            await EvidenceService(self._sessions, self.store, owner_user_id=owner_id,
                actor_user_id=actor_id, request_id="browser-evidence-worker").save_images(
                check_id, uploads, mode="browser_capture", manifest=manifest)
            return "succeeded"
        except Exception:
            return "failed"

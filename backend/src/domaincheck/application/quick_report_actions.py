from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import re
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select

from domaincheck.application.quick_reports import QuickReportNotFound, QuickReportService
from domaincheck.infrastructure.cache import business_day
from domaincheck.infrastructure.db.models import Artifact, AuditEvent, Check, ProviderSubmission, UserSettings
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.ports.artifacts import ArtifactIntegrityError


class ActionConflict(ValueError):
    pass


class QuickReportActions:
    """Exact provider preview and durable intent. Network runs after intent commits."""

    def __init__(self, sessions, store, *, owner: UUID, timezone: str, request_id: str, now,
                 adapter=None):
        self.sessions, self.store = sessions, store
        self.owner, self.timezone, self.request_id = owner, timezone, request_id
        self.now = now
        self.adapter = adapter or self._submit

    @staticmethod
    async def _submit(payload):
        helper = importlib.import_module("quick_report_actions")
        return await asyncio.to_thread(helper.submit_netcraft, payload)

    async def _check(self, session, check_id):
        check = await OwnedRepository(session, Check, self.owner).get(check_id)
        if check is None or check.workflow != "quick_report":
            raise QuickReportNotFound("Quick Report check not found")
        if check.status != "succeeded":
            raise ActionConflict("Quick Report check is not complete")
        return check

    async def _payload(self, session, row):
        artifact = await OwnedRepository(session, Artifact, self.owner).get(row.payload_artifact_id)
        if artifact is None:
            raise ActionConflict("Provider preview artifact is unavailable")
        try:
            raw = self.store.read_verified(storage_key=artifact.storage_key,
                expected_size=artifact.byte_size, expected_sha256=artifact.sha256)
            if hashlib.sha256(raw).hexdigest() != row.fingerprint:
                raise ActionConflict("Provider preview fingerprint changed")
            return json.loads(raw)
        except (ArtifactIntegrityError, OSError, ValueError) as exc:
            raise ActionConflict("Provider preview failed integrity validation") from exc

    def _recover(self, row):
        if row.state == "SENDING" and row.claim_expires_at <= self.now():
            row.state = "UNKNOWN"
            row.result_metadata = {"code": "interrupted_after_intent"}
            row.version += 1

    async def _row(self, session, row_id):
        row = await OwnedRepository(session, ProviderSubmission, self.owner).get(row_id, for_update=True)
        if row is None or row.provider != "netcraft":
            raise QuickReportNotFound("Provider submission not found")
        self._recover(row)
        return row

    def _audit(self, session, row, action):
        session.add(AuditEvent(owner_user_id=self.owner, actor_user_id=self.owner,
            action=f"quick_report.netcraft.{action}", resource_type="provider_submission",
            resource_id=row.id, outcome=row.state, request_id=self.request_id[:128],
            metadata_allowlist={"revision": row.revision, "state": row.state}))

    async def preview(self, check_id):
        stored = None
        try:
            async with self.sessions() as session, session.begin():
                check = await self._check(session, check_id)
                day = business_day(self.now(), self.timezone)
                if day != check.business_day:
                    raise ActionConflict("Check this URL again for the current business day")
                digest = hashlib.sha256(check.requested_url.encode()).hexdigest()
                lock = int.from_bytes(hashlib.sha256(f"netcraft:{self.owner}:{day}:{digest}".encode()).digest()[:8], "big", signed=True)
                await session.execute(select(func.pg_advisory_xact_lock(lock)))
                row = await session.scalar(select(ProviderSubmission).where(
                    ProviderSubmission.owner_user_id == self.owner, ProviderSubmission.provider == "netcraft",
                    ProviderSubmission.business_day == day, ProviderSubmission.url_hash == digest,
                ).with_for_update())
                if row:
                    self._recover(row)
                    if row.state in {"SUBMITTED", "SENDING", "UNKNOWN"}:
                        return self.dto(row, await self._payload(session, row))
                profile = await session.get(UserSettings, self.owner)
                email = profile.contact_email.strip() if profile else ""
                if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email):
                    raise ActionConflict("Configure a valid contact email in your organization profile first")
                helper = importlib.import_module("phishing_toolkit")
                reason = helper.generate_community_report_text(check.domain, {"brand_name": profile.brand_name}, check.requested_url)
                payload = {"email": email, "urls": [{"url": check.requested_url, "country": "VN", "reason": reason}]}
                raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                row_id = row.id if row else uuid4()
                stored = self.store.store_preview_payload(owner_user_id=self.owner, preview_id=row_id, data=raw)
                session.add(Artifact(id=stored.artifact_id, owner_user_id=self.owner, evidence_set_id=None,
                    kind="quick_report:netcraft_draft", mime_type=stored.mime_type, byte_size=stored.byte_size,
                    sha256=stored.sha256, storage_key=stored.storage_key, immutable=True))
                await session.flush()
                values = dict(check_id=check.id, state="PREVIEW", revision=row.revision + 1 if row else 1,
                    payload_artifact_id=stored.artifact_id, fingerprint=stored.sha256,
                    profile_version=profile.version, expires_at=self.now() + timedelta(minutes=30), result_metadata={})
                if row:
                    for key, value in values.items():
                        setattr(row, key, value)
                    row.version += 1
                else:
                    row = ProviderSubmission(id=row_id, owner_user_id=self.owner, actor_user_id=self.owner,
                        provider="netcraft", business_day=day, url_hash=digest, **values)
                    session.add(row)
                await session.flush()
                self._audit(session, row, "preview")
                result = self.dto(row, payload)
            return result
        except Exception:
            if stored:
                self.store.delete(storage_key=stored.storage_key)
            raise

    @staticmethod
    def dto(row, payload=None):
        return {"id": str(row.id), "state": row.state, "revision": row.revision,
            "fingerprint": row.fingerprint, "expires_at": row.expires_at.isoformat(),
            "payload": payload, "result": row.result_metadata}

    async def get(self, row_id):
        async with self.sessions() as session, session.begin():
            row = await self._row(session, row_id)
            return self.dto(row, await self._payload(session, row))

    async def find(self, check_id):
        async with self.sessions() as session, session.begin():
            check = await self._check(session, check_id)
            digest = hashlib.sha256(check.requested_url.encode()).hexdigest()
            row = await session.scalar(select(ProviderSubmission).where(
                ProviderSubmission.owner_user_id == self.owner, ProviderSubmission.provider == "netcraft",
                ProviderSubmission.business_day == check.business_day, ProviderSubmission.url_hash == digest,
            ).with_for_update())
            if row is None:
                return None
            self._recover(row)
            return self.dto(row, await self._payload(session, row))

    async def submit(self, row_id, *, fingerprint, revision, confirmed, retry=False):
        if not confirmed:
            raise ActionConflict("Explicit approval of the exact preview is required")
        async with self.sessions() as session, session.begin():
            row = await self._row(session, row_id)
            if row.state in {"SUBMITTED", "SENDING", "UNKNOWN"}:
                return self.dto(row)
            if row.state == "FAILED" and not retry:
                raise ActionConflict("Retry must be explicitly selected")
            if row.state not in {"PREVIEW", "FAILED"}:
                raise ActionConflict("Provider submission state does not allow sending")
            if row.fingerprint != fingerprint or row.revision != revision or row.expires_at <= self.now():
                raise ActionConflict("Preview is stale or expired; create a new preview")
            if row.business_day != business_day(self.now(), self.timezone):
                raise ActionConflict("Preview belongs to an earlier business day")
            await self._check(session, row.check_id)
            profile = await session.get(UserSettings, self.owner, with_for_update=True)
            if profile is None or profile.version != row.profile_version:
                raise ActionConflict("Profile changed; create a new preview")
            payload = await self._payload(session, row)
            row.state = "SENDING"
            row.claim_expires_at = self.now() + timedelta(seconds=60)
            row.version += 1
            attempt_version = row.version
            self._audit(session, row, "intent")
        # Intent is durable before any external request. Cancellation/crash leaves SENDING,
        # subsequently recovered as UNKNOWN. Never retry that state automatically.
        try:
            outcome = await self.adapter(payload)
        except Exception:
            outcome = {"state": "UNKNOWN", "code": "connection_outcome_unknown"}
        async with self.sessions() as session, session.begin():
            row = await self._row(session, row_id)
            if row.state == "SENDING" and row.version == attempt_version:
                state = outcome.get("state")
                row.state = state if state in {"SUBMITTED", "FAILED", "UNKNOWN"} else "UNKNOWN"
                row.result_metadata = {"code": {"SUBMITTED": "accepted", "FAILED": "provider_rejected", "UNKNOWN": "outcome_unknown"}[row.state]}
                if isinstance(outcome.get("http_status"), int):
                    row.result_metadata["http_status"] = outcome["http_status"]
                row.version += 1
                self._audit(session, row, "outcome")
            return self.dto(row)

    async def export_nictop(self, operation_id):
        async with self.sessions() as session, session.begin():
            operation = await QuickReportService(session, owner_user_id=self.owner, actor_user_id=self.owner,
                timezone=self.timezone, request_id=self.request_id, now=self.now()).get(operation_id)
            targets = [(check.domain, check.requested_url) for check in operation.checks
                if check.status == "succeeded" and check.domain.lower().endswith(".top")]
            if not targets:
                raise ActionConflict("No completed .top targets are available for export")
            profile = await session.get(UserSettings, self.owner)
            helper = importlib.import_module("quick_report_actions")
            return helper.build_nictop_workbook(targets, brand_name=profile.brand_name if profile else "",
                detected_date=operation.job.business_day.isoformat())

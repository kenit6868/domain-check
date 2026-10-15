from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.domain.previews import (
    PREVIEW_FEATURES,
    PREVIEW_SCHEMA_VERSION,
    ExactPreview,
    PreparedDelivery,
    canonical_payload_bytes,
    fingerprint_payload,
    validate_prepared_deliveries,
)
from domaincheck.infrastructure.db.models import (
    Artifact,
    AuditEvent,
    Check,
    EvidenceSet,
    PreviewPlan,
    ReviewCase,
    SenderAccount,
    UserSettings,
)
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.ports.artifacts import ArtifactIntegrityError, ArtifactStore


class PreviewNotFound(ValueError):
    pass


class PreviewConflict(ValueError):
    pass


_RESOURCE_MODELS = {
    "check": Check,
    "review_case": ReviewCase,
}


class PreviewService:
    def __init__(
        self,
        session: AsyncSession,
        artifact_store: ArtifactStore,
        *,
        owner_user_id: UUID,
        actor_user_id: UUID,
        request_id: str,
    ) -> None:
        self._session = session
        self._artifact_store = artifact_store
        self._owner_user_id = owner_user_id
        self._actor_user_id = actor_user_id
        self._request_id = request_id[:128] or "unknown"
        self._previews = OwnedRepository(session, PreviewPlan, owner_user_id)

    async def create_revision(
        self,
        *,
        resource_type: str,
        resource_id: UUID,
        feature: str,
        policy_version: str,
        deliveries: list[PreparedDelivery],
        attachment_artifact_ids: list[UUID] | None = None,
        inline_artifact_ids: list[UUID] | None = None,
        lifetime: timedelta = timedelta(minutes=30),
        now: datetime | None = None,
    ) -> ExactPreview:
        validate_prepared_deliveries(deliveries)
        if feature not in PREVIEW_FEATURES:
            raise ValueError("Preview feature is not registered")
        if not policy_version or len(policy_version) > 100:
            raise ValueError("Preview policy version is invalid")
        if not timedelta(minutes=1) <= lifetime <= timedelta(hours=24):
            raise ValueError("Preview lifetime must be between 1 minute and 24 hours")
        await self._require_owned_resource(resource_type, resource_id)
        await self._lock_revision_scope(resource_type, resource_id)

        account_ids = list(dict.fromkeys(row.sender_account_id for row in deliveries))
        accounts = await OwnedRepository(
            self._session, SenderAccount, self._owner_user_id
        ).list(criteria=(SenderAccount.id.in_(account_ids),), limit=len(account_ids))
        account_by_id = {account.id: account for account in accounts}
        if len(account_by_id) != len(account_ids) or any(
            not account_by_id[account_id].enabled for account_id in account_ids
        ):
            raise PreviewConflict("Preview contains an unavailable sender account")

        bindings = await self._resolve_artifact_bindings(
            resource_type=resource_type,
            resource_id=resource_id,
            attachment_ids=attachment_artifact_ids or [],
            inline_ids=inline_artifact_ids or [],
        )
        profile = await self._session.get(UserSettings, self._owner_user_id)
        profile_version = profile.version if profile is not None else 0
        payload = {
            "schema_version": PREVIEW_SCHEMA_VERSION,
            "policy_version": policy_version,
            "resource": {"type": resource_type, "id": str(resource_id)},
            "profile_version": profile_version,
            "deliveries": [
                {
                    "sender_account_id": str(row.sender_account_id),
                    "account_version": account_by_id[row.sender_account_id].version,
                    "recipient": row.recipient.strip().lower(),
                    "channel": row.channel,
                    "draft_identity": row.draft_identity,
                    "subject": row.subject,
                    "text_body": row.text_body,
                    "html_body": row.html_body,
                }
                for row in deliveries
            ],
            "artifacts": bindings,
        }
        payload_bytes = canonical_payload_bytes(payload)
        fingerprint = fingerprint_payload(payload)
        observed = now or datetime.now(UTC)
        latest_revision = await self._session.scalar(
            select(func.max(PreviewPlan.revision)).where(
                PreviewPlan.owner_user_id == self._owner_user_id,
                PreviewPlan.resource_type == resource_type,
                PreviewPlan.resource_id == resource_id,
            )
        )
        revision = int(latest_revision or 0) + 1
        preview_id = uuid4()
        stored = self._artifact_store.store_preview_payload(
            owner_user_id=self._owner_user_id,
            preview_id=preview_id,
            data=payload_bytes,
        )
        try:
            artifact = Artifact(
                id=stored.artifact_id,
                owner_user_id=self._owner_user_id,
                evidence_set_id=None,
                kind=f"{feature}:draft",
                mime_type=stored.mime_type,
                byte_size=stored.byte_size,
                sha256=stored.sha256,
                storage_key=stored.storage_key,
                original_name=None,
                immutable=True,
            )
            plan = PreviewPlan(
                id=preview_id,
                owner_user_id=self._owner_user_id,
                actor_user_id=self._actor_user_id,
                resource_type=resource_type,
                resource_id=resource_id,
                revision=revision,
                fingerprint=fingerprint,
                expires_at=observed + lifetime,
                account_versions={
                    str(account_id): account_by_id[account_id].version for account_id in account_ids
                } | {"profile": profile_version},
                recipients=[row.recipient.strip().lower() for row in deliveries],
                draft_artifact_id=artifact.id,
                attachment_refs=bindings,
                approved_at=None,
            )
            self._session.add_all([artifact, plan])
            self._audit(
                "previews.create_revision",
                preview_id,
                {
                    "feature": feature,
                    "resource_type": resource_type,
                    "revision": revision,
                    "delivery_count": len(deliveries),
                    "artifact_count": len(bindings),
                },
            )
            await self._session.flush()
        except Exception:
            self._artifact_store.delete(storage_key=stored.storage_key)
            raise
        return ExactPreview(preview_id, revision, fingerprint, payload)

    async def get_exact(
        self,
        preview_id: UUID,
        *,
        expected_revision: int | None = None,
        require_approved: bool = False,
        for_update: bool = False,
        now: datetime | None = None,
    ) -> ExactPreview:
        plan = await self._previews.get(preview_id, for_update=for_update)
        if plan is None:
            raise PreviewNotFound("Preview not found")
        if expected_revision is not None and plan.revision != expected_revision:
            raise PreviewConflict("Preview revision is stale")
        observed = now or datetime.now(UTC)
        if plan.expires_at <= observed:
            raise PreviewConflict("Preview has expired")
        latest_revision = await self._session.scalar(
            select(func.max(PreviewPlan.revision)).where(
                PreviewPlan.owner_user_id == self._owner_user_id,
                PreviewPlan.resource_type == plan.resource_type,
                PreviewPlan.resource_id == plan.resource_id,
            )
        )
        if plan.revision != latest_revision:
            raise PreviewConflict("A newer preview revision exists")
        if require_approved and plan.approved_at is None:
            raise PreviewConflict("Preview has not been approved")
        await self._require_owned_resource(plan.resource_type, plan.resource_id)

        artifact = await OwnedRepository(
            self._session, Artifact, self._owner_user_id
        ).get(plan.draft_artifact_id)
        if artifact is None or artifact.mime_type != "application/vnd.domaincheck.preview+json":
            raise PreviewConflict("Preview draft artifact is unavailable")
        try:
            data = self._artifact_store.read_verified(
                storage_key=artifact.storage_key,
                expected_size=artifact.byte_size,
                expected_sha256=artifact.sha256,
            )
        except (ArtifactIntegrityError, FileNotFoundError) as exc:
            raise PreviewConflict("Preview draft artifact failed integrity validation") from exc
        if hashlib.sha256(data).hexdigest() != plan.fingerprint:
            raise PreviewConflict("Preview fingerprint does not match its exact draft")
        try:
            payload = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PreviewConflict("Preview draft artifact is invalid") from exc
        if fingerprint_payload(payload) != plan.fingerprint:
            raise PreviewConflict("Preview exact draft is not canonical")
        if payload.get("schema_version") != PREVIEW_SCHEMA_VERSION:
            raise PreviewConflict("Preview schema version is unsupported")
        if payload.get("resource") != {"type": plan.resource_type, "id": str(plan.resource_id)}:
            raise PreviewConflict("Preview resource binding is invalid")

        await self._validate_account_versions(plan, payload, for_update=for_update)
        await self._validate_artifact_bindings(plan, payload)
        return ExactPreview(plan.id, plan.revision, plan.fingerprint, payload)

    async def approve(
        self,
        preview_id: UUID,
        *,
        expected_revision: int,
        confirmed: bool,
        now: datetime | None = None,
    ) -> ExactPreview:
        if not confirmed:
            raise ValueError("Explicit preview confirmation is required")
        observed = now or datetime.now(UTC)
        exact = await self.get_exact(
            preview_id,
            expected_revision=expected_revision,
            for_update=True,
            now=observed,
        )
        plan = await self._previews.get(preview_id, for_update=True)
        assert plan is not None
        if plan.approved_at is None:
            plan.approved_at = observed
            plan.version += 1
            self._audit(
                "previews.approve",
                plan.id,
                {"resource_type": plan.resource_type, "revision": plan.revision},
            )
            await self._session.flush()
        return exact

    async def _require_owned_resource(self, resource_type: str, resource_id: UUID) -> object:
        model = _RESOURCE_MODELS.get(resource_type)
        if model is None:
            raise ValueError("Preview resource type is not registered")
        resource = await OwnedRepository(
            self._session, model, self._owner_user_id
        ).get(resource_id)
        if resource is None:
            raise PreviewNotFound("Preview resource not found")
        return resource

    async def _lock_revision_scope(self, resource_type: str, resource_id: UUID) -> None:
        digest = hashlib.sha256(
            f"{self._owner_user_id}:{resource_type}:{resource_id}".encode("ascii")
        ).digest()
        lock_key = int.from_bytes(digest[:8], "big", signed=True)
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def _resolve_artifact_bindings(
        self,
        *,
        resource_type: str,
        resource_id: UUID,
        attachment_ids: list[UUID],
        inline_ids: list[UUID],
    ) -> list[dict]:
        all_ids = [*attachment_ids, *inline_ids]
        if len(all_ids) > 12 or len(set(all_ids)) != len(all_ids):
            raise ValueError("Preview artifact bindings are duplicated or exceed the limit")
        if not all_ids:
            return []
        artifacts = await OwnedRepository(
            self._session, Artifact, self._owner_user_id
        ).list(criteria=(Artifact.id.in_(all_ids),), limit=len(all_ids))
        by_id = {artifact.id: artifact for artifact in artifacts}
        if len(by_id) != len(all_ids):
            raise PreviewNotFound("One or more preview artifacts were not found")
        result: list[dict] = []
        for disposition, ids in (("attachment", attachment_ids), ("inline", inline_ids)):
            for artifact_id in ids:
                artifact = by_id[artifact_id]
                if not artifact.immutable:
                    raise PreviewConflict("Preview artifacts must be immutable")
                evidence_version = None
                if disposition == "attachment":
                    if artifact.evidence_set_id is None:
                        raise PreviewConflict("Evidence attachment is not bound to an evidence set")
                    evidence = await OwnedRepository(
                        self._session, EvidenceSet, self._owner_user_id
                    ).get(artifact.evidence_set_id)
                    if evidence is None or (
                        resource_type == "check" and evidence.check_id != resource_id
                    ) or (
                        resource_type == "review_case" and evidence.case_id != resource_id
                    ):
                        raise PreviewConflict("Evidence attachment belongs to another resource")
                    evidence_version = evidence.version
                elif artifact.evidence_set_id is not None or artifact.kind != "mail_settings:logo":
                    raise PreviewConflict("Inline artifact is not an approved profile logo")
                try:
                    self._artifact_store.read_verified(
                        storage_key=artifact.storage_key,
                        expected_size=artifact.byte_size,
                        expected_sha256=artifact.sha256,
                    )
                except (ArtifactIntegrityError, FileNotFoundError) as exc:
                    raise PreviewConflict("Preview artifact failed integrity validation") from exc
                result.append({
                    "artifact_id": str(artifact.id),
                    "disposition": disposition,
                    "content_id": f"logo-{artifact.id}" if disposition == "inline" else None,
                    "mime_type": artifact.mime_type,
                    "byte_size": artifact.byte_size,
                    "sha256": artifact.sha256,
                    "evidence_set_id": str(artifact.evidence_set_id) if artifact.evidence_set_id else None,
                    "evidence_version": evidence_version,
                })
        return result

    async def _validate_account_versions(
        self,
        plan: PreviewPlan,
        payload: dict,
        *,
        for_update: bool,
    ) -> None:
        expected = {key: int(value) for key, value in plan.account_versions.items() if key != "profile"}
        account_ids = [UUID(key) for key in expected]
        statement = select(SenderAccount).where(
            SenderAccount.owner_user_id == self._owner_user_id,
            SenderAccount.id.in_(account_ids),
        )
        if for_update:
            statement = statement.with_for_update()
        accounts = list(await self._session.scalars(statement))
        if len(accounts) != len(account_ids) or any(
            not account.enabled or account.version != expected[str(account.id)] for account in accounts
        ):
            raise PreviewConflict("Sender account configuration changed after preview")
        profile = await self._session.get(UserSettings, self._owner_user_id)
        profile_version = profile.version if profile is not None else 0
        if profile_version != int(plan.account_versions.get("profile", -1)):
            raise PreviewConflict("Profile configuration changed after preview")
        deliveries = payload.get("deliveries")
        if not isinstance(deliveries, list) or len(deliveries) != len(plan.recipients):
            raise PreviewConflict("Preview delivery plan is invalid")
        if [row.get("recipient") for row in deliveries] != plan.recipients:
            raise PreviewConflict("Preview recipients do not match the exact plan")

    async def _validate_artifact_bindings(self, plan: PreviewPlan, payload: dict) -> None:
        bindings = payload.get("artifacts")
        if bindings != plan.attachment_refs or not isinstance(bindings, list):
            raise PreviewConflict("Preview artifact bindings changed")
        for binding in bindings:
            try:
                artifact_id = UUID(binding["artifact_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise PreviewConflict("Preview artifact binding is invalid") from exc
            artifact = await OwnedRepository(
                self._session, Artifact, self._owner_user_id
            ).get(artifact_id)
            if artifact is None or (
                artifact.sha256 != binding.get("sha256")
                or artifact.byte_size != binding.get("byte_size")
                or artifact.mime_type != binding.get("mime_type")
            ):
                raise PreviewConflict("Preview artifact metadata changed")
            if binding.get("evidence_set_id"):
                evidence = await OwnedRepository(
                    self._session, EvidenceSet, self._owner_user_id
                ).get(UUID(binding["evidence_set_id"]))
                if evidence is None or evidence.version != binding.get("evidence_version"):
                    raise PreviewConflict("Evidence changed after preview")
            try:
                self._artifact_store.read_verified(
                    storage_key=artifact.storage_key,
                    expected_size=artifact.byte_size,
                    expected_sha256=artifact.sha256,
                )
            except (ArtifactIntegrityError, FileNotFoundError) as exc:
                raise PreviewConflict("Preview artifact failed integrity validation") from exc

    def _audit(self, action: str, resource_id: UUID, metadata: dict) -> None:
        self._session.add(AuditEvent(
            owner_user_id=self._owner_user_id,
            actor_user_id=self._actor_user_id,
            action=action,
            resource_type="preview_plan",
            resource_id=resource_id,
            outcome="success",
            metadata_allowlist=metadata,
            request_id=self._request_id,
        ))

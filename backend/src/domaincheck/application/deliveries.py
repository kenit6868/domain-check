from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.application.previews import PreviewConflict, PreviewService
from domaincheck.domain.deliveries import (
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_SENDING,
    DELIVERY_SENT,
    DELIVERY_UNKNOWN,
    DeliveryBusinessIdentity,
    DeliveryIntent,
    business_key,
    stable_message_id,
    validate_attempt_result,
    validate_workflow,
)
from domaincheck.infrastructure.db.models import Delivery, DeliveryAttempt, PreviewPlan
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.ports.artifacts import ArtifactStore


class DeliveryNotFound(ValueError):
    pass


class DeliveryConflict(ValueError):
    pass


class DeliveryService:
    """Durable boundary around an SMTP side effect.

    ``claim_intent`` only records an intent. Its transaction must commit before
    a caller invokes a transport. The caller then checkpoints SENT, FAILED or
    UNKNOWN in a separate transaction. This intentionally does not promise
    exactly-once SMTP delivery.
    """

    def __init__(self, session: AsyncSession, artifact_store: ArtifactStore, *,
                 owner_user_id: UUID, actor_user_id: UUID, request_id: str) -> None:
        self._session = session
        self._artifact_store = artifact_store
        self._owner_user_id = owner_user_id
        self._actor_user_id = actor_user_id
        self._request_id = request_id
        self._deliveries = OwnedRepository(session, Delivery, owner_user_id)

    def _previews(self) -> PreviewService:
        return PreviewService(
            self._session, self._artifact_store,
            owner_user_id=self._owner_user_id,
            actor_user_id=self._actor_user_id,
            request_id=self._request_id,
        )

    async def materialize(self, *, preview_id: UUID, expected_revision: int,
                          workflow: str,
                          identities: list[DeliveryBusinessIdentity],
                          now: datetime | None = None) -> list[Delivery]:
        validate_workflow(workflow)
        observed = now or datetime.now(UTC)
        exact = await self._previews().get_exact(
            preview_id, expected_revision=expected_revision,
            require_approved=True, for_update=True, now=observed,
        )
        rows = exact.payload["deliveries"]
        if len(identities) != len(rows) or {item.delivery_index for item in identities} != set(range(len(rows))):
            raise ValueError("Every exact preview delivery needs one server business identity")
        by_index = {item.delivery_index: item for item in identities}
        result: list[Delivery] = []
        for index, exact_row in enumerate(rows):
            key = business_key(
                workflow=workflow,
                business_identity=by_index[index].business_identity,
                recipient=exact_row["recipient"], channel=exact_row["channel"],
                draft_identity=exact_row["draft_identity"],
            )
            await self._lock_business_key(UUID(exact_row["sender_account_id"]), key)
            existing = await self._session.scalar(select(Delivery).where(
                Delivery.owner_user_id == self._owner_user_id,
                Delivery.sender_account_id == UUID(exact_row["sender_account_id"]),
                Delivery.business_key == key,
            ).with_for_update())
            if existing is not None:
                # A draft revision may be replaced before any accepted or
                # uncertain side effect. Keep the business ledger row and
                # Message-ID, but bind a pending/definitely-failed delivery to
                # the newly approved exact plan. SENT/SENDING/UNKNOWN are
                # immutable and can never gain a fresh send opportunity here.
                if existing.preview_id != preview_id and existing.state in {
                    DELIVERY_PENDING, DELIVERY_FAILED,
                }:
                    existing.preview_id = preview_id
                    existing.version += 1
                    await self._session.flush()
                result.append(existing)
                continue
            delivery_id = uuid4()
            row = Delivery(
                id=delivery_id,
                owner_user_id=self._owner_user_id,
                preview_id=preview_id,
                sender_account_id=UUID(exact_row["sender_account_id"]),
                recipient=exact_row["recipient"],
                workflow=workflow,
                business_key=key,
                channel=exact_row["channel"],
                draft_identity=exact_row["draft_identity"],
                message_id=stable_message_id(delivery_id),
                state=DELIVERY_PENDING,
                claim_token=None,
                claim_expires_at=None,
            )
            self._session.add(row)
            await self._session.flush()
            result.append(row)
        return result

    async def get(self, delivery_id: UUID, *, for_update: bool = False) -> Delivery:
        row = await self._deliveries.get(delivery_id, for_update=for_update)
        if row is None:
            raise DeliveryNotFound("Delivery not found")
        return row

    async def claim_intent(self, delivery_id: UUID, *, expected_version: int,
                           lease_seconds: int = 120,
                           now: datetime | None = None) -> DeliveryIntent:
        if not 30 <= lease_seconds <= 900:
            raise ValueError("Delivery lease must be between 30 and 900 seconds")
        observed = now or datetime.now(UTC)
        observed_delivery = await self.get(delivery_id)
        if observed_delivery.version != expected_version or observed_delivery.state != DELIVERY_PENDING:
            raise DeliveryConflict("Delivery is not claimable")
        plan = await self._session.scalar(select(PreviewPlan).where(
            PreviewPlan.owner_user_id == self._owner_user_id,
            PreviewPlan.id == observed_delivery.preview_id,
        ))
        if plan is None:
            raise DeliveryConflict("Delivery preview is unavailable")
        exact = await self._previews().get_exact(
            plan.id, expected_revision=plan.revision,
            require_approved=True, for_update=True, now=observed,
        )
        delivery = await self.get(delivery_id, for_update=True)
        if delivery.version != expected_version or delivery.state != DELIVERY_PENDING:
            raise DeliveryConflict("Delivery is not claimable")
        exact_row = self._match_exact_delivery(delivery, exact.payload["deliveries"])
        attempt_number = int(await self._session.scalar(select(
            func.coalesce(func.max(DeliveryAttempt.attempt_number), 0)
        ).where(DeliveryAttempt.delivery_id == delivery.id)) or 0) + 1
        token = secrets.token_urlsafe(32)
        attempt = DeliveryAttempt(
            owner_user_id=self._owner_user_id,
            delivery_id=delivery.id,
            attempt_number=attempt_number,
            smtp_stage="intent_recorded",
            state="intent",
            started_at=observed,
            finished_at=None,
            sanitized_result={},
        )
        delivery.state = DELIVERY_SENDING
        delivery.claim_token = token
        delivery.claim_expires_at = observed + timedelta(seconds=lease_seconds)
        if delivery.message_id is None:
            delivery.message_id = stable_message_id(delivery.id)
        delivery.version += 1
        self._session.add(attempt)
        await self._session.flush()
        return DeliveryIntent(
            delivery_id=delivery.id,
            attempt_id=attempt.id,
            attempt_number=attempt_number,
            claim_token=token,
            message_id=delivery.message_id,
            exact_delivery=dict(exact_row),
            artifacts=tuple(dict(item) for item in exact.payload.get("artifacts", [])),
        )

    async def checkpoint(self, delivery_id: UUID, *, attempt_id: UUID, claim_token: str,
                         outcome: str, smtp_stage: str, result: dict,
                         now: datetime | None = None) -> Delivery:
        if outcome not in {DELIVERY_SENT, DELIVERY_FAILED, DELIVERY_UNKNOWN}:
            raise ValueError("Delivery outcome is invalid")
        if not smtp_stage or len(smtp_stage) > 80:
            raise ValueError("SMTP stage is invalid")
        metadata = validate_attempt_result(result)
        observed = now or datetime.now(UTC)
        delivery = await self.get(delivery_id, for_update=True)
        if delivery.state != DELIVERY_SENDING or not secrets.compare_digest(
            delivery.claim_token or "", claim_token,
        ):
            raise DeliveryConflict("Delivery claim is no longer active")
        attempt = await self._session.scalar(select(DeliveryAttempt).where(
            DeliveryAttempt.owner_user_id == self._owner_user_id,
            DeliveryAttempt.delivery_id == delivery.id,
            DeliveryAttempt.id == attempt_id,
            DeliveryAttempt.state == "intent",
        ).with_for_update())
        if attempt is None:
            raise DeliveryConflict("Delivery attempt is unavailable")
        attempt.state = outcome
        attempt.smtp_stage = smtp_stage
        attempt.finished_at = observed
        attempt.sanitized_result = metadata
        delivery.state = outcome
        delivery.claim_token = None
        delivery.claim_expires_at = None
        delivery.version += 1
        await self._session.flush()
        return delivery

    async def retry_failed(self, delivery_id: UUID, *, expected_version: int,
                           now: datetime | None = None) -> Delivery:
        observed = now or datetime.now(UTC)
        delivery = await self.get(delivery_id, for_update=True)
        if delivery.version != expected_version or delivery.state != DELIVERY_FAILED:
            raise DeliveryConflict("Only a definitely failed delivery can be retried")
        plan = await self._session.scalar(select(PreviewPlan).where(
            PreviewPlan.owner_user_id == self._owner_user_id,
            PreviewPlan.id == delivery.preview_id,
        ))
        if plan is None:
            raise DeliveryConflict("Delivery preview is unavailable")
        await self._previews().get_exact(
            plan.id, expected_revision=plan.revision,
            require_approved=True, for_update=True, now=observed,
        )
        delivery.state = DELIVERY_PENDING
        delivery.claim_token = None
        delivery.claim_expires_at = None
        delivery.version += 1
        await self._session.flush()
        return delivery

    @staticmethod
    def _match_exact_delivery(delivery: Delivery, rows: list[dict]) -> dict:
        matches = [row for row in rows if (
            row.get("sender_account_id") == str(delivery.sender_account_id)
            and row.get("recipient") == delivery.recipient
            and row.get("channel") == delivery.channel
            and row.get("draft_identity") == delivery.draft_identity
        )]
        if len(matches) != 1:
            raise PreviewConflict("Delivery no longer matches its exact preview")
        return matches[0]

    async def _lock_business_key(self, account_id: UUID, key: str) -> None:
        digest = hashlib.sha256(
            f"{self._owner_user_id}:{account_id}:{key}".encode("ascii")
        ).digest()
        lock_key = int.from_bytes(digest[:8], "big", signed=True)
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))


class DeliveryRecoveryRepository:
    """Worker-only recovery; trusted owner comes from each persisted row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def mark_expired_unknown(self, *, now: datetime | None = None,
                                   limit: int = 100) -> list[Delivery]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        observed = now or datetime.now(UTC)
        rows = list(await self._session.scalars(select(Delivery).where(
            Delivery.state == DELIVERY_SENDING,
            Delivery.claim_expires_at < observed,
        ).order_by(Delivery.claim_expires_at, Delivery.id).with_for_update(skip_locked=True).limit(limit)))
        for delivery in rows:
            attempt = await self._session.scalar(select(DeliveryAttempt).where(
                DeliveryAttempt.owner_user_id == delivery.owner_user_id,
                DeliveryAttempt.delivery_id == delivery.id,
                DeliveryAttempt.state == "intent",
            ).order_by(DeliveryAttempt.attempt_number.desc()).with_for_update().limit(1))
            if attempt is not None:
                attempt.state = DELIVERY_UNKNOWN
                attempt.smtp_stage = "checkpoint_missing"
                attempt.finished_at = observed
                attempt.sanitized_result = {"reason_code": "claim_expired_after_intent"}
            delivery.state = DELIVERY_UNKNOWN
            delivery.claim_token = None
            delivery.claim_expires_at = None
            delivery.version += 1
        await self._session.flush()
        return rows

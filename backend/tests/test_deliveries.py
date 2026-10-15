from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from domaincheck.application.deliveries import (
    DeliveryConflict,
    DeliveryNotFound,
    DeliveryRecoveryRepository,
    DeliveryService,
)
from domaincheck.application.previews import PreviewService
from domaincheck.domain.deliveries import DeliveryBusinessIdentity
from domaincheck.domain.previews import PreparedDelivery
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.db.models import (
    DeliveryAttempt,
    ReviewCase,
    SenderAccount,
    User,
    UserSettings,
)
from domaincheck.infrastructure.db.session import Database


TEST_DATABASE_URL = os.environ.get("DOMAINCHECK_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "requires isolated PostgreSQL test database")
class DeliveryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = Database(TEST_DATABASE_URL or "")
        self.temporary = tempfile.TemporaryDirectory()
        self.store = FilesystemArtifactStore(Path(self.temporary.name))
        self.now = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)

    async def asyncTearDown(self):
        await self.database.dispose()
        self.temporary.cleanup()

    async def _fixture(self, session, suffix: str):
        owner = User(display_name=f"Owner {suffix}", status="active", timezone="Asia/Bangkok")
        session.add(owner)
        await session.flush()
        account = SenderAccount(
            owner_user_id=owner.id,
            label="Fixture sender",
            normalized_identity=f"{suffix}@example.test",
            enabled=True,
            is_default=False,
            smtp_host="mail.example.test",
            smtp_port=465,
            smtp_username=f"{suffix}@example.test",
            smtp_transport="implicit_tls",
            imap_config={}, proxy_config={}, signature_overrides={}, secret_metadata={},
        )
        case = ReviewCase(
            owner_user_id=owner.id,
            case_type="cloaking",
            canonical_url_key=f"case-{suffix}",
            full_url=f"https://{suffix}.example.test/login",
            business_day=date(2026, 10, 15),
            timezone="Asia/Bangkok",
            disposition="confirmed_cloaking",
            state="pending",
            evidence_version=1,
            required_account_ids=[],
        )
        session.add_all([UserSettings(owner_user_id=owner.id), account, case])
        await session.flush()
        previews = PreviewService(
            session, self.store, owner_user_id=owner.id, actor_user_id=owner.id,
            request_id="delivery-preview-test",
        )
        exact = await previews.create_revision(
            resource_type="review_case", resource_id=case.id,
            feature="cloaking_review", policy_version="cloaking-report-v1",
            deliveries=[PreparedDelivery(
                sender_account_id=account.id,
                recipient="abuse@example.test",
                channel="registrar",
                draft_identity="registrar-v1",
                subject="Abuse report",
                text_body=f"Reported URL: {case.full_url}\n\nPlease investigate.",
                html_body=f"<p>Reported URL: {case.full_url}</p>",
            )],
            now=self.now,
        )
        await previews.approve(exact.preview_id, expected_revision=exact.revision,
                               confirmed=True, now=self.now)
        return owner, account, case, exact

    def _service(self, session, owner):
        return DeliveryService(
            session, self.store, owner_user_id=owner.id, actor_user_id=owner.id,
            request_id="delivery-test",
        )

    async def _materialize(self, service, exact, identity="case:one"):
        rows = await service.materialize(
            preview_id=exact.preview_id,
            expected_revision=exact.revision,
            workflow="cloaking_review",
            identities=[DeliveryBusinessIdentity(0, identity)],
            now=self.now,
        )
        return rows[0]

    async def test_business_key_is_idempotent_and_message_id_is_stable(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner, _account, _case, exact = await self._fixture(session, f"idem-{uuid4().hex}")
            service = self._service(session, owner)
            first = await self._materialize(service, exact)
            second = await self._materialize(service, exact)
            self.assertEqual(first.id, second.id)
            self.assertEqual(first.message_id, f"<{first.id.hex}@delivery.domaincheck.invalid>")
            self.assertNotIn(str(exact.preview_id), first.business_key)
            await transaction.rollback()

    async def test_new_approved_revision_rebinds_only_unsent_business_delivery(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner, account, case, exact = await self._fixture(session, f"revision-{uuid4().hex}")
            service = self._service(session, owner)
            row = await self._materialize(service, exact)
            original_message_id = row.message_id
            previews = PreviewService(
                session, self.store, owner_user_id=owner.id, actor_user_id=owner.id,
                request_id="delivery-revision-test",
            )
            replacement = await previews.create_revision(
                resource_type="review_case", resource_id=case.id,
                feature="cloaking_review", policy_version="cloaking-report-v1",
                deliveries=[PreparedDelivery(
                    sender_account_id=account.id, recipient="abuse@example.test",
                    channel="registrar", draft_identity="registrar-v1",
                    subject="Edited abuse report",
                    text_body=f"Reported URL: {case.full_url}\n\nUpdated exact draft.",
                )], now=self.now,
            )
            await previews.approve(
                replacement.preview_id, expected_revision=replacement.revision,
                confirmed=True, now=self.now,
            )
            rebound = await self._materialize(service, replacement)
            self.assertEqual(rebound.id, row.id)
            self.assertEqual(rebound.preview_id, replacement.preview_id)
            self.assertEqual(rebound.message_id, original_message_id)
            await transaction.rollback()

    async def test_intent_must_commit_before_side_effect_and_success_is_terminal(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner, _account, _case, exact = await self._fixture(session, f"sent-{uuid4().hex}")
            service = self._service(session, owner)
            row = await self._materialize(service, exact)
            intent = await service.claim_intent(row.id, expected_version=row.version, now=self.now)
            self.assertEqual(row.state, "sending")
            self.assertEqual(intent.exact_delivery["subject"], "Abuse report")
            sent = await service.checkpoint(
                row.id, attempt_id=intent.attempt_id, claim_token=intent.claim_token,
                outcome="sent", smtp_stage="smtp_accepted",
                result={"response_code": 250, "sent_copy": "failed"},
                now=self.now + timedelta(seconds=1),
            )
            self.assertEqual(sent.state, "sent")
            self.assertEqual(sent.message_id, intent.message_id)
            with self.assertRaises(DeliveryConflict):
                await service.claim_intent(sent.id, expected_version=sent.version, now=self.now)
            await transaction.rollback()

    async def test_failed_retry_reuses_message_id_but_unknown_cannot_retry(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner, _account, _case, exact = await self._fixture(session, f"retry-{uuid4().hex}")
            service = self._service(session, owner)
            row = await self._materialize(service, exact)
            first = await service.claim_intent(row.id, expected_version=row.version, now=self.now)
            failed = await service.checkpoint(
                row.id, attempt_id=first.attempt_id, claim_token=first.claim_token,
                outcome="failed", smtp_stage="connect_failed",
                result={"error_code": "connection_timeout"}, now=self.now,
            )
            pending = await service.retry_failed(failed.id, expected_version=failed.version, now=self.now)
            second = await service.claim_intent(pending.id, expected_version=pending.version, now=self.now)
            self.assertEqual(second.attempt_number, 2)
            self.assertEqual(second.message_id, first.message_id)
            unknown = await service.checkpoint(
                row.id, attempt_id=second.attempt_id, claim_token=second.claim_token,
                outcome="unknown", smtp_stage="connection_lost_after_data",
                result={"error_code": "outcome_uncertain"}, now=self.now,
            )
            with self.assertRaises(DeliveryConflict):
                await service.retry_failed(unknown.id, expected_version=unknown.version, now=self.now)
            await transaction.rollback()

    async def test_expired_intent_recovers_to_unknown_without_retry(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner, _account, _case, exact = await self._fixture(session, f"crash-{uuid4().hex}")
            service = self._service(session, owner)
            row = await self._materialize(service, exact)
            intent = await service.claim_intent(
                row.id, expected_version=row.version, lease_seconds=30, now=self.now,
            )
            recovered = await DeliveryRecoveryRepository(session).mark_expired_unknown(
                now=self.now + timedelta(seconds=31),
            )
            self.assertEqual([item.id for item in recovered], [row.id])
            self.assertEqual(row.state, "unknown")
            attempt = await session.get(DeliveryAttempt, intent.attempt_id)
            self.assertEqual(attempt.state, "unknown")
            self.assertEqual(attempt.sanitized_result["reason_code"], "claim_expired_after_intent")
            with self.assertRaises(DeliveryConflict):
                await service.retry_failed(row.id, expected_version=row.version, now=self.now)
            await transaction.rollback()

    async def test_owner_boundary_and_result_secret_rejection(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner_a, _account, _case, exact = await self._fixture(session, f"owner-a-{uuid4().hex}")
            owner_b, _account_b, _case_b, _exact_b = await self._fixture(session, f"owner-b-{uuid4().hex}")
            service_a = self._service(session, owner_a)
            row = await self._materialize(service_a, exact)
            with self.assertRaises(DeliveryNotFound):
                await self._service(session, owner_b).get(row.id)
            intent = await service_a.claim_intent(row.id, expected_version=row.version, now=self.now)
            with self.assertRaises(ValueError):
                await service_a.checkpoint(
                    row.id, attempt_id=intent.attempt_id, claim_token=intent.claim_token,
                    outcome="failed", smtp_stage="auth_failed",
                    result={"smtp_password": "must-not-persist"}, now=self.now,
                )
            await transaction.rollback()

    async def test_two_sessions_only_one_can_claim(self):
        suffix = f"tabs-{uuid4().hex}"
        async with self.database.session_factory() as setup:
            async with setup.begin():
                owner, _account, _case, exact = await self._fixture(setup, suffix)
                row = await self._materialize(self._service(setup, owner), exact)
                owner_id, delivery_id, version = owner.id, row.id, row.version

        async def claim():
            async with self.database.session_factory() as session:
                async with session.begin():
                    service = DeliveryService(
                        session, self.store, owner_user_id=owner_id, actor_user_id=owner_id,
                        request_id="parallel-tab",
                    )
                    try:
                        return await service.claim_intent(
                            delivery_id, expected_version=version, now=self.now,
                        )
                    except DeliveryConflict:
                        return None

        outcomes = await asyncio.gather(claim(), claim())
        self.assertEqual(sum(item is not None for item in outcomes), 1)

from __future__ import annotations

import io
import os
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image
from sqlalchemy import select

from domaincheck.application.previews import PreviewConflict, PreviewNotFound, PreviewService
from domaincheck.domain.previews import PreparedDelivery
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.db.models import (
    Artifact,
    AuditEvent,
    EvidenceSet,
    PreviewPlan,
    ReviewCase,
    SenderAccount,
    User,
    UserSettings,
)
from domaincheck.infrastructure.db.session import Database
from domaincheck.ports.artifacts import ImageUpload


TEST_DATABASE_URL = os.environ.get("DOMAINCHECK_TEST_DATABASE_URL")


def sender(owner: User, identity: str) -> SenderAccount:
    return SenderAccount(
        owner_user_id=owner.id,
        label="Fixture sender",
        normalized_identity=identity,
        enabled=True,
        is_default=False,
        smtp_host="mail.example.test",
        smtp_port=465,
        smtp_username=identity,
        smtp_transport="implicit_tls",
        imap_config={},
        proxy_config={},
        signature_overrides={},
        secret_metadata={},
    )


def review_case(owner: User, suffix: str) -> ReviewCase:
    return ReviewCase(
        owner_user_id=owner.id,
        case_type="cloaking",
        canonical_url_key=f"case-{suffix}",
        full_url=f"https://{suffix}.example.test/login?flow=1",
        business_day=date(2026, 10, 15),
        timezone="Asia/Bangkok",
        disposition="confirmed_cloaking",
        state="pending",
        evidence_version=1,
        required_account_ids=[],
    )


def delivery(account_id: UUID, *, subject: str = "Abuse report") -> PreparedDelivery:
    return PreparedDelivery(
        sender_account_id=account_id,
        recipient="abuse@example.test",
        channel="registrar",
        draft_identity="registrar-v1",
        subject=subject,
        text_body="Reported URL: https://preview.example.test/login\n\nPlease investigate.",
        html_body="<p>Reported URL: https://preview.example.test/login</p>",
    )


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 2), color=(20, 40, 60)).save(output, format="PNG")
    return output.getvalue()


@unittest.skipUnless(TEST_DATABASE_URL, "requires isolated PostgreSQL test database")
class PreviewServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = Database(TEST_DATABASE_URL or "")
        self.temporary = tempfile.TemporaryDirectory()
        self.store = FilesystemArtifactStore(Path(self.temporary.name))

    async def asyncTearDown(self):
        await self.database.dispose()
        self.temporary.cleanup()

    async def _fixture(self, session, *, suffix: str = "one"):
        owner = User(display_name=f"Owner {suffix}", status="active", timezone="Asia/Bangkok")
        session.add(owner)
        await session.flush()
        profile = UserSettings(owner_user_id=owner.id)
        account = sender(owner, f"{suffix}@example.test")
        case = review_case(owner, suffix)
        session.add_all([profile, account, case])
        await session.flush()
        evidence = EvidenceSet(
            owner_user_id=owner.id,
            check_id=None,
            case_id=case.id,
            requested_url=case.full_url,
            landing_url=case.full_url,
            destination_url=None,
            capture_mode="manual_upload",
            manifest={"schema_version": 1},
        )
        session.add(evidence)
        await session.flush()
        stored = self.store.store_evidence_images(
            owner_user_id=owner.id,
            evidence_set_id=evidence.id,
            uploads=[ImageUpload("evidence.png", "image/png", png_bytes())],
        )[0]
        artifact = Artifact(
            id=stored.artifact_id,
            owner_user_id=owner.id,
            evidence_set_id=evidence.id,
            kind="cloaking_review:evidence",
            mime_type=stored.mime_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            storage_key=stored.storage_key,
            original_name=stored.original_name,
            immutable=True,
        )
        session.add(artifact)
        await session.flush()
        return owner, profile, account, case, evidence, artifact

    def _service(self, session, owner: User) -> PreviewService:
        return PreviewService(
            session,
            self.store,
            owner_user_id=owner.id,
            actor_user_id=owner.id,
            request_id="preview-test",
        )

    async def test_exact_body_is_private_approved_and_audit_does_not_copy_content(self):
        observed = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner, _profile, account, case, _evidence, artifact = await self._fixture(session)
            service = self._service(session, owner)
            exact = await service.create_revision(
                resource_type="review_case",
                resource_id=case.id,
                feature="cloaking_review",
                policy_version="cloaking-report-v1",
                deliveries=[delivery(account.id)],
                attachment_artifact_ids=[artifact.id],
                now=observed,
            )
            plan = await session.get(PreviewPlan, exact.preview_id)
            draft_artifact = await session.get(Artifact, plan.draft_artifact_id)
            self.assertNotIn("subject", plan.__table__.c)
            self.assertEqual(draft_artifact.sha256, exact.fingerprint)
            self.assertEqual(exact.payload["deliveries"][0]["subject"], "Abuse report")
            with self.assertRaises(PreviewConflict):
                await service.get_exact(exact.preview_id, require_approved=True, now=observed)
            await service.approve(
                exact.preview_id, expected_revision=1, confirmed=True, now=observed,
            )
            restored = await service.get_exact(
                exact.preview_id, expected_revision=1, require_approved=True, now=observed,
            )
            self.assertEqual(restored.payload, exact.payload)
            audits = list(await session.scalars(select(AuditEvent).where(
                AuditEvent.owner_user_id == owner.id,
                AuditEvent.resource_type == "preview_plan",
            )))
            self.assertEqual(len(audits), 2)
            audit_text = repr([row.metadata_allowlist for row in audits])
            self.assertNotIn("Abuse report", audit_text)
            self.assertNotIn("abuse@example.test", audit_text)
            await transaction.rollback()

    async def test_new_revision_invalidates_old_preview_and_client_cannot_supply_fingerprint(self):
        observed = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner, _profile, account, case, _evidence, artifact = await self._fixture(session, suffix="revision")
            service = self._service(session, owner)
            first = await service.create_revision(
                resource_type="review_case", resource_id=case.id,
                feature="cloaking_review", policy_version="v1",
                deliveries=[delivery(account.id, subject="First")],
                attachment_artifact_ids=[artifact.id], now=observed,
            )
            second = await service.create_revision(
                resource_type="review_case", resource_id=case.id,
                feature="cloaking_review", policy_version="v1",
                deliveries=[delivery(account.id, subject="Edited server revision")],
                attachment_artifact_ids=[artifact.id], now=observed,
            )
            self.assertEqual((first.revision, second.revision), (1, 2))
            self.assertNotEqual(first.fingerprint, second.fingerprint)
            with self.assertRaises(PreviewConflict):
                await service.approve(first.preview_id, expected_revision=1, confirmed=True, now=observed)
            with self.assertRaises(PreviewConflict):
                await service.approve(second.preview_id, expected_revision=1, confirmed=True, now=observed)
            await transaction.rollback()

    async def test_account_profile_evidence_expiry_and_file_mutation_make_preview_stale(self):
        observed = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner, profile, account, case, evidence, artifact = await self._fixture(session, suffix="stale")
            service = self._service(session, owner)

            async def make_preview():
                return await service.create_revision(
                    resource_type="review_case", resource_id=case.id,
                    feature="cloaking_review", policy_version="v1",
                    deliveries=[delivery(account.id)], attachment_artifact_ids=[artifact.id],
                    lifetime=timedelta(minutes=1), now=observed,
                )

            expired = await make_preview()
            with self.assertRaisesRegex(PreviewConflict, "expired"):
                await service.get_exact(expired.preview_id, now=observed + timedelta(minutes=2))

            account_changed = await make_preview()
            account.version += 1
            await session.flush()
            with self.assertRaisesRegex(PreviewConflict, "Sender account"):
                await service.get_exact(account_changed.preview_id, now=observed)
            account.version -= 1
            profile_changed = await make_preview()
            profile.version += 1
            await session.flush()
            with self.assertRaisesRegex(PreviewConflict, "Profile"):
                await service.get_exact(profile_changed.preview_id, now=observed)
            profile.version -= 1
            evidence_changed = await make_preview()
            evidence.version += 1
            await session.flush()
            with self.assertRaisesRegex(PreviewConflict, "Evidence"):
                await service.get_exact(evidence_changed.preview_id, now=observed)
            evidence.version -= 1
            mutated = await make_preview()
            evidence_path = self.store.root.joinpath(*artifact.storage_key.split("/"))
            evidence_path.write_bytes(b"mutated")
            with self.assertRaisesRegex(PreviewConflict, "integrity"):
                await service.get_exact(mutated.preview_id, now=observed)
            await transaction.rollback()

    async def test_cross_owner_resource_account_and_artifact_fail_closed(self):
        observed = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner_a, _profile_a, account_a, case_a, _evidence_a, artifact_a = await self._fixture(session, suffix="a")
            owner_b, _profile_b, account_b, case_b, _evidence_b, artifact_b = await self._fixture(session, suffix="b")
            service = self._service(session, owner_a)
            with self.assertRaises(PreviewNotFound):
                await service.create_revision(
                    resource_type="review_case", resource_id=case_b.id,
                    feature="cloaking_review", policy_version="v1",
                    deliveries=[delivery(account_a.id)], now=observed,
                )
            with self.assertRaises(PreviewConflict):
                await service.create_revision(
                    resource_type="review_case", resource_id=case_a.id,
                    feature="cloaking_review", policy_version="v1",
                    deliveries=[delivery(account_b.id)], now=observed,
                )
            with self.assertRaises(PreviewNotFound):
                await service.create_revision(
                    resource_type="review_case", resource_id=case_a.id,
                    feature="cloaking_review", policy_version="v1",
                    deliveries=[delivery(account_a.id)],
                    attachment_artifact_ids=[artifact_b.id], now=observed,
                )
            valid = await service.create_revision(
                resource_type="review_case", resource_id=case_a.id,
                feature="cloaking_review", policy_version="v1",
                deliveries=[delivery(account_a.id)],
                attachment_artifact_ids=[artifact_a.id], now=observed,
            )
            with self.assertRaises(PreviewNotFound):
                await self._service(session, owner_b).get_exact(valid.preview_id, now=observed)
            await transaction.rollback()


if __name__ == "__main__":
    unittest.main()

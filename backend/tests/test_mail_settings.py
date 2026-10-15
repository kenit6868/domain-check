from __future__ import annotations

import os
import unittest
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from domaincheck.api.dependencies import current_user, get_db_session
from domaincheck.application.auth import AuthenticatedUser
from domaincheck.application.mail_settings import (
    MailAccountValues,
    MailSettingsNotFound,
    MailSettingsService,
)
from domaincheck.core.mail import ConnectionTestResult
from domaincheck.infrastructure.db.models import AuditEvent, SenderAccount, User
from domaincheck.infrastructure.db.session import Database
from domaincheck.infrastructure.secrets import EncryptedValue, SecretEnvelope
from domaincheck.main import create_app
from domaincheck.settings import Settings


TEST_DATABASE_URL = os.environ.get("DOMAINCHECK_TEST_DATABASE_URL")


def values(identity: str, *, default: bool = False, label: str = "Sender") -> MailAccountValues:
    return MailAccountValues(
        label=label, smtp_host="mail.example.test", smtp_port=465,
        smtp_username=identity, smtp_transport="implicit_tls", enabled=True,
        is_default=default,
        imap_config={
            "host": "mail.example.test", "port": 993, "username": identity,
            "mailbox": "INBOX", "sent_mailbox": "Sent", "junk_mailbox": "Thư rác",
            "cloudflare_mailbox": "2-Cloudflare", "evidence_mailbox": "Evidence",
        },
        proxy_config={"scheme": None, "host": "", "port": None},
        signature_overrides={"contact_name": "Fixture", "contact_email": identity, "signature_role": "Review"},
    )


class RecordingConnectionTester:
    def __init__(self):
        self.smtp_specs = []
        self.imap_specs = []

    def test_smtp(self, spec):
        self.smtp_specs.append(spec)
        return ConnectionTestResult("success", "smtp", spec.transport, "authenticated", "ok", 1)

    def test_imap(self, spec):
        self.imap_specs.append(spec)
        return ConnectionTestResult("success", "imap", "implicit_tls", "authenticated", "ok", 1)


@unittest.skipUnless(TEST_DATABASE_URL, "requires isolated PostgreSQL test database")
class MailSettingsDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = Database(TEST_DATABASE_URL or "")
        self.envelope = SecretEnvelope({1: b"m" * 32}, active_version=1)

    async def asyncTearDown(self):
        await self.database.dispose()

    async def test_same_sender_identity_is_isolated_by_owner_and_cross_owner_is_hidden(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner_a = User(display_name="A", status="active", timezone="Asia/Bangkok")
            owner_b = User(display_name="B", status="active", timezone="Asia/Bangkok")
            session.add_all([owner_a, owner_b])
            await session.flush()
            account_a = await MailSettingsService(
                session, owner_user_id=owner_a.id, actor_user_id=owner_a.id,
                request_id="mail-a", envelope=self.envelope,
            ).create_account(values("shared@example.test"), {"smtp_password": "secret-a"})
            account_b = await MailSettingsService(
                session, owner_user_id=owner_b.id, actor_user_id=owner_b.id,
                request_id="mail-b", envelope=self.envelope,
            ).create_account(values("shared@example.test"), {"smtp_password": "secret-b"})
            self.assertNotEqual(account_a.id, account_b.id)
            with self.assertRaises(MailSettingsNotFound):
                await MailSettingsService(
                    session, owner_user_id=owner_a.id, actor_user_id=owner_a.id,
                    request_id="cross-owner", envelope=self.envelope,
                ).get_account(account_b.id)
            await transaction.rollback()

    async def test_mail_api_returns_404_for_another_owner_account_id(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner_a = User(display_name="A", status="active", timezone="Asia/Bangkok")
            owner_b = User(display_name="B", status="active", timezone="Asia/Bangkok")
            session.add_all([owner_a, owner_b])
            await session.flush()
            account_b = await MailSettingsService(
                session, owner_user_id=owner_b.id, actor_user_id=owner_b.id,
                request_id="mail-b-api", envelope=self.envelope,
            ).create_account(values("b@example.test"), {})
            actor_a = AuthenticatedUser(
                user_id=owner_a.id, display_name="A", telegram_username=None,
                status="active", timezone="Asia/Bangkok",
                permissions=frozenset({"mail_accounts.view_own"}),
                session_id=uuid4(), csrf_hash=b"x" * 32,
            )
            app = create_app(Settings(_env_file=None))

            async def same_transaction_session():
                yield session

            async def authenticated_owner_a():
                return actor_a

            app.dependency_overrides[get_db_session] = same_transaction_session
            app.dependency_overrides[current_user] = authenticated_owner_a
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(f"/api/v1/mail-accounts/{account_b.id}")
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("b@example.test", response.text)
            finally:
                await app.state.database.dispose()
                await app.state.redis.aclose()
            await transaction.rollback()

    async def test_secret_is_encrypted_owner_bound_and_omitted_patch_preserves_it(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner = User(display_name="Owner", status="active", timezone="Asia/Bangkok")
            session.add(owner)
            await session.flush()
            service = MailSettingsService(
                session, owner_user_id=owner.id, actor_user_id=owner.id,
                request_id="mail-secret", envelope=self.envelope,
            )
            account = await service.create_account(
                values("owner@example.test"),
                {"smtp_password": "smtp-fixture", "imap_password": "imap-fixture"},
            )
            before = account.encrypted_secret
            self.assertNotIn(b"smtp-fixture", before or b"")
            decoded = self.envelope.decrypt_json(
                EncryptedValue(account.encrypted_secret, account.secret_nonce, account.key_version),
                associated_data=f"{owner.id}:sender-account:{account.id}".encode(),
            )
            self.assertEqual(decoded["smtp_password"], "smtp-fixture")
            updated_values = values("owner@example.test", label="Renamed")
            await service.update_account(account.id, updated_values, {}, set())
            self.assertEqual(account.encrypted_secret, before)
            self.assertEqual(account.label, "Renamed")
            self.assertEqual(account.secret_metadata["fields"], ["imap_password", "smtp_password"])
            await transaction.rollback()

    async def test_setting_new_default_atomically_clears_previous_default(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner = User(display_name="Owner", status="active", timezone="Asia/Bangkok")
            session.add(owner)
            await session.flush()
            service = MailSettingsService(
                session, owner_user_id=owner.id, actor_user_id=owner.id,
                request_id="mail-default", envelope=self.envelope,
            )
            first = await service.create_account(values("one@example.test", default=True), {})
            second = await service.create_account(values("two@example.test", default=True), {})
            await session.refresh(first)
            self.assertFalse(first.is_default)
            self.assertTrue(second.is_default)
            count = int(await session.scalar(
                select(func.count()).select_from(SenderAccount).where(
                    SenderAccount.owner_user_id == owner.id,
                    SenderAccount.is_default.is_(True),
                )
            ) or 0)
            self.assertEqual(count, 1)
            await transaction.rollback()

    async def test_connection_tests_use_owned_decrypted_config_and_audit_only_metadata(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner = User(display_name="Owner", status="active", timezone="Asia/Bangkok")
            session.add(owner)
            await session.flush()
            service = MailSettingsService(
                session, owner_user_id=owner.id, actor_user_id=owner.id,
                request_id="mail-test-connection", envelope=self.envelope,
            )
            account = await service.create_account(
                values("owner@example.test"),
                {"smtp_password": "smtp-fixture", "imap_password": "imap-fixture"},
            )
            tester = RecordingConnectionTester()
            smtp_result = await service.test_smtp_connection(account.id, tester)
            imap_result = await service.test_imap_connection(account.id, tester)
            self.assertEqual((smtp_result.status, imap_result.status), ("success", "success"))
            self.assertEqual(tester.smtp_specs[0].password, "smtp-fixture")
            self.assertEqual(tester.imap_specs[0].password, "imap-fixture")
            audits = list(await session.scalars(
                select(AuditEvent).where(AuditEvent.request_id == "mail-test-connection")
            ))
            connection_audits = [item for item in audits if item.action.startswith("mail_accounts.test_")]
            self.assertEqual(len(connection_audits), 2)
            self.assertNotIn("fixture", repr([item.metadata_allowlist for item in connection_audits]))
            await transaction.rollback()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
import asyncio
import os
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from domaincheck.application.context import ExecutionContext
from domaincheck.infrastructure.db.models import FOUNDATION_TABLES
from domaincheck.infrastructure.db.base import Base
from domaincheck.domain.authz import DEFAULT_USER_PERMISSIONS, PERMISSIONS_BY_CODE, validate_permission_codes
from domaincheck.infrastructure.auth import generate_session_token, hash_token
from domaincheck.infrastructure.secrets import SecretEnvelope
from domaincheck.main import create_app
from domaincheck.settings import BACKEND_ENV_FILE, BACKEND_ROOT, Settings


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 15, tzinfo=timezone.utc)


class FoundationTests(unittest.TestCase):
    def test_runtime_env_is_owned_by_backend_package(self):
        self.assertEqual(BACKEND_ENV_FILE, BACKEND_ROOT / ".env")
        self.assertEqual(BACKEND_ENV_FILE.parent.name, "backend")
        self.assertTrue(Settings(_env_file=None).artifact_root.is_absolute())

    def test_metadata_contains_exact_foundation_table_set(self):
        self.assertEqual(
            FOUNDATION_TABLES,
            {
                "users", "auth_identities", "sessions", "roles", "permissions",
                "user_roles", "role_permissions", "user_settings", "sender_accounts",
                "targets", "checks", "jobs", "job_items", "review_cases",
                "case_sources", "preview_plans", "deliveries", "delivery_attempts",
                "evidence_sets", "artifacts", "cloudflare_records", "review_snapshots",
                "audit_events", "migration_runs", "provider_submissions",
            },
        )

    def test_every_owned_to_owned_foreign_key_carries_owner_scope(self):
        owned_tables = {
            table.name for table in Base.metadata.tables.values() if "owner_user_id" in table.c
        }
        violations = []
        for table in Base.metadata.tables.values():
            if table.name not in owned_tables:
                continue
            for constraint in table.foreign_key_constraints:
                remote_table = next(iter(constraint.elements)).column.table
                if remote_table.name not in owned_tables:
                    continue
                local_columns = set(constraint.column_keys)
                remote_columns = {element.column.name for element in constraint.elements}
                if "owner_user_id" not in local_columns or "owner_user_id" not in remote_columns:
                    violations.append(f"{table.name}->{remote_table.name}")
        self.assertEqual(violations, [])

    def test_settings_do_not_have_secret_defaults(self):
        settings = Settings(_env_file=None)
        self.assertIsNone(settings.telegram_client_secret)
        self.assertIsNone(settings.secret_master_key)
        with self.assertRaises(ValueError):
            settings.validate_for_production()
        self.assertNotIn("config.ini", repr(settings))

    def test_execution_context_is_immutable_and_checks_permissions(self):
        context = ExecutionContext(
            actor_user_id=uuid4(), owner_user_id=uuid4(), timezone="Asia/Bangkok",
            operation_id=uuid4(), request_id="req-test",
            permissions=frozenset({"quick_report.view"}), clock=FixedClock(),
        )
        context.require("quick_report.view")
        with self.assertRaises(PermissionError):
            context.require("quick_report.check")
        with self.assertRaises(FrozenInstanceError):
            context.timezone = "UTC"  # type: ignore[misc]

    def test_liveness_has_allowlisted_response(self):
        async def request():
            async with AsyncClient(
                transport=ASGITransport(app=create_app(Settings(_env_file=None))), base_url="http://test"
            ) as client:
                return await client.get("/api/v1/health/live")

        response = asyncio.run(request())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "domaincheck-api"})

    def test_telegram_route_fails_closed_when_not_configured(self):
        async def request():
            app = create_app(Settings(_env_file=None))
            app.state.oidc = None
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await client.get("/api/v1/auth/telegram/start")

        response = asyncio.run(request())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "http_503")
        self.assertTrue(response.json()["request_id"])
        self.assertNotIn("secret", response.text.lower())

    def test_telegram_status_exposes_configuration_state_without_secrets(self):
        async def run():
            transport = ASGITransport(app=create_app(Settings(_env_file=None)))
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get("/api/v1/auth/telegram/status")

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["configured"])
        self.assertNotIn("secret", response.text.lower())

    def test_telegram_denial_returns_to_login_without_exposing_provider_error(self):
        async def run():
            transport = ASGITransport(app=create_app(Settings(_env_file=None)))
            async with AsyncClient(
                transport=transport, base_url="http://test", follow_redirects=False,
            ) as client:
                return await client.get(
                    "/api/v1/auth/telegram/callback?error=access_denied&error_description=sensitive"
                )

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/?login_error=telegram_denied")
        self.assertNotIn("sensitive", response.text)

    def test_default_role_includes_business_permissions_and_excludes_administration(self):
        self.assertIn("cloaking_review.send", PERMISSIONS_BY_CODE)
        self.assertEqual(DEFAULT_USER_PERMISSIONS, frozenset(
            code for code in PERMISSIONS_BY_CODE if not code.startswith(("users.", "roles."))
        ))
        validate_permission_codes({"quick_report.view"})
        with self.assertRaises(ValueError):
            validate_permission_codes({"invented.permission"})

    def test_mail_connection_actions_are_explicit_post_routes(self):
        paths = create_app(Settings(_env_file=None)).openapi()["paths"]
        self.assertEqual(set(paths["/api/v1/mail-accounts/{account_id}/test-smtp"]), {"post"})
        self.assertEqual(set(paths["/api/v1/mail-accounts/{account_id}/test-imap"]), {"post"})

    def test_cache_clear_is_an_explicit_post_route(self):
        paths = create_app(Settings(_env_file=None)).openapi()["paths"]
        self.assertEqual(set(paths["/api/v1/cache/clear"]), {"post"})

    def test_private_artifact_download_is_an_explicit_get_route(self):
        paths = create_app(Settings(_env_file=None)).openapi()["paths"]
        self.assertEqual(set(paths["/api/v1/artifacts/{artifact_id}"]), {"get"})

    def test_secret_envelope_binds_owner_and_supports_rotation(self):
        owner_a = b"owner-a:sender-password"
        envelope = SecretEnvelope({1: os.urandom(32), 2: os.urandom(32)}, active_version=2)
        encrypted = envelope.encrypt_json({"password": "fixture-only"}, associated_data=owner_a)
        self.assertEqual(envelope.decrypt_json(encrypted, associated_data=owner_a), {"password": "fixture-only"})
        self.assertNotIn("fixture-only", repr(encrypted))
        with self.assertRaises(Exception):
            envelope.decrypt_json(encrypted, associated_data=b"owner-b:sender-password")

    def test_session_token_is_opaque_and_only_hash_is_persistable(self):
        token = generate_session_token()
        self.assertGreaterEqual(len(token), 32)
        digest = hash_token(token)
        self.assertEqual(len(digest), 32)
        self.assertNotIn(token.encode("utf-8"), digest)


if __name__ == "__main__":
    unittest.main()


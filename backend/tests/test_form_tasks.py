from __future__ import annotations

import os
import unittest
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from starlette.requests import Request

from domaincheck.api.form_tasks import _extension_origin
from domaincheck.application.form_tasks import (
    FormTaskConflict, FormTaskNotFound, FormTaskService,
)
from domaincheck.core.form_tasks import ProviderFormTask, QuickReportFormTaskAdapter
from domaincheck.infrastructure.db.models import Check, Target, User, UserSettings
from domaincheck.infrastructure.db.session import Database
from domaincheck.main import create_app
from domaincheck.settings import Settings


TEST_DATABASE_URL = os.environ.get("DOMAINCHECK_TEST_DATABASE_URL")


class FakeFormAdapter:
    def build(self, provider, *, target_url, domain, config):
        return ProviderFormTask(
            provider=provider,
            form_url=f"https://forms.example.test/{provider}",
            target_url=target_url,
            draft=f"Report {domain} for {config.get('brand_name', '')}",
        )


class FormTaskDomainTests(unittest.TestCase):
    def test_google_task_uses_characterized_formatter_and_microsoft_is_url_only(self):
        toolkit = SimpleNamespace(generate_safebrowsing_report_text=lambda domain, cfg, url: f"{cfg['brand_name']}:{domain}:{url}")
        with patch("domaincheck.core.form_tasks.importlib.import_module", return_value=toolkit):
            google = QuickReportFormTaskAdapter().build(
                "google_gsb", target_url="https://example.test/login", domain="example.test",
                config={"brand_name": "Fixture Brand"},
            )
        self.assertEqual(google.mode, "fill_only")
        self.assertIn("Fixture Brand:example.test", google.draft)
        self.assertNotIn("ptask", google.form_url)
        microsoft = QuickReportFormTaskAdapter().build(
            "microsoft_smartscreen", target_url="https://example.test/login",
            domain="example.test", config={},
        )
        self.assertEqual(microsoft.draft, "")
        self.assertEqual(microsoft.mode, "fill_only")

    def test_all_eight_providers_route_and_build_fill_only_public_payloads(self):
        from domaincheck.core.form_tasks import PROVIDER_FORMS, available_providers
        def draft(*args, **kwargs):
            return "Fixture English report"
        toolkit = SimpleNamespace(generate_safebrowsing_report_text=draft,
            generate_cloudflare_report_text=draft, get_registry_webform_draft_text=draft,
            get_webform_draft_text=draft, generate_community_report_text=draft)
        with patch("domaincheck.core.form_tasks.importlib.import_module", return_value=toolkit):
            for provider in PROVIDER_FORMS:
                task = QuickReportFormTaskAdapter().build(provider, target_url="https://fixture.xyz/login?q=1",
                    domain="fixture.xyz", config={"brand_name": "Fixture", "contact_email": "report@example.test"})
                self.assertEqual(task.mode, "fill_only")
                self.assertEqual(task.target_url, "https://fixture.xyz/login?q=1")
                self.assertTrue(task.form_url.startswith("https://"))
                if provider not in {"google_gsb", "microsoft_smartscreen"}:
                    self.assertEqual(task.contact_email, "report@example.test")
                if provider == "xyz_registry_abuse":
                    self.assertIn("submitticket.php?step=2&deptid=6", task.form_url)
        common = available_providers("fixture.test", {})
        self.assertEqual(set(common), {"google_gsb", "microsoft_smartscreen", "chongluadao", "coccoc_safe"})
        self.assertIn("cloudflare", available_providers("fixture.co", {"cloudflare": True}))
        self.assertIn("godaddy_phishing", available_providers("fixture.co", {"registrar": "GoDaddy.com, LLC"}))
        self.assertIn("registry_co_phishing", available_providers("fixture.co", {}))
        self.assertIn("xyz_registry_abuse", available_providers("fixture.xyz", {}))

    def test_api_contract_has_registration_exchange_status_and_owned_read(self):
        paths = create_app(Settings(_env_file=None)).openapi()["paths"]
        self.assertEqual(set(paths["/api/v1/extension/devices"]), {"post"})
        self.assertEqual(set(paths["/api/v1/quick-reports/checks/{check_id}/form-tasks"]), {"post"})
        self.assertEqual(set(paths["/api/v1/form-tasks/exchange"]), {"post"})
        self.assertEqual(set(paths["/api/v1/form-tasks/{task_id}/status"]), {"post"})
        self.assertEqual(set(paths["/api/v1/form-tasks/{task_id}"]), {"get"})

    def test_exchange_rejects_non_extension_origin(self):
        wrong_origin = Request({
            "type": "http", "method": "POST", "path": "/api/v1/form-tasks/exchange",
            "headers": [(b"origin", b"https://untrusted.example")],
        })
        with self.assertRaises(HTTPException) as raised:
            _extension_origin(wrong_origin)
        self.assertEqual(raised.exception.status_code, 403)

        extension_origin = Request({
            "type": "http", "method": "POST", "path": "/api/v1/form-tasks/exchange",
            "headers": [(b"origin", b"chrome-extension://abcdefghijklmnopabcdefghijklmnop")],
        })
        _extension_origin(extension_origin)


@unittest.skipUnless(TEST_DATABASE_URL, "requires isolated PostgreSQL test database")
class FormTaskServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = Database(TEST_DATABASE_URL or "")
        self.now = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        self.device_id = "device_fixture_1234567890"

    async def asyncTearDown(self):
        await self.database.dispose()

    async def _fixture(self, session, suffix):
        owner = User(display_name=f"Owner {suffix}", status="active", timezone="Asia/Bangkok")
        session.add(owner)
        await session.flush()
        session.add(UserSettings(owner_user_id=owner.id, brand_name=f"Brand {suffix}"))
        target = Target(
            owner_user_id=owner.id, full_url_identity=f"https://{suffix}.example.test/login",
            canonical_url_hash=uuid4().hex + uuid4().hex, hostname=f"{suffix}.example.test",
        )
        session.add(target)
        await session.flush()
        check = Check(
            owner_user_id=owner.id, actor_user_id=owner.id, target_id=target.id,
            requested_url=target.full_url_identity, workflow="quick_report",
            workflow_key=uuid4().hex, domain=target.hostname, business_day=date(2026, 10, 15),
            timezone="Asia/Bangkok", engine_version="fixture", status="succeeded",
            result={"cloudflare": False}, cache_provenance={},
        )
        session.add(check)
        await session.flush()
        return owner, check

    def _service(self, session, owner):
        return FormTaskService(
            session, owner_user_id=owner.id, actor_user_id=owner.id,
            request_id="form-task-test", now=self.now, ttl_seconds=300,
            adapter=FakeFormAdapter(),
        )

    async def test_registration_create_one_time_exchange_status_and_owner_read(self):
        async with self.database.session_factory() as session:
            async with session.begin():
                owner_a, check_a = await self._fixture(session, "a")
                owner_b, _ = await self._fixture(session, "b")
                service = self._service(session, owner_a)
                await service.register_device(self.device_id, "3.0.0")
                created = await service.create(check_a.id, provider="google_gsb", device_id=self.device_id)
                owner_a_id, owner_b_id, task_id = owner_a.id, owner_b.id, created.id
                self.assertNotIn(created.capability_token, repr((await service.get(task_id)).sanitized_state))
        async with self.database.session_factory() as session:
            async with session.begin():
                exchanged = await FormTaskService.exchange(
                    session, capability_token=created.capability_token,
                    device_id=self.device_id, now=self.now,
                )
                self.assertEqual(exchanged.payload.mode, "fill_only")
                self.assertEqual(exchanged.payload.provider, "google_gsb")
                self.assertNotIn(created.capability_token, exchanged.payload.form_url)
                with self.assertRaises(FormTaskConflict):
                    await FormTaskService.exchange(
                        session, capability_token=created.capability_token,
                        device_id=self.device_id, now=self.now,
                    )
                row = await FormTaskService.update_status(
                    session, task_id=task_id, status_token=exchanged.status_token,
                    state_name="FILLED", result="fields filled", adapter_id="google_gsb",
                    adapter_version="1.0.0", now=self.now,
                )
                self.assertEqual(row.sanitized_state["state"], "FILLED")
                owner_a = await session.get(User, owner_a_id)
                owner_b = await session.get(User, owner_b_id)
                self.assertEqual((await self._service(session, owner_a).get(task_id)).id, task_id)
                with self.assertRaises(FormTaskNotFound):
                    await self._service(session, owner_b).get(task_id)

    async def test_wrong_device_and_expired_capability_fail_closed(self):
        async with self.database.session_factory() as session:
            async with session.begin():
                owner, check = await self._fixture(session, "expiry")
                service = self._service(session, owner)
                await service.register_device(self.device_id, "3.0.0")
                wrong_device = await service.create(check.id, provider="microsoft_smartscreen", device_id=self.device_id)
                expired = await service.create(check.id, provider="google_gsb", device_id=self.device_id)
        async with self.database.session_factory() as session:
            async with session.begin():
                with self.assertRaises(FormTaskNotFound):
                    await FormTaskService.exchange(
                        session, capability_token=wrong_device.capability_token,
                        device_id="different_device_123456", now=self.now,
                    )
                with self.assertRaises(FormTaskConflict):
                    await FormTaskService.exchange(
                        session, capability_token=expired.capability_token,
                        device_id=self.device_id, now=self.now + timedelta(minutes=6),
                    )

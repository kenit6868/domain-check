from __future__ import annotations

import os
import asyncio
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from domaincheck.application.quick_reports import (
    QuickReportNotFound, QuickReportService, QuickReportWorker, canonical_target,
    quick_report_workflow_identity,
)
from domaincheck.core.quick_report import LegacyQuickReportAdapter, sanitize_quick_report_result
from domaincheck.infrastructure.db.models import User, Job
from domaincheck.infrastructure.db.session import Database
from domaincheck.main import create_app
from domaincheck.settings import Settings


TEST_DATABASE_URL = os.environ.get("DOMAINCHECK_TEST_DATABASE_URL")


class FakeCache:
    def __init__(self):
        self.values = {}

    async def get_json(self, scope):
        return self.values.get((scope.owner_user_id, scope.digest, scope.business_day))

    async def set_json(self, scope, value, **_kwargs):
        self.values[(scope.owner_user_id, scope.digest, scope.business_day)] = value
        return True


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def run(self, target, config):
        self.calls.append((target, config))
        return {
            "domain": "example.test",
            "cloudflare": True,
            "cdn_detected": ["Cloudflare"],
            "registrar": "Example Registrar",
            "registry_contact": {"abuse_email": "registry@example.test"},
            "report_recipients": [{"channel": "registrar", "email": "abuse@example.test"}],
            "cloaking": {
                "verdict": "NO_SIGNAL",
                "screenshot_paths": ["C:/private/evidence.png"],
                "raw_html": "<form>secret</form>",
            },
        }


class QuickReportDomainTests(unittest.TestCase):
    def test_worker_checks_dependencies_before_connecting_or_claiming_jobs(self):
        from domaincheck.workers import cli
        with patch.object(LegacyQuickReportAdapter, 'validate_runtime', side_effect=RuntimeError('missing dependencies')), patch.object(cli, 'Database') as database:
            with self.assertRaises(RuntimeError):
                asyncio.run(cli.run(once=True, poll_seconds=2))
            database.assert_not_called()

    def test_legacy_adapter_calls_only_run_cdn_check_with_resolved_config(self):
        calls = []
        toolkit = SimpleNamespace(
            run_cdn_check=lambda target, config: calls.append((target, config)) or {"domain": "example.test"}
        )
        with patch("domaincheck.core.quick_report.importlib.import_module", return_value=toolkit) as imported:
            result = LegacyQuickReportAdapter().run(
                "https://example.test/login", {"cloaking_vantage_points": []},
            )
        imported.assert_called_once_with("phishing_toolkit")
        self.assertEqual(calls, [("https://example.test/login", {"cloaking_vantage_points": []})])
        self.assertEqual(result, {"domain": "example.test"})

    def test_api_exposes_only_owned_operation_routes(self):
        paths = create_app(Settings(_env_file=None)).openapi()["paths"]
        self.assertEqual(set(paths["/api/v1/quick-reports/checks"]), {"post"})
        self.assertEqual(set(paths["/api/v1/quick-reports/checks/{operation_id}"]), {"get"})

    def test_canonical_target_preserves_full_path_query_and_scheme(self):
        self.assertEqual(
            canonical_target("http://Example.TEST/login?a=1"),
            ("http://example.test/login?a=1", "example.test"),
        )
        self.assertEqual(canonical_target("example.test"), ("https://example.test/", "example.test"))
        self.assertEqual(
            quick_report_workflow_identity("http://example.test:8080/login?a=1"),
            "https://example.test/login?a=1",
        )
        with self.assertRaises(ValueError):
            canonical_target("file:///etc/passwd")

    def test_result_sanitizer_removes_bodies_credentials_and_local_paths(self):
        result = sanitize_quick_report_result({
            "domain": "example.test",
            "cloaking": {"verdict": "POSSIBLE", "raw_html": "secret", "path": "C:/private.png"},
            "cookie": "secret",
        })
        self.assertEqual(result, {"domain": "example.test", "cloaking": {"verdict": "POSSIBLE"}})


@unittest.skipUnless(TEST_DATABASE_URL, "requires isolated PostgreSQL test database")
class QuickReportIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_check_renews_job_lease_before_checkpoint(self):
        async with self.database.session_factory() as session, session.begin():
            owner = await self._owner(session, uuid4().hex)
            operation = await self._service(session, owner).create(['https://slow.example.test/path'])
            job_id = operation.job.id
        worker = QuickReportWorker(self.database.session_factory, self.cache, self.adapter,
            now=lambda: self.now, heartbeat_seconds=0.01)
        original = worker._process_check
        async def slow(owner_id, check_id):
            await asyncio.sleep(0.12)
            return await original(owner_id, check_id)
        with patch.object(worker, '_process_check', side_effect=slow):
            await worker.run_next('slow-heartbeat-worker')
        async with self.database.session_factory() as session:
            job = await session.get(Job, job_id)
            self.assertEqual(job.state, 'completed')
            self.assertGreaterEqual(job.version, 5)
        self.assertEqual(len(self.adapter.calls), 1)

    async def asyncSetUp(self):
        self.database = Database(TEST_DATABASE_URL or "")
        self.cache = FakeCache()
        self.adapter = FakeAdapter()
        self.now = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)

    async def asyncTearDown(self):
        await self.database.dispose()

    async def _owner(self, session, suffix):
        owner = User(display_name=f"Owner {suffix}", status="active", timezone="Asia/Bangkok")
        session.add(owner)
        await session.flush()
        return owner

    def _service(self, session, owner):
        return QuickReportService(
            session, owner_user_id=owner.id, actor_user_id=owner.id,
            timezone=owner.timezone, request_id="quick-report-test", now=self.now,
        )

    async def test_operation_persists_full_urls_order_duplicates_and_worker_result(self):
        async with self.database.session_factory() as session:
            async with session.begin():
                owner = await self._owner(session, uuid4().hex)
                operation = await self._service(session, owner).create([
                    "https://example.test/login?a=1",
                    "https://second.example.test/path",
                    "https://example.test/login?a=1",
                ])
                owner_id, job_id = owner.id, operation.job.id
                self.assertEqual([row.requested_url for row in operation.checks], [
                    "https://example.test/login?a=1", "https://second.example.test/path",
                ])
                self.assertEqual(operation.duplicates, ("https://example.test/login?a=1",))
        worker = QuickReportWorker(
            self.database.session_factory, self.cache, self.adapter, now=lambda: self.now,
        )
        self.assertEqual(await worker.run_next("qr-worker-1"), job_id)
        async with self.database.session_factory() as session:
            async with session.begin():
                restored = await QuickReportService(
                    session, owner_user_id=owner_id, actor_user_id=owner_id,
                    timezone="Asia/Bangkok", request_id="restore", now=self.now,
                ).get(job_id)
                self.assertEqual(restored.job.state, "completed")
                self.assertEqual([row.status for row in restored.checks], ["succeeded", "succeeded"])
                self.assertEqual(restored.checks[0].result["cloaking"], {"verdict": "NO_SIGNAL"})
                self.assertEqual(restored.checks[0].cache_provenance["source"], "network")
        self.assertEqual(len(self.adapter.calls), 2)

    async def test_daily_cache_and_same_url_are_isolated_by_owner(self):
        async with self.database.session_factory() as session:
            async with session.begin():
                owner_a = await self._owner(session, f"a-{uuid4().hex}")
                owner_b = await self._owner(session, f"b-{uuid4().hex}")
                first = await self._service(session, owner_a).create(["example.test/login"])
                owner_a_id, first_id = owner_a.id, first.job.id
        worker = QuickReportWorker(self.database.session_factory, self.cache, self.adapter, now=lambda: self.now)
        await worker.run_next("qr-worker-a1")
        async with self.database.session_factory() as session:
            async with session.begin():
                owner_a = await session.get(User, owner_a_id)
                second = await self._service(session, owner_a).create(["example.test/login"])
                second_id = second.job.id
                third = await self._service(session, owner_b).create(["example.test/login"])
                third_id = third.job.id
        await worker.run_next("qr-worker-a2")
        await worker.run_next("qr-worker-b1")
        self.assertEqual(len(self.adapter.calls), 2)
        async with self.database.session_factory() as session:
            async with session.begin():
                second_restored = await QuickReportService(
                    session, owner_user_id=owner_a_id, actor_user_id=owner_a_id,
                    timezone="Asia/Bangkok", request_id="a", now=self.now,
                ).get(second_id)
                self.assertTrue(second_restored.checks[0].cache_provenance["reused"])
                with self.assertRaises(QuickReportNotFound):
                    await QuickReportService(
                        session, owner_user_id=owner_a_id, actor_user_id=owner_a_id,
                        timezone="Asia/Bangkok", request_id="cross-owner", now=self.now,
                    ).get(third_id)

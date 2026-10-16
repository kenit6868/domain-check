from __future__ import annotations

import asyncio
import io
import os
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from PIL import Image
from fastapi import HTTPException, Response
from sqlalchemy import func, select

from domaincheck.api.domain_worker import serialize
from domaincheck.application.domain_worker import DomainPrecheckWorker, DomainWorkerService, WorkerNotFound
from domaincheck.core.worker_precheck import LegacyWorkerPrecheckAdapter, disposition, email_examples, valid_recipients
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.db.models import CaseSource, Check, ReviewCase, User
from domaincheck.infrastructure.db.session import Database
from domaincheck.ports.artifacts import ImageUpload


class WorkerCoreTests(unittest.TestCase):
    def test_classification_fails_closed_and_no_email_never_queues_cloaking(self):
        recipient = [{"channel": "registrar", "email": "abuse@example.com"}]
        for verdict in ("LIKELY", "POSSIBLE", "INCONCLUSIVE", "UNRECOGNIZED", None):
            self.assertEqual(disposition([], {"verdict": verdict}), "excluded_no_email")
            self.assertEqual(disposition(recipient, {"verdict": verdict}), "cloaking_review")
        self.assertEqual(disposition(recipient, {"verdict": "NO_SIGNAL"}), "evidence_review")
        self.assertEqual(disposition(recipient, {"verdict": "BLOCKED_OR_UNAVAILABLE",
            "manual_review_required": True}), "evidence_review")
        self.assertEqual(disposition(recipient, {"verdict": "NO_SIGNAL", "coverage": {
            "multi_vantage_recommended": True}}), "cloaking_review")

    def test_email_filters_block_cloudflare_privacy_invalid_and_duplicates(self):
        rows = valid_recipients([{"channel": "registrar", "email": "abuse@example.com, invalid"},
            {"channel": "hosting", "email": "abuse@cloudflare.com, a@withheldforprivacy.com"},
            {"channel": "registry", "email": "ABUSE@example.com"}])
        self.assertEqual(rows, [{"channel": "registrar", "email": "abuse@example.com"}])

    def test_lookup_and_http_cloaking_are_parallel_and_do_not_write_legacy_state(self):
        import phishing_toolkit as toolkit
        import cloaking_detector as detector
        barrier = threading.Barrier(2)
        def who(_domain):
            barrier.wait(timeout=3)
            return {"registrar": "Namecheap", "emails": ["abuse@namecheap.com"]}
        def probe(target, **kwargs):
            barrier.wait(timeout=3)
            self.assertIsNone(kwargs["evidence_root"])
            self.assertEqual(target, "https://example.com/login?a=1")
            return {"verdict": "POSSIBLE", "evidence_path": "private", "password": "secret"}
        with patch.object(toolkit, "get_whois_info", side_effect=who), \
             patch.object(toolkit, "get_rdap_abuse_email", return_value={}), \
             patch.object(toolkit, "lookup_registry_contact", return_value={}), \
             patch.object(toolkit, "get_cert_info", return_value={}), \
             patch.object(toolkit, "scan_common_subdomains", return_value={}), \
             patch.object(detector, "probe_http_cloaking", side_effect=probe), \
             patch.object(toolkit, "run_check", side_effect=AssertionError("full pipeline")), \
             patch.object(toolkit, "log_case", side_effect=AssertionError("legacy write")):
            result = LegacyWorkerPrecheckAdapter().run("https://example.com/login?a=1", "example.com")
        self.assertEqual(result["disposition"], "cloaking_review")
        self.assertEqual(result["recipients"][0]["email"], "abuse@namecheap.com")
        self.assertNotIn("private", str(result))
        self.assertNotIn("secret", str(result))

    def test_email_bodies_match_legacy_written_drafts_including_exact_full_url(self):
        import phishing_toolkit as toolkit
        cfg = {"brand_name": "Fixture", "contact_name": "Operator", "contact_email": "operator@example.com"}
        domain, target = "fixture.com", "https://fixture.com/login?a=1"
        registry = {"source": "static_table", "registry": "Fixture Registry", "abuse_email": "abuse@registry.example"}
        hosting = {"org": "Fixture Host", "abuse_email": "abuse@hosting.example", "asn": "AS123"}
        who = {"registrar": "Namecheap", "emails": ["abuse@namecheap.com"]}
        recipients = toolkit.resolve_worker_report_recipients(who, {}, registry, hosting)
        with tempfile.TemporaryDirectory() as directory, patch.object(toolkit, "REPORTS_DIR", directory):
            paths = toolkit.generate_email_drafts(domain, {}, who, {}, cfg, target)
            paths.append(toolkit.generate_registry_draft(domain, registry, cfg, target))
            paths.append(toolkit.generate_hosting_draft(domain, "8.8.8.8", hosting, cfg))
            paths = [p for p in paths if p and any(channel in p for channel in ("registrar", "registry", "hosting"))]
            toolkit.append_reported_url_to_drafts(paths, target)
            old = {Path(p).read_text(encoding="utf-8") for p in paths}
            examples = email_examples(domain, target, {"recipients": recipients, "whois": who,
                "registry_contact": registry, "hosting_ip": "8.8.8.8", "origin_ip_whois": hosting}, cfg)
        self.assertEqual({item["draft"] for item in examples}, old)
        self.assertEqual(len(examples), 3)
        self.assertTrue(all(item["advisory"] and target in item["body"] for item in examples))
        self.assertTrue(all(item["subject"] for item in examples))

    def test_unknown_item_never_projects_an_earlier_success_result(self):
        check = SimpleNamespace(id="check", requested_url="https://example.com/", domain="example.com",
            status="succeeded", result={"disposition": "ready", "recipients": [{"email": "old"}]})
        job = SimpleNamespace(id="job", state="partial", stage="completed", business_day=datetime.now().date())
        dto = serialize(SimpleNamespace(job=job, checks=[check], items=[SimpleNamespace(id="item", state="unknown")]))
        self.assertEqual(dto["checks"][0]["disposition"], "unknown")
        self.assertEqual(dto["checks"][0]["recipients"], [])

    def test_routes_have_no_send_and_require_registered_feature_permissions(self):
        from domaincheck.main import create_app
        from domaincheck.settings import Settings
        paths = create_app(Settings(_env_file=None)).openapi()['paths']
        self.assertEqual(set(paths['/api/v1/domain-worker/prechecks']), {'post'})
        self.assertIn('/api/v1/domain-worker/prechecks/{operation_id}/send', paths)
        self.assertIn('/api/v1/domain-worker/send-jobs/{job_id}', paths)


class WorkerApiPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_view_only_user_cannot_create_precheck_and_origin_csrf_are_enforced(self):
        from httpx import ASGITransport, AsyncClient
        from unittest.mock import AsyncMock
        from uuid import uuid4
        from domaincheck.api.dependencies import current_user, get_db_session
        from domaincheck.infrastructure.auth.sessions import hash_token
        from domaincheck.main import create_app
        from domaincheck.settings import Settings
        actor = SimpleNamespace(user_id=uuid4(), status='active', timezone='Asia/Bangkok',
            permissions={'domain_worker.view'}, csrf_hash=hash_token('fixture-csrf'))
        app = create_app(Settings(_env_file=None))
        async def user(): return actor
        session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(brand_name='Fixture',
            contact_name='Operator', contact_email='operator@example.com')))
        async def db(): yield session
        app.dependency_overrides[current_user] = user
        app.dependency_overrides[get_db_session] = db
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            headers={'Origin': 'http://127.0.0.1:5173', 'X-CSRF-Token': 'fixture-csrf'}
            response = await client.post('/api/v1/domain-worker/prechecks', headers=headers,
                json={'targets': ['https://example.com/']})
            self.assertEqual(response.status_code, 403)
            actor.permissions.add('domain_worker.precheck')
            for invalid in ({}, {'Origin': 'https://untrusted.example', 'X-CSRF-Token': 'fixture-csrf'}):
                response = await client.post('/api/v1/domain-worker/prechecks', headers=invalid,
                    json={'targets': ['https://example.com/']})
                self.assertEqual(response.status_code, 403)
            self.assertEqual((await client.get('/api/v1/domain-worker/readiness')).status_code, 200)


@unittest.skipUnless(os.environ.get("DOMAINCHECK_TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class WorkerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(os.environ["DOMAINCHECK_TEST_DATABASE_URL"])
        self.now = datetime(2026, 10, 16, 8, tzinfo=UTC)
        async with self.db.session_factory() as session, session.begin():
            a, b = User(display_name="Worker A", status="active"), User(display_name="Worker B", status="active")
            session.add_all([a, b]); await session.flush()
            self.a, self.b = a.id, b.id

    async def asyncTearDown(self):
        await self.db.dispose()

    def svc(self, session, owner=None):
        return DomainWorkerService(session, owner=owner or self.a, actor=owner or self.a,
            timezone="Asia/Bangkok", request_id="test", now=self.now)

    async def test_batch_order_owner_no_email_queue_dedupe_and_evidence_gate(self):
        class Adapter:
            def run(_self, target, domain):
                recipients = [] if target.endswith('/no-email') else [{"channel": "registrar", "email": "abuse@example.com"}]
                cloak = {"verdict": "POSSIBLE" if target.endswith(('/cloak', '/no-email')) else "NO_SIGNAL"}
                return {"recipients": recipients, "cloaking": cloak, "disposition": disposition(recipients, cloak)}
        async with self.db.session_factory() as session, session.begin():
            op = await self.svc(session).create(['https://example.com/cloak', 'https://example.com/no-email',
                'https://example.com/normal?q=1', 'https://example.com/cloak'])
            job_id = op.job.id
        worker = DomainPrecheckWorker(self.db.session_factory, Adapter(), now=lambda: self.now)
        await worker.run_next('worker-test')
        async with self.db.session_factory() as session, session.begin():
            op = await self.svc(session).get(job_id)
            self.assertEqual([check.result['disposition'] for check in op.checks],
                ['cloaking_review', 'excluded_no_email', 'evidence_review'])
            self.assertEqual(op.checks[2].result['evidence_reason'], 'capture_disabled')
            self.assertEqual(op.job.state, 'completed')
            case_id = op.checks[0].result['case_id']
            with self.assertRaises(WorkerNotFound): await self.svc(session, self.b).get(job_id)
            second = await self.svc(session).create(['https://example.com/cloak'])
        await worker.run_next('worker-second')
        async with self.db.session_factory() as session:
            op = await self.svc(session).get(second.job.id)
            self.assertEqual(op.checks[0].result['case_id'], case_id)
            self.assertEqual(await session.scalar(select(func.count()).select_from(CaseSource).where(
                CaseSource.owner_user_id == self.a, CaseSource.case_id == UUID(case_id))), 2)
            self.assertEqual(await session.scalar(select(func.count()).select_from(ReviewCase).where(
                ReviewCase.owner_user_id == self.a)), 2)

    async def test_stop_resume_failed_retry_and_validated_capture(self):
        calls = []
        class Adapter:
            def run(_self, target, domain):
                calls.append(target)
                if target.endswith('/bad') and calls.count(target) == 1: raise RuntimeError('private failure')
                return {'recipients': [{'channel': 'registrar', 'email': 'abuse@example.com'}],
                    'cloaking': {'verdict': 'NO_SIGNAL'}, 'disposition': 'evidence_review'}
        class Capture:
            def capture(_self, target, **kwargs):
                data = io.BytesIO(); Image.new('RGB', (10, 10)).save(data, format='PNG')
                return [ImageUpload('source.png', 'image/png', data.getvalue())], {'terminal': False}
        async with self.db.session_factory() as session, session.begin():
            op = await self.svc(session).create(['https://example.com/good', 'https://example.com/bad'])
            job_id = op.job.id
            await self.svc(session).jobs().request_stop(job_id)
        with tempfile.TemporaryDirectory() as directory:
            worker = DomainPrecheckWorker(self.db.session_factory, Adapter(),
                store=FilesystemArtifactStore(Path(directory)), capture=Capture(), now=lambda: self.now)
            self.assertIsNone(await worker.run_next('stopped'))
            async with self.db.session_factory() as session, session.begin():
                await self.svc(session).jobs().resume(job_id)
            await worker.run_next('resume')
            async with self.db.session_factory() as session, session.begin():
                op = await self.svc(session).get(job_id)
                self.assertEqual(op.checks[0].result['disposition'], 'ready')
                self.assertTrue(op.checks[0].result['evidence_set_id'])
                self.assertEqual(op.items[1].state, 'failed')
                await self.svc(session).jobs().retry_failed(job_id, {op.items[1].id})
            await worker.run_next('retry')
        self.assertEqual(calls.count('https://example.com/good'), 1)
        self.assertEqual(calls.count('https://example.com/bad'), 2)

    async def test_stop_during_current_item_finishes_it_and_preserves_pending_membership(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        class Adapter:
            def run(_self, target, domain):
                calls.append(target); entered.set()
                if not release.wait(5): raise RuntimeError('test timeout')
                return {'recipients': [], 'cloaking': {'verdict': 'NO_SIGNAL'}, 'disposition': 'excluded_no_email'}
        async with self.db.session_factory() as session, session.begin():
            op = await self.svc(session).create(['https://example.com/first', 'https://example.com/second'])
            job_id = op.job.id
        worker = DomainPrecheckWorker(self.db.session_factory, Adapter(), now=lambda: self.now)
        worker._heartbeat_seconds = .02
        task = asyncio.create_task(worker.run_next('cooperative-stop'))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            async with self.db.session_factory() as session, session.begin():
                await self.svc(session).jobs().request_stop(job_id)
            await asyncio.sleep(.06)
            self.assertFalse(task.done())
        finally:
            release.set()
        await task
        async with self.db.session_factory() as session:
            op = await self.svc(session).get(job_id)
            self.assertEqual(op.job.state, 'stopped')
            self.assertEqual([item.state for item in op.items], ['succeeded', 'pending'])
        self.assertEqual(calls, ['https://example.com/first'])

    async def test_capture_failure_keeps_review_case_and_only_persists_a_safe_diagnostic(self):
        class Adapter:
            def run(_self, *_args):
                return {'recipients': [{'channel': 'registrar', 'email': 'abuse@example.com'}],
                    'cloaking': {'verdict': 'NO_SIGNAL'}, 'disposition': 'evidence_review'}
        class Capture:
            def capture(_self, *_args, **_kwargs):
                raise RuntimeError('Timeout at https://user:private-secret@example.com C:/private/path')
        async with self.db.session_factory() as session, session.begin():
            op = await self.svc(session).create(['https://example.com/capture-failure'])
            job_id = op.job.id
        with tempfile.TemporaryDirectory() as directory:
            await DomainPrecheckWorker(self.db.session_factory, Adapter(),
                store=FilesystemArtifactStore(Path(directory)), capture=Capture(), now=lambda: self.now).run_next('capture-test')
        async with self.db.session_factory() as session:
            op = await self.svc(session).get(job_id)
            row = serialize(op)['checks'][0]
            self.assertEqual(row['disposition'], 'evidence_review')
            self.assertEqual(row['evidence_failure_code'], 'browser_timeout')
            self.assertEqual(op.items[0].state, 'succeeded')
            self.assertNotIn('private-secret', str(op.checks[0].result))

    async def test_expired_running_item_is_unknown_and_not_reprobed(self):
        from domaincheck.infrastructure.db.repositories import JobRepository
        calls = []
        class Adapter:
            def run(_self, target, domain):
                calls.append(target)
                return {'disposition': 'excluded_no_email', 'recipients': []}
        async with self.db.session_factory() as session, session.begin():
            op = await self.svc(session).create(['https://example.com/interrupted', 'https://example.com/pending'])
            job_id = op.job.id
            repo = JobRepository(session)
            job = await repo.claim_next(worker_token='old', job_types=frozenset({'domain_worker_precheck'}),
                now=self.now - timedelta(minutes=2))
            await repo.start_item(owner_user_id=self.a, job_id=job_id, item_id=op.items[0].id,
                worker_token='old', expected_version=op.items[0].version, stage='checking',
                now=self.now - timedelta(minutes=2))
        await DomainPrecheckWorker(self.db.session_factory, Adapter(), now=lambda: self.now).run_next('recovered')
        async with self.db.session_factory() as session:
            op = await self.svc(session).get(job_id)
            self.assertEqual([item.state for item in op.items], ['unknown', 'succeeded'])
            self.assertEqual(serialize(op)['checks'][0]['disposition'], 'unknown')
        self.assertEqual(calls, ['https://example.com/pending'])

    async def test_draft_api_blocks_cross_owner_cloaking_and_uncheckpointed_results(self):
        from domaincheck.api.domain_worker import drafts
        async with self.db.session_factory() as session, session.begin():
            op = await self.svc(session).create(['https://example.com/draft'])
            check = op.checks[0]
            check.status = 'succeeded'
            check.result = {'disposition': 'evidence_review', 'recipients': [{'channel': 'registrar', 'email': 'abuse@example.com'}]}
            check_id = check.id
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(clock=SimpleNamespace(now=lambda: self.now))))
        async with self.db.session_factory() as session:
            for owner, expected in ((self.b, 404), (self.a, 409)):
                with self.assertRaises(HTTPException) as exc:
                    await drafts(check_id, request, Response(), session, SimpleNamespace(user_id=owner, timezone='Asia/Bangkok'))
                self.assertEqual(exc.exception.status_code, expected)
        # Leave no pending fixture job for another worker test to claim.
        async with self.db.session_factory() as session, session.begin():
            await self.svc(session).jobs().request_stop(op.job.id)

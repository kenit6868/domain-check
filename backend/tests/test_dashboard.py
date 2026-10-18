import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, skipUnless
from uuid import uuid4
from zoneinfo import ZoneInfo

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from domaincheck.api.dependencies import current_user, get_db_session
from domaincheck.application.dashboard import overview
from domaincheck.infrastructure.db.models import (
    Artifact, AuditEvent, CaseSource, Check, CloudflareRecord, Delivery, DeliveryAttempt,
    Job, JobItem, Permission, PreviewPlan, ReviewCase, Role, RolePermission, SenderAccount, Target, User,
)
from domaincheck.infrastructure.db.session import Database
from domaincheck.main import create_app
from domaincheck.settings import Settings
from domaincheck.infrastructure.db.seeds import sync_permission_catalog

DB = os.environ.get('DOMAINCHECK_TEST_DATABASE_URL')


@skipUnless(DB, 'requires isolated PostgreSQL')
class DashboardTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(DB)
        self.session = self.db.session_factory()
        self.transaction = await self.session.begin()
        self.now = datetime(2026, 10, 18, 18, tzinfo=UTC)  # October 19 in Bangkok
        self.today = self.now.astimezone(ZoneInfo('Asia/Bangkok')).date()
        self.a = User(display_name='Dashboard A', status='active', timezone='Asia/Bangkok')
        self.b = User(display_name='Dashboard B', status='disabled', timezone='Asia/Bangkok')
        self.session.add_all([self.a, self.b])
        await self.session.flush()
        self.actor = SimpleNamespace(user_id=self.a.id, timezone='Asia/Bangkok', status='active',
            permissions=frozenset({'dashboard.view', 'domain_worker.view', 'cloaking_review.view'}))
        self.checks = []
        for owner, workflow in [(self.a, 'domain_worker'), (self.a, 'quick_report'), (self.b, 'domain_worker')]:
            target = Target(owner_user_id=owner.id, full_url_identity=f'https://private-{uuid4().hex}.test/',
                canonical_url_hash=uuid4().hex, hostname='private.test')
            self.session.add(target)
            await self.session.flush()
            row = Check(owner_user_id=owner.id, actor_user_id=owner.id, target_id=target.id,
                requested_url=target.full_url_identity, workflow=workflow, workflow_key=uuid4().hex,
                domain='private.test', business_day=self.today, timezone='Asia/Bangkok', engine_version='test',
                status='succeeded', result={'recipients': ['abuse@example.test'], 'disposition': 'cloaking_review'})
            self.session.add(row)
            await self.session.flush()
            self.checks.append(row)
        self.session.add(Job(owner_user_id=self.b.id, actor_user_id=self.b.id, job_type='domain_worker_precheck',
            state='running', business_day=self.today, timezone='Asia/Bangkok', heartbeat_at=self.now - timedelta(minutes=3)))
        for day, recipients in [(self.today, True), (self.today - timedelta(days=1), True), (self.today, False)]:
            case = ReviewCase(owner_user_id=self.a.id, case_type='cloaking', canonical_url_key=uuid4().hex,
                full_url=self.checks[0].requested_url, business_day=day, timezone='Asia/Bangkok', state='pending')
            self.session.add(case)
            await self.session.flush()
            if recipients: self.session.add(CaseSource(owner_user_id=self.a.id, case_id=case.id, check_id=self.checks[0].id))
        artifact = Artifact(owner_user_id=self.a.id, kind='domain_worker:draft', mime_type='application/json',
            byte_size=1, sha256='a'*64, storage_key=f'dashboard-{uuid4()}')
        account = SenderAccount(owner_user_id=self.a.id, label='Fixture', normalized_identity='fixture@example.test',
            smtp_host='smtp.example.test', smtp_port=465, smtp_username='fixture@example.test', smtp_transport='implicit_tls')
        self.session.add_all([artifact, account])
        await self.session.flush()
        plan = PreviewPlan(owner_user_id=self.a.id, actor_user_id=self.a.id, resource_type='check', resource_id=self.checks[0].id,
            revision=1, fingerprint='a'*64, expires_at=self.now + timedelta(hours=1), account_versions={}, recipients=[], draft_artifact_id=artifact.id)
        self.session.add(plan)
        await self.session.flush()
        delivery = Delivery(owner_user_id=self.a.id, preview_id=plan.id, sender_account_id=account.id,
            recipient='abuse@example.test', workflow='domain_worker', business_key=uuid4().hex, channel='registrar', draft_identity='fixture', state='sent')
        self.session.add(delivery)
        await self.session.flush()
        for attempt, state in [(1, 'failed'), (2, 'sent'), (3, 'sent')]:
            self.session.add(DeliveryAttempt(owner_user_id=self.a.id, delivery_id=delivery.id,
                attempt_number=attempt, smtp_stage='checkpoint', state=state,
                started_at=self.now - timedelta(minutes=2), finished_at=self.now - timedelta(minutes=1)))
        self.session.add(CloudflareRecord(owner_user_id=self.b.id, full_url='https://private.test/',
            canonical_url_key=uuid4().hex, business_day=self.today, report_version='fixture', idempotency_key=uuid4().hex,
            request_hash='b'*64, state='SUBMITTED'))
        await self.session.flush()

    async def asyncTearDown(self):
        await self.transaction.rollback()
        await self.session.close()
        await self.db.dispose()

    async def test_personal_scope_respects_features_and_dedupes_smtp_retry(self):
        data = await overview(self.session, self.actor, days=1, now=self.now)
        self.assertEqual(data['end'], self.today)
        self.assertEqual(data['metrics']['checked_urls'], 1)
        self.assertEqual(data['metrics']['sent_emails'], 1)
        self.assertEqual(data['trend'][0]['sent_emails'], 1)
        self.assertNotIn('cloudflare_submitted', data['metrics'])
        self.assertEqual(data['pending'], [{'feature': 'cloaking_review', 'count': 1, 'href': '/cloaking-review'}])
        self.assertFalse(data['users'])
        self.assertFalse(data['workers'])
        self.assertNotIn('private-', str(data))

    async def test_removed_feature_disappears_in_response_and_filter_denies(self):
        self.actor.permissions = frozenset({'dashboard.view', 'cloaking_review.view'})
        data = await overview(self.session, self.actor, now=self.now)
        self.assertNotIn('checked_urls', data['metrics'])
        self.assertEqual(data['metrics']['sent_emails'], 0)
        with self.assertRaises(PermissionError): await overview(self.session, self.actor, feature='domain_worker', now=self.now)
        with self.assertRaises(PermissionError): await overview(self.session, self.actor, scope='all', now=self.now)
        with self.assertRaises(PermissionError): await overview(self.session, self.actor, user_id=self.b.id, now=self.now)

    async def test_system_scope_includes_disabled_users_without_private_drilldown(self):
        self.actor.permissions |= {'dashboard.view_all'}
        data = await overview(self.session, self.actor, scope='all', user_id=self.b.id, now=self.now)
        self.assertEqual(data['metrics']['checked_urls'], 1)
        self.assertEqual(data['metrics']['cloudflare_submitted'], 1)
        self.assertEqual(data['users'][0]['id'], str(self.b.id))
        self.assertIsNone(data['workers'][0]['href'])
        self.assertTrue(data['workers'][0]['stale'])
        self.assertFalse(data['events'])

    async def test_filters_are_bounded_and_dashboard_is_read_only(self):
        before = await self.session.scalar(select(func.count()).select_from(AuditEvent))
        for days in (1, 7, 30):
            data = await overview(self.session, self.actor, days=days, now=self.now)
            self.assertEqual(len(data['trend']), days)
        self.assertEqual(before, await self.session.scalar(select(func.count()).select_from(AuditEvent)))
        with self.assertRaises(ValueError): await overview(self.session, self.actor, days=365)
        with self.assertRaises(ValueError): await overview(self.session, self.actor, feature='invalid')
        self.actor.permissions = frozenset({'dashboard.view_all'})
        with self.assertRaises(PermissionError): await overview(self.session, self.actor)

    async def test_api_validates_scope_and_returns_no_store(self):
        app = create_app(Settings(_env_file=None))
        app.state.clock = SimpleNamespace(now=lambda: self.now)
        async def actor(): return self.actor
        async def db(): yield self.session
        app.dependency_overrides[current_user] = actor
        app.dependency_overrides[get_db_session] = db
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get('/api/v1/dashboard?days=1')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers['cache-control'], 'private, no-store')
                self.assertEqual((await client.get('/api/v1/dashboard?scope=all')).status_code, 403)
                self.assertEqual((await client.get('/api/v1/dashboard?days=100')).status_code, 422)
                self.assertEqual((await client.get('/api/v1/dashboard?user_id=invalid')).status_code, 422)
        finally:
            await app.state.database.dispose()
            await app.state.redis.aclose()

    async def test_explicit_rollout_grants_only_dashboard_then_normal_seed_keeps_choices(self):
        await sync_permission_catalog(self.session, grant_dashboard=True)
        role = await self.session.scalar(select(Role).where(Role.key == 'admin'))
        identity = await self.session.scalar(select(Permission.id).where(Permission.code == 'dashboard.view_all'))
        self.assertIsNotNone(await self.session.get(RolePermission, (role.id, identity)))
        await self.session.execute(delete(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id == identity))
        await sync_permission_catalog(self.session)
        self.assertFalse(await self.session.scalar(select(RolePermission.permission_id).where(RolePermission.role_id == role.id, RolePermission.permission_id == identity)))

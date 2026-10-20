import asyncio
import json
import os
import threading
import unittest
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import quote
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from domaincheck.application.cloudflare_runner import CloudflareRunner
from domaincheck.application.cloudflare_worker import CloudflareConflict, CloudflareService
from domaincheck.infrastructure.db.models import CloudflareRecord, Permission, ReviewSnapshot, Role, RolePermission, UserRole, UserSettings
from domaincheck.infrastructure.auth.sessions import hash_token
import tests.test_worker_send as fixtures


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class CloudflareWorkerWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.WorkerSendTests.asyncSetUp(self)
        self.account_id = 'a' * 32
        self.cookie = 'fixture-cookie-never-persist; curr-account=' + quote(json.dumps({'id': self.account_id}))
        self.calls, self.outcomes = [], []
        def submit(url, draft, cfg, cookie):
            self.calls.append((url, draft, cfg, cookie))
            value = self.outcomes.pop(0) if self.outcomes else {'report_id': 'report-fixture'}
            if isinstance(value, BaseException): raise value
            return value
        self.runner = CloudflareRunner(self.db.session_factory, self.store, checker=lambda url, cfg: {'cloudflare': 'noncf' not in url},
            submitter=submit, enabled=True, now=lambda: self.now)
        async with self.db.session_factory() as session, session.begin():
            role = Role(key=f'cf-fixture-{uuid4()}', name='CF fixture')
            session.add(role); await session.flush()
            session.add(UserRole(user_id=self.owner, role_id=role.id, assigned_by_user_id=self.owner))
            permissions = list(await session.scalars(select(Permission).where(Permission.code.like('cloudflare_worker.%'))))
            from domaincheck.domain.authz import PERMISSION_CATALOG
            existing = {permission.code for permission in permissions}
            for definition in PERMISSION_CATALOG:
                if definition.code.startswith('cloudflare_worker.') and definition.code not in existing:
                    permission = Permission(code=definition.code, description=definition.description)
                    session.add(permission); permissions.append(permission)
            await session.flush()
            self.assertTrue(permissions)
            for permission in permissions: session.add(RolePermission(role_id=role.id, permission_id=permission.id))

    async def asyncTearDown(self):
        await self.runner.close()
        await fixtures.WorkerSendTests.asyncTearDown(self)

    def svc(self, owner=None):
        return CloudflareService(self.db.session_factory, self.store, owner or self.owner, now=lambda: self.now)

    async def prepared(self, urls=None):
        identity = await self.svc().prepare(urls or ['https://example.com/path'])
        await self.runner.attach(self.owner, identity, 'Asia/Bangkok', checking=True)
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        self.assertEqual((await self.svc().view(identity))['batch']['phase'], 'CHECKED')
        return identity

    async def phase(self, identity, phase):
        for _ in range(200):
            value = await self.svc().view(identity)
            if value['batch']['phase'] == phase: return value
            await asyncio.sleep(.01)
        self.fail(f'Expected {phase}, got {value}')

    async def start(self, identity, **kwargs):
        await self.runner.attach(self.owner, identity, 'Asia/Bangkok', cookie=self.cookie,
            confirmed=True, delay=0, **kwargs)

    async def test_precheck_normalizes_dedupes_isolates_owner_and_keeps_draft_private(self):
        identity = await self.prepared(['example.com/path', 'https://example.com/path', 'https://noncf.com/', 'http://127.0.0.1/'])
        view = await self.svc().view(identity)
        self.assertEqual([row['state'] for row in view['records']], ['READY', 'NOT_CLOUDFLARE'])
        self.assertEqual(len(view['batch']['duplicates']), 1)
        self.assertEqual(len(view['batch']['invalid']), 1)
        self.assertIn('https://example.com/path', view['records'][0]['draft'])
        self.assertNotIn('storage_key', json.dumps(view))
        with self.assertRaises(LookupError): await self.svc(self.other).view(identity)
        self.assertIsNone((await self.svc(self.other).view())['batch'])
        async with self.db.session_factory() as session:
            record = await session.get(CloudflareRecord, UUID(view['records'][0]['id']))
            self.assertNotIn('draft', record.sanitized_result)

    async def test_account_id_is_owned_validated_and_cookie_mismatch_blocks_all_submission(self):
        await self.svc().settings(self.account_id.upper())
        self.assertEqual((await self.svc().settings())['account_id'], self.account_id)
        self.assertEqual((await self.svc(self.other).settings())['account_id'], '')
        with self.assertRaises(CloudflareConflict): await self.svc().settings('invalid')
        identity = await self.prepared()
        with self.assertRaises(CloudflareConflict) as caught:
            await self.runner.attach(self.owner, identity, 'Asia/Bangkok', cookie=self.cookie.replace('a' * 32, 'b' * 32), confirmed=True)
        self.assertEqual(caught.exception.code, 'settings_account_mismatch')
        self.assertEqual(self.calls, [])
        self.assertEqual((await self.svc().view(identity))['records'][0]['state'], 'READY')

    async def test_missing_account_configuration_uses_cookie_only_when_curr_account_is_valid(self):
        identity = await self.prepared()
        self.assertEqual((await self.svc().settings())['account_id'], '')
        with self.assertRaises(CloudflareConflict) as caught:
            await self.runner.attach(self.owner, identity, 'Asia/Bangkok', cookie='session=fixture-without-account', confirmed=True, delay=0)
        self.assertEqual(caught.exception.code, 'cookie_account_missing')
        for value in ('session=fixture; curr-account=broken', 'Cookie: curr-account=%7B%22id%22%3A%22invalid%22%7D'):
            with self.assertRaises(CloudflareConflict) as invalid:
                await self.runner.attach(self.owner, identity, 'Asia/Bangkok', cookie=value, confirmed=True, delay=0)
            self.assertEqual(invalid.exception.code, 'cookie_account_invalid')
        self.assertEqual(self.calls, [])
        self.assertEqual((await self.svc().view(identity))['records'][0]['state'], 'READY')
        await self.start(identity)
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][2]['cloudflare_account_id'], self.account_id)
        self.assertEqual((await self.svc().view(identity))['batch']['account_id'], self.account_id)

    async def test_new_precheck_accepts_another_cookie_account_but_retry_keeps_old_batch_binding(self):
        from domaincheck.engine.cloudflare_dashboard_session import CloudflareDashboardError
        identity = await self.prepared()
        self.outcomes = [CloudflareDashboardError('definite rejection')]
        await self.start(identity)
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        different_cookie = self.cookie.replace('a' * 32, 'b' * 32)
        with self.assertRaises(CloudflareConflict) as caught:
            await self.runner.attach(self.owner, identity, 'Asia/Bangkok', cookie=different_cookie,
                confirmed=True, retry=True, delay=0)
        self.assertEqual(caught.exception.code, 'batch_account_mismatch')
        self.assertEqual(len(self.calls), 1)
        newer = await self.prepared()
        self.assertNotIn('account_id', (await self.svc().view(newer))['batch'])
        await self.runner.attach(self.owner, newer, 'Asia/Bangkok', cookie=different_cookie, confirmed=True, delay=0)
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[newer]), 8)
        self.assertEqual(self.calls[-1][2]['cloudflare_account_id'], 'b' * 32)
        self.assertEqual(self.calls[-1][3], different_cookie)
        self.assertEqual((await self.svc().settings())['account_id'], '')

    async def test_configured_account_has_priority_when_cookie_curr_account_cannot_be_parsed(self):
        await self.svc().settings(self.account_id)
        for index, cookie in enumerate(('session=fixture; curr-account=unrecognized-shape',
            'Cookie: session=fixture; curr-account=' + quote(json.dumps({'otherA': 'a' * 32, 'otherB': 'b' * 32})),
            'session=fixture-without-curr-account')):
            identity = await self.prepared([f'https://example.com/configured-{index}'])
            await self.runner.attach(self.owner, identity, 'Asia/Bangkok', cookie=cookie, confirmed=True, delay=0)
            await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
            self.assertEqual(self.calls[-1][2]['cloudflare_account_id'], self.account_id)
            self.assertEqual((await self.svc().view(identity))['records'][0]['state'], 'SUBMITTED')

    async def test_exact_payload_success_duplicate_and_recheck_cannot_resubmit(self):
        from domaincheck.engine.cloudflare_dashboard_session import CloudflareDashboardDuplicateError
        identity = await self.prepared(['https://example.com/a', 'https://example.com/b'])
        drafts = [row['draft'] for row in (await self.svc().view(identity))['records']]
        self.outcomes = [{'report_id': 'accepted-123'}, CloudflareDashboardDuplicateError('duplicate')]
        await self.start(identity)
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        view = await self.svc().view(identity)
        self.assertEqual(view['batch']['phase'], 'COMPLETED')
        self.assertEqual([row['state'] for row in view['records']], ['SUBMITTED', 'ALREADY_SUBMITTED'])
        self.assertEqual([call[1] for call in self.calls], drafts)
        self.assertTrue(all(call[2]['cloudflare_account_id'] == self.account_id for call in self.calls))
        self.assertEqual(self.runner.cookies, {})
        with self.assertRaises(CloudflareConflict): await self.start(identity, retry=True)
        newer = await self.prepared(['https://example.com/a', 'https://example.com/b'])
        self.assertEqual([row['state'] for row in (await self.svc().view(newer))['records']], ['SUBMITTED', 'ALREADY_SUBMITTED'])
        self.assertEqual(len(self.calls), 2)

    async def test_cookie_expiry_replacement_resumes_same_item_without_persisting_secret(self):
        from domaincheck.engine.cloudflare_dashboard_session import CloudflareDashboardSessionError
        identity = await self.prepared()
        self.outcomes = [CloudflareDashboardSessionError('must not persist Cookie secret')]
        await self.start(identity)
        await self.phase(identity, 'WAITING_FOR_SESSION')
        self.assertEqual(self.runner.cookies, {})
        await self.start(identity, action='resume')
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        view = await self.svc().view(identity)
        self.assertEqual(view['records'][0]['attempts'], 2)
        self.assertEqual(view['records'][0]['state'], 'SUBMITTED')
        async with self.db.session_factory() as session:
            snapshots = list(await session.scalars(select(ReviewSnapshot).where(ReviewSnapshot.owner_user_id == self.owner)))
            records = list(await session.scalars(select(CloudflareRecord).where(CloudflareRecord.owner_user_id == self.owner)))
            persisted = json.dumps([*[row.sanitized_state for row in snapshots], *[row.sanitized_result for row in records]])
            self.assertNotIn('fixture-cookie-never-persist', persisted)
            self.assertNotIn('must not persist', persisted)

    async def test_unknown_blocks_retry_and_resume_only_sends_remaining_urls(self):
        from domaincheck.engine.cloudflare_dashboard_session import CloudflareDashboardUnknownError
        identity = await self.prepared(['https://example.com/a', 'https://example.com/b'])
        self.outcomes = [CloudflareDashboardUnknownError('timeout')]
        await self.start(identity)
        paused = await self.phase(identity, 'PAUSED')
        self.assertEqual([row['state'] for row in paused['records']], ['UNKNOWN', 'QUEUED'])
        await self.start(identity, action='resume')
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        self.assertEqual([row['state'] for row in (await self.svc().view(identity))['records']], ['UNKNOWN', 'SUBMITTED'])
        self.assertEqual(len(self.calls), 2)

    async def test_failed_retry_keeps_success_and_profile_hash_changes_block_send(self):
        from domaincheck.engine.cloudflare_dashboard_session import CloudflareDashboardError
        identity = await self.prepared(['https://example.com/a', 'https://example.com/b'])
        self.outcomes = [{'report_id': 'one'}, CloudflareDashboardError('definite rejection')]
        await self.start(identity)
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        await self.start(identity, retry=True)
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[-1][0], 'https://example.com/b')
        other = await self.prepared(['https://example.com/new'])
        async with self.db.session_factory() as session, session.begin():
            profile = await session.get(UserSettings, self.owner); profile.version += 1
        with self.assertRaises(CloudflareConflict): await self.start(other)
        self.assertEqual(len(self.calls), 3)

    async def test_pause_cancel_during_request_checkpoints_success_and_never_starts_next_url(self):
        identity = await self.prepared(['https://example.com/a', 'https://example.com/b'])
        gate, entered = threading.Event(), threading.Event()
        def submit(*args):
            entered.set(); gate.wait(5); return {'report_id': 'accepted-in-flight'}
        self.runner.submitter = submit
        await self.start(identity)
        for _ in range(100):
            if entered.is_set(): break
            await asyncio.sleep(.01)
        self.assertTrue(entered.is_set())
        await self.svc().command(identity, 'pause')
        await self.svc().command(identity, 'cancel')
        gate.set()
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        view = await self.svc().view(identity)
        self.assertEqual(view['batch']['phase'], 'CANCELLED')
        self.assertEqual([row['state'] for row in view['records']], ['SUBMITTED', 'QUEUED'])
        with self.assertRaises(CloudflareConflict): await self.start(identity, action='resume')

    async def test_runner_restart_recovers_committed_intent_as_unknown_and_requires_cookie(self):
        identity = await self.prepared(['https://example.com/a', 'https://example.com/b'])
        await self.svc().command(identity, 'start', delay=0, confirmed=True)
        async with self.db.session_factory() as session, session.begin():
            batch = await self.svc().batch(session, identity)
            records = await self.svc().rows(session, batch)
            records[0].state = 'SUBMITTING'
        self.now += timedelta(seconds=91)
        view = await self.svc().view(identity)
        self.assertEqual(view['batch']['phase'], 'WAITING_FOR_SESSION')
        self.assertEqual(view['records'][0]['state'], 'UNKNOWN')
        with self.assertRaises(CloudflareConflict):
            await self.runner.attach(self.owner, identity, 'Asia/Bangkok', action='resume', confirmed=True, delay=0)
        await self.start(identity, action='resume')
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        self.assertEqual([call[0] for call in self.calls], ['https://example.com/b'])

    async def test_artifact_mutation_day_scope_permission_and_clear_keep_accepted_history(self):
        identity = await self.prepared()
        async with self.db.session_factory() as session:
            row = (await self.svc().rows(session, await self.svc().batch(session, identity)))[0]
            path = self.store.root / row.sanitized_result['storage_key']
        path.write_bytes(b'changed')
        with self.assertRaises(Exception): await self.start(identity)
        self.assertEqual(self.calls, [])
        self.now += timedelta(days=1)
        with self.assertRaises(CloudflareConflict): await self.start(identity)
        self.assertIsNone((await self.svc().view())['batch'])

    async def test_rate_limit_pauses_and_live_batch_blocks_new_precheck(self):
        from domaincheck.engine.cloudflare_dashboard_session import CloudflareDashboardRateLimitError
        identity = await self.prepared()
        self.outcomes = [CloudflareDashboardRateLimitError('challenge')]
        await self.start(identity)
        await self.phase(identity, 'PAUSED')
        with self.assertRaises(CloudflareConflict): await self.svc().prepare(['https://example.com/new'])
        await self.start(identity, action='resume')
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        await self.svc().clear()
        self.assertIsNone((await self.svc().view())['batch'])
        newer = await self.prepared()
        self.assertEqual((await self.svc().view(newer))['records'][0]['state'], 'READY')
        self.assertEqual((await self.svc().view(identity))['records'][0]['state'], 'SUBMITTED')

    async def test_daily_dedupe_resets_next_day_and_clear_preserves_history(self):
        from unittest.mock import Mock
        self.runner.checker = Mock(return_value={'cloudflare': True})
        identity = await self.prepared()
        await self.start(identity)
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        original = (await self.svc().view(identity))['records'][0]
        same_day = await self.prepared()
        self.assertTrue((await self.svc().view(same_day))['records'][0]['excluded'])
        self.assertEqual(self.runner.checker.call_count, 1)
        self.now += timedelta(days=1)
        next_day = await self.prepared()
        fresh = (await self.svc().view(next_day))['records'][0]
        self.assertEqual(self.runner.checker.call_count, 2)
        self.assertEqual(fresh['state'], 'READY')
        self.assertNotEqual(fresh['id'], original['id'])
        await self.start(next_day)
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[next_day]), 8)
        await self.svc().clear()
        reset = await self.prepared()
        reset_record = (await self.svc().view(reset))['records'][0]
        self.assertEqual(reset_record['state'], 'READY')
        self.assertFalse(reset_record['excluded'])
        self.assertNotEqual(reset_record['id'], fresh['id'])
        await self.start(reset)
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[reset]), 8)
        duplicate = await self.prepared()
        self.assertTrue((await self.svc().view(duplicate))['records'][0]['excluded'])
        for old in (identity, next_day, reset):
            self.assertEqual((await self.svc().view(old))['records'][0]['state'], 'SUBMITTED')
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.runner.checker.call_count, 3)

    async def test_provider_duplicate_is_scoped_to_day_and_each_cache_reset(self):
        from domaincheck.engine.cloudflare_dashboard_session import CloudflareDashboardDuplicateError
        identity = await self.prepared()
        self.outcomes = [CloudflareDashboardDuplicateError('duplicate')]
        await self.start(identity)
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        self.assertEqual((await self.svc().view(identity))['records'][0]['state'], 'ALREADY_SUBMITTED')
        self.now += timedelta(days=1)
        newer = await self.prepared()
        self.assertEqual((await self.svc().view(newer))['records'][0]['state'], 'READY')
        for _ in range(2):
            await self.svc().clear()
            reset = await self.prepared()
            self.assertEqual((await self.svc().view(reset))['records'][0]['state'], 'READY')
            self.assertEqual((await self.svc().view(identity))['records'][0]['state'], 'ALREADY_SUBMITTED')

    async def test_clear_keeps_unknown_protected_and_blocks_old_ready_batch(self):
        identity = await self.prepared()
        await self.svc().clear()
        with self.assertRaises(CloudflareConflict): await self.start(identity)
        fresh = await self.prepared()
        async with self.db.session_factory() as session, session.begin():
            record = (await self.svc().rows(session, await self.svc().batch(session, fresh)))[0]
            record.state = 'UNKNOWN'
        await self.svc().clear()
        self.now += timedelta(days=1)
        newer = await self.prepared()
        self.assertEqual((await self.svc().view(newer))['records'][0]['state'], 'UNKNOWN')
        self.assertTrue((await self.svc().view(newer))['records'][0]['excluded'])
        self.assertEqual(self.calls, [])

    async def test_api_owned_permissions_csrf_flag_and_private_runner_auth(self):
        from domaincheck.api.dependencies import current_user
        from domaincheck.main import create_app
        from domaincheck.settings import Settings
        from domaincheck.cloudflare_runner import create_runner_app, bridge_token
        settings = Settings(_env_file=None, database_url=os.environ['DOMAINCHECK_TEST_DATABASE_URL'],
            secret_master_key='eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=', cloudflare_submit_enabled=True)
        app = create_app(settings)
        await app.state.database.dispose()
        app.state.database, app.state.artifact_store = self.db, self.store
        app.state.clock = SimpleNamespace(now=lambda: self.now)
        actor = SimpleNamespace(user_id=self.owner, status='active', timezone='Asia/Bangkok',
            permissions={'cloudflare_worker.view'}, csrf_hash=hash_token('csrf'))
        async def user(): return actor
        app.dependency_overrides[current_user] = user
        async def bridge(settings, payload):
            await self.runner.attach(UUID(payload['owner']), UUID(payload['batch_id']), payload['timezone'],
                cookie=payload.get('cookie', ''), checking=payload.get('checking', False), action=payload.get('action', 'start'),
                delay=payload.get('delay', 30), confirmed=payload.get('confirmed', False), retry=payload.get('retry', False))
        app.state.cloudflare_dispatch = bridge
        headers = {'Origin': 'http://127.0.0.1:5173', 'X-CSRF-Token': 'csrf'}
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.get('/api/v1/settings/cloudflare-dashboard')).status_code, 403)
            actor.permissions.update({'mail_accounts.view_own', 'mail_accounts.manage_own'})
            reply = await client.put('/api/v1/settings/cloudflare-dashboard', json={'account_id': self.account_id}, headers=headers)
            self.assertEqual(reply.status_code, 200, reply.text)
            self.assertEqual((await client.get('/api/v1/settings/cloudflare-dashboard')).json(), {'account_id': self.account_id})
            self.assertEqual((await client.put('/api/v1/settings/cloudflare-dashboard', json={'account_id': 'invalid'}, headers=headers)).status_code, 409)
            self.assertEqual((await client.post('/api/v1/cloudflare-worker/precheck', json={'urls': ['example.com']}, headers=headers)).status_code, 403)
            actor.permissions.add('cloudflare_worker.precheck')
            self.assertEqual((await client.post('/api/v1/cloudflare-worker/precheck', json={'urls': ['example.com']})).status_code, 403)
            reply = await client.post('/api/v1/cloudflare-worker/precheck', json={'urls': ['example.com']}, headers=headers)
            self.assertEqual(reply.status_code, 200, reply.text)
            identity = UUID(reply.json()['batch']['id'])
            await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
            self.assertEqual((await client.get('/api/v1/cloudflare-worker')).headers['cache-control'], 'private, no-store')
            actor.permissions.add('cloudflare_worker.start')
            actor.user_id = self.other
            self.assertEqual((await client.post(f'/api/v1/cloudflare-worker/{identity}/start', json={'cookie': self.cookie}, headers=headers)).status_code, 409)  # missing profile
            actor.user_id = self.owner
            self.assertEqual((await client.post(f'/api/v1/cloudflare-worker/{uuid4()}/start', json={'cookie': self.cookie}, headers=headers)).status_code, 404)
            app.state.settings.cloudflare_submit_enabled = False
            self.assertEqual((await client.post(f'/api/v1/cloudflare-worker/{identity}/start', json={'cookie': self.cookie, 'confirmed': True}, headers=headers)).status_code, 409)
        private = create_runner_app(settings, self.runner)
        async with AsyncClient(transport=ASGITransport(app=private), base_url='http://runner') as client:
            body = {'owner': str(self.owner), 'batch_id': str(identity), 'timezone': 'Asia/Bangkok', 'cookie': 'fixture-cookie-never-persist'}
            self.assertEqual((await client.post('/run', json=body)).status_code, 403)
            body['delay'] = -1
            reply = await client.post('/run', json=body, headers={'Authorization': 'Bearer ' + bridge_token(settings)})
            self.assertEqual(reply.status_code, 422)
            self.assertNotIn('fixture-cookie-never-persist', reply.text)
        await app.state.redis.aclose()

    async def test_two_tabs_cannot_start_two_senders_and_other_runner_cannot_take_live_cookie(self):
        identity = await self.prepared()
        outcomes = await asyncio.gather(self.start(identity), self.start(identity), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, CloudflareConflict) for value in outcomes), 1)
        await asyncio.wait_for(asyncio.shield(self.runner.tasks[identity]), 8)
        self.assertEqual(len(self.calls), 1)
        second = await self.prepared(['https://example.com/second'])
        from domaincheck.engine.cloudflare_dashboard_session import CloudflareDashboardRateLimitError
        self.outcomes = [CloudflareDashboardRateLimitError('pause')]
        await self.start(second)
        await self.phase(second, 'PAUSED')
        other = CloudflareRunner(self.db.session_factory, self.store, enabled=True, now=lambda: self.now)
        with self.assertRaises(CloudflareConflict):
            await other.attach(self.owner, second, 'Asia/Bangkok', cookie=self.cookie, action='resume', confirmed=True)
        self.assertEqual(other.cookies, {})
        await other.close()

    async def test_revoked_permission_and_changed_account_block_before_next_request(self):
        from domaincheck.engine.cloudflare_dashboard_session import CloudflareDashboardRateLimitError
        identity = await self.prepared()
        self.outcomes = [CloudflareDashboardRateLimitError('pause')]
        await self.start(identity)
        await self.phase(identity, 'PAUSED')
        await self.svc().settings('b' * 32)
        with self.assertRaises(CloudflareConflict):
            await self.runner.attach(self.owner, identity, 'Asia/Bangkok', action='resume', confirmed=True)
        self.assertEqual(len(self.calls), 1)

        await self.svc().settings(self.account_id)
        async with self.db.session_factory() as session, session.begin():
            from sqlalchemy import delete
            await session.execute(delete(UserRole).where(UserRole.user_id == self.owner))
        with self.assertRaises(CloudflareConflict): await self.start(identity, action='resume')
        self.assertEqual(len(self.calls), 1)

    async def test_previous_day_waiting_batch_does_not_block_new_day_and_old_intent_stays_unknown(self):
        identity = await self.prepared()
        await self.svc().command(identity, 'start', delay=0, confirmed=True)
        async with self.db.session_factory() as session, session.begin():
            batch = await self.svc().batch(session, identity)
            records = await self.svc().rows(session, batch)
            records[0].state = 'SUBMITTING'
        self.now += timedelta(days=1)
        newer = await self.prepared()
        self.assertEqual((await self.svc().view(identity))['batch']['phase'], 'CANCELLED')
        self.assertEqual((await self.svc().view(newer))['records'][0]['state'], 'UNKNOWN')


class CloudflareCoreTests(unittest.TestCase):
    def test_dashboard_transport_cookie_account_and_reporter_email_are_independent(self):
        from unittest.mock import Mock
        from domaincheck.engine.cloudflare_dashboard_session import submit_dashboard_report
        transport = Mock()
        response = Mock(status_code=200, reason='OK')
        response.json.return_value = {'success': True, 'result': {'id': 'fixture-report'}}
        transport.post.return_value = response
        profile = {'contact_name': 'Fixture', 'contact_email': 'profile@example.com', 'brand_name': 'Fixture'}
        for account, cookie in (('a' * 32, 'session=old-fixture'), ('b' * 32, 'session=new-fixture')):
            submit_dashboard_report('https://example.com/', 'Approved phishing evidence report for review.',
                {**profile, 'cloudflare_account_id': account}, cookie, session=transport)
        first, second = transport.post.call_args_list
        self.assertIn('/accounts/' + 'a' * 32 + '/', first.args[0])
        self.assertIn('/accounts/' + 'b' * 32 + '/', second.args[0])
        self.assertEqual(second.kwargs['headers']['Cookie'], 'session=new-fixture')
        self.assertEqual(second.kwargs['json']['email'], 'profile@example.com')
        self.assertEqual(second.kwargs['json']['email2'], 'profile@example.com')

    def test_cookie_account_parser_handles_wrappers_and_uses_account_fields_only(self):
        from domaincheck.engine.cloudflare_dashboard_session import _account_id_from_cookie, CloudflareDashboardConfig, CloudflareDashboardError
        account = 'a' * 32
        other = 'b' * 32
        samples = [account, json.dumps(account), json.dumps({'id': account, 'user_id': other}),
            json.dumps({'accountId': account, 'id': other}), json.dumps({'account_id': account, 'userId': other}),
            json.dumps({'account': {'id': account}, 'user_id': other}), json.dumps({'account': 'name', 'id': account}),
            json.dumps({'account': account}), json.dumps({'legacy_selected': account, 'name': 'fixture'})]
        for raw in samples:
            for encoded in (raw, quote(raw), quote(quote(raw)), json.dumps(quote(raw))):
                with self.subTest(shape=type(raw).__name__, encoded=encoded[:15]):
                    self.assertEqual(_account_id_from_cookie('Cookie: session=fixture; curr-account=' + encoded), account)
        for raw in (json.dumps({'accountId': account, 'account_id': other}), json.dumps({'one': account, 'two': other}),
            json.dumps({'id': 'not-an-id'}), 'broken'):
            self.assertEqual(_account_id_from_cookie('curr-account=' + quote(raw)), '')
        self.assertEqual(_account_id_from_cookie(f'curr-account={account}; curr-account={other}'), '')
        self.assertEqual(_account_id_from_cookie(f'curr-account={account}; curr-account={account}'), account)
        self.assertEqual(CloudflareDashboardConfig.from_mapping({'cloudflare_account_id': account}, 'curr-account=broken').account_id, account)
        with self.assertRaises(CloudflareDashboardError):
            CloudflareDashboardConfig.from_mapping({'cloudflare_account_id': 'a'})

    def test_private_runner_errors_preserve_typed_reason_and_never_echo_cookie(self):
        from domaincheck.cloudflare_runner import create_runner_app, bridge_token, dispatch
        from domaincheck.settings import Settings
        from unittest.mock import Mock, patch
        settings = Settings(_env_file=None, secret_master_key='eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=')
        class Manager:
            code = 'cookie_account_missing'
            async def attach(self, *args, **kwargs):
                raise CloudflareConflict('fixture-cookie-never-echo', code=self.code)
        manager = Manager()
        app = create_runner_app(settings, manager)
        async def scenario():
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://runner') as client:
                for code in ('cookie_account_missing', 'cookie_account_invalid', 'settings_account_mismatch', 'batch_account_mismatch'):
                    manager.code = code
                    reply = await client.post('/run', json={'owner': str(uuid4()), 'batch_id': str(uuid4()),
                        'timezone': 'Asia/Bangkok', 'cookie': 'fixture-cookie-never-echo'},
                        headers={'Authorization': 'Bearer ' + bridge_token(settings)})
                    self.assertEqual(reply.status_code, 409)
                    self.assertEqual(reply.json(), {'code': code})
                    self.assertNotIn('fixture-cookie-never-echo', reply.text)
                    response = Mock(status_code=409, headers={'Content-Type': 'application/json'})
                    response.json.return_value = reply.json()
                    with patch('domaincheck.cloudflare_runner.requests.post', return_value=response):
                        with self.assertRaises(CloudflareConflict) as caught: await dispatch(settings, {})
                    self.assertNotIn('Cookie does not match', str(caught.exception))
                    self.assertNotIn('fixture-cookie-never-echo', str(caught.exception))
        asyncio.run(scenario())

    def test_annotation_parser_keeps_urls_separate_and_matches_legacy(self):
        from domaincheck.core.cloudflare_input import parse_target_input
        parsed, invalid, duplicates = parse_target_input('Source: https://example.com/a -> https://example.net/login\nhttps://example.com/a')
        self.assertEqual(parsed, ['https://example.com/a', 'https://example.net/login'])
        self.assertEqual(invalid, [])
        self.assertEqual(duplicates, ['https://example.com/a'])

    def test_precheck_child_is_bounded_and_dashboard_redirects_are_disabled(self):
        from unittest.mock import Mock, patch
        from domaincheck.core.cloudflare_check import check
        completed = SimpleNamespace(returncode=0, stdout='{"cloudflare":true}')
        with patch('domaincheck.core.cloudflare_check.subprocess.run', return_value=completed) as run:
            self.assertEqual(check('https://example.com/', {'brand_name': 'Fixture'}), {'cloudflare': True})
        self.assertEqual(run.call_args.kwargs['timeout'], 180)
        self.assertNotIn('cookie', run.call_args.kwargs['input'])
        from domaincheck.engine.cloudflare_dashboard_session import submit_dashboard_report
        response = Mock(status_code=200, reason='OK')
        response.json.return_value = {'success': True, 'result': {'id': 'fixture-id'}}
        transport = Mock(); transport.post.return_value = response
        submit_dashboard_report('https://example.com/', 'Approved phishing report evidence for review.', {'cloudflare_account_id': 'a' * 32,
            'contact_name': 'Fixture', 'contact_email': 'fixture@example.com', 'brand_name': 'Fixture'}, 'session=fixture', session=transport)
        self.assertFalse(transport.post.call_args.kwargs['allow_redirects'])
        self.assertEqual(transport.post.call_args.kwargs['timeout'], 30)

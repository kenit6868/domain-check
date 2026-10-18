import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, skipUnless
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from domaincheck.api.dependencies import current_user
from domaincheck.application.audit_log import AuditLogReader, record, safe_metadata
from domaincheck.application.auth import AuthService
from domaincheck.domain.authz import DEFAULT_USER_PERMISSIONS
from domaincheck.infrastructure.auth.sessions import hash_token
from domaincheck.infrastructure.db.models import AuditEvent, AuthIdentity, Session, User
from domaincheck.main import create_app
from domaincheck.settings import Settings

DB = os.environ.get('DOMAINCHECK_TEST_DATABASE_URL')


class AuditMetadataTests(TestCase):
    def test_secrets_free_text_and_nested_values_are_dropped(self):
        value = safe_metadata({'password': 'SECRET', 'body': 'SECRET', 'cookie': 'SECRET',
            'state': {'private': 'SECRET'}, 'reason_code': 'error with SECRET raw exception',
            'url_count': 5, 'fields': ['smtp_password'], 'added_permissions': ['audit_logs.view', 'SECRET']})
        self.assertEqual(value, {'url_count': 5, 'fields': ['smtp_password'], 'added_permissions': ['audit_logs.view']})
        self.assertNotIn('audit_logs.view_all', DEFAULT_USER_PERMISSIONS)
        self.assertIn('audit_logs.view', DEFAULT_USER_PERMISSIONS)


class AuditCaptureTests(IsolatedAsyncioTestCase):
    async def test_polling_is_not_logged_and_commands_never_copy_body_or_query(self):
        from domaincheck.api.audit_capture import capture_command
        async def send(): pass
        actor = SimpleNamespace(user_id=uuid4())
        request = SimpleNamespace(state=SimpleNamespace(audit_actor=actor),
            app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(api_prefix='/api/v1'))),
            scope={'route': SimpleNamespace(path='/api/v1/domain-checks/{identity}', endpoint=send)},
            path_params={'identity': uuid4()}, method='GET')
        with patch('domaincheck.api.audit_capture.persist', new_callable=AsyncMock) as recorder:
            await capture_command(request, SimpleNamespace(status_code=200))
            recorder.assert_not_awaited()
            request.method = 'POST'
            await capture_command(request, SimpleNamespace(status_code=202))
            self.assertEqual(recorder.await_args.kwargs['outcome'], 'accepted')
            self.assertEqual(recorder.await_args.kwargs['metadata'], {'http_status': 202})
            await capture_command(request, SimpleNamespace(status_code=403))
            self.assertEqual(recorder.await_args.kwargs['action'], 'security.access_denied')

    async def test_unhandled_error_is_recorded_without_exception_text(self):
        app = create_app(Settings(_env_file=None))
        actor = SimpleNamespace(user_id=uuid4())
        from fastapi import Request
        @app.post('/api/v1/domain-checks/fixture')
        async def crash(request: Request):
            request.state.audit_actor = actor
            raise RuntimeError('SECRET raw failure')
        try:
            with patch('domaincheck.api.audit_capture.persist', new_callable=AsyncMock) as recorder:
                async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test') as client:
                    response = await client.post('/api/v1/domain-checks/fixture')
                self.assertEqual(response.status_code, 500)
                self.assertEqual(recorder.await_args.kwargs['outcome'], 'failed')
                self.assertNotIn('SECRET', repr(recorder.await_args))
        finally:
            await app.state.database.dispose(); await app.state.redis.aclose()


@skipUnless(DB, 'requires isolated PostgreSQL')
class AuditLogTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.app = create_app(Settings(_env_file=None, database_url=DB, artifact_root=Path(self.temp.name)))
        self.db = self.app.state.database
        async with self.db.session_factory() as session, session.begin():
            self.first = User(display_name='Audit Reader ' + uuid4().hex, status='active', timezone='Asia/Bangkok')
            self.second = User(display_name='Audit Other ' + uuid4().hex, status='active', timezone='Asia/Bangkok')
            session.add_all([self.first, self.second]); await session.flush()
            self.first_id, self.second_id = self.first.id, self.second.id
        self.actor = SimpleNamespace(user_id=self.first_id, status='active', timezone='Asia/Bangkok',
            permissions=frozenset({'audit_logs.view'}), csrf_hash=hash_token('csrf'))
        self.request_id = uuid4().hex

    async def asyncTearDown(self):
        await self.db.dispose(); await self.app.state.redis.aclose(); self.temp.cleanup()

    async def fixture_events(self):
        async with self.db.session_factory() as session, session.begin():
            for owner in [self.first_id, self.first_id, self.second_id, None]:
                await record(session, owner=owner, actor=owner, action='auth.login_failed' if owner is None else 'auth.login_success',
                    request_id=self.request_id, metadata={'password': 'SECRET', 'reason_code': 'fixture'})
            await record(session, owner=self.first_id, actor=self.first_id, action='roles.update', request_id=self.request_id)
            await record(session, owner=self.first_id, actor=self.first_id, action='cloudflare_worker.checkpoint',
                request_id=self.request_id, outcome='success', metadata={'state': 'FAILED'})

    async def test_api_owner_global_anonymous_scope_and_read_only(self):
        await self.fixture_events()
        async def user(): return self.actor
        self.app.dependency_overrides[current_user] = user
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url='http://test') as client:
            own = await client.get('/api/v1/audit-logs', params={'request_id': self.request_id})
            self.assertEqual(own.status_code, 200)
            self.assertEqual(len(own.json()['items']), 2)
            self.assertNotIn('SECRET', own.text)
            self.assertIn('no-store', own.headers['cache-control'])
            self.assertEqual((await client.get('/api/v1/audit-logs?scope=all')).status_code, 403)
            self.actor.permissions = frozenset({'audit_logs.view', 'audit_logs.view_all'})
            all_events = (await client.get('/api/v1/audit-logs', params={'scope': 'all', 'request_id': self.request_id})).json()['items']
            self.assertEqual(len(all_events), 4)
            anonymous = next(row for row in all_events if row['actor_type'] == 'anonymous')
            self.actor.permissions = frozenset({'audit_logs.view'})
            self.assertEqual((await client.get('/api/v1/audit-logs/' + anonymous['id'])).status_code, 404)
            self.assertEqual((await client.get('/api/v1/audit-logs/' + anonymous['id'] + '?scope=all')).status_code, 403)
            self.assertEqual((await client.delete('/api/v1/audit-logs/' + own.json()['items'][0]['id'])).status_code, 405)
            self.actor.permissions = frozenset()
            self.assertEqual((await client.get('/api/v1/audit-logs')).status_code, 403)

    async def test_cursor_filters_and_invalid_ranges_cannot_escape_scope(self):
        await self.fixture_events()
        today = datetime.now(UTC).date()
        params = dict(scope='own', category='auth', start=today - timedelta(days=1), end=today + timedelta(days=1), request_id=self.request_id)
        async with self.db.session_factory() as session:
            reader = AuditLogReader(session, self.actor)
            first = await reader.list(**params, limit=1)
            second = await reader.list(**params, cursor=first['next_cursor'], limit=1)
            self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])
            self.assertIsNone(second['next_cursor'])
            self.assertEqual((await reader.list(**{**params, 'category': 'admin'}))['items'][0]['action'], 'roles.update')
            failed = await reader.list(**{**params, 'category': 'activity'}, outcome='failed')
            self.assertEqual(failed['items'][0]['outcome'], 'failed')
            self.assertFalse((await reader.list(**params, user='%'))['items'])
            self.assertEqual(len((await reader.list(**params, user=self.first.display_name))['items']), 2)
            with self.assertRaises(ValueError): await reader.list(**params, cursor='invalid')
            with self.assertRaises(ValueError): await reader.list(**{**params, 'start': today, 'end': today - timedelta(days=1)})

    async def test_login_logout_and_expired_session_are_recorded_once_without_tokens(self):
        async with self.db.session_factory() as session, session.begin():
            login = await AuthService(session, self.request_id).login_oidc_identity(issuer='https://issuer.test',
                subject=uuid4().hex, display_name='Login fixture', telegram_username=None, timezone='Asia/Bangkok')
            identity = await session.scalar(select(Session.id).where(Session.token_hash == hash_token(login.token)))
            owner = login.user.id
        async with self.db.session_factory() as session, session.begin():
            await AuthService(session).revoke(identity)
            await AuthService(session).revoke(identity)
        async with self.db.session_factory() as session:
            events = list(await session.scalars(select(AuditEvent).where(AuditEvent.owner_user_id == owner)))
            self.assertEqual([row.action for row in events].count('auth.logout'), 1)
            self.assertNotIn(login.token, json.dumps([row.metadata_allowlist for row in events]))
        async with self.db.session_factory() as session, session.begin():
            expired = await AuthService(session).login_oidc_identity(issuer='https://issuer.test', subject=uuid4().hex,
                display_name='Expired fixture', telegram_username=None, timezone='Asia/Bangkok', session_lifetime=timedelta(seconds=-1))
            expired_id = await session.scalar(select(Session.id).where(Session.token_hash == hash_token(expired.token)))
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url='http://test', cookies={'dc_session': expired.token}) as client:
            for _ in range(2): self.assertEqual((await client.get('/api/v1/me')).status_code, 401)
        async with self.db.session_factory() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.event_key == f'session-terminal:{expired_id}')), 1)

    async def test_failed_callback_is_durable_and_redacted_and_reader_is_owner_scoped(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url='http://test') as client:
            response = await client.get('/api/v1/auth/telegram/callback?error=SECRET&code=SECRET', headers={'X-Request-ID': self.request_id})
            self.assertEqual(response.status_code, 303)
        async with self.db.session_factory() as session:
            rows = list(await session.scalars(select(AuditEvent).where(AuditEvent.request_id == self.request_id)))
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0].owner_user_id)
            self.assertNotIn('SECRET', json.dumps(rows[0].metadata_allowlist))

    async def test_disabled_account_login_is_attributed_without_creating_a_session(self):
        subject = uuid4().hex
        async with self.db.session_factory() as session, session.begin():
            await session.execute(update(User).where(User.id == self.second_id).values(status='disabled'))
            session.add(AuthIdentity(user_id=self.second_id, provider='telegram', issuer='https://issuer.test', subject=subject))
        self.app.state.oidc = SimpleNamespace(exchange_code=AsyncMock(return_value=(
            {'iss': 'https://issuer.test', 'sub': subject}, '/')))
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url='http://test', cookies={'dc_oidc_binding': 'fixture'}) as client:
            response = await client.get('/api/v1/auth/telegram/callback?code=fixture&state=fixture', headers={'X-Request-ID': self.request_id})
            self.assertEqual(response.status_code, 303)
        async with self.db.session_factory() as session:
            event = await session.scalar(select(AuditEvent).where(AuditEvent.request_id == self.request_id))
            self.assertEqual(event.owner_user_id, self.second_id)
            self.assertEqual(event.metadata_allowlist['reason_code'], 'account_disabled')
            self.assertEqual(await session.scalar(select(func.count()).select_from(Session).where(Session.user_id == self.second_id)), 0)

    async def test_atomic_event_rolls_back_with_business_transaction_and_key_dedupes(self):
        key = uuid4().hex
        async with self.db.session_factory() as session:
            async with session.begin() as transaction:
                await record(session, owner=self.first_id, actor=self.first_id, action='delivery.outcome', event_key=key)
                await transaction.rollback()
            self.assertIsNone(await session.scalar(select(AuditEvent.id).where(AuditEvent.event_key == key)))
        async with self.db.session_factory() as session, session.begin():
            for _ in range(2):
                await record(session, owner=self.first_id, actor=self.first_id, action='delivery.outcome', event_key=key)
            self.assertEqual(await session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_key == key)), 1)

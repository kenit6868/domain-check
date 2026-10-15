from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from domaincheck.api.dependencies import current_user, get_db_session
from domaincheck.application.quick_reports import QuickReportService
from domaincheck.infrastructure.db.models import User, JobItem
from sqlalchemy import select
from domaincheck.infrastructure.db.session import Database
from domaincheck.main import create_app
from domaincheck.settings import Settings


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class DailyQuickReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_accumulates_latest_unique_urls_with_owner_day_and_permission_boundaries(self):
        database = Database(os.environ['DOMAINCHECK_TEST_DATABASE_URL'])
        app = create_app(Settings(_env_file=None))
        now = datetime(2026, 10, 16, 12, tzinfo=UTC)
        app.state.clock = SimpleNamespace(now=lambda: now)
        try:
            async with database.session_factory() as session, session.begin():
                first = User(display_name='Daily A', status='active', timezone='Asia/Bangkok')
                second = User(display_name='Daily B', status='active', timezone='Asia/Bangkok')
                session.add_all([first, second])
                await session.flush()

                async def create(owner, urls, at):
                    result = await QuickReportService(session, owner_user_id=owner.id,
                        actor_user_id=owner.id, timezone=owner.timezone, request_id='daily-test', now=at).create(urls)
                    for check in result.checks:
                        check.created_at = at
                        check.status = 'succeeded'
                        check.result = {}
                    await session.flush()
                    return result

                initial = await create(first, ['https://one.test/login?q=1', 'https://two.test/'], now - timedelta(hours=2))
                latest = await create(first, ['https://one.test/login?q=1', 'https://three.test/'], now - timedelta(hours=1))
                await create(first, ['https://yesterday.test/'], now - timedelta(days=1))
                other = await create(first, ['https://other-workflow.test/'], now)
                other.checks[0].workflow = 'other'
                foreign = await create(second, ['https://one.test/login?q=1'], now)
                await session.flush()
                actor = SimpleNamespace(user_id=first.id, status='active', permissions={'quick_report.view'}, timezone='Asia/Bangkok')
                async def same_session():
                    yield session
                app.dependency_overrides[get_db_session] = same_session
                app.dependency_overrides[current_user] = lambda: actor
                async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                    with patch('requests.sessions.Session.request', side_effect=AssertionError('No network')):
                        response = await client.get('/api/v1/quick-reports/daily')
                    self.assertEqual(response.status_code, 200, response.text)
                    body = response.json()
                    self.assertEqual(body['business_day'], '2026-10-16')
                    self.assertEqual(len(body['checks']), 3)
                    ids = {row['id'] for row in body['checks']}
                    self.assertIn(str(initial.checks[1].id), ids)
                    self.assertIn(str(latest.checks[0].id), ids)
                    self.assertNotIn(str(initial.checks[0].id), ids)
                    self.assertNotIn(str(foreign.checks[0].id), ids)
                    self.assertIn('private, no-store', response.headers['cache-control'])
                    self.assertEqual(len((await client.get('/api/v1/quick-reports/daily')).json()['checks']), 3)
                    orphan = latest.checks[1]
                    orphan.status = 'pending'
                    initial.checks[1].status = 'pending'
                    item = await session.scalar(select(JobItem).where(JobItem.job_id == latest.job.id,
                        JobItem.result_metadata['check_id'].as_string() == str(orphan.id)))
                    await session.delete(item)
                    await session.flush()
                    status_rows = {row['id']: row['status'] for row in (await client.get('/api/v1/quick-reports/daily')).json()['checks']}
                    self.assertEqual(status_rows[str(orphan.id)], 'not_queued')
                    self.assertEqual(status_rows[str(initial.checks[1].id)], 'pending')
                    self.assertEqual(orphan.status, 'pending')  # GET must not modify durable history.
                    actor.user_id = second.id
                    foreign_result = (await client.get('/api/v1/quick-reports/daily')).json()
                    self.assertEqual([row['id'] for row in foreign_result['checks']], [str(foreign.checks[0].id)])
                    actor.permissions = set()
                    self.assertEqual((await client.get('/api/v1/quick-reports/daily')).status_code, 403)
                    actor.permissions = {'quick_report.view'}
                    app.state.clock = SimpleNamespace(now=lambda: now + timedelta(days=1))
                    self.assertEqual((await client.get('/api/v1/quick-reports/daily')).json()['checks'], [])
                # This scenario creates queued fixture jobs; do not expose them to worker tests.
                await session.rollback()
        finally:
            await database.dispose()
            await app.state.database.dispose()
            await app.state.redis.aclose()

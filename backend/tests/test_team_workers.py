import asyncio
import os
import threading
import time
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from domaincheck.api.dependencies import current_user
from domaincheck.api.health import worker_health
from domaincheck.application.quick_reports import QuickReportService, QuickReportWorker
from domaincheck.infrastructure.db.models import AuditEvent, Check, Job, JobItem, Target, User
from domaincheck.infrastructure.db.repositories import JobRepository
from domaincheck.infrastructure.db.session import Database
from domaincheck.main import create_app
from domaincheck.settings import Settings
from domaincheck.workers.runtime import HEALTH_PREFIX, publish_health, run_slot


class MemoryRedis:
    def __init__(self): self.rows = {}
    async def set(self, key, value, **kwargs): self.rows[key] = value
    async def get(self, key): return self.rows.get(key)
    async def delete(self, key): self.rows.pop(key, None)
    async def scan_iter(self, match, **kwargs):
        import fnmatch
        for key in list(self.rows):
            if fnmatch.fnmatch(key, match): yield key


class TeamRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_heartbeat_recovers_after_transient_redis_failure(self):
        redis = MemoryRedis()
        original = redis.set
        recovered = asyncio.Event()
        calls = 0
        async def flaky(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('fixture Redis outage')
            await original(*args, **kwargs)
            if calls >= 3: recovered.set()
        original_sleep = asyncio.sleep
        async def short_sleep(_): await original_sleep(.001)
        with patch.object(redis, 'set', side_effect=flaky), \
             patch('domaincheck.workers.runtime.asyncio.sleep', side_effect=short_sleep):
            async with publish_health(redis, 'cloudflare_runner') as task:
                await asyncio.wait_for(recovered.wait(), timeout=1)
                self.assertFalse(task.done())
    def test_twenty_mock_mail_sends_keep_message_identity_and_unknown_is_not_retried(self):
        import smtplib
        from concurrent.futures import ThreadPoolExecutor
        from email.message import EmailMessage
        from domaincheck.core.mail import SmtpConnectionSpec
        from domaincheck.core.mail.sending import SmtpReportSender
        sender = SmtpReportSender(())
        sent = []
        lock = threading.Lock()
        active = peak = 0
        def open_smtp(*_):
            server = Mock()
            def send(message):
                nonlocal active, peak
                with lock:
                    active += 1; peak = max(peak, active); sent.append(message['Message-ID'])
                time.sleep(.005)
                with lock: active -= 1
                if message['Message-ID'] == '<fixture-7@example.test>':
                    raise smtplib.SMTPServerDisconnected('fixture disconnect after DATA')
                return {}
            server.send_message.side_effect = send
            return server
        messages = []
        for index in range(20):
            message = EmailMessage()
            message['Message-ID'] = f'<fixture-{index}@example.test>'
            messages.append(message)
        spec = SmtpConnectionSpec('mail.example.test', 465, 'fixture', 'fixture-only', transport='implicit_tls')
        with patch.object(sender.policy, '_check'), \
             patch('domaincheck.core.mail.sending.StandardMailConnectionTester._open_smtp', side_effect=open_smtp) as connect, \
             ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda message: sender.send(spec, message), messages))
        self.assertEqual(connect.call_count, 20)
        self.assertEqual(len(set(sent)), 20)
        self.assertEqual([result[0] for result in results].count('sent'), 19)
        self.assertEqual(results[7], ('unknown', 'send_message', 'smtp_outcome_uncertain'))
        self.assertLessEqual(peak, 2)

    async def test_cloudflare_twenty_batch_tasks_are_bounded_without_submission(self):
        from domaincheck.application.cloudflare_runner import CloudflareRunner
        runner = CloudflareRunner(None, None, slots=2)
        active = peak = 0
        async def step(*_):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.005)
            active -= 1
        with patch.object(runner, '_send_step', side_effect=step) as check:
            await asyncio.gather(*(runner.send(None, uuid4()) for _ in range(20)))
        self.assertEqual(check.await_count, 20)
        self.assertEqual(peak, 2)

    async def test_paused_cloudflare_batches_do_not_hold_capacity_for_other_owners(self):
        from domaincheck.application.cloudflare_runner import CloudflareRunner
        runner = CloudflareRunner(None, None, slots=2)
        progressed = asyncio.Event()
        async def step(_service, identity):
            if identity == 3:
                progressed.set()
                return None
            return 'waiting'
        with patch.object(runner, '_send_step', side_effect=step):
            tasks = [asyncio.create_task(runner.send(None, identity)) for identity in (1, 2, 3)]
            try:
                await asyncio.wait_for(progressed.wait(), 1)
            finally:
                for task in tasks: task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def test_cli_all_includes_browser_and_respects_bounded_slots(self):
        from domaincheck.workers import cli
        @asynccontextmanager
        async def connect():
            yield SimpleNamespace(execute=AsyncMock())
        db = SimpleNamespace(engine=SimpleNamespace(connect=connect), session_factory=None, dispose=AsyncMock())
        redis = SimpleNamespace(aclose=AsyncMock())
        settings = Settings(_env_file=None, browser_capture_enabled=True)
        fake = SimpleNamespace(run_next=AsyncMock(return_value=None))
        with patch.object(cli, 'get_settings', return_value=settings), \
             patch.object(cli, 'Database', return_value=db), \
             patch.object(cli.Redis, 'from_url', return_value=redis), \
             patch.object(cli.DomainQuickReportAdapter, 'validate_runtime'), \
             patch.object(cli.DomainInvestigationAdapter, 'validate_runtime'), \
             patch.object(cli.DomainWorkerPrecheckAdapter, 'validate_runtime'), \
             patch.object(cli.EngineBrowserCaptureAdapter, 'validate_runtime'), \
             patch.object(cli, 'QuickReportWorker', return_value=fake), \
             patch.object(cli, 'DomainCheckWorker', return_value=fake), \
             patch.object(cli, 'DomainPrecheckWorker', return_value=fake), \
             patch.object(cli, 'DomainSendWorker', return_value=fake), \
             patch.object(cli, 'BrowserEvidenceWorker', return_value=fake) as capture, \
             patch.object(cli, 'run_slot', new=AsyncMock()) as slot:
            await cli.run(once=False, poll_seconds=.1, job_type='all')
        self.assertEqual(slot.await_count, 9)
        self.assertEqual([call.args[2] for call in slot.await_args_list].count('browser_evidence'), 1)
        capture.assert_called_once()
        db.dispose.assert_awaited_once()
        redis.aclose.assert_awaited_once()

    async def test_busy_slot_does_not_block_other_job_type_and_drains(self):
        entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def slow(_):
            entered.set()
            await release.wait()
            return uuid4()
        redis = MemoryRedis()
        worker = SimpleNamespace(run_next=slow)
        task = asyncio.create_task(run_slot(worker, redis, 'slow', stopped, poll_seconds=.01))
        await asyncio.wait_for(entered.wait(), 1)
        fast = SimpleNamespace(run_next=AsyncMock(return_value=uuid4()))
        await run_slot(fast, redis, 'fast', asyncio.Event(), poll_seconds=.01, once=True)
        stopped.set()
        self.assertFalse(task.done())  # graceful shutdown waits for in-flight work
        release.set()
        await asyncio.wait_for(task, 1)
        fast.run_next.assert_awaited_once()
        self.assertEqual(redis.rows, {})

    async def test_heartbeat_cleanup_and_expired_marker_means_offline(self):
        redis = MemoryRedis()
        async with publish_health(redis, 'quick_report'):
            result = await worker_health(redis, ['quick_report', 'browser_evidence'])
            self.assertEqual([row['state'] for row in result], ['ready', 'offline'])
            self.assertEqual(result[0]['slots'], 1)
        self.assertEqual((await worker_health(redis, ['quick_report']))[0]['state'], 'offline')

    async def test_health_is_authenticated_permission_scoped_and_sanitized(self):
        app = create_app(Settings(_env_file=None, browser_capture_enabled=True))
        redis = MemoryRedis()
        await app.state.redis.aclose()
        app.state.redis = redis
        redis.rows[HEALTH_PREFIX + 'quick_report:instance'] = 'ready'
        redis.rows[HEALTH_PREFIX + 'domain_worker_send:instance'] = 'ready'
        actor = SimpleNamespace(status='active', permissions={'quick_report.view'})
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                self.assertEqual((await client.get('/api/v1/health/workers')).status_code, 401)
                app.dependency_overrides[current_user] = lambda: actor
                response = await client.get('/api/v1/health/workers')
                self.assertEqual(response.json(), {'services': [{'name': 'quick_report', 'state': 'ready', 'slots': 1}]})
                self.assertIn('no-store', response.headers['cache-control'])
                actor.status = 'pending'
                self.assertEqual((await client.get('/api/v1/health/workers')).status_code, 403)
        finally:
            await app.state.database.dispose()

    async def test_health_dependency_failure_is_unknown_not_ready(self):
        from domaincheck.api.health import workers
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            settings=Settings(_env_file=None), redis=SimpleNamespace())))
        actor = SimpleNamespace(status='active', permissions={'quick_report.view'})
        from fastapi import Response
        with patch('domaincheck.api.health.worker_health', new=AsyncMock(side_effect=RuntimeError('private host/password'))):
            result = await workers(request, Response(), actor)
        self.assertEqual(result, {'services': [{'name': 'quick_report', 'state': 'unknown', 'slots': 0}]})

    def test_config_bounds_and_evidence_opt_in(self):
        from pydantic import ValidationError
        settings = Settings(_env_file=None)
        self.assertNotIn('browser_evidence', settings.worker_slots())
        self.assertEqual(sum(settings.worker_slots().values()), 8)
        self.assertEqual(Settings(_env_file=None, browser_capture_enabled=True).worker_slots()['browser_evidence'], 1)
        for invalid in (0, 100):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, worker_domain_worker_precheck_slots=invalid)


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class TeamQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(os.environ['DOMAINCHECK_TEST_DATABASE_URL'])
        self.kind = f'capacity_{uuid4().hex}'

    async def asyncTearDown(self):
        # Remove only fixtures belonging to this test's unique job type. Generic
        # repository tests deliberately claim any job, so no queued fixture may leak.
        async with self.db.session_factory() as session, session.begin():
            owners = list(await session.scalars(select(Job.owner_user_id).where(Job.job_type == self.kind)))
            if owners:
                for model in (AuditEvent, JobItem, Job, Check, Target):
                    await session.execute(delete(model).where(model.owner_user_id.in_(owners)))
                await session.execute(delete(User).where(User.id.in_(owners)))
        await self.db.dispose()

    async def test_drain_checkpoints_current_item_then_restart_only_processes_pending(self):
        now = datetime.now(UTC)
        async with self.db.session_factory() as session, session.begin():
            owner = User(display_name='drain-test', status='active', timezone='Asia/Bangkok')
            session.add(owner)
            await session.flush()
            result = await QuickReportService(session, owner_user_id=owner.id, actor_user_id=owner.id,
                timezone=owner.timezone, request_id='drain-test', now=now).create(
                [f'https://drain-{uuid4().hex}.example.test/' for _ in range(2)])
            result.job.job_type = self.kind
            job_id = result.job.id
        stop = threading.Event()
        calls = []
        class Adapter:
            def run(self, target, config):
                calls.append(target)
                stop.set()
                return {'domain': target}
        cache = SimpleNamespace(get_json=AsyncMock(return_value=None), set_json=AsyncMock())
        worker = QuickReportWorker(self.db.session_factory, cache, Adapter(), job_type=self.kind)
        worker.should_drain = stop.is_set
        await worker.run_next('old-process')
        async with self.db.session_factory() as session:
            job = await session.get(Job, job_id)
            self.assertEqual(job.state, 'queued')
            self.assertIsNone(job.lease_token)
            states = list(await session.scalars(select(JobItem.state).where(JobItem.job_id == job_id).order_by(JobItem.input_index)))
            self.assertEqual(states, ['succeeded', 'pending'])
        replacement = QuickReportWorker(self.db.session_factory, cache, Adapter(), job_type=self.kind)
        await replacement.run_next('replacement-process')
        async with self.db.session_factory() as session:
            self.assertEqual((await session.get(Job, job_id)).state, 'completed')
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(set(calls)), 2)

    async def make_job(self, session, owner, **kwargs):
        job = Job(owner_user_id=owner.id, actor_user_id=owner.id, job_type=self.kind,
                  state='queued', business_day=datetime.now(UTC).date(), timezone='Asia/Bangkok',
                  account_scope=[], result_metadata={}, **kwargs)
        session.add(job)
        await session.flush()
        return job

    async def test_parallel_claims_spread_across_owners_and_types(self):
        async with self.db.session_factory() as session, session.begin():
            owners = [User(display_name=f'fair-{i}', status='active', timezone='Asia/Bangkok') for i in range(3)]
            session.add_all(owners)
            await session.flush()
            for owner in owners:
                for _ in range(3): await self.make_job(session, owner)
        async def claim():
            async with self.db.session_factory() as session, session.begin():
                job = await JobRepository(session).claim_next(worker_token=uuid4().hex,
                    job_types=frozenset({self.kind}), fair_owner=True)
                return (job.id, job.owner_user_id) if job else None
        results = [row for row in await asyncio.gather(*(claim() for _ in range(8))) if row]
        self.assertEqual(len(results), 3)
        self.assertEqual(len({row[0] for row in results}), 3)
        self.assertEqual(len({row[1] for row in results}), 3)

    async def test_requeued_batch_yields_to_unserved_owner_and_expired_lease_recovers(self):
        async with self.db.session_factory() as session, session.begin():
            owners = [User(display_name=f'round-{i}', status='active', timezone='Asia/Bangkok') for i in range(2)]
            session.add_all(owners)
            await session.flush()
            old = await self.make_job(session, owners[0], heartbeat_at=datetime.now(UTC)-timedelta(seconds=5))
            fresh = await self.make_job(session, owners[1])
            fresh_id = fresh.id
        async with self.db.session_factory() as session, session.begin():
            claimed = await JobRepository(session).claim_next(worker_token='fresh', job_types=frozenset({self.kind}), fair_owner=True)
            self.assertEqual(claimed.id, fresh_id)
            claimed.state = 'completed'
        async with self.db.session_factory() as session, session.begin():
            expired = await session.get(Job, old.id)
            expired.state, expired.lease_expires_at = 'running', datetime.now(UTC)-timedelta(seconds=1)
        async with self.db.session_factory() as session, session.begin():
            reclaimed = await JobRepository(session).claim_next(worker_token='recovery', job_types=frozenset({self.kind}), fair_owner=True)
            self.assertEqual(reclaimed.id, old.id)

    async def test_new_batch_cannot_bypass_another_owner_waiting_longer(self):
        async with self.db.session_factory() as session, session.begin():
            owners = [User(display_name=f'history-{i}', status='active', timezone='Asia/Bangkok') for i in range(2)]
            session.add_all(owners)
            await session.flush()
            prior = await self.make_job(session, owners[0], heartbeat_at=datetime.now(UTC)-timedelta(seconds=1))
            prior.state = 'completed'
            await self.make_job(session, owners[0])
            waiting = await self.make_job(session, owners[1], heartbeat_at=datetime.now(UTC)-timedelta(seconds=10))
            waiting_id = waiting.id
        async with self.db.session_factory() as session, session.begin():
            selected = await JobRepository(session).claim_next(worker_token='fair-history',
                job_types=frozenset({self.kind}), fair_owner=True)
            self.assertEqual(selected.id, waiting_id)

    async def test_url_turns_rotate_owners_and_keep_failed_item_counts_across_turns(self):
        now = datetime.now(UTC)
        ids = []
        async with self.db.session_factory() as session, session.begin():
            for index in range(2):
                owner = User(display_name=f'turn-{index}', status='active', timezone='Asia/Bangkok')
                session.add(owner)
                await session.flush()
                result = await QuickReportService(session, owner_user_id=owner.id, actor_user_id=owner.id,
                    timezone=owner.timezone, request_id='turn-test', now=now).create(
                    [f'https://turn-{index}-{n}-{self.kind}.example.test/' for n in range(2)])
                result.job.job_type = self.kind
                result.job.created_at = now + timedelta(milliseconds=index)
                ids.append(result.job.id)
        calls = []
        class Adapter:
            def run(self, target, config):
                calls.append(target)
                if 'turn-0-0-' in target: raise RuntimeError('fixture failure')
                return {'domain': target}
        cache = SimpleNamespace(get_json=AsyncMock(return_value=None), set_json=AsyncMock())
        worker = QuickReportWorker(self.db.session_factory, cache, Adapter(), job_type=self.kind)
        worker.items_per_turn = 1
        for index in range(4):
            self.assertEqual(await worker.run_next(f'turn-{index}'), ids[index % 2])
        async with self.db.session_factory() as session:
            self.assertEqual((await session.get(Job, ids[0])).result_metadata['failed_count'], 1)
            self.assertEqual((await session.get(Job, ids[0])).state, 'partial')
            self.assertEqual((await session.get(Job, ids[1])).state, 'completed')
        self.assertEqual(len(set(calls)), 4)

    async def test_twenty_users_fifty_domains_each_with_bounded_real_queue(self):
        await self.capacity_fixture(20)

    async def test_thirty_users_fifty_domains_each_with_bounded_real_queue(self):
        await self.capacity_fixture(30)

    async def capacity_fixture(self, user_count):
        asyncio.get_running_loop().set_debug(False)
        now = datetime.now(UTC)
        async def submit(index):
            async with self.db.session_factory() as session, session.begin():
                owner = User(display_name=f'load-{index}', status='active', timezone='Asia/Bangkok')
                session.add(owner)
                await session.flush()
                result = await QuickReportService(session, owner_user_id=owner.id, actor_user_id=owner.id,
                    timezone=owner.timezone, request_id='capacity-test', now=now).create(
                    [f'https://u{index}-d{n}-{self.kind}.example.test/' for n in range(50)])
                result.job.job_type = self.kind
                return result.job.id, owner.id
        started = time.monotonic()
        pairs = await asyncio.gather(*(submit(index) for index in range(user_count)))
        ids, owners = [row[0] for row in pairs], [row[1] for row in pairs]
        lock = threading.Lock()
        calls, active, peak = [], 0, 0
        class Adapter:
            def run(self, target, config):
                nonlocal active, peak
                with lock:
                    calls.append(target); active += 1; peak = max(peak, active)
                time.sleep(.001)  # no network; exercise thread offload/concurrent slots
                with lock: active -= 1
                return {'domain': target, 'cloaking': {'verdict': 'NO_SIGNAL'}}
        cache = SimpleNamespace(get_json=AsyncMock(return_value=None), set_json=AsyncMock())
        worker = QuickReportWorker(self.db.session_factory, cache, Adapter(), job_type=self.kind)
        worker.items_per_turn = 1
        async def drain():
            while await worker.run_next(uuid4().hex) is not None: pass
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No real network')):
            await asyncio.wait_for(asyncio.gather(drain(), drain()), timeout=180)
        async with self.db.session_factory() as session:
            states = list(await session.scalars(select(Job.state).where(Job.id.in_(ids))))
            self.assertEqual(states, ['completed'] * user_count)
            self.assertEqual(await session.scalar(select(func.count()).select_from(Check).where(
                Check.owner_user_id.in_(owners), Check.status == 'succeeded')), user_count * 50)
            self.assertEqual(await session.scalar(select(func.count()).select_from(JobItem).where(
                JobItem.job_id.in_(ids), JobItem.state == 'succeeded')), user_count * 50)
        self.assertEqual(len(calls), user_count * 50)
        self.assertEqual(len(set(calls)), user_count * 50)
        self.assertLessEqual(peak, 2)
        print(f'CAPACITY_FIXTURE: {user_count} owners / {user_count * 50} domains, 2 slots, {time.monotonic()-started:.2f}s, no network')

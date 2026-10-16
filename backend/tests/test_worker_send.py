import asyncio
import io
import os
import smtplib
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import UUID

from PIL import Image
from sqlalchemy import select, update

from domaincheck.application.domain_worker import DomainPrecheckWorker, DomainWorkerService
from domaincheck.application.jobs import JobConflict
from domaincheck.application.mail_settings import MailSettingsService
from domaincheck.application.worker_send import DomainSendWorker, WorkerSendService
from domaincheck.core.mail.connectivity import SmtpConnectionSpec
from domaincheck.core.mail.sending import SmtpReportSender
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.db.models import Artifact, Delivery, DeliveryAttempt, Job, JobItem, SenderAccount, User, UserSettings
from domaincheck.infrastructure.db.session import Database
from domaincheck.infrastructure.secrets import SecretEnvelope
from domaincheck.ports.artifacts import ImageUpload


class SmtpTransportTests(unittest.TestCase):
    def test_authentication_rejection_never_retries(self):
        sender = SmtpReportSender(()); server = Mock()
        server.login.side_effect = smtplib.SMTPAuthenticationError(535, b'private diagnostic')
        with patch.object(sender.policy, '_check'), patch('domaincheck.core.mail.sending.StandardMailConnectionTester._open_smtp', return_value=server) as open_smtp:
            result = sender.send(SmtpConnectionSpec('mail.example.com', 465, 'a@example.com', 'private', transport='implicit_tls'), Mock())
        self.assertEqual(result, ('failed', 'authenticate', 'smtp_rejected'))
        self.assertEqual(open_smtp.call_count, 1); server.send_message.assert_not_called()

    def test_disconnect_after_data_is_unknown_and_not_retried(self):
        sender = SmtpReportSender(())
        server = Mock(); server.send_message.side_effect = smtplib.SMTPServerDisconnected('secret must not leak')
        with patch.object(sender.policy, '_check'), patch('domaincheck.core.mail.sending.StandardMailConnectionTester._open_smtp', return_value=server) as open_smtp:
            result = sender.send(SmtpConnectionSpec('mail.example.com', 465, 'a@example.com', 'private', transport='implicit_tls'), Mock())
        self.assertEqual(result, ('unknown', 'send_message', 'smtp_outcome_uncertain'))
        self.assertEqual(open_smtp.call_count, 1)

    def test_transient_before_data_retries_once_with_same_message(self):
        sender = SmtpReportSender(()); server = Mock(); server.send_message.return_value = {}
        message = Mock()
        with patch.object(sender.policy, '_check'), patch('domaincheck.core.mail.sending.StandardMailConnectionTester._open_smtp', side_effect=[TimeoutError(), server]) as open_smtp:
            result = sender.send(SmtpConnectionSpec('mail.example.com', 587, 'a@example.com', 'private'), message)
        self.assertEqual(result[0], 'sent'); self.assertEqual(open_smtp.call_count, 2)
        server.send_message.assert_called_once_with(message)


class WorkerSendApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_requires_permission_origin_csrf_and_server_scope(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from uuid import uuid4
        from httpx import ASGITransport, AsyncClient
        from domaincheck.api.dependencies import current_user, get_db_session
        from domaincheck.infrastructure.auth.sessions import hash_token
        from domaincheck.main import create_app
        from domaincheck.settings import Settings
        actor = SimpleNamespace(user_id=uuid4(), status='active', timezone='Asia/Bangkok',
            permissions={'domain_worker.view'}, csrf_hash=hash_token('fixture-csrf'))
        app = create_app(Settings(_env_file=None))
        async def user(): return actor
        async def db(): yield SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(brand_name='Fixture', contact_name='Operator', contact_email='a@example.com')))
        app.dependency_overrides[current_user] = user; app.dependency_overrides[get_db_session] = db
        path = f'/api/v1/domain-worker/prechecks/{uuid4()}/send'
        headers = {'Origin': 'http://127.0.0.1:5173', 'X-CSRF-Token': 'fixture-csrf'}
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.post(path, json={'confirmed': True}, headers=headers)).status_code, 403)
            actor.permissions.add('domain_worker.start')
            self.assertEqual((await client.post(path, json={'confirmed': True})).status_code, 403)
            self.assertEqual((await client.post(path, json={'confirmed': True, 'sender_account_ids': [str(uuid4())]}, headers=headers)).status_code, 422)
            with patch.object(WorkerSendService, 'start', new=AsyncMock(return_value={'id': 'fixture'})):
                response = await client.post(path, json={'confirmed': True, 'batch_size': 5, 'interval_seconds': 300}, headers=headers)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.headers['cache-control'], 'private, no-store')


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class WorkerSendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(os.environ['DOMAINCHECK_TEST_DATABASE_URL'])
        self.temp = tempfile.TemporaryDirectory(); self.store = FilesystemArtifactStore(Path(self.temp.name))
        self.now = datetime(2026, 10, 16, 8, tzinfo=UTC)
        self.envelope = SecretEnvelope({1: b'x' * 32}, 1)
        self.messages = []; self.outcomes = []
        async with self.db.session_factory() as session, session.begin():
            owner, other = User(display_name='Sender', status='active'), User(display_name='Other', status='active')
            session.add_all([owner, other]); await session.flush(); self.owner, self.other = owner.id, other.id
            session.add(UserSettings(owner_user_id=owner.id, brand_name='Fixture Brand', company_name='Fixture Inc',
                contact_name='Fixture Operator', contact_email='operator@example.com', signature_role='Brand Protection'))
            account = SenderAccount(owner_user_id=owner.id, label='Sender', normalized_identity='sender',
                smtp_host='smtp.example.com', smtp_port=465, smtp_username='sender@example.com',
                smtp_transport='implicit_tls', enabled=True, is_default=True)
            session.add(account); await session.flush(); self.account = account.id
            mail = MailSettingsService(session, owner_user_id=owner.id, actor_user_id=owner.id, request_id='test', envelope=self.envelope)
            mail._store_secrets(account, {'smtp_password': 'fixture-only'})
        parent = self
        class Sender:
            def send(_self, spec, message):
                parent.messages.append(message)
                return parent.outcomes.pop(0) if parent.outcomes else ('sent', 'send_message', 'accepted')
        self.worker = DomainSendWorker(self.db.session_factory, self.store, Sender(), self.envelope, now=lambda: self.now, heartbeat_seconds=.01)

    async def asyncTearDown(self):
        async with self.db.session_factory() as session, session.begin():
            await session.execute(update(Job).where(Job.owner_user_id.in_([self.owner, self.other]),
                Job.state.in_(['queued', 'running', 'stopping'])).values(state='stopped', lease_token=None, lease_expires_at=None))
        await self.db.dispose(); self.temp.cleanup()

    def svc(self, session, owner=None):
        return WorkerSendService(session, self.store, owner=owner or self.owner, actor=owner or self.owner,
            timezone='Asia/Bangkok', request_id='test', now=self.now, envelope=self.envelope)

    async def precheck(self, targets, *, dom=False, no_email_domains=()):
        async with self.db.session_factory() as session, session.begin():
            operation = await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok',
                request_id='test', now=self.now).create(targets, [self.account]); operation_id = operation.job.id
        class Adapter:
            def run(_self, target, domain):
                return {'recipients': [] if domain in no_email_domains else [{'channel': 'registrar', 'email': 'abuse@namecheap.com'}],
                    'cloaking': {'verdict': 'NO_SIGNAL'}, 'whois': {'registrar': 'Namecheap'}}
        class Capture:
            def capture(_self, target, **kwargs):
                data = io.BytesIO(); Image.new('RGB', (3, 2), 'blue').save(data, format='PNG')
                if dom:
                    from domaincheck.core.evidence import capture_manifest
                    destination = 'https://destination.example/wap/'
                    manifest = capture_manifest({'requested_url': target}, {
                        'requested_url': target, 'landing_url': target, 'final_url': destination,
                        'evidence_type': 'dom_destination_opened', 'destination_opened': True,
                        'navigation_verified': False, 'control': {'found': True, 'label': 'ĐĂNG KÝ',
                            'resolved_destination': destination, 'raw_href': destination},
                        'navigation': {'source_url': target, 'destination_url': destination,
                            'mode': 'new_tab_direct', 'referrer_sent': True, 'redirect_chain': []}})
                    return [ImageUpload('source.png', 'image/png', data.getvalue()),
                            ImageUpload('destination.png', 'image/png', data.getvalue())], manifest
                return [ImageUpload('capture.png', 'image/png', data.getvalue())], {'terminal': False, 'evidence_type': 'passive'}
        await DomainPrecheckWorker(self.db.session_factory, Adapter(), store=self.store, capture=Capture(), now=lambda: self.now).run_next('precheck')
        return operation_id

    async def start(self, source, batch_size=5, interval_seconds=300):
        async with self.db.session_factory() as session, session.begin():
            return await self.svc(session).start(source, batch_size=batch_size, interval_seconds=interval_seconds, confirmed=True)

    async def test_batches_checkpoint_mime_dedupe_new_paths_and_reset_next_day(self):
        source = await self.precheck(['https://a.example.com/login', 'https://b.example.com/'])
        job = await self.start(source, batch_size=1, interval_seconds=300)
        repeated = await self.start(source); self.assertEqual(job['id'], repeated['id'])
        await self.worker.run_next('send-1')
        self.assertEqual(len(self.messages), 1)
        self.assertIsNone(await self.worker.run_next('too-early'))
        self.now += timedelta(seconds=301)
        await self.worker.run_next('send-2'); self.assertEqual(len(self.messages), 2)
        async with self.db.session_factory() as session:
            result = await self.svc(session).get(UUID(job['id']))
            self.assertEqual(result['state'], 'completed')
            self.assertEqual((await self.svc(session).summary(source))['already_sent'], 2)
            attempts = list(await session.scalars(select(DeliveryAttempt).where(DeliveryAttempt.owner_user_id == self.owner)))
            self.assertEqual(len(attempts), 2)
        message = self.messages[0]
        parts = list(message.walk())
        self.assertEqual(len([part for part in parts if part.get_content_disposition() == 'attachment']), 2)
        self.assertEqual(len([part for part in parts if part.get_content_disposition() == 'inline']), 1)
        self.assertIn('--- Observed Phishing Behavior and Supporting Evidence ---', message.get_body(preferencelist=('plain',)).get_content())
        async with self.db.session_factory() as session, session.begin():
            same = await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now).create(['https://a.example.com/other-path'], [self.account])
            self.assertIsNone(same.job)
            self.assertEqual(same.input_summary['sent_count'], 1)
        self.now += timedelta(days=1)
        fresh = await self.precheck(['https://a.example.com/other-path'])
        await self.start(fresh); await self.worker.run_next('tomorrow')
        self.assertEqual(len(self.messages), 3)

    async def test_failed_retry_preserves_sent_and_unknown_is_not_retryable(self):
        source = await self.precheck(['https://retry.example.com/', 'https://uncertain.example.com/'])
        job = await self.start(source, interval_seconds=0)
        self.outcomes = [('failed', 'authenticate', 'smtp_rejected'), ('unknown', 'send_message', 'smtp_outcome_uncertain')]
        await self.worker.run_next('first'); await self.worker.run_next('second')
        async with self.db.session_factory() as session, session.begin():
            state = await self.svc(session).control(UUID(job['id']), 'retry')
            self.assertEqual([item['state'] for item in state['items']], ['pending', 'unknown'])
        original_id = self.messages[0]['Message-ID']
        await self.worker.run_next('retry')
        self.assertEqual(self.messages[-1]['Message-ID'], original_id)
        self.assertEqual(len(self.messages), 3)
        async with self.db.session_factory() as session:
            summary = await self.svc(session).summary(source)
            self.assertEqual(summary['already_sent'], 1); self.assertEqual(summary['blocked_deliveries'], 1)

    async def test_stop_resume_and_owner_and_confirmation_gates(self):
        source = await self.precheck(['https://stop-a.example.com/', 'https://stop-b.example.com/'])
        async with self.db.session_factory() as session, session.begin():
            with self.assertRaises(JobConflict):
                await self.svc(session).start(source, batch_size=5, interval_seconds=0, confirmed=False)
        job = await self.start(source)
        async with self.db.session_factory() as session, session.begin():
            await self.svc(session).control(UUID(job['id']), 'stop')
        self.assertIsNone(await self.worker.run_next('stopped')); self.assertEqual(self.messages, [])
        async with self.db.session_factory() as session, session.begin():
            await self.svc(session).control(UUID(job['id']), 'resume')
        await self.worker.run_next('resumed')
        self.assertEqual(len(self.messages), 1)
        async with self.db.session_factory() as session:
            from domaincheck.application.jobs import JobNotFound
            with self.assertRaises(JobNotFound):
                await self.svc(session, self.other).get(UUID(job['id']))

    async def test_changed_evidence_or_profile_prevents_smtp(self):
        source = await self.precheck(['https://changed.example.com/'])
        job = await self.start(source)
        async with self.db.session_factory() as session, session.begin():
            profile = await session.get(UserSettings, self.owner); profile.version += 1
        await self.worker.run_next('changed')
        self.assertEqual(self.messages, [])
        async with self.db.session_factory() as session:
            result = await self.svc(session).get(UUID(job['id']))
            self.assertEqual(result['items'][0]['state'], 'failed')

    async def test_midnight_stops_pending_work_without_sending(self):
        source = await self.precheck(['https://midnight.example.com/'])
        job = await self.start(source)
        self.now += timedelta(days=1)
        await self.worker.run_next('next-day'); self.assertEqual(self.messages, [])
        async with self.db.session_factory() as session:
            result = await self.svc(session).get(UUID(job['id'])); self.assertEqual(result['state'], 'stopped')

    async def test_partial_domain_retry_sends_only_failed_recipient(self):
        source = await self.precheck(['https://partial.example.com/'])
        async with self.db.session_factory() as session, session.begin():
            operation = await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now).get(source)
            check = operation.checks[0]
            check.result = {**check.result, 'recipients': check.result['recipients'] + [{'channel': 'registry', 'email': 'abuse@registry.example'}],
                'registry_contact': {'registry': 'Fixture Registry', 'abuse_email': 'abuse@registry.example'}}
        job = await self.start(source)
        self.outcomes = [('sent', 'send_message', 'accepted'), ('failed', 'authenticate', 'smtp_rejected')]
        await self.worker.run_next('partial')
        async with self.db.session_factory() as session, session.begin():
            await self.svc(session).control(UUID(job['id']), 'retry')
        await self.worker.run_next('retry-partial')
        self.assertEqual(len(self.messages), 3)
        self.assertEqual(self.messages[1]['To'], self.messages[2]['To'])
        self.assertNotEqual(self.messages[0]['To'], self.messages[2]['To'])
        self.assertEqual(self.messages[1]['Message-ID'], self.messages[2]['Message-ID'])

    async def test_expired_delivery_intent_becomes_unknown_without_second_smtp(self):
        from domaincheck.application.deliveries import DeliveryService
        source = await self.precheck(['https://crash.example.com/'])
        job = await self.start(source)
        async with self.db.session_factory() as session, session.begin():
            delivery = await session.get(Delivery, UUID(job['items'][0]['deliveries'][0]['id']))
            await DeliveryService(session, self.store, owner_user_id=self.owner, actor_user_id=self.owner,
                request_id='test').claim_intent(delivery.id, expected_version=delivery.version, lease_seconds=30, now=self.now)
        self.now += timedelta(seconds=31)
        await self.worker.run_next('recover')
        self.assertEqual(self.messages, [])
        async with self.db.session_factory() as session:
            result = await self.svc(session).get(UUID(job['id']))
            self.assertEqual(result['items'][0]['state'], 'unknown')
            self.assertEqual(result['items'][0]['deliveries'][0]['state'], 'unknown')

    async def test_invalid_later_domain_rolls_back_new_files_before_any_smtp(self):
        source = await self.precheck(['https://valid.example.com/', 'https://invalid.example.com/'])
        async with self.db.session_factory() as session, session.begin():
            operation = await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now).get(source)
            evidence_id = UUID(operation.checks[1].result['evidence_set_id'])
            artifact = await session.scalar(select(Artifact).where(Artifact.evidence_set_id == evidence_id))
            self.store.delete(storage_key=artifact.storage_key)
        before = {path for path in self.store.root.rglob('*') if path.is_file()}
        with self.assertRaises((ValueError, FileNotFoundError, RuntimeError)):
            await self.start(source)
        after = {path for path in self.store.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after); self.assertEqual(self.messages, [])

    async def test_sent_copy_failure_never_retries_smtp_and_repair_only_archives_original(self):
        from domaincheck.application.sent_copy import SentCopyService
        from email import policy
        from email.parser import BytesParser
        copier = Mock(); copier.save.return_value = 'failed'; self.worker.sent_copier = copier
        source = await self.precheck(['https://sent-copy.example.com/'])
        job = await self.start(source)
        await self.worker.run_next('sent-copy')
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(copier.save.call_args.args[1], self.messages[0].as_bytes())
        async with self.db.session_factory() as session:
            result = await self.svc(session).get(UUID(job['id']))
            self.assertEqual(result['state'], 'completed')
            delivery = result['items'][0]['deliveries'][0]
            self.assertEqual(delivery['state'], 'sent'); self.assertEqual(delivery['sent_copy_status'], 'failed')
        copier.reset_mock(); copier.save.return_value = 'saved'
        repair = SentCopyService(self.db.session_factory, self.store, copier, self.envelope)
        delivery_id = UUID(delivery['id'])
        self.assertEqual(await repair.save(self.other, self.other, delivery_id), 'failed')
        copier.save.assert_not_called()
        self.assertEqual(await repair.save(self.owner, self.owner, delivery_id), 'saved')
        copied = BytesParser(policy=policy.default).parsebytes(copier.save.call_args.args[1])
        self.assertEqual(copied['Message-ID'], self.messages[0]['Message-ID'])
        self.assertEqual(copied['To'], self.messages[0]['To'])
        self.assertEqual(copied.get_body(preferencelist=('plain',)).get_content(), self.messages[0].get_body(preferencelist=('plain',)).get_content())
        self.assertEqual(len(list(copied.iter_attachments())), len(list(self.messages[0].iter_attachments())))
        self.assertEqual(await repair.save(self.owner, self.owner, delivery_id), 'saved')
        self.assertEqual(copier.save.call_count, 1); self.assertEqual(len(self.messages), 1)

    async def test_dom_precheck_preserves_complete_legacy_narrative_in_sent_plain_and_html(self):
        target = 'https://source.example.com/vi-vn/'
        source = await self.precheck([target], dom=True)
        await self.start(source); await self.worker.run_next('dom-narrative')
        self.assertEqual(len(self.messages), 1)
        text = self.messages[0].get_body(preferencelist=('plain',)).get_content()
        expected = '''--- Observed Phishing Behavior and Supporting Evidence ---
The reported website contains a visible "ĐĂNG KÝ" control that links visitors to the following destination:

https://destination.example/wap/

The destination is exposed directly in the page markup as:

href="https://destination.example/wap/"

For evidence capture, that disclosed URL was opened in a separate isolated browser tab using the source page as the referrer.
Final destination observed: https://destination.example/wap/

Steps to reproduce:
1. Visit https://source.example.com/vi-vn/
2. Locate the "ĐĂNG KÝ" control.
3. Inspect or follow its link target: https://destination.example/wap/
4. Observe the resulting destination: https://destination.example/wap/

The attached screenshots show the reported page, the identified control, and the destination page. The attached manifest preserves the URLs and file hashes.
Please investigate the reported URL, the disclosed destination, and their relationship, and take appropriate action under your phishing and abuse policies.
Capture scope: the destination URL was opened directly from the observed markup; no credentials were entered and no form was submitted.
--- End of Supporting Evidence ---'''
        self.assertIn(expected, text)
        self.assertLess(text.index(expected), text.index('Regards,'))
        self.assertEqual(text.count('--- Observed Phishing Behavior and Supporting Evidence ---'), 1)
        html = self.messages[0].get_body(preferencelist=('html',)).get_content()
        self.assertIn('ĐĂNG KÝ', html)
        self.assertIn('href=&quot;https://destination.example/wap/&quot;', html)
        self.assertIn('Steps to reproduce:', html)
        self.assertEqual(len(list(self.messages[0].iter_attachments())), 3)
        html_container = self.messages[0].get_payload()[0].get_payload()[-1]
        self.assertEqual(html_container.get_content_type(), 'multipart/related')
        self.assertEqual(html_container.get_payload()[0].get_content_type(), 'text/html')
        self.assertEqual(html_container.get_payload()[1].get_content_disposition(), 'inline')

    async def test_owner_lock_serializes_concurrent_precheck_and_workspace_restores_it(self):
        from domaincheck.application.worker_batches import workspace_state
        async def create(target, owner=None):
            async with self.db.session_factory() as session, session.begin():
                return await DomainWorkerService(session, owner=owner or self.owner, actor=owner or self.owner,
                    timezone='Asia/Bangkok', request_id='race', now=self.now).create([target], [self.account] if owner is None else None)
        results = await asyncio.gather(create('https://race-a.example.com/'), create('https://race-b.example.com/'), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, JobConflict) for result in results), 1)
        winner = next(result for result in results if not isinstance(result, Exception))
        other = await create('https://other-owner.example.com/', self.other)
        self.assertIsNotNone(other.job)
        async with self.db.session_factory() as session, session.begin():
            state = await workspace_state(session, self.owner, self.now, 'Asia/Bangkok')
            self.assertEqual(state['current_id'], str(winner.job.id)); self.assertTrue(state['locked'])
            self.assertEqual(len(state['history']), 1)

    async def test_ready_waits_for_confirmation_and_close_preserves_history(self):
        from domaincheck.application.worker_batches import workspace_state
        from domaincheck.api.domain_worker import close_precheck
        from fastapi import Response
        from types import SimpleNamespace
        source = await self.precheck(['https://waiting.example.com/'])
        async with self.db.session_factory() as session:
            with self.assertRaises(JobConflict):
                await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now).create(['next.example.com'], [self.account])
        async with self.db.session_factory() as session, session.begin():
            state = await workspace_state(session, self.owner, self.now, 'Asia/Bangkok')
            self.assertEqual(state['phase'], 'awaiting_send')
            request = SimpleNamespace(state=SimpleNamespace(request_id='test'), app=SimpleNamespace(state=SimpleNamespace(clock=SimpleNamespace(now=lambda: self.now))))
            await close_precheck(source, request, Response(), session, SimpleNamespace(user_id=self.owner, timezone='Asia/Bangkok'))
        async with self.db.session_factory() as session, session.begin():
            operation = await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now).create(['next.example.com'], [self.account])
            state = await workspace_state(session, self.owner, self.now, 'Asia/Bangkok')
            self.assertEqual(state['current_id'], str(operation.job.id)); self.assertEqual(len(state['history']), 2)

    async def test_database_filters_abc_before_precheck_and_all_sent_creates_no_job(self):
        from sqlalchemy import func
        urls = [f'https://{name}.example.com/' for name in 'abc']
        source = await self.precheck(urls); await self.start(source, interval_seconds=0)
        for index in range(3): await self.worker.run_next(f'abc-{index}')
        async with self.db.session_factory() as session, session.begin():
            service = DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now)
            before = await session.scalar(select(func.count()).select_from(Job).where(Job.owner_user_id == self.owner))
            empty = await service.create(urls, [self.account])
            self.assertIsNone(empty.job); self.assertEqual(empty.input_summary['sent_count'], 3)
            self.assertEqual(before, await session.scalar(select(func.count()).select_from(Job).where(Job.owner_user_id == self.owner)))
            operation = await service.create(['A.example.com', *urls, *[f'{name}.example.com' for name in 'def']], [self.account])
            self.assertEqual([check.domain for check in operation.checks], [f'{name}.example.com' for name in 'def'])
            self.assertEqual(operation.input_summary['duplicate_count'], 1)
            self.assertEqual(operation.input_summary['sent_count'], 3)
            operation_id = operation.job.id
        observed = []
        class Adapter:
            def run(_self, target, domain):
                observed.append(domain)
                return {'recipients': [], 'cloaking': {'verdict': 'NO_SIGNAL'}}
        await DomainPrecheckWorker(self.db.session_factory, Adapter(), now=lambda: self.now).run_next('only-def')
        self.assertEqual(observed, [f'{name}.example.com' for name in 'def'])

    async def test_no_email_ab_and_sent_cd_are_filtered_before_any_new_probe(self):
        from sqlalchemy import func
        from domaincheck.application.worker_batches import filter_sent_inputs
        urls = [f'https://{name}.example.com/' for name in 'abcd']
        source = await self.precheck(urls, no_email_domains={'a.example.com', 'b.example.com'})
        await self.start(source, interval_seconds=0)
        await self.worker.run_next('send-c'); await self.worker.run_next('send-d')
        async with self.db.session_factory() as session, session.begin():
            service = DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now)
            count = await session.scalar(select(func.count()).select_from(Job).where(Job.owner_user_id == self.owner))
            empty = await service.create(urls, [self.account])
            self.assertIsNone(empty.job)
            self.assertEqual(empty.input_summary['no_email_count'], 2)
            self.assertEqual(empty.input_summary['sent_count'], 2)
            self.assertEqual(count, await session.scalar(select(func.count()).select_from(Job).where(Job.owner_user_id == self.owner)))
            incoming = [*urls, 'https://e.example.com/', 'https://f.example.com/', 'A.example.com']
            operation = await service.create(incoming, [self.account])
            self.assertEqual([check.domain for check in operation.checks], ['e.example.com', 'f.example.com'])
            self.assertEqual(operation.input_summary['duplicate_count'], 1)
            self.assertEqual(operation.input_summary['no_email_count'], 2)
            self.assertEqual(operation.input_summary['sent_count'], 2)
            reasons = {row['requested_url']: row['reason'] for row in operation.input_summary['excluded'] if row['reason'] != 'duplicate'}
            self.assertEqual(reasons, dict(zip(urls, ['no_email', 'no_email', 'already_sent', 'already_sent'])))
            parsed = [('https://a.example.com/other-path', 'a.example.com')]
            self.assertEqual((await filter_sent_inputs(session, self.owner, self.now.date(), [], parsed))[1]['no_email_count'], 1)
            self.assertEqual((await filter_sent_inputs(session, self.other, self.now.date(), [], parsed))[0], parsed)
            self.assertEqual((await filter_sent_inputs(session, self.owner, (self.now + timedelta(days=1)).date(), [], parsed))[0], parsed)
        observed = []
        class Adapter:
            def run(_self, target, domain):
                observed.append(domain)
                return {'recipients': [], 'cloaking': {'verdict': 'NO_SIGNAL'}}
        await DomainPrecheckWorker(self.db.session_factory, Adapter(), now=lambda: self.now).run_next('only-ef')
        self.assertEqual(observed, ['e.example.com', 'f.example.com'])

    async def test_failed_or_contradictory_no_email_result_is_not_cached(self):
        from domaincheck.infrastructure.db.models import Check
        from domaincheck.application.worker_batches import filter_sent_inputs
        await self.precheck(['https://a.example.com/'], no_email_domains={'a.example.com'})
        parsed = [('https://a.example.com/', 'a.example.com')]
        async with self.db.session_factory() as session, session.begin():
            check = await session.scalar(select(Check).where(Check.owner_user_id == self.owner))
            check.status = 'failed'; await session.flush()
            self.assertEqual((await filter_sent_inputs(session, self.owner, self.now.date(), [], parsed))[0], parsed)
            check.status = 'succeeded'
            check.result = {**check.result, 'recipients': [{'channel': 'registrar', 'email': 'abuse@example.com'}]}
            await session.flush()
            self.assertEqual((await filter_sent_inputs(session, self.owner, self.now.date(), [], parsed))[0], parsed)
            check.result = {**check.result, 'recipients': []}
            check.updated_at = self.now
            newer = Check(**{name: getattr(check, name) for name in ('owner_user_id', 'actor_user_id', 'target_id',
                'requested_url', 'workflow', 'workflow_key', 'domain', 'business_day', 'timezone', 'engine_version')},
                status='failed', result={'disposition': 'precheck_error'}, created_at=check.created_at,
                updated_at=self.now + timedelta(seconds=1))
            session.add(newer); await session.flush()
            self.assertEqual((await filter_sent_inputs(session, self.owner, self.now.date(), [], parsed))[0], parsed)

    async def test_pause_reserves_batch_and_resume_preserves_successful_deliveries(self):
        from domaincheck.application.worker_batches import workspace_state
        source = await self.precheck(['https://pause-a.example.com/', 'https://pause-b.example.com/'])
        job = await self.start(source, batch_size=1, interval_seconds=300)
        await self.worker.run_next('first')
        first_message_id = self.messages[0]['Message-ID']
        async with self.db.session_factory() as session, session.begin():
            paused = await self.svc(session).control(UUID(job['id']), 'pause')
            self.assertEqual((paused['state'], paused['control_mode']), ('stopped', 'pause'))
            self.assertIsNone(paused['next_run_at'])
            summary = await self.svc(session).summary(source)
            self.assertEqual(summary['send_job_id'], job['id'])
            self.assertEqual((summary['already_sent'], summary['pending_deliveries']), (1, 1))
            self.assertEqual((await self.svc(session).get(UUID(job['id'])))['control_mode'], 'pause')
            with self.assertRaisesRegex(JobConflict, 'Resume the paused sending job'):
                await self.svc(session).start(source, batch_size=1, interval_seconds=300, confirmed=True)
            state = await workspace_state(session, self.owner, self.now, 'Asia/Bangkok')
            self.assertEqual(state['phase'], 'paused'); self.assertTrue(state['locked'])
        self.assertIsNone(await self.worker.run_next('no-auto-resume'))
        async with self.db.session_factory() as session:
            with self.assertRaises(JobConflict):
                await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now).create(['next.example.com'], [self.account])
        async with self.db.session_factory() as session, session.begin():
            resumed = await self.svc(session).control(UUID(job['id']), 'resume')
            self.assertEqual(resumed['state'], 'queued'); self.assertIsNone(resumed['control_mode'])
        await self.worker.run_next('remaining')
        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.messages[0]['Message-ID'], first_message_id)
        async with self.db.session_factory() as session:
            self.assertEqual((await self.svc(session).get(UUID(job['id'])))['state'], 'completed')

    async def test_cancel_running_job_finishes_current_domain_and_rejects_all_restart_paths(self):
        import threading
        from domaincheck.application.worker_batches import workspace_state
        source = await self.precheck(['https://cancel-a.example.com/', 'https://cancel-b.example.com/'])
        job = await self.start(source, interval_seconds=0)
        entered, release = threading.Event(), threading.Event()
        original = self.worker.sender
        class SlowSender:
            def send(_self, spec, message):
                entered.set()
                if not release.wait(5): raise TimeoutError('test release missing')
                return original.send(spec, message)
        self.worker.sender = SlowSender()
        task = asyncio.create_task(self.worker.run_next('in-flight'))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            async with self.db.session_factory() as session, session.begin():
                state = await self.svc(session).control(UUID(job['id']), 'cancel')
                self.assertEqual((state['state'], state['control_mode']), ('stopping', 'cancel'))
        finally:
            release.set(); await task
        async with self.db.session_factory() as session, session.begin():
            final = await self.svc(session).get(UUID(job['id']))
            self.assertEqual((final['state'], final['control_mode']), ('stopped', 'cancel'))
            self.assertEqual([item['state'] for item in final['items']], ['succeeded', 'pending'])
            summary = await self.svc(session).summary(source)
            self.assertEqual(summary['send_job_id'], job['id'])
            self.assertEqual((summary['already_sent'], summary['pending_deliveries']), (1, 1))
            self.assertEqual((await self.svc(session).get(UUID(job['id'])))['control_mode'], 'cancel')
            self.assertFalse((await workspace_state(session, self.owner, self.now, 'Asia/Bangkok'))['locked'])
        for action in ('resume', 'retry'):
            async with self.db.session_factory() as session:
                with self.assertRaises(JobConflict): await self.svc(session).control(UUID(job['id']), action)
        async with self.db.session_factory() as session:
            with self.assertRaises(JobConflict): await self.svc(session).start(source, batch_size=5, interval_seconds=0, confirmed=True)
        async with self.db.session_factory() as session, session.begin():
            unchanged = await self.svc(session).control(UUID(job['id']), 'pause')
            self.assertEqual(unchanged['control_mode'], 'cancel')
        self.assertIsNone(await self.worker.run_next('must-not-send-b'))
        self.assertEqual(len(self.messages), 1)

    async def test_new_account_is_not_filtered_by_other_accounts_success(self):
        source = await self.precheck(['https://account-scope.example.com/'])
        await self.start(source); await self.worker.run_next('account-one')
        async with self.db.session_factory() as session, session.begin():
            second = SenderAccount(owner_user_id=self.owner, label='Second', normalized_identity='second',
                smtp_host='smtp.example.com', smtp_port=465, smtp_username='second@example.com', smtp_transport='implicit_tls', enabled=True)
            session.add(second); await session.flush()
            operation = await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now).create(['account-scope.example.com'], [self.account, second.id])
            self.assertIsNotNone(operation.job); self.assertEqual(operation.input_summary['sent_count'], 0)

    async def test_retry_race_refreshes_expired_plan_and_preserves_message_id(self):
        from domaincheck.infrastructure.db.models import PreviewPlan
        source = await self.precheck(['https://retry-race.example.com/'])
        job = await self.start(source); self.outcomes = [('failed', 'authenticate', 'smtp_rejected')]
        await self.worker.run_next('failed-first')
        original_id = self.messages[0]['Message-ID']
        async with self.db.session_factory() as session, session.begin():
            row = await session.get(Delivery, UUID(job['items'][0]['deliveries'][0]['id']))
            plan = await session.get(PreviewPlan, row.preview_id); plan.expires_at = self.now - timedelta(seconds=1)
        async def retry():
            async with self.db.session_factory() as session, session.begin():
                return await self.svc(session).control(UUID(job['id']), 'retry')
        results = await asyncio.gather(retry(), retry())
        self.assertTrue(all(result['state'] == 'queued' for result in results))
        await self.worker.run_next('one-retry')
        self.assertEqual(len(self.messages), 2); self.assertEqual(self.messages[-1]['Message-ID'], original_id)
        async with self.db.session_factory() as session:
            result = await self.svc(session).get(UUID(job['id']))
            self.assertEqual(result['items'][0]['deliveries'][0]['attempt_count'], 2)

    async def test_expired_worker_does_not_lock_owner_forever_or_resend_uncertain(self):
        from domaincheck.application.deliveries import DeliveryService
        from domaincheck.application.worker_batches import workspace_state
        source = await self.precheck(['https://crash-owner.example.com/'])
        job = await self.start(source)
        async with self.db.session_factory() as session, session.begin():
            stored_job = await session.get(Job, UUID(job['id']))
            stored_job.state = 'running'; stored_job.lease_token = 'dead-worker'
            stored_job.lease_expires_at = self.now - timedelta(seconds=1)
            item = await session.get(JobItem, UUID(job['items'][0]['id'])); item.state = 'running'
            row = await session.get(Delivery, UUID(job['items'][0]['deliveries'][0]['id']))
            await DeliveryService(session, self.store, owner_user_id=self.owner, actor_user_id=self.owner, request_id='crash').claim_intent(row.id, expected_version=row.version, lease_seconds=300, now=self.now)
        async with self.db.session_factory() as session, session.begin():
            state = await workspace_state(session, self.owner, self.now, 'Asia/Bangkok')
            self.assertFalse(state['locked'])
            result = await self.svc(session).get(UUID(job['id']))
            self.assertEqual(result['items'][0]['deliveries'][0]['state'], 'unknown')
            self.assertEqual(result['items'][0]['deliveries'][0]['last_error_code'], 'worker_lease_expired')
        self.assertIsNone(await self.worker.run_next('restart')); self.assertEqual(self.messages, [])

    async def test_queued_send_blocks_new_precheck_and_restores_progress_from_database(self):
        from domaincheck.application.worker_batches import workspace_state
        source = await self.precheck(['https://active-send.example.com/'])
        job = await self.start(source)
        async with self.db.session_factory() as session:
            with self.assertRaises(JobConflict):
                await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='tab-two', now=self.now).create(['new.example.com'], [self.account])
        async with self.db.session_factory() as session, session.begin():
            state = await workspace_state(session, self.owner, self.now, 'Asia/Bangkok')
            self.assertEqual(state['current_id'], str(source)); self.assertEqual(state['send_job_id'], job['id'])
            self.assertTrue(state['locked']); self.assertEqual(state['phase'], 'sending')
        await self.worker.run_next('complete-send')
        async with self.db.session_factory() as session, session.begin():
            state = await workspace_state(session, self.owner, self.now, 'Asia/Bangkok')
            self.assertFalse(state['locked']); self.assertEqual(state['history'][0]['send_state'], 'completed')

    async def test_partial_recipient_success_does_not_filter_whole_domain(self):
        source = await self.precheck(['https://recipient-scope.example.com/'])
        async with self.db.session_factory() as session, session.begin():
            operation = await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now).get(source)
            check = operation.checks[0]
            check.result = {**check.result, 'recipients': check.result['recipients'] + [{'channel': 'registry', 'email': 'abuse@registry.example'}],
                'registry_contact': {'registry': 'Fixture Registry', 'abuse_email': 'abuse@registry.example'}}
        await self.start(source)
        self.outcomes = [('sent', 'send_message', 'accepted'), ('failed', 'authenticate', 'smtp_rejected')]
        await self.worker.run_next('partial-routes')
        async with self.db.session_factory() as session, session.begin():
            operation = await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now).create(['recipient-scope.example.com'], [self.account])
            self.assertIsNotNone(operation.job); self.assertEqual(operation.input_summary['sent_count'], 0)

    async def test_known_failure_inside_unknown_item_can_retry_without_resending_unknown(self):
        source = await self.precheck(['https://mixed-unknown.example.com/'])
        async with self.db.session_factory() as session, session.begin():
            operation = await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now).get(source)
            check = operation.checks[0]
            check.result = {**check.result, 'recipients': check.result['recipients'] + [{'channel': 'registry', 'email': 'abuse@registry.example'}],
                'registry_contact': {'registry': 'Fixture Registry', 'abuse_email': 'abuse@registry.example'}}
        job = await self.start(source)
        self.outcomes = [('unknown', 'send_message', 'smtp_outcome_uncertain'), ('failed', 'authenticate', 'smtp_rejected')]
        await self.worker.run_next('mixed-first')
        async with self.db.session_factory() as session, session.begin():
            await self.svc(session).control(UUID(job['id']), 'retry')
        await self.worker.run_next('mixed-retry')
        self.assertEqual(len(self.messages), 3)
        self.assertEqual(self.messages[1]['Message-ID'], self.messages[2]['Message-ID'])
        self.assertNotEqual(self.messages[0]['Message-ID'], self.messages[2]['Message-ID'])
        async with self.db.session_factory() as session:
            result = await self.svc(session).get(UUID(job['id']))
            self.assertEqual([row['state'] for row in result['items'][0]['deliveries']], ['unknown', 'sent'])

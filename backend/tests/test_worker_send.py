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
            await session.execute(update(Job).where(Job.owner_user_id == self.owner,
                Job.state.in_(['queued', 'running', 'stopping'])).values(state='stopped', lease_token=None, lease_expires_at=None))
        await self.db.dispose(); self.temp.cleanup()

    def svc(self, session, owner=None):
        return WorkerSendService(session, self.store, owner=owner or self.owner, actor=owner or self.owner,
            timezone='Asia/Bangkok', request_id='test', now=self.now, envelope=self.envelope)

    async def precheck(self, targets, *, dom=False):
        async with self.db.session_factory() as session, session.begin():
            operation = await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok',
                request_id='test', now=self.now).create(targets, [self.account]); operation_id = operation.job.id
        class Adapter:
            def run(_self, target, domain):
                return {'recipients': [{'channel': 'registrar', 'email': 'abuse@namecheap.com'}],
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
        same = await self.precheck(['https://a.example.com/other-path'])
        async with self.db.session_factory() as session:
            op = await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now).get(same)
            self.assertEqual(op.checks[0].result['disposition'], 'excluded_already_sent')
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

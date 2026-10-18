import asyncio
import email
import os
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import provider_replies as legacy
from sqlalchemy import select
import tests.test_worker_send as fixtures
import tests.test_evidence_review as evidence
from domaincheck.application.provider_replies import ProviderRepliesService, ProviderReplySender
from domaincheck.application.previews import PreviewConflict
from domaincheck.application.domain_worker import DomainWorkerService
from domaincheck.infrastructure.db.models import Artifact, Delivery, Check, ReviewCase, ReviewSnapshot
from domaincheck.ports.artifacts import ImageUpload, ArtifactIntegrityError


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class ProviderRepliesWebTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WorkerSendTests.asyncSetUp
    asyncTearDown = fixtures.WorkerSendTests.asyncTearDown
    precheck = fixtures.WorkerSendTests.precheck
    start = fixtures.WorkerSendTests.start
    svc = fixtures.WorkerSendTests.svc
    second_account = evidence.EvidenceReviewTests.second_account

    def service(self, session, owner=None, now=None):
        return ProviderRepliesService(session, self.store, owner=owner or self.owner, actor=owner or self.owner,
            timezone='Asia/Bangkok', now=now or self.now, envelope=self.envelope)

    async def original_report(self):
        source = await self.precheck(['https://reported.example.com/path'])
        async with self.db.session_factory() as session, session.begin():
            op = await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok',
                now=self.now, request_id='fixture').get(source)
            check = op.checks[0]
            check.result = {**check.result, 'whois': {'registrar': 'Dynadot'},
                'recipients': [{'channel': 'registrar', 'email': 'abuse@dynadot.com'}]}
            check.version += 1
        await self.start(source, interval_seconds=0); await self.worker.run_next('seed')
        return self.messages.pop()['Message-ID']

    def message(self, reference, kind='full_url', uid='12', mid='<provider-request@cloudflare.com>'):
        body = {'full_url': 'Please provide the full URL.', 'screenshot': 'Please provide a screenshot.',
            'ack': 'We received your report.', 'legal': 'Please provide proof of ownership.'}[kind]
        if kind != 'ack': body += ' Please provide additional information.'
        raw = (f'From: abuse@cloudflare.com\r\nTo: sender@example.com\r\nMessage-ID: {mid}\r\n'
            f'In-Reply-To: {reference}\r\nReferences: {reference}\r\nSubject: Request regarding reported.example.com\r\n'
            f'Date: Fri, 16 Oct 2026 08:00:00 +0000\r\n\r\n{body}\nReported URL: https://reported.example.com/path').encode()
        mail = legacy.parse_message(uid, 'sender@example.com', raw)
        mail.source_mailbox = 'INBOX'
        return {'mail': mail.to_dict(), 'uidvalidity': '8', 'references': [reference], 'in_reply_to': [reference],
            'received_at': self.now.isoformat(), 'seen': False}

    async def ingest(self, payload, account=None):
        async with self.db.session_factory() as session, session.begin():
            await self.service(session).ingest(account or self.account, {'messages': [payload], 'folders': []})
        async with self.db.session_factory() as session:
            rows = await self.service(session).list(account or self.account, self.now.date(), self.now.date())
            return rows[0]['id']

    async def prepare(self, case, fields=None, uploads=()):
        async with self.db.session_factory() as session, session.begin():
            return await self.service(session).preview(case, fields or {}, uploads)

    async def send(self, case, exact, sender=None):
        return await ProviderReplySender(self.db.session_factory, self.store, sender or self.worker.sender,
            self.envelope, now=lambda: self.now).send(self.owner, self.owner, 'Asia/Bangkok', case,
                exact['id'], exact['revision'], exact['fingerprint'], True)

    async def test_exact_threaded_reply_logo_sent_dedupe_and_no_body_in_database(self):
        ref = await self.original_report(); payload = self.message(ref)
        case = await self.ingest(payload)
        self.assertEqual(case, await self.ingest(payload))
        exact = await self.prepare(case)
        async with self.db.session_factory() as session:
            snapshot = await session.scalar(select(ReviewSnapshot).where(ReviewSnapshot.owner_user_id == self.owner,
                ReviewSnapshot.feature == 'provider_replies'))
            self.assertNotIn('Please provide', str(snapshot.sanitized_state))
            self.assertEqual(snapshot.sanitized_state['match_reason'], 'message_id')
        result = await self.send(case, exact)
        self.assertEqual(result['state'], 'sent'); self.assertEqual(len(self.messages), 1)
        sent = self.messages[0]
        self.assertEqual(sent['In-Reply-To'], payload['mail']['message_id'])
        self.assertIn(ref, sent['References'])
        self.assertEqual(sent['To'], 'abuse@cloudflare.com')
        self.assertEqual(sent.get_body(preferencelist=('plain',)).get_content().strip(), exact['deliveries'][0]['text_body'].strip())
        self.assertEqual(len([part for part in sent.walk() if part.get_content_disposition() == 'inline']), 1)
        await self.send(case, exact); self.assertEqual(len(self.messages), 1)

    async def test_legacy_sent_original_matches_without_database_delivery(self):
        payload = self.message('<legacy-report@example.com>')
        async with self.db.session_factory() as session, session.begin():
            await self.service(session).ingest(self.account, {'messages': [payload], 'folders': [],
                'sent_headers': [{'message_id': '<legacy-report@example.com>', 'references': [],
                    'recipients': ['abuse@cloudflare.com']}]})
        async with self.db.session_factory() as session:
            rows = await self.service(session).list(self.account, self.now.date(), self.now.date())
            case = rows[0]['id']
            self.assertTrue(rows[0]['matched'])
            detail = await self.service(session).detail(case)
            self.assertTrue(detail['matched'])
            self.assertEqual(detail['match_reason'], 'sent_thread')
            self.assertIsNone(await session.scalar(select(Delivery).where(Delivery.owner_user_id == self.owner)))
        exact = await self.prepare(case)
        self.assertEqual(exact['deliveries'][0]['in_reply_to'], payload['mail']['message_id'])

    async def test_cloudflare_without_delivery_history_can_capture_preview_and_send(self):
        payload = self.message('<old-unindexed@example.com>', kind='screenshot')
        case = await self.ingest(payload)
        async with self.db.session_factory() as session, session.begin():
            svc = self.service(session)
            detail = await svc.detail(case)
            self.assertFalse(detail['matched'])
            self.assertTrue(detail['reply_supported'])
            self.assertIsNone(detail['reply_block_reason'])
            await svc.capture(case, lambda target, decision: ([evidence.image()],
                {'source': 'browser_capture', 'evidence_type': 'passive', 'requested_url': target, 'terminal': False}))
        exact = await self.prepare(case)
        self.assertEqual((await self.send(case, exact))['state'], 'sent')

    async def test_other_providers_readonly_and_cloudflare_ack_not_actionable(self):
        payload = self.message('<old@example.com>')
        payload['mail'].update(provider='dynadot', provider_label='Dynadot', sender='abuse@dynadot.com', reply_to='abuse@dynadot.com')
        case = await self.ingest(payload)
        async with self.db.session_factory() as session:
            self.assertEqual((await self.service(session).detail(case))['reply_block_reason'], 'provider_not_supported')
        with self.assertRaises(PreviewConflict): await self.prepare(case)
        async with self.db.session_factory() as session, session.begin():
            with self.assertRaises(PreviewConflict): await self.service(session).capture(case, Mock())

    async def test_failed_retry_keeps_message_id_unknown_never_resends(self):
        case = await self.ingest(self.message(await self.original_report()))
        exact = await self.prepare(case)
        self.outcomes = [('failed', 'authenticate', 'smtp_rejected'), ('unknown', 'send_message', 'smtp_outcome_uncertain')]
        self.assertEqual((await self.send(case, exact))['state'], 'failed')
        self.assertEqual((await self.send(case, exact))['state'], 'unknown')
        self.assertEqual(self.messages[0]['Message-ID'], self.messages[1]['Message-ID'])
        await self.send(case, exact); self.assertEqual(len(self.messages), 2)
        with self.assertRaises(PreviewConflict): await self.prepare(case)

    async def test_owner_account_matching_ack_and_missing_thread_block_send(self):
        ref = await self.original_report()
        case = await self.ingest(self.message(ref), account=await self.second_account())
        self.assertTrue(await self.prepare(case))
        async with self.db.session_factory() as session:
            with self.assertRaises(LookupError): await self.service(session, self.other).detail(case)
        ack = await self.ingest(self.message(ref, kind='ack', mid='<ack@cloudflare.com>'))
        with self.assertRaises(PreviewConflict): await self.prepare(ack)
        no_id = await self.ingest(self.message(ref, mid=''))
        with self.assertRaises(PreviewConflict): await self.prepare(no_id)

    async def test_requested_images_atomic_and_preview_invalidated_by_replacement(self):
        case = await self.ingest(self.message(await self.original_report(), kind='screenshot'))
        with self.assertRaises(PreviewConflict): await self.prepare(case)
        before = {p for p in self.store.root.rglob('*') if p.is_file()}
        with self.assertRaises(ValueError):
            await self.prepare(case, uploads=[evidence.image(), ImageUpload('bad.png', 'image/png', b'bad')])
        self.assertEqual(before, {p for p in self.store.root.rglob('*') if p.is_file()})
        first = await self.prepare(case, uploads=[evidence.image()])
        second = await self.prepare(case, uploads=[evidence.image()])
        with self.assertRaises(PreviewConflict): await self.send(case, first)
        self.assertEqual((await self.send(case, second))['state'], 'sent')
        self.assertEqual(len(list(self.messages[0].iter_attachments())), 2)

    async def test_legal_approval_and_capture_reuse(self):
        case = await self.ingest(self.message(await self.original_report(), kind='legal'))
        with self.assertRaises(PreviewConflict): await self.prepare(case)
        exact = await self.prepare(case, {'legal_approved': True})
        self.assertIn('authorization', exact['deliveries'][0]['text_body'])

    async def test_capture_is_reused_and_new_target_requires_new_evidence(self):
        case = await self.ingest(self.message(await self.original_report(), kind='screenshot'))
        def capture(target, decision):
            return [evidence.image()], {'version': 1, 'source': 'browser_capture', 'evidence_type': 'passive',
                'requested_url': target, 'terminal': False}
        async with self.db.session_factory() as session, session.begin():
            detail = await self.service(session).capture(case, capture)
            self.assertEqual(len(detail['images']), 1)
        await self.prepare(case)
        with self.assertRaises(PreviewConflict): await self.prepare(case, {'reported_url': 'https://different.example.com/'})

    async def test_existing_sent_reply_is_matched_without_changing_smtp_truth(self):
        payload = self.message(await self.original_report())
        case = await self.ingest(payload)
        async with self.db.session_factory() as session, session.begin():
            await self.service(session).ingest(self.account, {'messages': [], 'folders': [], 'sent_headers': [
                {'message_id': '<previous-reply@example.com>', 'references': [payload['mail']['message_id']],
                 'recipients': ['abuse@cloudflare.com']}]})
        async with self.db.session_factory() as session:
            self.assertEqual((await self.service(session).detail(case))['state'], 'replied_in_sent')
        with self.assertRaises(PreviewConflict): await self.prepare(case)
        self.assertEqual(self.messages, [])

    async def test_expired_intent_is_unknown_and_concurrent_send_is_locked(self):
        from domaincheck.application.deliveries import DeliveryService
        from domaincheck.domain.deliveries import DeliveryBusinessIdentity
        case = await self.ingest(self.message(await self.original_report()))
        exact = await self.prepare(case)
        async with self.db.session_factory() as session, session.begin():
            service = self.service(session)
            await service.plans().approve(exact['id'], expected_revision=exact['revision'], confirmed=True, now=self.now)
            ledger = DeliveryService(session, self.store, owner_user_id=self.owner, actor_user_id=self.owner, request_id='fixture')
            row = (await ledger.materialize(preview_id=exact['id'], expected_revision=exact['revision'], workflow='provider_replies',
                identities=[DeliveryBusinessIdentity(0, str(case))], now=self.now))[0]
            await ledger.claim_intent(row.id, expected_version=row.version, lease_seconds=30, now=self.now)
        self.now += timedelta(seconds=31)
        self.assertEqual((await self.send(case, exact))['state'], 'unknown')
        self.assertEqual(self.messages, [])

    async def test_two_sessions_cannot_send_or_replace_the_same_reply(self):
        import threading
        case = await self.ingest(self.message(await self.original_report()))
        exact = await self.prepare(case)
        entered, release = threading.Event(), threading.Event()
        original = self.worker.sender
        class Slow:
            def send(_self, spec, message):
                entered.set()
                if not release.wait(5): raise TimeoutError('fixture')
                return original.send(spec, message)
        task = asyncio.create_task(self.send(case, exact, Slow()))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            with self.assertRaises(PreviewConflict): await self.send(case, exact)
            with self.assertRaises(ValueError): await self.prepare(case)
        finally:
            release.set(); await task
        self.assertEqual(len(self.messages), 1)

    async def test_api_sync_seen_preview_send_permissions_and_owned_resources(self):
        import json
        from httpx import ASGITransport, AsyncClient
        from domaincheck.api.dependencies import current_user
        from domaincheck.infrastructure.auth.sessions import hash_token
        from domaincheck.main import create_app
        from domaincheck.settings import Settings
        from domaincheck.infrastructure.db.models import SenderAccount
        payload = self.message(await self.original_report())
        async with self.db.session_factory() as session, session.begin():
            account = await session.get(SenderAccount, self.account)
            account.imap_config = {'host': 'imap.example.com', 'port': 993}
        app = create_app(Settings(_env_file=None))
        app.state.database, app.state.artifact_store, app.state.secret_envelope = self.db, self.store, self.envelope
        app.state.clock = SimpleNamespace(now=lambda: self.now)
        app.state.provider_mail_adapter = Mock()
        app.state.provider_mail_adapter.sync.return_value = {'messages': [payload], 'folders': []}
        app.state.provider_reply_sender = self.worker.sender
        archive = Mock(); archive.save.return_value = 'saved'; app.state.provider_reply_sent_copier = archive
        actor = SimpleNamespace(user_id=self.owner, status='active', timezone='Asia/Bangkok',
            permissions={'provider_replies.view'}, csrf_hash=hash_token('csrf'))
        async def user(): return actor
        app.dependency_overrides[current_user] = user
        root = '/api/v1/provider-replies'
        headers = {'Origin': 'http://127.0.0.1:5173', 'X-CSRF-Token': 'csrf'}
        data = {'account_id': str(self.account), 'date_from': str(self.now.date()), 'date_to': str(self.now.date())}
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.get(root+'/accounts')).status_code, 200)
            app.state.provider_mail_adapter.sync.assert_not_called()
            self.assertEqual((await client.post(root+'/sync', json=data, headers=headers)).status_code, 403)
            actor.permissions.update({'provider_replies.sync','provider_replies.preview','provider_replies.send','provider_replies.mark_seen'})
            self.assertEqual((await client.post(root+'/sync', json=data)).status_code, 403)
            import threading
            entered, release = threading.Event(), threading.Event()
            def reading(*args, progress=None):
                progress({'stage': 'reading', 'folder': 'INBOX', 'processed': 2, 'total': 10,
                    'folder_index': 1, 'folder_total': 5, 'folders': []})
                entered.set()
                if not release.wait(5): raise TimeoutError('fixture')
                return {'messages': [payload], 'folders': []}
            app.state.provider_mail_adapter.sync.side_effect = reading
            syncing = asyncio.create_task(client.post(root+'/sync', json=data, headers=headers))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                progress = await client.get(root+'/sync-progress', params={'account_id': str(self.account)})
                self.assertEqual(progress.json()['progress']['processed'], 2)
                self.assertEqual(progress.json()['progress']['total'], 10)
                self.assertEqual(progress.json()['progress']['status'], 'running')
                self.assertEqual((await client.post(root+'/sync', json=data, headers=headers)).status_code, 409)
            finally:
                release.set()
                response = await syncing
            self.assertEqual(response.status_code, 200, response.text)
            progress = await client.get(root+'/sync-progress', params={'account_id': str(self.account)})
            self.assertEqual(progress.status_code, 200, progress.text)
            self.assertEqual(progress.json()['progress']['status'], 'completed')
            response = await client.get(root, params=data)
            self.assertEqual(response.status_code, 200, response.text)
            case = response.json()['items'][0]['id']; path = root+'/'+case
            self.assertEqual((await client.get(path)).status_code, 200)
            app.state.provider_mail_adapter.mark_seen.assert_not_called()
            self.assertEqual((await client.post(path+'/seen', headers=headers)).status_code, 200)
            self.assertEqual(app.state.provider_mail_adapter.mark_seen.call_args.args[1]['uidvalidity'], '8')
            response = await client.post(path+'/previews', data={'details': json.dumps({})}, headers=headers)
            self.assertEqual(response.status_code, 201, response.text)
            plan = response.json()
            actor.user_id = self.other
            self.assertEqual((await client.get(path)).status_code, 404)
            actor.user_id = self.owner
            response = await client.post(path+'/send', json={'preview_id':plan['id'], 'revision':plan['revision'],
                'fingerprint':plan['fingerprint'],'confirmed':True}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['state'], 'sent')
            self.assertEqual(response.json()['sent_copy_status'], 'saved')
            archive.save.assert_called_once()


class ProviderMailboxTests(unittest.TestCase):
    def test_dynamic_folders_replace_legacy_and_keep_provider_detection(self):
        from datetime import date
        from domaincheck.api.mail_settings import ImapInput
        from domaincheck.core.provider_mail import ProviderMailboxAdapter
        from domaincheck.core.mail.connectivity import ImapConnectionSpec
        from pydantic import ValidationError
        name = '3 - Cần bằng chứng'
        config = ImapInput(reply_mailboxes=[f' {name} ', name, 'Other providers']).model_dump()
        self.assertEqual(config['reply_mailboxes'], [name, 'Other providers'])
        for invalid in ([''], ['INBOX\r\n'], ['a' * 501], ['a'] * 31):
            with self.assertRaises(ValidationError): ImapInput(reply_mailboxes=invalid)
        adapter = ProviderMailboxAdapter(())
        server = Mock()
        server.list.return_value = ('OK', [])
        server.select.return_value = ('OK', [b'1'])
        server.response.return_value = ('UIDVALIDITY', [b'8'])
        raw = b'From: abuse@cloudflare.com\r\nMessage-ID: <cf@example.com>\r\nSubject: Request\r\n\r\nPlease provide a screenshot.'
        def uid(command, *args):
            if command == 'search': return 'OK', [b'12 13']
            message = raw if args[0] == b'12' else raw.replace(b'abuse@cloudflare.com', b'abuse@dynadot.com').replace(b'<cf@example.com>', b'<other@example.com>')
            return 'OK', [(b'12 (INTERNALDATE "16-Oct-2026 08:00:00 +0000" FLAGS ())', message)]
        server.uid.side_effect = uid
        updates = []
        with patch.object(adapter, 'connect', return_value=server):
            result = adapter.sync(ImapConnectionSpec('imap.example.com', 993, 'sender@example.com', 'fixture'),
                config, date(2026, 10, 16), date(2026, 10, 16), 'Asia/Bangkok', progress=updates.append)
        self.assertEqual([row['mailbox'] for row in result['folders']], [name, 'Other providers'])
        self.assertEqual(len(result['messages']), 4)
        self.assertEqual({row['mail']['provider'] for row in result['messages']}, {'cloudflare', 'dynadot'})
        self.assertTrue(all(row['folder_total'] == 2 for row in updates if row['folder_index']))
        self.assertEqual(result['messages'][0]['mail']['provider'], 'cloudflare')
        server.reset_mock()
        with patch.object(adapter, 'connect', return_value=server):
            result = adapter.sync(ImapConnectionSpec('imap.example.com', 993, 'sender@example.com', 'fixture'),
                {'reply_mailboxes': []}, date(2026, 10, 16), date(2026, 10, 16), 'Asia/Bangkok')
        server.select.assert_not_called()
        self.assertEqual(result['messages'], [])

    def test_visible_capture_child_and_safe_failure_diagnostics(self):
        import json
        import subprocess
        from pathlib import Path
        from domaincheck.core.review_capture import capture_review
        from domaincheck.core.evidence import CaptureFailed
        from domaincheck.api.provider_replies import failure
        def child(command, **kwargs):
            self.assertEqual(command[-1], 'headed')
            self.assertEqual(kwargs['timeout'], 180)
            (Path(command[-2]) / 'error.json').write_text(json.dumps({'reason': 'browser_executable_missing'}), encoding='utf-8')
            raise subprocess.CalledProcessError(1, command)
        with patch('domaincheck.core.review_capture.subprocess.run', side_effect=child):
            with self.assertRaisesRegex(CaptureFailed, '^browser_executable_missing$'):
                capture_review('https://example.com/', 'not_cloaking', headed=True)
        problem = failure(CaptureFailed('browser_executable_missing'))
        self.assertEqual(problem.status_code, 409)
        self.assertIn('[browser_executable_missing]', problem.detail)

    def test_real_folder_names_and_sent_headers_are_account_scoped(self):
        from domaincheck.core.provider_mail import ProviderMailboxAdapter
        from domaincheck.core.mail.connectivity import ImapConnectionSpec
        from datetime import date
        adapter = ProviderMailboxAdapter(())
        server = Mock(); selected = ['']
        server.list.return_value = ('OK', [b'(\\Junk) "/" "[Gmail]/Spam"', b'(\\Sent) "/" "[Gmail]/Sent Mail"', b'() "/" "6 - Auto.Cloudflare"'])
        def choose(folder, readonly=True):
            self.assertTrue(readonly); selected[0] = folder; return 'OK', [b'1']
        server.select.side_effect = choose
        server.response.return_value = ('UIDVALIDITY', [b'8'])
        def uid(command, *args):
            if command == 'search': return 'OK', [b'12']
            if 'Sent' in selected[0]:
                self.assertIn('HEADER.FIELDS', args[-1])
                raw = b'From: sender@example.com\r\nTo: abuse@cloudflare.com\r\nMessage-ID: <sent@example.com>\r\nIn-Reply-To: <request@cloudflare.com>\r\n\r\n'
            else: raw = b'From: abuse@cloudflare.com\r\nMessage-ID: <request@cloudflare.com>\r\nSubject: Request\r\n\r\nPlease provide a screenshot.'
            return 'OK', [(b'12 (INTERNALDATE "16-Oct-2026 08:00:00 +0000" FLAGS ())', raw)]
        server.uid.side_effect = uid
        with patch.object(adapter, 'connect', return_value=server):
            result = adapter.sync(ImapConnectionSpec('imap.example.com',993,'sender@example.com','fixture'),
                {'junk_mailbox':'Junk','sent_mailbox':'Sent','cloudflare_mailbox':'2-Cloudflare'},
                date(2026,10,16),date(2026,10,16),'Asia/Bangkok')
        self.assertIn('[Gmail]/Spam', [row['mailbox'] for row in result['folders']])
        self.assertIn('6 - Auto.Cloudflare', [row['mailbox'] for row in result['folders']])
        self.assertEqual(result['sent_headers'][0]['message_id'], '<sent@example.com>')
        self.assertEqual(result['sent_headers'][0]['references'], ['<request@cloudflare.com>'])

    def test_read_is_peek_folder_failure_isolated_and_seen_requires_uidvalidity(self):
        from domaincheck.core.provider_mail import ProviderMailboxAdapter
        from domaincheck.core.mail.connectivity import ImapConnectionSpec
        from datetime import date
        adapter = ProviderMailboxAdapter(())
        server = Mock()
        server.list.return_value = ('OK', [b'(\\HasNoChildren \\Junk) "/" "Junk"', b'(\\HasNoChildren) "/" "2-Cloudflare"'])
        server.select.side_effect = lambda folder, **kw: ('NO' if 'C' in folder and 'Cloudflare' not in folder else 'OK', [b'1'])
        server.response.return_value = ('UIDVALIDITY', [b'8'])
        raw = b'From: abuse@cloudflare.com\r\nMessage-ID: <request@cloudflare.com>\r\nSubject: Request\r\n\r\nPlease provide a screenshot.'
        def uid(command, *args):
            if command == 'search': return 'OK', [b'12']
            if command == 'store': return 'OK', []
            return 'OK', [(b'12 (INTERNALDATE "16-Oct-2026 08:00:00 +0000" FLAGS ())', raw)]
        server.uid.side_effect = uid
        spec = ImapConnectionSpec('imap.example.com', 993, 'sender@example.com', 'fixture')
        with patch.object(adapter, 'connect', return_value=server):
            updates = []
            result = adapter.sync(spec, {}, date(2026,10,16), date(2026,10,16), 'Asia/Bangkok', progress=updates.append)
            self.assertEqual(updates[0]['stage'], 'connecting')
            self.assertTrue(any(row['folder'] == 'INBOX' and row['processed'] == 1 and row['total'] == 1 for row in updates))
            self.assertEqual(updates[-1]['stage'], 'saving')
            self.assertEqual(len(result['messages']), 3)
            self.assertEqual(len(result['folders']), 4)
            self.assertTrue(any(row['error_code'] for row in result['folders']))
            self.assertTrue(all('BODY.PEEK' in call.args[-1] for call in server.uid.call_args_list if call.args[0] == 'fetch'))
            server.response.return_value = ('UIDVALIDITY', [b'9'])
            with self.assertRaises(ValueError): adapter.mark_seen(spec, {'mailbox': 'INBOX', 'uid': '12', 'uidvalidity': '8'})

import asyncio
import io
import os
import threading
import unittest
from datetime import timedelta
from uuid import UUID

from PIL import Image
from sqlalchemy import func, select

import tests.test_worker_send as fixtures
from domaincheck.application.domain_worker import DomainPrecheckWorker, DomainWorkerService
from domaincheck.application.evidence_review import EvidenceReviewSender, EvidenceReviewService
from domaincheck.application.mail_settings import MailSettingsService
from domaincheck.application.previews import PreviewConflict
from domaincheck.application.worker_review import list_reviews
from domaincheck.infrastructure.db.models import Artifact, Delivery, Job, SenderAccount, ReviewCase
from domaincheck.ports.artifacts import ArtifactIntegrityError, ArtifactValidationError, ImageUpload


def image():
    buffer = io.BytesIO(); Image.new('RGB', (4, 3), 'blue').save(buffer, format='PNG')
    return ImageUpload('manual.png', 'image/png', buffer.getvalue())


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class EvidenceReviewTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WorkerSendTests.asyncSetUp
    asyncTearDown = fixtures.WorkerSendTests.asyncTearDown

    def service(self, session, owner=None, now=None):
        return EvidenceReviewService(session, self.store, owner=owner or self.owner, actor=owner or self.owner,
            timezone='Asia/Bangkok', now=now or self.now)

    async def missing(self, accounts=None):
        async with self.db.session_factory() as session, session.begin():
            await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='test', now=self.now).create(
                ['https://missing.example.com/path?q=1'], accounts or [self.account])
        class Adapter:
            def run(_self, target, domain):
                return {'recipients': [{'channel': 'registrar', 'email': 'abuse@namecheap.com'}],
                        'cloaking': {'verdict': 'NO_SIGNAL'}, 'whois': {'registrar': 'Namecheap'}}
        await DomainPrecheckWorker(self.db.session_factory, Adapter(), now=lambda: self.now).run_next('missing')
        async with self.db.session_factory() as session:
            return (await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, 'domain_evidence'))[0]['id']

    async def second_account(self):
        async with self.db.session_factory() as session, session.begin():
            account = SenderAccount(owner_user_id=self.owner, label='Second', normalized_identity='second', smtp_host='smtp.example.com',
                smtp_port=465, smtp_username='second@example.com', smtp_transport='implicit_tls', enabled=True)
            session.add(account); await session.flush()
            MailSettingsService(session, owner_user_id=self.owner, actor_user_id=self.owner, request_id='test', envelope=self.envelope)._store_secrets(account, {'smtp_password': 'fixture'})
            return account.id

    async def prepare(self, case, accounts=None, uploads=None):
        async with self.db.session_factory() as session, session.begin():
            return (await self.service(session).preview(case, accounts or [self.account], [image()] if uploads is None else uploads))['preview']

    async def test_legacy_missing_scope_can_be_opened_and_explicitly_bound(self):
        case = await self.missing()
        async with self.db.session_factory() as session, session.begin():
            row = await session.get(ReviewCase, case)
            row.required_account_ids = []
        async with self.db.session_factory() as session, session.begin():
            detail = await self.service(session).detail(case)
            self.assertEqual(detail['sender_account_ids'], [])
            self.assertIn(self.account, [account['id'] for account in detail['accounts']])
            with self.assertRaises(PreviewConflict):
                await self.service(session).preview(case, [self.other], [image()])
        exact = await self.prepare(case)
        async with self.db.session_factory() as session, session.begin():
            detail = await self.service(session).detail(case)
            self.assertEqual(detail['sender_account_ids'], [str(self.account)])
            self.assertEqual(detail['preview']['fingerprint'], exact['fingerprint'])

    async def test_legacy_account_choices_remain_available_after_first_preview(self):
        second = await self.second_account()
        case = await self.missing()
        async with self.db.session_factory() as session, session.begin():
            row = await session.get(ReviewCase, case)
            row.required_account_ids = []
            for job in await session.scalars(select(Job).where(Job.owner_user_id == self.owner)):
                job.account_scope = []
        await self.prepare(case)
        async with self.db.session_factory() as session, session.begin():
            detail = await self.service(session).detail(case)
            self.assertEqual({row['id'] for row in detail['accounts']}, {self.account, second})
        exact = await self.prepare(case, [second], uploads=[])
        self.assertEqual({row['sender_account_id'] for row in exact['deliveries']}, {str(second)})
        async with self.db.session_factory() as session, session.begin():
            detail = await self.service(session).detail(case)
            self.assertEqual(set(detail['sender_account_ids']), {str(self.account), str(second)})

    async def send(self, case, exact, sender=None):
        return await EvidenceReviewSender(self.db.session_factory, self.store, sender or self.worker.sender, self.envelope,
            now=lambda: self.now).send(owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', case_id=case,
                check_id=exact['check_id'], preview_id=exact['id'], revision=exact['revision'], fingerprint=exact['fingerprint'], confirmed=True)

    async def test_preview_is_exact_and_direct_send_uses_both_source_accounts_without_job(self):
        second = await self.second_account()
        case = await self.missing([self.account]); self.assertEqual(case, await self.missing([second]))
        exact = await self.prepare(case, [self.account, second])
        self.assertEqual(len(exact['deliveries']), 2)
        async with self.db.session_factory() as session, session.begin():
            detail = await self.service(session).detail(case)
            self.assertEqual(len(detail['images']), 1)
            self.assertEqual(detail['preview']['fingerprint'], exact['fingerprint'])
            before = await session.scalar(select(func.count()).select_from(Job).where(Job.owner_user_id == self.owner))
        result = await self.send(case, exact)
        self.assertEqual(result['state'], 'sent'); self.assertEqual(len(self.messages), 2)
        expected = {row['sender_email']: row['text_body'].strip() for row in exact['deliveries']}
        for message in self.messages:
            self.assertEqual(message.get_body(preferencelist=('plain',)).get_content().strip(), expected[message['From'].addresses[0].addr_spec])
            self.assertEqual(len(list(message.iter_attachments())), 2)
        async with self.db.session_factory() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(Job).where(Job.owner_user_id == self.owner)), before)
            self.assertEqual(await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, 'domain_evidence'), [])
            from domaincheck.application.worker_batches import workspace_state
            self.assertFalse((await workspace_state(session, self.owner, self.now, 'Asia/Bangkok'))['locked'])
            self.assertEqual((await self.service(session).detail(case))['preview']['fingerprint'], exact['fingerprint'])
        await self.send(case, exact); self.assertEqual(len(self.messages), 2)

    async def test_partial_retry_preserves_sent_delivery_message_id_and_evidence(self):
        second = await self.second_account(); case = await self.missing([self.account, second])
        exact = await self.prepare(case, [self.account, second])
        self.outcomes = [('sent', 'send_message', 'accepted'), ('failed', 'authenticate', 'smtp_rejected')]
        result = await self.send(case, exact)
        self.assertEqual(result['state'], 'partial')
        failed_message_id = self.messages[1]['Message-ID']
        retry = await self.send(case, exact)
        self.assertEqual(retry['state'], 'sent'); self.assertEqual(len(self.messages), 3)
        self.assertEqual(self.messages[2]['Message-ID'], failed_message_id)
        self.assertEqual(result['images'], retry['images'])

    async def test_subset_is_partial_until_every_source_account_sent_and_unknown_never_retries(self):
        second = await self.second_account(); case = await self.missing([self.account, second])
        first = await self.prepare(case, [self.account])
        self.assertEqual((await self.send(case, first))['state'], 'partial')
        other = await self.prepare(case, [second], [])
        self.outcomes = [('unknown', 'send_message', 'smtp_outcome_uncertain')]
        result = await self.send(case, other)
        self.assertEqual(result['state'], 'partial')
        self.assertIn('unknown', [row['state'] for row in result['deliveries']])
        await self.send(case, other); self.assertEqual(len(self.messages), 2)

    async def test_foreign_scope_old_day_and_changed_preview_fail_before_smtp(self):
        case = await self.missing(); exact = await self.prepare(case)
        async with self.db.session_factory() as session:
            with self.assertRaises(LookupError): await self.service(session, self.other).detail(case)
            with self.assertRaises(PreviewConflict): await self.service(session, now=self.now + timedelta(days=1)).detail(case)
            with self.assertRaises(PreviewConflict): await self.service(session).preview(case, [self.other], [])
        await self.prepare(case)  # Replacing evidence invalidates the first immutable preview.
        with self.assertRaises(PreviewConflict): await self.send(case, exact)
        self.assertEqual(self.messages, [])

    async def test_mixed_invalid_upload_is_atomic_and_tampered_bytes_cannot_be_sent(self):
        case = await self.missing()
        before = set(self.store.root.rglob('*'))
        with self.assertRaises(ArtifactValidationError):
            await self.prepare(case, uploads=[image(), ImageUpload('bad.png', 'image/png', b'bad')])
        self.assertEqual({path for path in self.store.root.rglob('*') if path.is_file()}, {path for path in before if path.is_file()})
        exact = await self.prepare(case)
        async with self.db.session_factory() as session:
            artifact = await session.scalar(select(Artifact).where(Artifact.owner_user_id == self.owner, Artifact.kind == 'domain_worker:evidence'))
            (self.store.root / artifact.storage_key).write_bytes(b'tampered')
        with self.assertRaises((ValueError, OSError, ArtifactIntegrityError)): await self.send(case, exact)
        self.assertEqual(self.messages, [])

    async def test_concurrent_send_and_upload_cannot_change_the_inflight_case(self):
        case = await self.missing(); exact = await self.prepare(case)
        entered, release = threading.Event(), threading.Event(); original = self.worker.sender
        class Slow:
            def send(_self, spec, message):
                entered.set()
                if not release.wait(5): raise TimeoutError('test timeout')
                return original.send(spec, message)
        task = asyncio.create_task(self.send(case, exact, Slow()))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            with self.assertRaises(PreviewConflict): await self.send(case, exact)
            with self.assertRaises(ValueError): await self.prepare(case)
        finally:
            release.set(); await task
        self.assertEqual(len(self.messages), 1)

    async def test_expired_intent_becomes_unknown_and_never_gains_another_send(self):
        from domaincheck.application.deliveries import DeliveryService
        from domaincheck.application.previews import PreviewService
        from domaincheck.application.worker_send import daily_identity
        from domaincheck.domain.deliveries import DeliveryBusinessIdentity
        from domaincheck.infrastructure.db.models import Check
        case = await self.missing(); exact = await self.prepare(case)
        async with self.db.session_factory() as session, session.begin():
            await PreviewService(session, self.store, owner_user_id=self.owner, actor_user_id=self.owner, request_id='test').approve(
                exact['id'], expected_revision=exact['revision'], confirmed=True, now=self.now)
            check = await session.get(Check, exact['check_id'])
            ledger = DeliveryService(session, self.store, owner_user_id=self.owner, actor_user_id=self.owner, request_id='test')
            row = (await ledger.materialize(preview_id=exact['id'], expected_revision=exact['revision'], workflow='domain_worker',
                identities=[DeliveryBusinessIdentity(0, daily_identity(check.business_day, check.domain))], now=self.now))[0]
            await ledger.claim_intent(row.id, expected_version=row.version, now=self.now)
            row.claim_expires_at = self.now - timedelta(seconds=1)
        result = await self.send(case, exact)
        self.assertEqual(result['deliveries'][0]['state'], 'unknown')
        self.assertEqual(self.messages, [])

    async def test_api_enforces_permissions_csrf_case_images_and_exact_send_payload(self):
        import json
        from types import SimpleNamespace
        from httpx import ASGITransport, AsyncClient
        from domaincheck.api.dependencies import current_user
        from domaincheck.infrastructure.auth.sessions import hash_token
        from domaincheck.main import create_app
        from domaincheck.settings import Settings
        case = await self.missing()
        actor = SimpleNamespace(user_id=self.owner, status='active', timezone='Asia/Bangkok',
            permissions={'domain_evidence_review.view'}, csrf_hash=hash_token('fixture-csrf'))
        app = create_app(Settings(_env_file=None))
        app.state.database, app.state.artifact_store, app.state.secret_envelope = self.db, self.store, self.envelope
        app.state.clock = SimpleNamespace(now=lambda: self.now)
        app.state.evidence_review_sender = self.worker.sender
        class Archive:
            def save(_self, spec, message, message_id): return 'saved'
        app.state.evidence_review_sent_copier = Archive()
        async def user(): return actor
        app.dependency_overrides[current_user] = user
        root = f'/api/v1/domain-worker/reviews/evidence/{case}'
        headers = {'Origin': 'http://127.0.0.1:5173', 'X-CSRF-Token': 'fixture-csrf'}
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.get(root)).status_code, 200)
            self.assertEqual((await client.post(root + '/previews', data={'account_ids': json.dumps([str(self.account)])}, headers=headers)).status_code, 403)
            actor.permissions.update({'domain_evidence_review.upload', 'domain_evidence_review.preview', 'domain_evidence_review.send'})
            self.assertEqual((await client.post(root + '/previews', data={'account_ids': json.dumps([str(self.account)])})).status_code, 403)
            response = await client.post(root + '/previews', data={'account_ids': json.dumps([str(self.account)])},
                files={'files': ('manual.png', image().data, 'image/png')}, headers=headers)
            self.assertEqual(response.status_code, 201, response.text)
            exact = response.json()['preview']
            detail = (await client.get(root)).json()
            image_response = await client.get(root + f"/images/{detail['images'][0]['id']}")
            self.assertEqual(image_response.status_code, 200); self.assertEqual(image_response.headers['cache-control'], 'private, no-store')
            actor.user_id = self.other
            self.assertEqual((await client.get(root)).status_code, 404)
            self.assertEqual((await client.get(root + f"/images/{detail['images'][0]['id']}")).status_code, 404)
            actor.user_id = self.owner
            body = {key: exact[key] for key in ('check_id', 'revision', 'fingerprint')}
            body.update(preview_id=exact['id'], confirmed=True)
            self.assertEqual((await client.post(root + '/send', json=body)).status_code, 403)
            sent = await client.post(root + '/send', json=body, headers=headers)
            self.assertEqual(sent.status_code, 200, sent.text)
            self.assertEqual(sent.json()['state'], 'sent')
            self.assertEqual(sent.json()['deliveries'][0]['sent_copy_status'], 'saved')
            self.assertEqual(len(self.messages), 1)

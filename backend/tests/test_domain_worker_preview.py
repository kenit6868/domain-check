from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from PIL import Image
from sqlalchemy import select

from domaincheck.application.domain_worker import DomainWorkerService
from domaincheck.application.domain_worker_preview import DomainWorkerPreviewService
from domaincheck.application.previews import PreviewConflict, PreviewNotFound
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.db.models import Artifact, AuditEvent, EvidenceSet, PreviewPlan, SenderAccount, User, UserSettings
from domaincheck.infrastructure.db.session import Database
from domaincheck.ports.artifacts import ArtifactIntegrityError, ImageUpload


class PreviewApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_permission_csrf_and_server_owned_request_contract(self):
        from httpx import ASGITransport, AsyncClient
        from domaincheck.api.dependencies import current_user, get_db_session
        from domaincheck.infrastructure.auth.sessions import hash_token
        from domaincheck.main import create_app
        from domaincheck.settings import Settings
        actor = SimpleNamespace(user_id=uuid4(), status='active', timezone='Asia/Bangkok',
            permissions={'domain_worker.view'}, csrf_hash=hash_token('csrf'))
        app = create_app(Settings(_env_file=None))
        async def user(): return actor
        session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(brand_name='Fixture',
            contact_name='Operator', contact_email='operator@example.com')))
        async def db(): yield session
        app.dependency_overrides[current_user] = user
        app.dependency_overrides[get_db_session] = db
        path = f'/api/v1/domain-worker/checks/{uuid4()}'
        headers = {'Origin': 'http://127.0.0.1:5173', 'X-CSRF-Token': 'csrf'}
        body = {'sender_account_ids': [str(uuid4())]}
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.get(path + '/preview-options')).status_code, 403)
            self.assertEqual((await client.post(path + '/previews', json=body, headers=headers)).status_code, 403)
            actor.permissions.add('domain_worker.preview')
            self.assertEqual((await client.post(path + '/previews', json=body)).status_code, 403)
            for extra in ('owner_user_id', 'body', 'recipient', 'fingerprint', 'attachment_artifact_ids'):
                response = await client.post(path + '/previews', json={**body, extra: 'untrusted'}, headers=headers)
                self.assertEqual(response.status_code, 422)
            with patch.object(DomainWorkerPreviewService, 'create', new=AsyncMock(return_value={'id': 'fixture'})):
                response = await client.post(path + '/previews', json=body, headers=headers)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.headers['cache-control'], 'private, no-store')


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class DomainWorkerPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(os.environ['DOMAINCHECK_TEST_DATABASE_URL'])
        self.temp = tempfile.TemporaryDirectory()
        self.store = FilesystemArtifactStore(Path(self.temp.name))
        self.now = datetime(2026, 10, 16, 8, tzinfo=UTC)
        async with self.db.session_factory() as session, session.begin():
            a, b = User(display_name='Preview A', status='active'), User(display_name='Preview B', status='active')
            session.add_all([a, b]); await session.flush()
            self.owner, self.other = a.id, b.id
            profile = UserSettings(owner_user_id=a.id, brand_name='Fixture Brand', contact_name='Profile Operator',
                contact_email='profile@example.com', company_name='Fixture Company', signature_role='Brand Protection')
            self.accounts = []
            for owner, name in ((a.id, 'Alice'), (a.id, 'Bob'), (b.id, 'Foreign')):
                account = SenderAccount(owner_user_id=owner, label=name, normalized_identity=name,
                    smtp_host='mail.example.test', smtp_port=465, smtp_username=f'{name.lower()}@example.com',
                    smtp_transport='implicit_tls', signature_overrides={'contact_name': name},
                    enabled=True, is_default=False)
                session.add(account); await session.flush()
                self.accounts.append(account.id)
            session.add(profile); await session.flush()
            operation = await DomainWorkerService(session, owner=a.id, actor=a.id, timezone='Asia/Bangkok',
                request_id='fixture', now=self.now).create(['https://example.com/login?q=1'], self.accounts[:2])
            check = operation.checks[0]
            check.status = operation.items[0].state = 'succeeded'
            operation.job.state = 'completed'
            image = io.BytesIO(); Image.new('RGB', (3, 2), 'blue').save(image, format='PNG')
            evidence = EvidenceSet(owner_user_id=a.id, check_id=check.id, requested_url=check.requested_url,
                capture_mode='browser_capture', manifest={})
            session.add(evidence); await session.flush()
            stored = self.store.store_evidence_images(owner_user_id=a.id, evidence_set_id=evidence.id,
                uploads=[ImageUpload('capture.png', 'image/png', image.getvalue())])[0]
            artifact = Artifact(id=stored.artifact_id, owner_user_id=a.id, evidence_set_id=evidence.id,
                kind='domain_worker:evidence', mime_type=stored.mime_type, byte_size=stored.byte_size,
                sha256=stored.sha256, storage_key=stored.storage_key, immutable=True)
            session.add(artifact)
            evidence.manifest = {'version': 1, 'source': 'browser_capture', 'terminal': False,
                'requested_url': check.requested_url, 'evidence_type': 'passive', 'screenshots': [{
                    'artifact_id': str(artifact.id), 'sha256': stored.sha256, 'byte_size': stored.byte_size}]}
            check.result = {'disposition': 'ready', 'evidence_set_id': str(evidence.id), 'cloaking': {'verdict': 'NO_SIGNAL'},
                'recipients': [{'channel': 'registrar', 'email': 'abuse@namecheap.com'},
                               {'channel': 'registry', 'email': 'abuse@registry.example'}],
                'whois': {'registrar': 'Namecheap'}, 'registry_contact': {'registry': 'Fixture Registry',
                    'abuse_email': 'abuse@registry.example'}}
            self.check_id, self.evidence_id, self.image_id = check.id, evidence.id, artifact.id

    async def asyncTearDown(self):
        await self.db.dispose()
        self.temp.cleanup()

    def service(self, session, owner=None, now=None):
        return DomainWorkerPreviewService(session, self.store, owner=owner or self.owner,
            actor=owner or self.owner, timezone='Asia/Bangkok', request_id='preview-test', now=now or self.now)

    async def make(self):
        async with self.db.session_factory() as session, session.begin():
            return await self.service(session).create(self.check_id, self.accounts[:2])

    async def test_exact_plan_personalizes_every_account_and_recipient_and_survives_reload(self):
        with patch('smtplib.SMTP', side_effect=AssertionError('SMTP')), \
             patch('smtplib.SMTP_SSL', side_effect=AssertionError('SMTP')), \
             patch('imaplib.IMAP4_SSL', side_effect=AssertionError('IMAP')):
            exact = await self.make()
            self.assertEqual(len(exact['deliveries']), 4)
            self.assertEqual(len(exact['artifacts']), 3)  # image, manifest, inline logo
            self.assertEqual(exact['expires_at'], self.now + timedelta(minutes=30))
            for row in exact['deliveries']:
                self.assertIn(row['sender_email'], row['text_body'])
                self.assertIn(row['sender_name'], row['text_body'])
                self.assertNotIn('profile@example.com', row['text_body'])
                self.assertIn('Reported URL: https://example.com/login?q=1', row['text_body'])
                self.assertLess(row['text_body'].index('--- Observed Phishing Behavior and Supporting Evidence ---'), row['text_body'].index('Regards,'))
                self.assertIn(f'cid:{exact["signature_logo"]["content_id"]}', row['html_body'])
                from domaincheck.core.email_content import InlineLogo, set_email_content
                from email.message import EmailMessage
                import base64
                message = EmailMessage()
                logo = exact['signature_logo']
                set_email_content(message, row['text_body'], InlineLogo(
                    base64.b64decode(logo['data_url'].split(',', 1)[1]), logo['mime_type'], logo['content_id']))
                self.assertEqual(message.get_body(('plain',)).get_content().rstrip(), row['text_body'].rstrip())
                self.assertEqual(message.get_body(('html',)).get_content().rstrip(), row['html_body'].rstrip())
            async with self.db.session_factory() as session:
                loaded = await self.service(session).get(self.check_id, exact['id'])
                self.assertEqual(loaded, exact)
                plan = await session.get(PreviewPlan, exact['id'])
                self.assertIsNone(plan.approved_at)
                profile = await session.get(UserSettings, self.owner)
                self.assertIsNone(profile.logo_artifact_id)  # default was bound without editing profile
                artifact = await session.get(Artifact, plan.draft_artifact_id)
                payload = json.loads(self.store.read_verified(storage_key=artifact.storage_key,
                    expected_size=artifact.byte_size, expected_sha256=artifact.sha256))
                self.assertEqual(payload['deliveries'], exact['deliveries'])
                audit = list(await session.scalars(select(AuditEvent).where(AuditEvent.owner_user_id == self.owner)))
                self.assertNotIn(exact['deliveries'][0]['text_body'], str([event.metadata_allowlist for event in audit]))

    async def test_cross_owner_account_check_and_preview_are_blocked(self):
        exact = await self.make()
        async with self.db.session_factory() as session, session.begin():
            with self.assertRaises(PreviewNotFound):
                await self.service(session, self.other).get(self.check_id, exact['id'])
            with self.assertRaises(PreviewConflict):
                await self.service(session).create(self.check_id, [self.accounts[2]])
            with self.assertRaises(PreviewNotFound):
                await self.service(session).get(self.check_id, uuid4())

    async def test_cloaking_missing_evidence_old_day_and_uncheckpointed_cannot_preview(self):
        from domaincheck.infrastructure.db.models import Check, JobItem
        async with self.db.session_factory() as session, session.begin():
            check = await session.get(Check, self.check_id)
            original = dict(check.result)
            for change in ({'disposition': 'cloaking_review'}, {'disposition': 'evidence_review'},
                           {'evidence_set_id': str(uuid4())}, {'cloaking': {'verdict': 'POSSIBLE'}},
                           {'browser_verification_pending': True}, {'recipients': []}):
                check.result = {**original, **change}; await session.flush()
                with self.assertRaises(PreviewConflict):
                    await self.service(session).options(self.check_id)
            check.result = original; await session.flush()
            check.result = {**original, 'cloaking': {'verdict': 'BLOCKED_OR_UNAVAILABLE', 'manual_review_required': True}}
            await session.flush()
            self.assertEqual((await self.service(session).options(self.check_id))['check_id'], self.check_id)
            check.result = original; await session.flush()
            with self.assertRaises(PreviewConflict):
                await self.service(session, now=self.now + timedelta(days=1)).options(self.check_id)
            item = await session.scalar(select(JobItem).where(JobItem.owner_user_id == self.owner))
            item.state = 'unknown'; await session.flush()
            with self.assertRaises(PreviewConflict):
                await self.service(session).create(self.check_id, self.accounts[:1])

    async def test_new_revision_expiry_and_config_changes_invalidate_old_preview(self):
        first, second = await self.make(), await self.make()
        self.assertEqual((first['revision'], second['revision']), (1, 2))
        async with self.db.session_factory() as session, session.begin():
            with self.assertRaisesRegex(PreviewConflict, 'newer'):
                await self.service(session).get(self.check_id, first['id'])
            with self.assertRaisesRegex(PreviewConflict, 'expired'):
                await self.service(session, now=self.now + timedelta(minutes=31)).get(self.check_id, second['id'])
            for model, key, message in ((SenderAccount, self.accounts[0], 'Sender account'),
                                        (UserSettings, self.owner, 'Profile')):
                resource = await session.get(model, key); resource.version += 1; await session.flush()
                with self.assertRaisesRegex(PreviewConflict, message):
                    await self.service(session).get(self.check_id, second['id'])
                resource.version -= 1; await session.flush()

    async def test_evidence_manifest_resource_and_bytes_tampering_are_rejected(self):
        from domaincheck.infrastructure.db.models import Check
        exact = await self.make()
        async with self.db.session_factory() as session, session.begin():
            check = await session.get(Check, self.check_id)
            check.version += 1; await session.flush()
            with self.assertRaisesRegex(PreviewConflict, 'resource changed'):
                await self.service(session).get(self.check_id, exact['id'])
            check.version -= 1; await session.flush()
            original = dict(check.result)
            check.result = {**original, 'recipients': [{'channel': 'registrar', 'email': 'different@example.com'}]}
            await session.flush()
            with self.assertRaisesRegex(PreviewConflict, 'resource result changed'):
                await self.service(session).get(self.check_id, exact['id'])
            check.result = original; await session.flush()
            evidence = await session.get(EvidenceSet, self.evidence_id)
            manifest = dict(evidence.manifest)
            evidence.manifest = {**manifest, 'final_url': 'https://different.example/'}; await session.flush()
            with self.assertRaisesRegex(PreviewConflict, 'manifest changed'):
                await self.service(session).get(self.check_id, exact['id'])
            evidence.manifest = manifest; await session.flush()
            artifact = await session.get(Artifact, self.image_id)
            self.store.root.joinpath(*artifact.storage_key.split('/')).write_bytes(b'changed')
            with self.assertRaises(ArtifactIntegrityError):
                await self.service(session).get(self.check_id, exact['id'])

    async def test_logo_is_an_integrity_checked_inline_artifact(self):
        exact = await self.make()
        logo = next(row for row in exact['artifacts'] if row['disposition'] == 'inline')
        from uuid import UUID
        async with self.db.session_factory() as session:
            artifact = await session.get(Artifact, UUID(logo['artifact_id']))
            self.store.root.joinpath(*artifact.storage_key.split('/')).write_bytes(b'changed')
            with self.assertRaisesRegex(PreviewConflict, 'integrity'):
                await self.service(session).get(self.check_id, exact['id'])

    async def test_custom_logo_and_signature_overrides_are_used_without_changing_sender_address(self):
        async with self.db.session_factory() as session, session.begin():
            data = io.BytesIO(); Image.new('RGB', (2, 2), 'red').save(data, format='PNG')
            stored = self.store.store_evidence_images(owner_user_id=self.owner, evidence_set_id=uuid4(),
                uploads=[ImageUpload('logo.png', 'image/png', data.getvalue())])[0]
            session.add(Artifact(id=stored.artifact_id, owner_user_id=self.owner, kind='mail_settings:logo',
                mime_type=stored.mime_type, byte_size=stored.byte_size, sha256=stored.sha256,
                storage_key=stored.storage_key, immutable=True))
            await session.flush()
            profile = await session.get(UserSettings, self.owner)
            profile.logo_artifact_id = stored.artifact_id; profile.version += 1
            account = await session.get(SenderAccount, self.accounts[0])
            account.signature_overrides = {'contact_name': 'Alice Team', 'contact_email': 'contact@example.com',
                                           'signature_role': 'Abuse Response'}
            account.version += 1
        exact = await self.make()
        self.assertEqual(exact['signature_logo']['content_id'], f'logo-{stored.artifact_id}')
        self.assertEqual(exact['signature_logo']['sha256'], stored.sha256)
        for delivery in exact['deliveries'][:2]:
            self.assertEqual(delivery['sender_email'], 'alice@example.com')
            self.assertIn('contact@example.com', delivery['text_body'])
            self.assertIn('Alice Team', delivery['text_body'])
            self.assertIn('Abuse Response', delivery['text_body'])

    async def test_invalid_selection_and_quality_gate_do_not_leave_new_artifacts(self):
        async with self.db.session_factory() as session, session.begin():
            svc = self.service(session)
            for ids in ([], [self.accounts[0], self.accounts[0]]):
                with self.assertRaises(ValueError):
                    await svc.create(self.check_id, ids)
            account = await session.get(SenderAccount, self.accounts[0])
            account.enabled = False; await session.flush()
            with self.assertRaises(PreviewConflict):
                await svc.create(self.check_id, self.accounts[:2])
            account.enabled = True; await session.flush()
            before = set(self.store.root.rglob('*.*'))
            with patch('domaincheck.application.domain_worker_preview.prepare_deliveries',
                       side_effect=PreviewConflict('quality gate')):
                with self.assertRaises(PreviewConflict):
                    await svc.create(self.check_id, self.accounts[:2])
            self.assertEqual(set(self.store.root.rglob('*.*')), before)

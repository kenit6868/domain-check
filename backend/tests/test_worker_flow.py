import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime

from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select

from domaincheck.api.domain_worker import PrecheckCreate
from domaincheck.application.domain_worker import DomainPrecheckWorker, DomainWorkerService
from domaincheck.application.jobs import JobConflict
from domaincheck.application.worker_review import list_reviews, upload_review_images
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.db.models import Check, SenderAccount, User
from domaincheck.infrastructure.db.session import Database
from domaincheck.ports.artifacts import ArtifactValidationError, ImageUpload
from domaincheck.settings import BACKEND_ROOT


def image():
    data = io.BytesIO(); Image.new('RGB', (4, 3), 'blue').save(data, format='PNG')
    return ImageUpload('fixture.png', 'image/png', data.getvalue())


class WorkerContractTests(unittest.TestCase):
    def test_precheck_requires_accounts_before_queuing(self):
        for payload in ({'targets': ['example.com']}, {'targets': ['example.com'], 'sender_account_ids': []}):
            with self.assertRaises(ValidationError):
                PrecheckCreate.model_validate(payload)

    def test_artifact_root_is_identical_from_api_worker_and_unrelated_directories(self):
        script = "from domaincheck.settings import Settings; print(Settings(_env_file=None, artifact_root='backend/.runtime/artifacts').artifact_root)"
        with tempfile.TemporaryDirectory() as directory:
            paths = [subprocess.check_output([sys.executable, '-c', script], cwd=cwd, text=True).strip()
                     for cwd in (str(BACKEND_ROOT), str(BACKEND_ROOT.parent), directory)]
        self.assertEqual(len(set(paths)), 1)
        self.assertEqual(Path(paths[0]), BACKEND_ROOT / '.runtime/artifacts')


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class WorkerFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(os.environ['DOMAINCHECK_TEST_DATABASE_URL'])
        self.temp = tempfile.TemporaryDirectory()
        self.store = FilesystemArtifactStore(Path(self.temp.name))
        self.now = datetime(2026, 10, 16, 8, tzinfo=UTC)
        async with self.db.session_factory() as session, session.begin():
            a, b = User(display_name='Flow A', status='active'), User(display_name='Flow B', status='active')
            session.add_all([a, b]); await session.flush()
            self.owner, self.other = a.id, b.id
            self.ids = []
            for index, owner in enumerate((a.id, a.id, b.id)):
                account = SenderAccount(owner_user_id=owner, label=f'Account {index}', normalized_identity=str(index),
                    smtp_host='mail.example.com', smtp_port=465, smtp_username=f'account{index}@example.com',
                    smtp_transport='implicit_tls', enabled=True, is_default=False)
                session.add(account); await session.flush(); self.ids.append(account.id)

    async def asyncTearDown(self):
        await self.db.dispose(); self.temp.cleanup()

    def service(self, session):
        return DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok', request_id='flow', now=self.now)

    async def operation(self, urls, account=None):
        async with self.db.session_factory() as session, session.begin():
            operation = await self.service(session).create(urls, [account or self.ids[0]])
            operation_id = operation.job.id
        class Adapter:
            def run(_self, target, domain):
                return {'recipients': [] if '/no-email' in target else [{'channel': 'registrar', 'email': 'abuse@example.com'}],
                    'cloaking': {'verdict': 'POSSIBLE' if '/cloak' in target else 'NO_SIGNAL'}}
        class Capture:
            def capture(_self, target, **kwargs):
                if '/missing' in target:
                    raise RuntimeError('capture failed')
                return [image()], {'terminal': False, 'evidence_type': 'dom_observed'}
        await DomainPrecheckWorker(self.db.session_factory, Adapter(), store=self.store, capture=Capture(),
                                  now=lambda: self.now).run_next('flow-worker')
        return operation_id

    async def test_accounts_are_owned_and_enabled_before_precheck_job_is_created(self):
        async with self.db.session_factory() as session, session.begin():
            with self.assertRaises(JobConflict):
                await self.service(session).create(['example.com'], [self.ids[2]])
            account = await session.get(SenderAccount, self.ids[0]); account.enabled = False; await session.flush()
            with self.assertRaises(JobConflict):
                await self.service(session).create(['example.com'], [self.ids[0]])

    async def test_ready_requires_email_and_evidence_and_queues_keep_source_accounts(self):
        operation_id = await self.operation(['example.com/ready', 'example.com/cloak', 'example.com/missing', 'example.com/no-email'])
        async with self.db.session_factory() as session:
            op = await self.service(session).get(operation_id)
            self.assertEqual(op.job.account_scope, [str(self.ids[0])])
            self.assertEqual([row.result['disposition'] for row in op.checks],
                ['ready', 'cloaking_review', 'evidence_review', 'excluded_no_email'])
            self.assertTrue(op.checks[0].result['evidence_set_id'])
            for kind in ('cloaking', 'domain_evidence'):
                rows = await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, kind)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['sender_account_ids'], [str(self.ids[0])])
                self.assertEqual(await list_reviews(session, self.other, 'Asia/Bangkok', self.now, kind), [])

    async def test_manual_evidence_resolves_all_sources_without_changing_accounts(self):
        first = await self.operation(['example.com/missing'])
        second = await self.operation(['example.com/missing'], self.ids[1])
        async with self.db.session_factory() as session, session.begin():
            rows = await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, 'domain_evidence')
            self.assertEqual(len(rows), 1)
            self.assertEqual(set(rows[0]['sender_account_ids']), {str(self.ids[0]), str(self.ids[1])})
            case_id = rows[0]['id']
            with self.assertRaises(LookupError):
                await upload_review_images(session, self.store, self.other, 'Asia/Bangkok', self.now, case_id, [image()])
            result = await upload_review_images(session, self.store, self.owner, 'Asia/Bangkok', self.now, case_id, [image()])
            self.assertEqual(len(result['check_ids']), 2)
        async with self.db.session_factory() as session:
            self.assertEqual(await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, 'domain_evidence'), [])
            for operation_id, expected_account in ((first, self.ids[0]), (second, self.ids[1])):
                op = await self.service(session).get(operation_id)
                self.assertEqual(op.checks[0].result['disposition'], 'ready')
                self.assertEqual(op.job.account_scope, [str(expected_account)])

    async def test_invalid_manual_batch_writes_nothing_and_cannot_be_applied_to_cloaking(self):
        await self.operation(['example.com/missing', 'example.com/cloak'])
        async with self.db.session_factory() as session, session.begin():
            normal = (await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, 'domain_evidence'))[0]
            before = set(self.store.root.rglob('*.png'))
            with self.assertRaises(ArtifactValidationError):
                await upload_review_images(session, self.store, self.owner, 'Asia/Bangkok', self.now,
                    normal['id'], [image(), ImageUpload('bad.png', 'image/png', b'bad')])
            self.assertEqual(set(self.store.root.rglob('*.png')), before)
            cloaking = (await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, 'cloaking'))[0]
            with self.assertRaises(LookupError):
                await upload_review_images(session, self.store, self.owner, 'Asia/Bangkok', self.now, cloaking['id'], [image()])

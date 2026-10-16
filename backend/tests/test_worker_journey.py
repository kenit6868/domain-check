"""One composed journey across normal batches and both review queues, no network."""
import os
import unittest
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select

import tests.test_worker_send as fixtures
import tests.test_evidence_review as evidence
from domaincheck.application.domain_worker import DomainPrecheckWorker, DomainWorkerService
from domaincheck.application.evidence_review import EvidenceReviewService, EvidenceReviewSender
from domaincheck.application.cloaking_review import CloakingReviewService, CloakingReviewSender
from domaincheck.application.worker_batches import workspace_state, filter_sent_inputs
from domaincheck.infrastructure.cache import business_day
from domaincheck.application.worker_review import list_reviews
from domaincheck.infrastructure.db.models import Delivery, Job


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class WorkerJourneyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WorkerSendTests.asyncSetUp
    asyncTearDown = fixtures.WorkerSendTests.asyncTearDown
    second_account = evidence.EvidenceReviewTests.second_account
    svc = fixtures.WorkerSendTests.svc
    start = fixtures.WorkerSendTests.start

    async def test_precheck_batch_both_reviews_retry_and_next_input_share_one_ledger(self):
        accounts = [self.account, await self.second_account()]
        targets = [f'https://{kind}.example.com/' for kind in ['ready', 'manual', 'cloak', 'nomail']]
        async with self.db.session_factory() as session, session.begin():
            op = await DomainWorkerService(session, owner=self.owner, actor=self.owner,
                timezone='Asia/Bangkok', now=self.now, request_id='journey').create(targets, accounts)
            source = op.job.id

        class Adapter:
            def run(_self, target, domain):
                return {'recipients': [] if domain.startswith('nomail.') else [
                    {'channel': 'registrar', 'email': 'abuse@namecheap.com'}],
                    'whois': {'registrar': 'Namecheap'},
                    'cloaking': {'verdict': 'LIKELY' if domain.startswith('cloak.') else 'NO_SIGNAL'}}
        class Capture:
            def capture(_self, target, **kwargs):
                if 'manual.' in target: raise RuntimeError('fixture capture failure')
                return [evidence.image()], {'requested_url': target, 'terminal': False, 'evidence_type': 'passive'}
        def capture_cloak(target, decision):
            return [evidence.image(), evidence.image()], {'version': 1, 'source': 'browser_capture',
                'evidence_type': 'cloaking_comparison', 'requested_url': target, 'terminal': False,
                'cloaking': {'verdict': 'POSSIBLE', 'evidence_pair': {'profiles': ['desktop_direct', 'googlebot_smartphone']}}}
        await DomainPrecheckWorker(self.db.session_factory, Adapter(), store=self.store, capture=Capture(),
            cloaking_capture=capture_cloak, now=lambda: self.now).run_next('journey-precheck')
        async with self.db.session_factory() as session, session.begin():
            op = await DomainWorkerService(session, owner=self.owner, actor=self.owner,
                timezone='Asia/Bangkok', now=self.now, request_id='reload').get(source)
            self.assertEqual([c.result['disposition'] for c in op.checks],
                ['ready', 'evidence_review', 'cloaking_review', 'excluded_no_email'])
            self.assertEqual((await workspace_state(session, self.owner, self.now, 'Asia/Bangkok'))['phase'], 'awaiting_send')
            normal = (await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, 'domain_evidence'))[0]['id']
            cloak = (await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, 'cloaking'))[0]['id']
        job = await self.start(source, batch_size=1, interval_seconds=0)
        await self.worker.run_next('journey-send')
        async with self.db.session_factory() as session:
            self.assertEqual((await self.svc(session).get(UUID(job['id'])))['state'], 'completed')
        self.assertEqual(len(self.messages), 2)

        async def preview(service_type, case, **kwargs):
            async with self.db.session_factory() as session, session.begin():
                service = service_type(session, self.store, owner=self.owner, actor=self.owner,
                    timezone='Asia/Bangkok', now=self.now)
                return (await service.preview(case, accounts, **kwargs))['preview']
        async def send(sender_type, case, exact):
            return await sender_type(self.db.session_factory, self.store, self.worker.sender, self.envelope,
                now=lambda: self.now).send(owner=self.owner, actor=self.owner, timezone='Asia/Bangkok',
                    case_id=case, check_id=exact['check_id'], preview_id=exact['id'], revision=exact['revision'],
                    fingerprint=exact['fingerprint'], confirmed=True)
        exact = await preview(EvidenceReviewService, normal, uploads=[evidence.image()])
        self.outcomes = [('sent', 'send_message', 'accepted'), ('failed', 'authenticate', 'smtp_rejected')]
        self.assertEqual((await send(EvidenceReviewSender, normal, exact))['state'], 'partial')
        self.assertEqual((await send(EvidenceReviewSender, normal, exact))['state'], 'sent')
        self.assertEqual(len(self.messages), 5)
        exact = await preview(CloakingReviewService, cloak, decision='confirmed_cloaking',
            confirmed_difference=True, use_capture=True)
        self.outcomes = [('sent', 'send_message', 'accepted'), ('failed', 'authenticate', 'smtp_rejected')]
        self.assertEqual((await send(CloakingReviewSender, cloak, exact))['state'], 'partial')
        async with self.db.session_factory() as session:
            remaining, _ = await filter_sent_inputs(session, self.owner, business_day(self.now, 'Asia/Bangkok'),
                accounts, [(targets[2], 'cloak.example.com')])
            self.assertEqual(len(remaining), 1)  # A single successful account is not the whole domain.
        self.assertEqual((await send(CloakingReviewSender, cloak, exact))['state'], 'sent')
        await send(CloakingReviewSender, cloak, exact)
        self.assertEqual(len(self.messages), 8)
        async with self.db.session_factory() as session, session.begin():
            self.assertFalse((await workspace_state(session, self.owner, self.now, 'Asia/Bangkok'))['locked'])
            for kind in ['domain_evidence', 'cloaking']:
                self.assertEqual(await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, kind), [])
            self.assertEqual(await session.scalar(select(func.count()).select_from(Delivery).where(
                Delivery.owner_user_id == self.owner, Delivery.state == 'sent')), 6)
            before = await session.scalar(select(func.count()).select_from(Job).where(Job.owner_user_id == self.owner))
            repeated = await DomainWorkerService(session, owner=self.owner, actor=self.owner,
                timezone='Asia/Bangkok', now=self.now, request_id='repeat').create(targets, accounts)
            self.assertIsNone(repeated.job)
            self.assertEqual(repeated.input_summary['sent_count'], 3)
            self.assertEqual(repeated.input_summary['no_email_count'], 1)
            self.assertEqual(await session.scalar(select(func.count()).select_from(Job).where(Job.owner_user_id == self.owner)), before)
            tomorrow, _ = await filter_sent_inputs(session, self.owner,
                business_day(self.now + timedelta(days=1), 'Asia/Bangkok'), accounts,
                [(target, target.split('/')[2]) for target in targets])
            self.assertEqual(len(tomorrow), 4)

import os
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select

import tests.test_evidence_review as evidence
import tests.test_worker_send as fixtures
from domaincheck.application.cloaking_review import CloakingReviewService, CloakingReviewSender
from domaincheck.application.domain_worker import DomainPrecheckWorker, DomainWorkerService
from domaincheck.application.previews import PreviewConflict
from domaincheck.application.worker_review import list_reviews
from domaincheck.infrastructure.db.models import Artifact, Check, Job, ReviewCase
from domaincheck.ports.artifacts import ArtifactValidationError, ImageUpload


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class CloakingReviewTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WorkerSendTests.asyncSetUp
    asyncTearDown = fixtures.WorkerSendTests.asyncTearDown
    second_account = evidence.EvidenceReviewTests.second_account
    test_concurrent_send_and_upload_cannot_change_the_inflight_case = evidence.EvidenceReviewTests.test_concurrent_send_and_upload_cannot_change_the_inflight_case
    test_expired_intent_becomes_unknown_and_never_gains_another_send = evidence.EvidenceReviewTests.test_expired_intent_becomes_unknown_and_never_gains_another_send

    def service(self, session, owner=None, now=None):
        return CloakingReviewService(session, self.store, owner=owner or self.owner, actor=owner or self.owner,
            timezone='Asia/Bangkok', now=now or self.now)

    async def missing(self, accounts=None, *, cloaking_capture=None, verdict='INCONCLUSIVE', no_email=False):
        async with self.db.session_factory() as session, session.begin():
            await DomainWorkerService(session, owner=self.owner, actor=self.owner, timezone='Asia/Bangkok',
                request_id='fixture', now=self.now).create(['https://cloak.example.com/path'], accounts or [self.account])
        class Adapter:
            def run(_self, target, domain):
                return {'recipients': [] if no_email else [{'channel': 'registrar', 'email': 'abuse@namecheap.com'}],
                    'cloaking': {'verdict': verdict, 'score': 12, 'target_url': target},
                    'whois': {'registrar': 'Namecheap'}}
        await DomainPrecheckWorker(self.db.session_factory, Adapter(), store=self.store,
            cloaking_capture=cloaking_capture, now=lambda: self.now).run_next('cloak')
        async with self.db.session_factory() as session:
            rows = await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, 'cloaking')
            return rows[0]['id'] if rows else None

    async def test_precheck_automatically_captures_pair_without_approving_or_sending(self):
        from unittest.mock import Mock
        def capture(target, decision):
            return [evidence.image(), evidence.image()], {'version': 1, 'source': 'browser_capture',
                'evidence_type': 'cloaking_comparison', 'requested_url': target, 'terminal': False,
                'cloaking': {'verdict': 'POSSIBLE', 'evidence_pair': {'profiles': ['desktop_direct', 'googlebot_smartphone']}}}, [evidence.image()]
        capture_fn = Mock(side_effect=capture)
        case = await self.missing(verdict='LIKELY', cloaking_capture=capture_fn)
        capture_fn.assert_called_once_with('https://cloak.example.com/path', 'confirmed_cloaking')
        async with self.db.session_factory() as session:
            detail = await self.service(session).detail(case)
            self.assertEqual(len(detail['capture_images']), 2)
            self.assertEqual(detail['capture_profile_names'], ['desktop_direct', 'googlebot_smartphone'])
            self.assertFalse(detail['capture_pending'])
            self.assertIsNone(detail['decision']); self.assertIsNone(detail['preview'])
            check = await session.get(Check, detail['check_id'])
            self.assertEqual(check.result['disposition'], 'cloaking_review')
            self.assertEqual(check.result['cloaking']['verdict'], 'LIKELY')
        self.assertEqual(self.messages, [])

    async def test_automatic_capture_failure_keeps_case_for_manual_review(self):
        from unittest.mock import Mock
        case = await self.missing(cloaking_capture=Mock(side_effect=RuntimeError('fixture browser failure')))
        async with self.db.session_factory() as session:
            detail = await self.service(session).detail(case)
            self.assertEqual(detail['capture_images'], [])
            self.assertEqual(detail['capture_reason'], 'capture_failed')
            self.assertEqual((await session.get(Check, detail['check_id'])).status, 'succeeded')

    async def test_precheck_without_recipient_never_runs_browser_capture(self):
        from unittest.mock import Mock
        capture = Mock()
        self.assertIsNone(await self.missing(no_email=True, verdict='LIKELY', cloaking_capture=capture))
        capture.assert_not_called()

    async def prepare(self, case, accounts=None, decision='confirmed_cloaking', uploads=None, **kwargs):
        async with self.db.session_factory() as session, session.begin():
            return (await self.service(session).preview(case, accounts or [self.account],
                uploads if uploads is not None else [evidence.image(), evidence.image()],
                decision=decision, confirmed_difference=decision == 'confirmed_cloaking', **kwargs))['preview']

    async def send(self, case, exact, sender=None):
        return await CloakingReviewSender(self.db.session_factory, self.store, sender or self.worker.sender,
            self.envelope, now=lambda: self.now).send(owner=self.owner, actor=self.owner, timezone='Asia/Bangkok',
                case_id=case, check_id=exact['check_id'], preview_id=exact['id'], revision=exact['revision'],
                fingerprint=exact['fingerprint'], confirmed=True)

    async def test_confirmed_pair_exact_send_partial_retry_and_shared_dedupe(self):
        second = await self.second_account(); case = await self.missing([self.account, second])
        exact = await self.prepare(case, [self.account, second])
        self.assertTrue(all('CONFIRMED CLOAKING' in row['text_body'] for row in exact['deliveries']))
        self.assertNotIn('Observed Phishing Behavior', exact['deliveries'][0]['text_body'])
        self.outcomes = [('sent', 'send_message', 'accepted'), ('failed', 'authenticate', 'smtp_rejected')]
        result = await self.send(case, exact)
        self.assertEqual(result['state'], 'partial')
        failed_id = self.messages[1]['Message-ID']
        result = await self.send(case, exact)
        self.assertEqual(result['state'], 'sent'); self.assertEqual(len(self.messages), 3)
        self.assertEqual(self.messages[2]['Message-ID'], failed_id)
        self.assertEqual(len(list(self.messages[0].iter_attachments())), 3)
        self.assertEqual(self.messages[0].get_body(preferencelist=('plain',)).get_content().strip(),
                         exact['deliveries'][0]['text_body'].strip())
        await self.send(case, exact); self.assertEqual(len(self.messages), 3)
        async with self.db.session_factory() as session:
            self.assertEqual(await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, 'cloaking'), [])
            self.assertEqual(len(list(await session.scalars(select(Job).where(Job.owner_user_id == self.owner)))), 1)
            check = await session.get(Check, exact['check_id'])
            self.assertEqual(check.result['disposition'], 'cloaking_review')
            self.assertEqual(check.result['cloaking']['verdict'], 'INCONCLUSIVE')

    async def test_not_cloaking_requires_separate_normal_evidence_and_scrubs_cloaking(self):
        case = await self.missing(); first = await self.prepare(case)
        with self.assertRaises(PreviewConflict):
            await self.prepare(case, decision='not_cloaking', uploads=[])
        normal = await self.prepare(case, decision='not_cloaking', uploads=[evidence.image()])
        body = normal['deliveries'][0]['text_body']
        self.assertNotIn('CONFIRMED CLOAKING', body)
        self.assertNotIn('Multi-profile Cloaking', body)
        self.assertIn('Evidence', body)
        with self.assertRaises(PreviewConflict): await self.send(case, first)
        self.assertEqual((await self.send(case, normal))['state'], 'sent')
        self.assertEqual(len(list(self.messages[0].iter_attachments())), 2)

    async def test_pair_confirmation_owner_day_atomic_upload_and_tamper(self):
        case = await self.missing()
        async with self.db.session_factory() as session:
            with self.assertRaises(ValueError):
                await self.service(session).preview(case, [self.account], [evidence.image(), evidence.image()],
                    decision='confirmed_cloaking', confirmed_difference=False)
            with self.assertRaises(LookupError): await self.service(session, self.other).detail(case)
            with self.assertRaises(PreviewConflict): await self.service(session, now=self.now + timedelta(days=1)).detail(case)
        before = {p for p in self.store.root.rglob('*') if p.is_file()}
        with self.assertRaises(ArtifactValidationError): await self.prepare(case, uploads=[evidence.image()])
        with self.assertRaises(ArtifactValidationError):
            await self.prepare(case, uploads=[evidence.image(), ImageUpload('bad.png', 'image/png', b'bad')])
        self.assertEqual(before, {p for p in self.store.root.rglob('*') if p.is_file()})
        exact = await self.prepare(case)
        async with self.db.session_factory() as session:
            artifact = await session.scalar(select(Artifact).where(Artifact.owner_user_id == self.owner,
                Artifact.kind == 'domain_worker:evidence'))
            (self.store.root / artifact.storage_key).write_bytes(b'changed')
        with self.assertRaises(Exception): await self.send(case, exact)
        self.assertEqual(self.messages, [])

    async def test_unknown_is_never_retried_or_reclassified_or_skipped(self):
        case = await self.missing(); exact = await self.prepare(case)
        self.outcomes = [('unknown', 'send_message', 'smtp_outcome_uncertain')]
        result = await self.send(case, exact)
        self.assertEqual(result['state'], 'partial')
        await self.send(case, exact); self.assertEqual(len(self.messages), 1)
        with self.assertRaises(PreviewConflict):
            await self.prepare(case, decision='not_cloaking', uploads=[evidence.image()])
        async with self.db.session_factory() as session, session.begin():
            with self.assertRaises(PreviewConflict): await self.service(session).skip(case)

    async def test_skip_is_durable_and_does_not_send(self):
        case = await self.missing()
        async with self.db.session_factory() as session, session.begin():
            self.assertEqual((await self.service(session).skip(case))['state'], 'skipped')
        async with self.db.session_factory() as session:
            self.assertEqual(await list_reviews(session, self.owner, 'Asia/Bangkok', self.now, 'cloaking'), [])
            self.assertEqual((await session.get(ReviewCase, case)).disposition, 'skip')
        with self.assertRaises(PreviewConflict): await self.prepare(case)
        self.assertEqual(self.messages, [])

    async def test_new_http_source_keeps_reviewed_evidence_and_exact_preview(self):
        case = await self.missing(); exact = await self.prepare(case)
        self.assertEqual(case, await self.missing())
        async with self.db.session_factory() as session, session.begin():
            detail = await self.service(session).detail(case)
            self.assertEqual(detail['preview']['fingerprint'], exact['fingerprint'])
            self.assertEqual(len(detail['images']), 2)
            self.assertEqual(len(detail['source_job_ids']), 2)
        reused = await self.prepare(case, uploads=[])
        self.assertEqual((await self.send(case, reused))['state'], 'sent')

    async def test_capture_is_unapproved_until_images_are_reviewed_and_promoted(self):
        case = await self.missing()
        def capture(target, decision):
            return [evidence.image(), evidence.image()], {'version': 1, 'source': 'browser_capture',
                'evidence_type': 'cloaking_comparison', 'requested_url': target, 'terminal': False,
                'cloaking': {'verdict': 'NO_SIGNAL', 'target_url': target,
                    'evidence_pair': {'profiles': ['desktop_direct', 'mobile_google'],
                        'reason': 'profile_comparison_only', 'difference_observed': False}}}, [evidence.image()]
        async with self.db.session_factory() as session, session.begin():
            detail = await self.service(session).capture(case, 'confirmed_cloaking', capture)
            self.assertEqual(len(detail['capture_images']), 2)
            self.assertIsNone(detail['preview']); self.assertIsNone(detail['decision'])
            self.assertTrue(detail['capture_comparison_only'])
            self.assertIsNone(detail['capture_reason'])
            self.assertEqual(len(list(await session.scalars(select(Artifact).where(
                Artifact.owner_user_id == self.owner, Artifact.kind == 'cloaking_review:observation')))), 1)
        exact = await self.prepare(case, uploads=[], use_capture=True)
        self.assertIn('CONFIRMED CLOAKING', exact['deliveries'][0]['text_body'])
        self.assertEqual((await self.send(case, exact))['state'], 'sent')

    async def test_api_checks_approval_permissions_csrf_owner_images_and_direct_send(self):
        import json
        from types import SimpleNamespace
        from httpx import ASGITransport, AsyncClient
        from domaincheck.api.dependencies import current_user
        from domaincheck.infrastructure.auth.sessions import hash_token
        from domaincheck.main import create_app
        from domaincheck.settings import Settings
        case = await self.missing()
        actor = SimpleNamespace(user_id=self.owner, status='active', timezone='Asia/Bangkok',
            permissions={'cloaking_review.view', 'cloaking_review.preview', 'cloaking_review.upload'},
            csrf_hash=hash_token('fixture-csrf'))
        app = create_app(Settings(_env_file=None))
        app.state.database, app.state.artifact_store, app.state.secret_envelope = self.db, self.store, self.envelope
        app.state.clock = SimpleNamespace(now=lambda: self.now)
        app.state.cloaking_review_sender = self.worker.sender
        class Archive:
            def save(_self, spec, message, message_id): return 'saved'
        app.state.cloaking_review_sent_copier = Archive()
        async def user(): return actor
        app.dependency_overrides[current_user] = user
        root = f'/api/v1/domain-worker/reviews/cloaking/{case}'
        headers = {'Origin': 'http://127.0.0.1:5173', 'X-CSRF-Token': 'fixture-csrf'}
        form = {'account_ids': json.dumps([str(self.account)]), 'decision': 'confirmed_cloaking', 'confirmed_difference': 'true'}
        files = [('files', ('a.png', evidence.image().data, 'image/png')), ('files', ('b.png', evidence.image().data, 'image/png'))]
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.get(root)).status_code, 200)
            self.assertEqual((await client.post(root + '/previews', data=form, files=files, headers=headers)).status_code, 403)
            actor.permissions.add('cloaking_review.approve')
            self.assertEqual((await client.post(root + '/previews', data=form, files=files)).status_code, 403)
            response = await client.post(root + '/previews', data=form, files=files, headers=headers)
            self.assertEqual(response.status_code, 201, response.text)
            exact = response.json()['preview']
            detail = (await client.get(root)).json()
            path = root + f"/images/{detail['images'][0]['id']}"
            self.assertEqual((await client.get(path)).status_code, 200)
            actor.user_id = self.other
            self.assertEqual((await client.get(path)).status_code, 404)
            actor.user_id = self.owner
            body = {key: exact[key] for key in ('check_id', 'revision', 'fingerprint')}
            body.update(preview_id=exact['id'], confirmed=True)
            self.assertEqual((await client.post(root + '/send', json=body, headers=headers)).status_code, 403)
            actor.permissions.add('cloaking_review.send')
            response = await client.post(root + '/send', json=body, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['state'], 'sent')
            self.assertEqual(response.json()['deliveries'][0]['sent_copy_status'], 'saved')
            self.assertEqual(len(self.messages), 1)


class ReviewCaptureTests(unittest.TestCase):
    def test_child_projects_only_representative_pair_and_keeps_internal_observations(self):
        import json
        import tempfile
        from pathlib import Path
        from domaincheck.core.review_capture import _capture
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.png'; path.write_bytes(evidence.image().data)
            observed = {'verdict': 'POSSIBLE', 'screenshots': [{'path': str(path), 'profile': 'desktop_direct'}, {'path': str(path), 'profile': 'googlebot_smartphone'}],
                'profiles': {'desktop': {'screenshot_path': str(path), 'title': 'Fixture'}}}
            with patch('domaincheck.engine.cloaking_detector.probe_playwright_cloaking', return_value=observed) as probe:
                _capture('https://example.com/path', 'confirmed_cloaking', directory)
            self.assertEqual(probe.call_args.kwargs['timeout_ms'], 15_000)
            data = json.loads((Path(directory) / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(len(data['images']), 2)
            self.assertEqual(len(data['internal_images']), 1)
            self.assertNotIn(directory, json.dumps(data['manifest']))

    def test_capture_runs_in_bounded_process_without_inheriting_output(self):
        from domaincheck.core.review_capture import capture_review
        from domaincheck.core.evidence import CaptureFailed
        import subprocess
        with patch('domaincheck.core.review_capture.subprocess.run', side_effect=subprocess.TimeoutExpired('capture', 180)) as run:
            with self.assertRaises(CaptureFailed): capture_review('https://example.com/', 'confirmed_cloaking')
        self.assertEqual(run.call_args.kwargs['timeout'], 180)
        self.assertEqual(run.call_args.kwargs['stdout'], subprocess.DEVNULL)

"""Manual cloaking dispositions using the shared exact preview and SMTP ledger."""
import importlib
from uuid import UUID, uuid4

from sqlalchemy import select

from domaincheck.application.domain_worker_preview import DomainWorkerPreviewService
from domaincheck.application.evidence_review import EvidenceReviewService, EvidenceReviewSender
from domaincheck.application.previews import PreviewConflict
from domaincheck.application.worker_review import lock_review
from domaincheck.application.worker_send import _TrackedStore
from domaincheck.core.evidence import CaptureFailed
from domaincheck.infrastructure.cache import business_day
from domaincheck.infrastructure.db.models import Artifact, AuditEvent, Check, EvidenceSet, Job, JobItem
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.ports.artifacts import ImageUpload


class CloakingPreviewService(DomainWorkerPreviewService):
    policy_version = 'cloaking-review-email-v1'
    feature = 'cloaking_review'

    async def _check(self, check_id):
        check = await OwnedRepository(self.session, Check, self.owner).get(check_id, for_update=True)
        if (check is None or check.workflow != 'domain_worker' or check.status != 'succeeded'
                or check.business_day != business_day(self.now, self.timezone)
                or check.result.get('disposition') != 'cloaking_review'
                or check.result.get('review_decision') not in {'confirmed_cloaking', 'not_cloaking'}):
            raise PreviewConflict('Review this current-day cloaking case before creating a preview')
        completed = await self.session.scalar(select(JobItem.id).join(Job,
            (Job.id == JobItem.job_id) & (Job.owner_user_id == JobItem.owner_user_id)).where(
            Job.owner_user_id == self.owner, Job.job_type == 'domain_worker_precheck',
            JobItem.state == 'succeeded', JobItem.result_metadata['check_id'].astext == str(check.id)))
        if completed is None:
            raise PreviewConflict('Wait for the source precheck to finish')
        return check

    async def _evidence(self, check):
        evidence, images = await super()._evidence(check)
        decision = check.result.get('review_decision')
        if evidence.manifest.get('review_decision') != decision:
            raise PreviewConflict('Select evidence for the current disposition')
        if decision == 'confirmed_cloaking' and (len(images) != 2 or not evidence.manifest.get('confirmed_difference')):
            raise PreviewConflict('Confirmed cloaking requires two reviewed comparison images')
        return evidence, images

    def evidence_block(self, check, manifest):
        if check.result['review_decision'] == 'not_cloaking':
            return super().evidence_block(check, manifest)
        observed = dict(manifest.get('cloaking') or {})
        observed.update(verdict=observed.get('verdict') if observed.get('verdict') in {'LIKELY', 'POSSIBLE'} else 'POSSIBLE',
                        target_url=check.requested_url)
        observed['operator_evidence'] = {'confirmed_difference': True, 'acquisition_url': check.requested_url}
        return importlib.import_module('cloaking_detector').format_evidence_block(observed, operator_confirmed=True)


class CloakingReviewService(EvidenceReviewService):
    case_type = 'cloaking'
    dispositions = {'cloaking_review'}

    def previews(self):
        return CloakingPreviewService(self.session, self.store, owner=self.owner, actor=self.actor,
            timezone=self.timezone, now=self.now, request_id=self.request_id)

    async def context(self, case_id):
        case, checks = await super().context(case_id)
        if case.state == 'skipped':
            raise PreviewConflict('This case was skipped')
        # A later HTTP-only source must not replace the evidence already reviewed
        # for this same URL/day. Keep its exact preview as the primary source.
        checks.sort(key=lambda check: bool(check.result.get('evidence_set_id')), reverse=True)
        return case, checks

    async def detail(self, case_id):
        data = await super().detail(case_id)
        case, checks = await self.context(case_id)
        data.update(decision=case.disposition, cloaking=checks[0].result.get('cloaking') or {},
                    capture_pending=bool(checks[0].result.get('browser_verification_pending')),
                    capture_reason=checks[0].result.get('cloaking_capture_reason'),
                    confirmed_difference=checks[0].result.get('review_decision') == 'confirmed_cloaking')
        candidate, images = await self.candidate(checks[0])
        browser_observation = ((candidate.manifest.get('cloaking') or {}).get('playwright') or {}) if candidate else {}
        data.update(capture_decision=candidate.manifest.get('review_decision') if candidate else None,
                    capture_images=[{'id': image.id, 'mime_type': image.mime_type, 'byte_size': image.byte_size} for image in images],
                    capture_profile_names=(browser_observation.get('evidence_pair') or {}).get('profiles', []),
                    capture_comparison_only=(browser_observation.get('evidence_pair') or {}).get('reason') == 'profile_comparison_only')
        return data

    async def candidate(self, check):
        value = check.result.get('review_capture_id')
        if not value:
            return None, []
        candidate = await OwnedRepository(self.session, EvidenceSet, self.owner).get(UUID(value))
        if not candidate or candidate.check_id != check.id or candidate.requested_url != check.requested_url:
            raise PreviewConflict('Captured evidence does not belong to this case')
        projected = type('CaptureCheck', (), {'id': check.id, 'requested_url': check.requested_url,
                                             'result': {'evidence_set_id': str(candidate.id)}})()
        return await DomainWorkerPreviewService._evidence(self.previews(), projected)

    async def capture(self, case_id, decision, capture_fn):
        if decision not in {'confirmed_cloaking', 'not_cloaking'}:
            raise ValueError('Choose a review disposition')
        await lock_review(self.session, self.owner, case_id)
        case, checks = await self.context(case_id)
        rows = await self.ledger(case, checks)
        if case.state == 'sent' or any(row.state == 'sending' for row in rows):
            raise PreviewConflict('This case is already sent or sending')
        import asyncio
        captured = await asyncio.to_thread(capture_fn, case.full_url, decision)
        uploads, manifest = captured[:2]
        internal_uploads = captured[2] if len(captured) == 3 else []
        if manifest.get('requested_url') != case.full_url or manifest.get('terminal'):
            raise CaptureFailed('invalid_capture_artifacts')
        if decision == 'confirmed_cloaking':
            manifest = {**manifest, 'cloaking': importlib.import_module('cloaking_detector').merge_playwright_result(
                checks[0].result.get('cloaking') or {}, manifest.get('cloaking') or {})}
        evidence_id = uuid4()
        images = self.store.store_evidence_images(owner_user_id=self.owner, evidence_set_id=evidence_id,
            uploads=uploads, minimum_count=2 if decision == 'confirmed_cloaking' else 1,
            maximum_count=2 if decision == 'confirmed_cloaking' else 3)
        internal = []
        try:
            if internal_uploads:
                internal = self.store.store_evidence_images(owner_user_id=self.owner, evidence_set_id=evidence_id,
                    uploads=internal_uploads, minimum_count=1, maximum_count=20)
            manifest = {**manifest, 'review_decision': decision, 'screenshots': [
                {'artifact_id': str(image.artifact_id), 'sha256': image.sha256, 'byte_size': image.byte_size} for image in images],
                'internal_observations': [{'artifact_id': str(image.artifact_id), 'sha256': image.sha256} for image in internal]}
            self.session.add(EvidenceSet(id=evidence_id, owner_user_id=self.owner, check_id=checks[0].id,
                requested_url=case.full_url, capture_mode='browser_capture', manifest=manifest))
            await self.session.flush()
            for image in images:
                self.session.add(Artifact(id=image.artifact_id, owner_user_id=self.owner, evidence_set_id=evidence_id,
                    kind='domain_worker:evidence', mime_type=image.mime_type, byte_size=image.byte_size,
                    sha256=image.sha256, storage_key=image.storage_key, immutable=True))
            for image in internal:
                self.session.add(Artifact(id=image.artifact_id, owner_user_id=self.owner, evidence_set_id=evidence_id,
                    kind='cloaking_review:observation', mime_type=image.mime_type, byte_size=image.byte_size,
                    sha256=image.sha256, storage_key=image.storage_key, immutable=True))
            checks[0].result = {**checks[0].result, 'review_capture_id': str(evidence_id),
                'browser_verification_pending': False, 'cloaking_capture_reason': None}
            checks[0].version += 1
            self.session.add(AuditEvent(owner_user_id=self.owner, actor_user_id=self.actor,
                action='cloaking_review.capture', resource_type='review_case', resource_id=case.id,
                outcome='success', request_id=self.request_id,
                metadata_allowlist={'decision': decision, 'image_count': len(images), 'internal_count': len(internal)}))
            await self.session.flush()
            return await self.detail(case_id)
        except BaseException:
            for image in [*images, *internal]:
                self.store.delete(storage_key=image.storage_key)
            raise

    async def preview(self, case_id, account_ids, uploads=(), *, decision, confirmed_difference=False, use_capture=False):
        if decision not in {'confirmed_cloaking', 'not_cloaking'}:
            raise ValueError('Choose confirmed_cloaking or not_cloaking')
        if decision == 'confirmed_cloaking' and not confirmed_difference:
            raise ValueError('Confirm that the comparison images show different content at this URL')
        await lock_review(self.session, self.owner, case_id)
        case, checks = await self.context(case_id)
        captured_manifest = None
        if use_capture:
            if uploads:
                raise ValueError('Choose captured evidence or uploaded images')
            candidate, images = await self.candidate(checks[0])
            if not candidate or candidate.manifest.get('review_decision') != decision:
                raise PreviewConflict('Capture separate evidence for the selected disposition')
            captured_manifest = candidate.manifest
            uploads = [ImageUpload('capture.png', image.mime_type, self.store.read_verified(
                storage_key=image.storage_key, expected_size=image.byte_size, expected_sha256=image.sha256)) for image in images]
        deliveries = await self.ledger(case, checks)
        if case.state == 'sent':
            raise PreviewConflict('This case is already complete')
        if case.disposition and case.disposition != decision and any(row.state in {'sent', 'sending', 'unknown'} for row in deliveries):
            raise PreviewConflict('Cannot change disposition after an accepted or uncertain email; finish or reconcile the existing report')
        if not uploads and checks[0].result.get('review_decision') != decision:
            raise PreviewConflict('Provide separate evidence for this disposition')
        original, tracked = self.store, _TrackedStore(self.store)
        self.store = tracked
        try:
            if uploads:
                minimum, maximum = (2, 2) if decision == 'confirmed_cloaking' else (1, 3)
                for check in checks:
                    evidence_id = uuid4()
                    images = tracked.store_evidence_images(owner_user_id=self.owner, evidence_set_id=evidence_id,
                        uploads=uploads, minimum_count=minimum, maximum_count=maximum)
                    manifest = {**(captured_manifest or {}), 'version': 1, 'source': 'browser_capture' if use_capture else 'manual_upload',
                        'evidence_type': (captured_manifest or {}).get('evidence_type', 'manual_upload'),
                        'requested_url': check.requested_url, 'terminal': False, 'navigation_verified': False,
                        'review_decision': decision, 'confirmed_difference': bool(confirmed_difference),
                        'cloaking': ((captured_manifest or {}).get('cloaking') or check.result.get('cloaking') or {}) if decision == 'confirmed_cloaking' else {},
                        'screenshots': [{'artifact_id': str(image.artifact_id), 'sha256': image.sha256,
                                         'byte_size': image.byte_size} for image in images]}
                    self.session.add(EvidenceSet(id=evidence_id, owner_user_id=self.owner, check_id=check.id,
                        requested_url=check.requested_url, capture_mode=manifest['source'], manifest=manifest))
                    await self.session.flush()
                    for image in images:
                        self.session.add(Artifact(id=image.artifact_id, owner_user_id=self.owner, evidence_set_id=evidence_id,
                            kind='domain_worker:evidence', mime_type=image.mime_type, byte_size=image.byte_size,
                            sha256=image.sha256, storage_key=image.storage_key, immutable=True))
                    check.result = {**check.result, 'review_decision': decision, 'evidence_set_id': str(evidence_id)}
                    check.version += 1
                case.evidence_version += 1
            case.disposition = decision
            case.version += 1
            self.session.add(AuditEvent(owner_user_id=self.owner, actor_user_id=self.actor,
                action='cloaking_review.preview', resource_type='review_case', resource_id=case.id,
                outcome='success', request_id=self.request_id,
                metadata_allowlist={'decision': decision, 'image_count': len(uploads)}))
            await self.session.flush()
            return await super().preview(case_id, account_ids, ())
        except BaseException:
            for artifact in tracked.created:
                original.delete(storage_key=artifact.storage_key)
            raise
        finally:
            self.store = original

    async def skip(self, case_id):
        await lock_review(self.session, self.owner, case_id)
        case, checks = await self.context(case_id)
        rows = await self.ledger(case, checks)
        if any(row.state in {'sent', 'sending', 'unknown'} for row in rows):
            raise PreviewConflict('This case has accepted or uncertain emails; finish or reconcile it instead of skipping')
        case.state, case.disposition = 'skipped', 'skip'
        case.version += 1
        self.session.add(AuditEvent(owner_user_id=self.owner, actor_user_id=self.actor,
            action='cloaking_review.skip', resource_type='review_case', resource_id=case.id,
            outcome='success', request_id=self.request_id, metadata_allowlist={}))
        await self.session.flush()
        return {'id': case.id, 'state': case.state}


class CloakingReviewSender(EvidenceReviewSender):
    service_type = CloakingReviewService

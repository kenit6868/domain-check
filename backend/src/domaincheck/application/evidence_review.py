"""Owned, exact-preview and direct SMTP workflow for manual normal evidence review."""
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

from domaincheck.application.deliveries import DeliveryService
from domaincheck.application.domain_worker_preview import DomainWorkerPreviewService
from domaincheck.application.mail_settings import MailSettingsService
from domaincheck.application.previews import PreviewConflict, PreviewService
from domaincheck.application.worker_review import lock_review, review_checks, review_lock_key, upload_review_images
from domaincheck.application.worker_send import _TrackedStore, compose_message, daily_identity, delivery_key
from domaincheck.core.mail.connectivity import SmtpConnectionSpec
from domaincheck.core.worker_precheck import valid_recipients
from domaincheck.domain.deliveries import DeliveryBusinessIdentity
from domaincheck.infrastructure.cache import business_day
from domaincheck.infrastructure.db.models import Artifact, AuditEvent, Delivery, DeliveryAttempt, Job, JobItem, PreviewPlan, ReviewCase, SenderAccount, User
from domaincheck.infrastructure.db.owned import OwnedRepository


class EvidenceReviewService:
    case_type = 'domain_evidence'
    dispositions = {'evidence_review', 'ready', 'excluded_already_sent'}
    def __init__(self, session, store, *, owner, actor, timezone, now, request_id='evidence-review'):
        self.session, self.store, self.owner, self.actor = session, store, owner, actor
        self.timezone, self.now, self.request_id = timezone, now, request_id

    def previews(self):
        return DomainWorkerPreviewService(self.session, self.store, owner=self.owner, actor=self.actor,
            timezone=self.timezone, now=self.now, request_id=self.request_id)

    async def legacy_scope(self, checks):
        """Only old jobs without a recorded sending scope allow manual scope recovery."""
        scopes = list(await self.session.scalars(select(Job.account_scope).join(JobItem,
            (JobItem.job_id == Job.id) & (JobItem.owner_user_id == Job.owner_user_id)).where(
            Job.owner_user_id == self.owner, Job.job_type == 'domain_worker_precheck',
            JobItem.result_metadata['check_id'].astext.in_([str(check.id) for check in checks]))))
        return bool(scopes) and not any(scopes)

    async def context(self, case_id):
        case = await OwnedRepository(self.session, ReviewCase, self.owner).get(case_id, for_update=True)
        if case is None or case.case_type != self.case_type:
            raise LookupError('Evidence review case not found')
        if case.business_day != business_day(self.now, self.timezone):
            raise PreviewConflict('This case belongs to an earlier day; run a new precheck')
        cloaking = await self.session.scalar(select(ReviewCase.id).where(ReviewCase.owner_user_id == self.owner,
            ReviewCase.canonical_url_key == case.canonical_url_key, ReviewCase.business_day == case.business_day,
            ReviewCase.case_type == 'cloaking', ReviewCase.state.in_(['pending', 'partial', 'failed', 'ready', 'sending'])).limit(1))
        if cloaking and self.case_type != 'cloaking':
            raise PreviewConflict('This URL needs Cloaking Review before a report can be sent')
        checks = [check for check in await review_checks(self.session, self.owner, case)
            if check.business_day == case.business_day and check.requested_url == case.full_url
            and check.result.get('disposition') in self.dispositions
            and valid_recipients(check.result.get('recipients') or [])]
        if not checks:
            raise PreviewConflict('No current-day normal report with valid recipients remains in this case')
        return case, checks

    async def ledger(self, case, checks):
        keys = {delivery_key(case.business_day, check.domain, recipient) for check in checks
                for recipient in valid_recipients(check.result.get('recipients') or [])}
        accounts = [UUID(value) for value in case.required_account_ids]
        rows = list(await self.session.scalars(select(Delivery).where(Delivery.owner_user_id == self.owner,
            Delivery.sender_account_id.in_(accounts), Delivery.business_key.in_(keys)).with_for_update()))
        for row in rows:
            if row.state == 'sending' and row.claim_expires_at and row.claim_expires_at < self.now:
                attempts = list(await self.session.scalars(select(DeliveryAttempt).where(
                    DeliveryAttempt.owner_user_id == self.owner, DeliveryAttempt.delivery_id == row.id,
                    DeliveryAttempt.state == 'intent').with_for_update()))
                for attempt in attempts:
                    attempt.state, attempt.smtp_stage, attempt.finished_at = 'unknown', 'checkpoint_missing', self.now
                    attempt.sanitized_result = {'reason_code': 'claim_expired_after_intent'}
                row.state, row.claim_token, row.claim_expires_at = 'unknown', None, None
                row.version += 1
        states = {(row.sender_account_id, row.business_key): row.state for row in rows}
        expected = [states.get((account, key), 'pending') for account in accounts for key in keys]
        if expected and all(state == 'sent' for state in expected):
            case.state = 'sent'
        elif 'sending' in expected:
            case.state = 'sending'
        elif 'sent' in expected or 'unknown' in expected:
            case.state = 'partial'
        elif 'failed' in expected:
            case.state = 'failed'
        else:
            case.state = 'ready' if checks[0].result.get('evidence_set_id') else 'pending'
        await self.session.flush()
        return rows

    async def detail(self, case_id):
        case, checks = await self.context(case_id)
        # Older prechecks did not record a sending scope. Expose owned choices,
        # but bind them only through the explicit, locked preview action.
        rows = await self.ledger(case, checks)
        check = checks[0]
        legacy = await self.legacy_scope(checks)
        accounts = list(await self.session.scalars(select(SenderAccount).where(
            SenderAccount.owner_user_id == self.owner,
            SenderAccount.id.in_([UUID(value) for value in case.required_account_ids])
            if case.required_account_ids and not legacy else SenderAccount.enabled.is_(True))))
        sources = list(await self.session.scalars(select(Job.id).join(JobItem,
            (JobItem.job_id == Job.id) & (JobItem.owner_user_id == Job.owner_user_id)).where(
            Job.owner_user_id == self.owner, Job.job_type == 'domain_worker_precheck',
            JobItem.result_metadata['check_id'].astext.in_([str(check.id) for check in checks]))))
        images = []
        if check.result.get('evidence_set_id'):
            evidence, artifacts = await self.previews()._evidence(check)
            images = [{'id': image.id, 'mime_type': image.mime_type, 'byte_size': image.byte_size} for image in artifacts]
        plan = await self.session.scalar(select(PreviewPlan).where(PreviewPlan.owner_user_id == self.owner,
            PreviewPlan.resource_type == 'check', PreviewPlan.resource_id == check.id)
            .order_by(PreviewPlan.revision.desc()).limit(1))
        exact = None
        if plan:
            try:
                exact = await self.previews().get(check.id, plan.id)
            except (ValueError, FileNotFoundError):
                pass  # Evidence remains reusable even when an earlier preview has expired.
        attempts, counts = {}, {}
        for attempt in await self.session.scalars(select(DeliveryAttempt).where(DeliveryAttempt.owner_user_id == self.owner,
            DeliveryAttempt.delivery_id.in_([row.id for row in rows])).order_by(DeliveryAttempt.attempt_number.desc())):
            attempts.setdefault(attempt.delivery_id, attempt)
            counts[attempt.delivery_id] = counts.get(attempt.delivery_id, 0) + 1
        by_key = {(row.sender_account_id, row.business_key): row for row in rows}
        routes = {delivery_key(case.business_day, source.domain, recipient): recipient for source in checks
                  for recipient in valid_recipients(source.result.get('recipients') or [])}
        deliveries = []
        for account in case.required_account_ids:
            for key, recipient in routes.items():
                row = by_key.get((UUID(account), key))
                attempt = attempts.get(row.id) if row else None
                deliveries.append({'id': row.id if row else None, 'sender_account_id': account,
                    'recipient': recipient['email'], 'channel': recipient['channel'], 'state': row.state if row else 'pending',
                    'attempt_count': counts.get(row.id, 0) if row else 0,
                    'error_code': attempt.sanitized_result.get('reason_code') if attempt and attempt.state in {'failed', 'unknown'} else None,
                    'sent_copy_status': attempt.sanitized_result.get('sent_copy_status') if attempt else None})
        return {'id': case.id, 'check_id': check.id, 'state': case.state, 'requested_url': case.full_url,
            'evidence_version': case.evidence_version, 'accounts': [{'id': account.id, 'sender_email': account.smtp_username,
                'enabled': account.enabled} for account in accounts], 'sender_account_ids': case.required_account_ids,
            'recipients': valid_recipients(check.result.get('recipients') or []),
            'evidence_reason': check.result.get('evidence_reason'), 'source_job_ids': list(dict.fromkeys(sources)),
            'images': images, 'preview': exact, 'deliveries': deliveries}

    async def preview(self, case_id, account_ids, uploads=()):
        await lock_review(self.session, self.owner, case_id)
        case, checks = await self.context(case_id)
        if not case.required_account_ids or await self.legacy_scope(checks):
            selected = {str(value) for value in account_ids}
            owned = set(map(str, await self.session.scalars(select(SenderAccount.id).where(
                SenderAccount.owner_user_id == self.owner, SenderAccount.enabled.is_(True),
                SenderAccount.id.in_(account_ids)))))
            if not selected or selected != owned:
                raise PreviewConflict('Choose enabled sender accounts belonging to your account')
            case.required_account_ids = sorted(set(case.required_account_ids or []) | selected)
            case.version += 1
            self.session.add(AuditEvent(owner_user_id=self.owner, actor_user_id=self.actor,
                action='domain_evidence_review.bind_legacy_scope', resource_type='review_case',
                resource_id=case.id, outcome='success', request_id=self.request_id,
                metadata_allowlist={'sender_account_ids': sorted(selected)}))
            await self.session.flush()
        if not account_ids or not set(map(str, account_ids)).issubset(set(case.required_account_ids)):
            raise PreviewConflict('Choose only sender accounts belonging to the source case')
        if case.state == 'sent':
            raise PreviewConflict('This case is already complete')
        original, tracked = self.store, _TrackedStore(self.store)
        self.store = tracked
        try:
            if uploads:
                await upload_review_images(self.session, tracked, self.owner, self.timezone, self.now, case_id, uploads)
                case, checks = await self.context(case_id)
            check = checks[0]
            recipients = valid_recipients([recipient for source in checks for recipient in source.result.get('recipients') or []])
            if check.result.get('recipients') != recipients:
                check.result = {**check.result, 'recipients': recipients}
                check.version += 1
                await self.session.flush()
            exact = await self.previews().create(check.id, account_ids, review_scope=case.required_account_ids)
            return {'preview': exact, 'case_id': case.id, 'evidence_version': case.evidence_version}
        except Exception:
            for artifact in tracked.created:
                original.delete(storage_key=artifact.storage_key)
            raise
        finally:
            self.store = original


class EvidenceReviewSender:
    service_type = EvidenceReviewService
    def __init__(self, sessions, store, sender, envelope, *, now=lambda: datetime.now(UTC), sent_copier=None):
        self.sessions, self.store, self.sender, self.envelope = sessions, store, sender, envelope
        self.now, self.sent_copier = now, sent_copier

    async def send(self, *, owner, actor, timezone, case_id, check_id, preview_id, revision, fingerprint, confirmed):
        if not confirmed:
            raise PreviewConflict('Confirm the exact preview before sending')
        key = review_lock_key(owner, case_id)
        async with self.sessions.kw['bind'].connect() as connection:
            acquired = await connection.scalar(select(func.pg_try_advisory_lock(key)))
            await connection.commit()
            if not acquired:
                raise PreviewConflict('This evidence case is being processed in another session')
            try:
                return await self._send(owner, actor, timezone, case_id, check_id, preview_id, revision, fingerprint)
            finally:
                try:
                    await connection.execute(select(func.pg_advisory_unlock(key)))
                    await connection.commit()
                except BaseException:
                    await connection.invalidate()
                    raise

    async def _send(self, owner, actor, timezone, case_id, check_id, preview_id, revision, fingerprint):
        async with self.sessions() as session, session.begin():
            review = self.service_type(session, self.store, owner=owner, actor=actor, timezone=timezone, now=self.now())
            case, checks = await review.context(case_id)
            await review.ledger(case, checks)
            if case.state == 'sent':
                return await review.detail(case_id)
            if check_id not in {check.id for check in checks}:
                raise PreviewConflict('Preview does not belong to this case')
            exact = await review.previews().get(check_id, preview_id)
            if exact['revision'] != revision or exact['fingerprint'] != fingerprint:
                raise PreviewConflict('Preview changed; create a new preview')
            if not {row['sender_account_id'] for row in exact['deliveries']}.issubset(set(case.required_account_ids)):
                raise PreviewConflict('Preview accounts no longer belong to this case')
            plans = PreviewService(session, self.store, owner_user_id=owner, actor_user_id=actor, request_id='evidence-review-send')
            await plans.approve(preview_id, expected_revision=revision, confirmed=True, now=self.now())
            ledger = DeliveryService(session, self.store, owner_user_id=owner, actor_user_id=actor, request_id='evidence-review-send')
            check = next(check for check in checks if check.id == check_id)
            rows = await ledger.materialize(preview_id=preview_id, expected_revision=revision, workflow='domain_worker',
                identities=[DeliveryBusinessIdentity(index, daily_identity(check.business_day, check.domain))
                            for index in range(len(exact['deliveries']))], now=self.now())
            for row in rows:
                if row.state == 'failed':
                    await ledger.retry_failed(row.id, expected_version=row.version, now=self.now())
            ids = [row.id for row in rows]
            user = await session.get(User, actor)
            if not user or user.status != 'active':
                raise PreviewConflict('Sender authorization is no longer active')
            authz_version = user.authz_version
            case.state = 'sending'
        errors = []
        for delivery_id in ids:
            try:
                async with self.sessions() as session, session.begin():
                    review = self.service_type(session, self.store, owner=owner, actor=actor, timezone=timezone, now=self.now())
                    await review.context(case_id)
                    user = await session.get(User, actor)
                    if not user or user.status != 'active' or user.authz_version != authz_version:
                        raise PreviewConflict('Authorization changed during sending')
                    ledger = DeliveryService(session, self.store, owner_user_id=owner, actor_user_id=actor, request_id='evidence-review-send')
                    row = await ledger.get(delivery_id)
                    if row.state != 'pending':
                        continue  # SENT and uncertain outcomes never gain a new SMTP opportunity.
                    if row.preview_id != preview_id:
                        raise PreviewConflict('Delivery plan changed in another session; create a new preview')
                    mail = MailSettingsService(session, owner_user_id=owner, actor_user_id=actor, request_id='evidence-review-send', envelope=self.envelope)
                    account = await mail.get_account(row.sender_account_id)
                    secrets = mail._read_secrets(account)
                    if not account.enabled or not secrets.get('smtp_password'):
                        raise PreviewConflict('Sender account is unavailable')
                    spec = SmtpConnectionSpec(host=account.smtp_host, port=account.smtp_port, username=account.smtp_username,
                        password=secrets['smtp_password'], transport='implicit_tls' if account.smtp_port == 465 else account.smtp_transport,
                        timeout_seconds=60, proxy=mail._proxy_spec(account, secrets))
                    intent = await ledger.claim_intent(row.id, expected_version=row.version, lease_seconds=300, now=self.now())
                    blobs = []
                    for binding in intent.artifacts:
                        artifact = await OwnedRepository(session, Artifact, owner).get(UUID(binding['artifact_id']))
                        blobs.append((binding, self.store.read_verified(storage_key=artifact.storage_key,
                            expected_size=artifact.byte_size, expected_sha256=artifact.sha256)))
                    message = compose_message(intent, blobs)
                from domaincheck.application.delivery_dispatch import dispatch_intent
                await dispatch_intent(self.sessions, self.store, self.sender, self.envelope, owner=owner, actor=actor,
                    intent=intent, spec=spec, message=message, now=self.now, sent_copier=self.sent_copier)
            except Exception:
                errors.append('delivery_validation_or_checkpoint_failed')
        async with self.sessions() as session, session.begin():
            result = await self.service_type(session, self.store, owner=owner, actor=actor, timezone=timezone, now=self.now()).detail(case_id)
            return {**result, 'errors': errors}

"""Owned provider inbox, source-linked exact replies and durable SMTP delivery."""
import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from dataclasses import replace
from email.utils import parseaddr
from uuid import UUID, uuid4

from domaincheck.engine import provider_replies as legacy
from sqlalchemy import func, select

from domaincheck.application.deliveries import DeliveryService
from domaincheck.application.mail_settings import MailSettingsService
from domaincheck.application.previews import PreviewConflict, PreviewService
from domaincheck.application.signature_logo import load_profile_logo
from domaincheck.application.worker_review import lock_review, review_lock_key
from domaincheck.application.worker_send import _TrackedStore
from domaincheck.core.email_content import html_report_body
from domaincheck.core.investigation import validate_target
from domaincheck.core.mail.connectivity import ImapConnectionSpec, SmtpConnectionSpec
from domaincheck.core.mail.sending import compose_message
from domaincheck.domain.deliveries import DeliveryBusinessIdentity
from domaincheck.domain.previews import PreparedDelivery, canonical_payload_bytes
from domaincheck.infrastructure.cache import business_day
from domaincheck.infrastructure.db.models import Artifact, AuditEvent, Check, Delivery, DeliveryAttempt, EvidenceSet, PreviewPlan, ReviewCase, ReviewSnapshot, User, UserSettings
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.ports.artifacts import ImageUpload

POLICY = 'provider-threaded-reply-v1'


class ProviderRepliesService:
    def __init__(self, session, store, *, owner, actor, timezone, now, envelope=None):
        self.session, self.store, self.owner, self.actor = session, store, owner, actor
        self.timezone, self.now, self.envelope = timezone, now, envelope

    def plans(self):
        return PreviewService(self.session, self.store, owner_user_id=self.owner, actor_user_id=self.actor, request_id='provider-replies')

    @staticmethod
    def reply_state(delivery, snapshot):
        if delivery and delivery.state in {'sent', 'sending', 'unknown'}: return delivery.state
        if snapshot.sanitized_state.get('sent_match_id'): return 'replied_in_sent'
        return delivery.state if delivery else 'pending'

    @staticmethod
    def matched(snapshot):
        return bool(snapshot.sanitized_state.get('linked_delivery_id') or snapshot.sanitized_state.get('sent_source_message_id'))

    @staticmethod
    def reply_block_reason(mail):
        if mail.provider != 'cloudflare': return 'provider_not_supported'
        if not legacy.needs_reply(mail): return 'no_reply_requested'
        if mail.channel != 'email' or not mail.reply_to: return 'email_reply_unavailable'
        if not re.fullmatch(r'<[^<>\s]+>', mail.message_id): return 'missing_message_id'
        return None

    def mail_settings(self):
        return MailSettingsService(self.session, owner_user_id=self.owner, actor_user_id=self.actor,
            request_id='provider-replies', envelope=self.envelope)

    async def connection(self, account_id):
        account = await self.mail_settings().get_account(account_id)
        secrets = self.mail_settings()._read_secrets(account)
        config = account.imap_config or {}
        username = str(config.get('username') or account.smtp_username)
        password = secrets.get('imap_password') or (secrets.get('smtp_password') if username == account.smtp_username else None)
        if not account.enabled or not config.get('host') or not password:
            raise PreviewConflict('Configure an enabled IMAP account before syncing')
        return account, ImapConnectionSpec(str(config['host']), int(config.get('port') or 993), username, password, 60)

    async def context(self, case_id):
        case = await OwnedRepository(self.session, ReviewCase, self.owner).get(case_id, for_update=True)
        if case is None or case.case_type != 'provider_reply': raise LookupError('Provider message not found')
        snapshot = await self.session.scalar(select(ReviewSnapshot).where(ReviewSnapshot.owner_user_id == self.owner,
            ReviewSnapshot.feature == 'provider_replies', ReviewSnapshot.context_key == str(case.id)).with_for_update())
        if snapshot is None: raise LookupError('Provider message not found')
        return case, snapshot

    async def original(self, snapshot):
        artifact = await OwnedRepository(self.session, Artifact, self.owner).get(UUID(snapshot.sanitized_state['artifact_id']))
        if artifact is None or artifact.kind != 'provider_replies:message': raise PreviewConflict('Original message unavailable')
        payload = json.loads(self.store.read_verified(storage_key=artifact.storage_key,
            expected_size=artifact.byte_size, expected_sha256=artifact.sha256))
        return legacy.ProviderMail(**payload['mail']), payload

    async def match(self, account_id, payload):
        refs = set(payload.get('in_reply_to') or []) | set(payload.get('references') or [])
        direct = await self.session.scalar(select(Delivery).where(Delivery.owner_user_id == self.owner,
            Delivery.sender_account_id == account_id, Delivery.state == 'sent', Delivery.message_id.in_(refs))
            .order_by(Delivery.created_at.desc()).limit(1)) if refs else None
        if direct: return direct.id, 'message_id'
        mail = legacy.ProviderMail(**payload['mail'])
        target = legacy.extract_reply_context(mail)['reported_url']
        try: domain = validate_target(target)[1]
        except ValueError: return None, None
        candidates = list(await self.session.scalars(select(Delivery).join(PreviewPlan,
            (PreviewPlan.id == Delivery.preview_id) & (PreviewPlan.owner_user_id == Delivery.owner_user_id)).join(Check,
            (Check.id == PreviewPlan.resource_id) & (Check.owner_user_id == PreviewPlan.owner_user_id)).where(
            Delivery.owner_user_id == self.owner, Delivery.sender_account_id == account_id, Delivery.state == 'sent',
            Delivery.workflow == 'domain_worker', PreviewPlan.resource_type == 'check', Check.domain == domain)
            .order_by(Delivery.created_at.desc()).limit(100)))
        sender = parseaddr(mail.sender)[1].lower()
        for row in candidates:
            same_provider = mail.provider != 'unknown' and legacy.detect_provider(row.recipient, '')[0] == mail.provider
            if row.recipient.lower() == sender or same_provider: return row.id, 'domain_provider'
        return None, None

    async def ingest(self, account_id, result):
        original, tracked = self.store, _TrackedStore(self.store)
        self.store = tracked
        try: return await self._ingest(account_id, result)
        except BaseException:
            for blob in tracked.created: original.delete(storage_key=blob.storage_key)
            raise
        finally: self.store = original

    async def _ingest(self, account_id, result):
        account = await self.mail_settings().get_account(account_id)
        imported = 0
        for payload in result['messages']:
            mail = legacy.ProviderMail(**payload['mail'])
            location = {'mailbox': mail.source_mailbox, 'uid': mail.uid, 'uidvalidity': payload['uidvalidity']}
            identity = mail.message_id if re.fullmatch(r'<[^<>\s]+>', mail.message_id) else json.dumps(location, sort_keys=True)
            digest = hashlib.sha256(f'{account.id}:{identity}'.encode()).hexdigest()
            await self.session.execute(select(func.pg_advisory_xact_lock(review_lock_key(self.owner, digest))))
            case = await self.session.scalar(select(ReviewCase).where(ReviewCase.owner_user_id == self.owner,
                ReviewCase.case_type == 'provider_reply', ReviewCase.canonical_url_key == digest).with_for_update())
            if case:
                _, snapshot = await self.context(case.id)
                snapshot.sanitized_state = {**snapshot.sanitized_state, 'location': location, 'seen': bool(payload.get('seen'))}
                if not snapshot.sanitized_state.get('linked_delivery_id'):
                    link, reason = await self.match(account_id, payload)
                    if link:
                        snapshot.sanitized_state = {**snapshot.sanitized_state,
                            'linked_delivery_id': str(link), 'match_reason': reason}
                        case.version += 1
                continue
            link, reason = await self.match(account_id, payload)
            target = legacy.extract_reply_context(mail)['reported_url']
            try: target = validate_target(target)[0]
            except ValueError: target = ''
            if not target and link:
                sent = await OwnedRepository(self.session, Delivery, self.owner).get(link)
                plan = await OwnedRepository(self.session, PreviewPlan, self.owner).get(sent.preview_id)
                if plan.resource_type == 'check':
                    checked = await OwnedRepository(self.session, Check, self.owner).get(plan.resource_id)
                    if checked: target = checked.requested_url
            case = ReviewCase(owner_user_id=self.owner, case_type='provider_reply', canonical_url_key=digest,
                full_url=target, business_day=business_day(datetime.fromisoformat(payload['received_at']), self.timezone),
                timezone=self.timezone, state='pending', required_account_ids=[str(account_id)])
            self.session.add(case); await self.session.flush()
            blob = self.store.store_preview_payload(owner_user_id=self.owner, preview_id=uuid4(), data=canonical_payload_bytes(payload))
            self.session.add(Artifact(id=blob.artifact_id, owner_user_id=self.owner, kind='provider_replies:message',
                mime_type=blob.mime_type, byte_size=blob.byte_size, sha256=blob.sha256, storage_key=blob.storage_key, immutable=True))
            self.session.add(ReviewSnapshot(owner_user_id=self.owner, feature='provider_replies', context_key=str(case.id),
                business_day=case.business_day, sanitized_state={'account_id': str(account_id), 'artifact_id': str(blob.artifact_id),
                    'subject': mail.subject[:998], 'sender': mail.sender[:500], 'provider': mail.provider,
                    'provider_label': mail.provider_label, 'request_type': mail.request_type, 'request_label': mail.request_label,
                    'received_at': payload['received_at'], 'location': location, 'seen': bool(payload.get('seen')),
                    'linked_delivery_id': str(link) if link else None, 'match_reason': reason,
                    'needs_reply': legacy.needs_reply(mail), 'channel': mail.channel, 'evidence_id': None}))
            imported += 1
        if result.get('sent_headers'):
            snapshots = list(await self.session.scalars(select(ReviewSnapshot).where(ReviewSnapshot.owner_user_id == self.owner,
                ReviewSnapshot.feature == 'provider_replies', ReviewSnapshot.sanitized_state['account_id'].astext == str(account_id)).limit(500)))
            for snapshot in snapshots:
                mail, original = await self.original(snapshot)
                refs = set(original.get('references') or []) | set(original.get('in_reply_to') or [])
                source = next((header for header in result['sent_headers'] if header['message_id'] in refs and any(
                    recipient in {parseaddr(mail.sender)[1].lower(), mail.reply_to.lower()} or
                    (mail.provider != 'unknown' and legacy.detect_provider(recipient, '')[0] == mail.provider)
                    for recipient in header['recipients'])), None)
                if source and not self.matched(snapshot):
                    snapshot.sanitized_state = {**snapshot.sanitized_state,
                        'sent_source_message_id': source['message_id'], 'match_reason': 'sent_thread'}
                    case = await OwnedRepository(self.session, ReviewCase, self.owner).get(UUID(snapshot.context_key))
                    case.version += 1
                found = next((header for header in result['sent_headers'] if mail.message_id and
                    mail.message_id in header['references'] and mail.reply_to.lower() in header['recipients']), None)
                if found: snapshot.sanitized_state = {**snapshot.sanitized_state, 'sent_match_id': found['message_id']}
        self.session.add(AuditEvent(owner_user_id=self.owner, actor_user_id=self.actor, action='provider_replies.sync',
            resource_type='sender_account', resource_id=account.id, outcome='success', request_id='provider-replies',
            metadata_allowlist={'imported': imported, 'folder_count': len(result['folders'])}))
        await self.session.flush()
        return {'imported': imported, 'folders': result['folders']}

    async def delivery(self, case):
        row = await self.session.scalar(select(Delivery).join(PreviewPlan,
            (PreviewPlan.id == Delivery.preview_id) & (PreviewPlan.owner_user_id == Delivery.owner_user_id)).where(
            Delivery.owner_user_id == self.owner, Delivery.workflow == 'provider_replies',
            PreviewPlan.resource_type == 'review_case', PreviewPlan.resource_id == case.id).with_for_update())
        if row and row.state == 'sending' and row.claim_expires_at and row.claim_expires_at < self.now:
            for attempt in await self.session.scalars(select(DeliveryAttempt).where(DeliveryAttempt.owner_user_id == self.owner,
                DeliveryAttempt.delivery_id == row.id, DeliveryAttempt.state == 'intent').with_for_update()):
                attempt.state, attempt.smtp_stage, attempt.finished_at = 'unknown', 'checkpoint_missing', self.now
                attempt.sanitized_result = {'reason_code': 'claim_expired_after_intent'}
            row.state, row.claim_token, row.claim_expires_at = 'unknown', None, None
            row.version += 1
        return row

    async def list(self, account_id, date_from, date_to):
        await self.mail_settings().get_account(account_id)
        rows = (await self.session.execute(select(ReviewCase, ReviewSnapshot).join(ReviewSnapshot,
            (ReviewSnapshot.context_key == func.cast(ReviewCase.id, ReviewSnapshot.context_key.type)) &
            (ReviewSnapshot.owner_user_id == ReviewCase.owner_user_id)).where(ReviewCase.owner_user_id == self.owner,
            ReviewCase.case_type == 'provider_reply', ReviewSnapshot.feature == 'provider_replies',
            ReviewSnapshot.sanitized_state['account_id'].astext == str(account_id),
            ReviewCase.business_day.between(date_from, date_to)).order_by(ReviewCase.created_at.desc()).limit(500))).all()
        result = []
        for case, snapshot in rows:
            delivery = await self.delivery(case)
            result.append({'id': case.id, 'state': self.reply_state(delivery, snapshot), 'requested_url': case.full_url,
                           'matched': self.matched(snapshot),
                           **{key: value for key, value in snapshot.sanitized_state.items() if key not in {'artifact_id', 'evidence_id'}}})
        return result

    async def evidence(self, case, snapshot):
        evidence_id = snapshot.sanitized_state.get('evidence_id')
        if not evidence_id: return None, []
        evidence = await OwnedRepository(self.session, EvidenceSet, self.owner).get(UUID(evidence_id))
        if not evidence or evidence.case_id != case.id: raise PreviewConflict('Evidence does not belong to this reply')
        images = list(await self.session.scalars(select(Artifact).where(Artifact.owner_user_id == self.owner,
            Artifact.evidence_set_id == evidence.id, Artifact.kind == 'provider_replies:evidence')))
        shots = evidence.manifest.get('screenshots') or []
        if len(images) != len(shots) or not 1 <= len(images) <= 3: raise PreviewConflict('Evidence manifest is incomplete')
        for image in images:
            if not any(s.get('artifact_id') == str(image.id) and s.get('sha256') == image.sha256 for s in shots):
                raise PreviewConflict('Evidence bindings changed')
            self.store.read_verified(storage_key=image.storage_key, expected_size=image.byte_size, expected_sha256=image.sha256)
        return evidence, images

    async def detail(self, case_id):
        case, snapshot = await self.context(case_id)
        mail, payload = await self.original(snapshot)
        mail.source_mailbox = snapshot.sanitized_state['location']['mailbox']
        mail.uid = str(snapshot.sanitized_state['location']['uid'])
        row = await self.delivery(case)
        evidence, images = await self.evidence(case, snapshot)
        plan = await self.session.scalar(select(PreviewPlan).where(PreviewPlan.owner_user_id == self.owner,
            PreviewPlan.resource_type == 'review_case', PreviewPlan.resource_id == case.id).order_by(PreviewPlan.revision.desc()).limit(1))
        exact = None
        if plan:
            try:
                resolved = await self.plans().get_exact(plan.id, now=self.now)
                exact = {'id': plan.id, 'revision': plan.revision, 'fingerprint': plan.fingerprint,
                    'expires_at': plan.expires_at, 'deliveries': resolved.payload['deliveries']}
            except ValueError: pass
        attempts = list(await self.session.scalars(select(DeliveryAttempt).where(DeliveryAttempt.owner_user_id == self.owner,
            DeliveryAttempt.delivery_id == row.id).order_by(DeliveryAttempt.attempt_number.desc()))) if row else []
        profile = await self.session.get(UserSettings, self.owner)
        context = {**legacy.extract_reply_context(mail), 'reported_url': case.full_url or legacy.extract_reply_context(mail)['reported_url']}
        suggested_subject, suggested_body, _ = legacy.build_reply(replace(mail, provider_label='Provider') if mail.provider == 'unknown' else mail, {**context,
            'contact_name': getattr(profile, 'contact_name', 'Reporter'),
            'contact_email': getattr(profile, 'contact_email', '')})
        account = await self.mail_settings().get_account(UUID(snapshot.sanitized_state['account_id']))
        return {'id': case.id, 'account_id': snapshot.sanitized_state['account_id'], 'state': self.reply_state(row, snapshot),
            'reply_supported': mail.provider == 'cloudflare', 'reply_block_reason': self.reply_block_reason(mail),
            'sender_email': account.smtp_username, 'suggestion': {'subject': suggested_subject, 'body': suggested_body},
            'reading_aid': legacy.provider_message_vi(mail),
            'capture_summary': {key: (evidence.manifest.get('control') or {}).get(key) or '' for key in ['label', 'resolved_destination']} if evidence else None,
            'mail': mail.to_dict(), 'context': context, 'matched': self.matched(snapshot),
            'match_reason': snapshot.sanitized_state.get('match_reason'), 'needs_reply': legacy.needs_reply(mail),
            'seen': bool(snapshot.sanitized_state.get('seen')), 'preview': exact,
            'images': [{'id': image.id, 'mime_type': image.mime_type, 'byte_size': image.byte_size} for image in images],
            'attempt_count': len(attempts), 'error_code': attempts[0].sanitized_result.get('reason_code') if attempts else None,
            'sent_copy_status': attempts[0].sanitized_result.get('sent_copy_status') if attempts else None}

    async def attach(self, case, snapshot, uploads, manifest):
        evidence_id = uuid4()
        images = self.store.store_evidence_images(owner_user_id=self.owner, evidence_set_id=evidence_id,
            uploads=uploads, minimum_count=1, maximum_count=3)
        manifest = {**manifest, 'version': 1, 'requested_url': case.full_url, 'screenshots': [
            {'artifact_id': str(image.artifact_id), 'sha256': image.sha256, 'byte_size': image.byte_size} for image in images]}
        self.session.add(EvidenceSet(id=evidence_id, owner_user_id=self.owner, case_id=case.id,
            requested_url=case.full_url, capture_mode=manifest['source'], manifest=manifest))
        await self.session.flush()
        for image in images:
            self.session.add(Artifact(id=image.artifact_id, owner_user_id=self.owner, evidence_set_id=evidence_id,
                kind='provider_replies:evidence', mime_type=image.mime_type, byte_size=image.byte_size,
                sha256=image.sha256, storage_key=image.storage_key, immutable=True))
        snapshot.sanitized_state = {**snapshot.sanitized_state, 'evidence_id': str(evidence_id)}
        case.evidence_version += 1; case.version += 1
        await self.session.flush()

    async def capture(self, case_id, capture_fn, target=None):
        await lock_review(self.session, self.owner, case_id)
        case, snapshot = await self.context(case_id)
        row = await self.delivery(case)
        if snapshot.sanitized_state.get('sent_match_id'): raise PreviewConflict('A matching reply is already present in Sent; do not send again')
        if row and row.state in {'sent', 'sending', 'unknown'}: raise PreviewConflict('This reply is already sent or uncertain')
        mail, _ = await self.original(snapshot)
        reason = self.reply_block_reason(mail)
        if reason: raise PreviewConflict(reason)
        if target:
            target = validate_target(target)[0]
            if target != case.full_url: case.full_url = target; case.version += 1
        if not case.full_url: raise PreviewConflict('A matched reported URL is required before capture')
        captured = await asyncio.to_thread(capture_fn, case.full_url, 'not_cloaking')
        if captured[1].get('terminal') or captured[1].get('requested_url') != case.full_url: raise PreviewConflict('Capture failed')
        tracked = _TrackedStore(self.store); original = self.store; self.store = tracked
        try:
            await self.attach(case, snapshot, captured[0], captured[1])
            self.session.add(AuditEvent(owner_user_id=self.owner, actor_user_id=self.actor, action='provider_replies.capture',
                resource_type='review_case', resource_id=case.id, outcome='success', request_id='provider-replies',
                metadata_allowlist={'image_count': len(captured[0])}))
            return await self.detail(case_id)
        except BaseException:
            for blob in tracked.created: original.delete(storage_key=blob.storage_key)
            raise
        finally: self.store = original

    async def preview(self, case_id, details, uploads=()):
        await lock_review(self.session, self.owner, case_id)
        case, snapshot = await self.context(case_id)
        mail, payload = await self.original(snapshot)
        row = await self.delivery(case)
        if snapshot.sanitized_state.get('sent_match_id'): raise PreviewConflict('A matching reply is already present in Sent; do not send again')
        if row and row.state in {'sent', 'sending', 'unknown'}: raise PreviewConflict('Do not resend an accepted or uncertain reply')
        reason = self.reply_block_reason(mail)
        if reason: raise PreviewConflict(reason)
        if not re.fullmatch(r'<[^<>\s]+>', mail.message_id): raise PreviewConflict('Original Message-ID is missing or invalid')
        account = await self.mail_settings().get_account(UUID(snapshot.sanitized_state['account_id']))
        if not account.enabled: raise PreviewConflict('Sender account is disabled')
        if mail.risk == 'approval_required' and not details.get('legal_approved'):
            raise PreviewConflict('An authorized representative must approve this legal or identity response')
        target = str(details.get('reported_url') or case.full_url)
        if target: target = validate_target(target)[0]
        if target != case.full_url:
            if snapshot.sanitized_state.get('evidence_id') and not uploads:
                raise PreviewConflict('Capture or upload evidence for the new reported URL')
            case.full_url = target; case.version += 1
        tracked = _TrackedStore(self.store); original = self.store; self.store = tracked
        try:
            if uploads: await self.attach(case, snapshot, uploads, {'source': 'manual_upload', 'evidence_type': 'manual_upload', 'terminal': False})
            evidence, images = await self.evidence(case, snapshot)
            profile = await self.session.get(UserSettings, self.owner)
            from domaincheck.application.profile_readiness import missing_report_profile
            if missing_report_profile(profile): raise PreviewConflict('Complete your organization profile')
            values = {field: getattr(profile, field) for field in ['brand_name', 'contact_name', 'contact_email', 'company_name', 'signature_role', 'business_registration_no']}
            for field in ['contact_name', 'contact_email', 'signature_role']:
                values[field] = (account.signature_overrides or {}).get(field) or values[field]
            values.update(reported_url=target, official_url=str(details.get('official_url') or ''), evidence=str(details.get('evidence') or ''), screenshot_attached=bool(images))
            if evidence and evidence.manifest.get('evidence_type') == 'dom_destination_opened':
                control = evidence.manifest.get('control') or {}
                values.update(redirect_url=control.get('resolved_destination') or '', button_label=control.get('label') or 'Register/Login', browser_evidence=evidence.manifest)
            subject, body, warnings = legacy.build_reply(replace(mail, provider_label='Provider') if mail.provider == 'unknown' else mail, values)
            if mail.provider == 'cloudflare' and mail.request_type in {'technical_evidence', 'clarification'} and not values.get('redirect_url'):
                raise PreviewConflict('Capture source/destination DOM evidence before replying to this Cloudflare request')
            if details.get('subject'): subject = details['subject']
            if details.get('body'): body = details['body']
            if '[PLEASE' in body or '[REPORT ID]' in body: raise PreviewConflict('Complete the requested information before preview')
            if mail.request_type == 'screenshot' and not images: raise PreviewConflict('Attach the requested screenshot evidence')
            if mail.request_type == 'full_url' and not target: raise PreviewConflict('Provide the complete reported URL')
            if mail.request_type == 'official_url' and not values['official_url']: raise PreviewConflict('Provide your official website URL')
            if values['official_url']: validate_target(values['official_url'])
            if any(term in body.lower() for term in ['urlscan', 'urlscan.io']): raise PreviewConflict('Unsupported evidence service in reply')
            attachments = [image.id for image in images]
            if evidence:
                if not values.get('browser_evidence'):
                    from domaincheck.engine import browser_evidence
                    from domaincheck.engine import phishing_toolkit
                    body = phishing_toolkit._insert_before_email_signature(body, browser_evidence.format_email_evidence_manifest(evidence.manifest))
                blob = self.store.store_evidence_manifest(owner_user_id=self.owner, evidence_set_id=evidence.id, data=canonical_payload_bytes(evidence.manifest))
                self.session.add(Artifact(id=blob.artifact_id, owner_user_id=self.owner, evidence_set_id=evidence.id,
                    kind='provider_replies:manifest', mime_type=blob.mime_type, byte_size=blob.byte_size,
                    sha256=blob.sha256, storage_key=blob.storage_key, immutable=True)); attachments.append(blob.artifact_id)
            logo = await load_profile_logo(self.session, self.store, self.owner, profile)
            logo_id = profile.logo_artifact_id
            if logo_id is None:
                stored = self.store.store_evidence_images(owner_user_id=self.owner, evidence_set_id=uuid4(),
                    uploads=[ImageUpload('logo.jpg', logo.mime_type, logo.data)], minimum_count=1, maximum_count=1)[0]
                logo_id = stored.artifact_id
                self.session.add(Artifact(id=logo_id, owner_user_id=self.owner, kind='mail_settings:logo', mime_type=stored.mime_type,
                    byte_size=stored.byte_size, sha256=stored.sha256, storage_key=stored.storage_key, immutable=True))
            await self.session.flush()
            refs = list(dict.fromkeys([*(payload.get('references') or []), *(payload.get('in_reply_to') or []), mail.message_id]))[-15:]
            while len(' '.join(refs)) > 998 and len(refs) > 1: refs.pop(0)
            exact = await self.plans().create_revision(resource_type='review_case', resource_id=case.id,
                feature='provider_replies', policy_version=POLICY, resource_version=case.version,
                deliveries=[PreparedDelivery(sender_account_id=account.id, recipient=mail.reply_to,
                    channel='reply', draft_identity='provider-reply-v1', subject=subject, text_body=body,
                    html_body=html_report_body(body, f'logo-{logo_id}'), sender_email=account.smtp_username,
                    sender_name=values['contact_name'], in_reply_to=mail.message_id, references=' '.join(refs))],
                attachment_artifact_ids=attachments, inline_artifact_ids=[logo_id], now=self.now)
            return {'id': exact.preview_id, 'revision': exact.revision, 'fingerprint': exact.fingerprint,
                    'deliveries': exact.payload['deliveries'], 'warnings': warnings}
        except BaseException:
            for blob in tracked.created: original.delete(storage_key=blob.storage_key)
            raise
        finally: self.store = original


class ProviderReplySender:
    def __init__(self, sessions, store, sender, envelope, *, sent_copier=None, now=lambda: datetime.now(UTC)):
        self.sessions, self.store, self.sender, self.envelope = sessions, store, sender, envelope
        self.sent_copier, self.now = sent_copier, now

    async def send(self, owner, actor, timezone, case_id, preview_id, revision, fingerprint, confirmed):
        if not confirmed: raise PreviewConflict('Confirm the exact reply before sending')
        async with self.sessions.kw['bind'].connect() as connection:
            key = review_lock_key(owner, case_id)
            acquired = await connection.scalar(select(func.pg_try_advisory_lock(key))); await connection.commit()
            if not acquired: raise PreviewConflict('This reply is being processed in another session')
            try: return await self._send(owner, actor, timezone, case_id, preview_id, revision, fingerprint)
            finally:
                try:
                    await connection.execute(select(func.pg_advisory_unlock(key))); await connection.commit()
                except BaseException:
                    await connection.invalidate(); raise

    async def _send(self, owner, actor, timezone, case_id, preview_id, revision, fingerprint):
        async with self.sessions() as session, session.begin():
            service = ProviderRepliesService(session, self.store, owner=owner, actor=actor, timezone=timezone, now=self.now(), envelope=self.envelope)
            case, snapshot = await service.context(case_id)
            previous = await service.delivery(case)
            if snapshot.sanitized_state.get('sent_match_id'): return await service.detail(case_id)
            if previous and previous.state in {'sent', 'sending', 'unknown'}: return await service.detail(case_id)
            plan = await OwnedRepository(session, PreviewPlan, owner).get(preview_id)
            if plan is None or plan.resource_type != 'review_case' or plan.resource_id != case.id: raise LookupError('Reply preview not found')
            exact = await service.plans().get_exact(preview_id, expected_revision=revision, now=self.now())
            if exact.fingerprint != fingerprint or exact.payload['policy_version'] != POLICY: raise PreviewConflict('Reply preview changed')
            original, _ = await service.original(snapshot)
            reason = service.reply_block_reason(original)
            if reason: raise PreviewConflict(reason)
            prepared = exact.payload['deliveries'][0]
            if prepared['sender_account_id'] != snapshot.sanitized_state['account_id'] or prepared['recipient'].lower() != original.reply_to.lower() or prepared.get('in_reply_to') != original.message_id:
                raise PreviewConflict('Reply account, recipient or thread changed')
            await service.plans().approve(preview_id, expected_revision=revision, confirmed=True, now=self.now())
            ledger = DeliveryService(session, self.store, owner_user_id=owner, actor_user_id=actor, request_id='provider-reply-send')
            row = (await ledger.materialize(preview_id=preview_id, expected_revision=revision, workflow='provider_replies',
                identities=[DeliveryBusinessIdentity(0, str(case.id))], now=self.now()))[0]
            if row.state == 'failed': await ledger.retry_failed(row.id, expected_version=row.version, now=self.now())
            account = await service.mail_settings().get_account(row.sender_account_id)
            secrets = service.mail_settings()._read_secrets(account)
            user = await session.get(User, actor)
            if not user or user.status != 'active' or not account.enabled or not secrets.get('smtp_password'):
                raise PreviewConflict('Sender authorization or account unavailable')
            spec = SmtpConnectionSpec(account.smtp_host, account.smtp_port, account.smtp_username, secrets['smtp_password'],
                'implicit_tls' if account.smtp_port == 465 else account.smtp_transport, 60,
                service.mail_settings()._proxy_spec(account, secrets))
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
        async with self.sessions() as session, session.begin():
            return await ProviderRepliesService(session, self.store, owner=owner, actor=actor, timezone=timezone,
                now=self.now(), envelope=self.envelope).detail(case_id)

"""Save accepted mail to Sent without performing another SMTP delivery."""
import asyncio
import json
from email.utils import formatdate, parseaddr
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select

from domaincheck.application.deliveries import DeliveryService
from domaincheck.application.mail_settings import MailSettingsService
from domaincheck.core.mail.sending import compose_message
from domaincheck.domain.previews import fingerprint_payload
from domaincheck.infrastructure.db.models import Artifact, Delivery, DeliveryAttempt, PreviewPlan
from domaincheck.infrastructure.db.owned import OwnedRepository


class LegacySentCopyAdapter:
    def __init__(self, endpoints):
        from domaincheck.core.mail.connectivity import AllowlistedMailConnectionTester
        self.policy = AllowlistedMailConnectionTester(None, endpoints)

    def save(self, account, raw_message, message_id):
        host = account['imap_host'].lower().rstrip('.')
        if host in {'gmail.com', 'smtp.gmail.com', 'imap.gmail.com', 'smtp.googlemail.com', 'imap.googlemail.com'}:
            return 'server_managed'
        try:
            self.policy._check(account['imap_host'], account['imap_port'])
        except Exception:
            return 'endpoint_denied'
        import phishing_toolkit
        error = phishing_toolkit._imap_save_sent(account, raw_message, message_id=message_id)
        return 'saved' if error is None else 'failed'


class SentCopyService:
    def __init__(self, sessions, store, adapter, envelope):
        self.sessions, self.store, self.adapter, self.envelope = sessions, store, adapter, envelope

    async def save(self, owner, actor, delivery_id, *, message=None):
        try:
            return await self._save(owner, actor, delivery_id, message=message)
        except Exception:
            # Archiving failure must not turn an accepted SMTP delivery into retry.
            async with self.sessions() as session, session.begin():
                attempt = await session.scalar(select(DeliveryAttempt).where(DeliveryAttempt.owner_user_id == owner,
                    DeliveryAttempt.delivery_id == delivery_id, DeliveryAttempt.state == 'sent')
                    .order_by(DeliveryAttempt.attempt_number.desc()).limit(1).with_for_update())
                if attempt:
                    status = 'unknown' if attempt.sanitized_result.get('sent_copy_status') == 'saving' else 'failed'
                    attempt.sanitized_result = {**attempt.sanitized_result, 'sent_copy_status': status}
            return 'failed'

    async def _save(self, owner, actor, delivery_id, *, message=None):
        async with self.sessions() as session, session.begin():
            row = await OwnedRepository(session, Delivery, owner).get(delivery_id, for_update=True)
            if row is None or row.workflow not in {'domain_worker', 'provider_replies'} or row.state != 'sent':
                raise ValueError('Only accepted owned Domain Worker mail can be saved to Sent')
            attempt = await session.scalar(select(DeliveryAttempt).where(DeliveryAttempt.owner_user_id == owner,
                DeliveryAttempt.delivery_id == row.id, DeliveryAttempt.state == 'sent')
                .order_by(DeliveryAttempt.attempt_number.desc()).limit(1).with_for_update())
            if attempt is None:
                raise ValueError('Accepted delivery attempt is missing')
            previous = attempt.sanitized_result.get('sent_copy_status')
            if previous in {'saved', 'server_managed', 'saving', 'unknown'}:
                return previous
            mail = MailSettingsService(session, owner_user_id=owner, actor_user_id=actor,
                request_id='sent-copy', envelope=self.envelope)
            account = await mail.get_account(row.sender_account_id)
            secrets = mail._read_secrets(account)
            imap = account.imap_config or {}
            username = str(imap.get('username') or account.smtp_username)
            password = secrets.get('imap_password') or (secrets.get('smtp_password') if username == account.smtp_username else None)
            if not account.enabled or not password:
                attempt.sanitized_result = {**attempt.sanitized_result, 'sent_copy_status': 'configuration_missing'}
                return 'configuration_missing'
            spec = {'imap_host': str(imap.get('host') or account.smtp_host), 'imap_port': int(imap.get('port') or 993),
                    'username': username, 'password': password}
            if message is None:
                # Historical copy: verify the immutable original plan, independently
                # of today's profile/plan expiry. No new SMTP authorization is implied.
                plan = await OwnedRepository(session, PreviewPlan, owner).get(row.preview_id)
                artifact = await OwnedRepository(session, Artifact, owner).get(plan.draft_artifact_id)
                payload = json.loads(self.store.read_verified(storage_key=artifact.storage_key,
                    expected_size=artifact.byte_size, expected_sha256=artifact.sha256))
                if fingerprint_payload(payload) != plan.fingerprint or not plan.approved_at:
                    raise ValueError('Original mail plan failed integrity validation')
                exact = DeliveryService._match_exact_delivery(row, payload['deliveries'])
                blobs = []
                for binding in payload['artifacts']:
                    artifact = await OwnedRepository(session, Artifact, owner).get(UUID(binding['artifact_id']))
                    if artifact.sha256 != binding['sha256'] or artifact.byte_size != binding['byte_size']:
                        raise ValueError('Original mail attachment changed')
                    blobs.append((binding, self.store.read_verified(storage_key=artifact.storage_key,
                        expected_size=artifact.byte_size, expected_sha256=artifact.sha256)))
                message = compose_message(SimpleNamespace(exact_delivery=exact, message_id=row.message_id), blobs)
                message.replace_header('Date', formatdate(attempt.started_at.timestamp(), usegmt=True))
            if message['Message-ID'] != row.message_id:
                raise ValueError('Sent copy does not match the accepted Message-ID')
            if parseaddr(message['From'])[1].casefold() != account.smtp_username.casefold():
                raise ValueError('Sent copy account identity changed')
            raw_message = message.as_bytes()
            message_id, attempt_id = row.message_id, attempt.id
            attempt.sanitized_result = {**attempt.sanitized_result, 'sent_copy_status': 'saving'}
        # SMTP SENT checkpoint already committed; a copy failure never retries SMTP.
        try:
            status = await asyncio.to_thread(self.adapter.save, spec, raw_message, message_id)
            if status not in {'saved', 'server_managed', 'endpoint_denied', 'failed'}:
                status = 'unknown'
        except Exception:
            status = 'unknown'
        async with self.sessions() as session, session.begin():
            attempt = await OwnedRepository(session, DeliveryAttempt, owner).get(attempt_id, for_update=True)
            attempt.sanitized_result = {**attempt.sanitized_result, 'sent_copy_status': status}
        return status

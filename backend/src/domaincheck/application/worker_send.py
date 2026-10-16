"""Daily, owned Domain Worker SMTP batches with a durable delivery ledger."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from domaincheck.application.deliveries import DeliveryService, DeliveryRecoveryRepository
from domaincheck.application.domain_worker import DomainWorkerService
from domaincheck.application.domain_worker_preview import DomainWorkerPreviewService
from domaincheck.application.jobs import JobConflict, JobItemInput, JobService
from domaincheck.application.worker_batches import guard_batch
from domaincheck.application.mail_settings import MailSettingsService
from domaincheck.application.previews import PreviewService
from domaincheck.core.mail.connectivity import SmtpConnectionSpec
from domaincheck.core.mail.sending import compose_message
from domaincheck.core.worker_precheck import valid_recipients
from domaincheck.domain.deliveries import DeliveryBusinessIdentity, business_key
from domaincheck.infrastructure.cache import business_day
from domaincheck.infrastructure.db.models import Artifact, Check, Delivery, DeliveryAttempt, Job, User
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.infrastructure.db.repositories import JobRepository


def daily_identity(day, domain):
    # Legacy dedupe is per domain, account, channel and recipient, across URL paths.
    return f'{day.isoformat()}:{domain.lower().rstrip(".")}'


def delivery_key(day, domain, recipient):
    return business_key(workflow='domain_worker', business_identity=daily_identity(day, domain),
        recipient=recipient['email'], channel=recipient['channel'],
        draft_identity=f"domain-worker-{recipient['channel']}-v1")


class _TrackedStore:
    """Clean newly written files when a batch cannot be fully prepared."""
    def __init__(self, delegate):
        self.delegate, self.created = delegate, []

    def __getattr__(self, name):
        method = getattr(self.delegate, name)
        if not name.startswith('store_'):
            return method
        def write(*args, **kwargs):
            result = method(*args, **kwargs)
            self.created.extend(result if isinstance(result, tuple) else [result])
            return result
        return write


class WorkerSendService:
    def __init__(self, session, store, *, owner, actor, timezone, request_id, now, envelope=None):
        self.session, self.store, self.owner, self.actor = session, store, owner, actor
        self.timezone, self.request_id, self.now, self.envelope = timezone, request_id, now, envelope

    def jobs(self):
        return JobService(self.session, owner_user_id=self.owner, actor_user_id=self.actor, request_id=self.request_id)

    def deliveries(self):
        return DeliveryService(self.session, self.store, owner_user_id=self.owner,
            actor_user_id=self.actor, request_id=self.request_id)

    async def source(self, source_id):
        operation = await DomainWorkerService(self.session, owner=self.owner, actor=self.actor,
            timezone=self.timezone, request_id=self.request_id, now=self.now).get(source_id)
        if operation.job.business_day != business_day(self.now, operation.job.timezone):
            raise JobConflict('Run a new precheck for today')
        if operation.job.state in {'queued', 'running', 'stopping'} or not operation.job.account_scope:
            raise JobConflict('Complete precheck with selected sender accounts first')
        return operation

    async def summary(self, source_id):
        operation = await self.source(source_id)
        accounts = [UUID(value) for value in operation.job.account_scope]
        checks, total, sent, blocked = [], 0, 0, 0
        eligible = [(check, check.result.get('disposition'))
            for check, item in zip(operation.checks, operation.items, strict=True)
            if item.state == 'succeeded' and check.status == 'succeeded'
            and check.result.get('disposition') in {'ready', 'excluded_already_sent'}
            and (check.result.get('disposition') == 'excluded_already_sent' or check.result.get('evidence_set_id'))]
        routes = [(check, disposition, [delivery_key(check.business_day, check.domain, recipient)
            for recipient in valid_recipients(check.result.get('recipients') or [])]) for check, disposition in eligible]
        keys = list({key for _, _, route_keys in routes for key in route_keys})
        ledger = list(await self.session.scalars(select(Delivery).where(Delivery.owner_user_id == self.owner,
            Delivery.sender_account_id.in_(accounts), Delivery.business_key.in_(keys)))) if keys else []
        states = {(row.sender_account_id, row.business_key): row.state for row in ledger}
        seen = set()
        for check, disposition, route_keys in routes:
            pending = 0
            for key in route_keys:
                for account in accounts:
                    if (account, key) in seen:
                        continue
                    seen.add((account, key)); total += 1
                    state = states.get((account, key))
                    if state == 'sent':
                        sent += 1
                    elif state in {'sending', 'unknown'}:
                        blocked += 1
                    else:
                        pending += 1
            if pending and disposition == 'ready':
                checks.append({'check_id': str(check.id), 'requested_url': check.requested_url,
                               'pending_deliveries': pending})
        latest = await self.session.scalar(select(Job).where(Job.owner_user_id == self.owner,
            Job.job_type == 'domain_worker_send', Job.business_day == operation.job.business_day,
            Job.result_metadata['source_job_id'].astext == str(source_id)).order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
        return {'business_day': str(operation.job.business_day), 'ready_domains': len(checks),
            'total_deliveries': total, 'already_sent': sent, 'blocked_deliveries': blocked,
            'pending_deliveries': total - sent - blocked, 'checks': checks,
            'send_job_id': str(latest.id) if latest else None}

    async def start(self, source_id, *, batch_size, interval_seconds, confirmed):
        original, tracked = self.store, _TrackedStore(self.store)
        self.store = tracked
        try:
            return await self._start(source_id, batch_size=batch_size, interval_seconds=interval_seconds, confirmed=confirmed)
        except Exception:
            for artifact in tracked.created:
                original.delete(storage_key=artifact.storage_key)
            raise
        finally:
            self.store = original

    async def _start(self, source_id, *, batch_size, interval_seconds, confirmed):
        if not confirmed:
            raise JobConflict('Confirm sending this batch')
        if not 1 <= batch_size <= 100 or not 0 <= interval_seconds <= 86400:
            raise ValueError('Invalid batch configuration')
        await guard_batch(self.session, self.owner, self.now, self.timezone, allowed_source=source_id)
        operation = await self.source(source_id)
        previous = await self.session.scalar(select(Job).where(Job.owner_user_id == self.owner,
            Job.job_type == 'domain_worker_send', Job.result_metadata['source_job_id'].astext == str(source_id))
            .order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
        if previous and previous.result_metadata.get('control_mode') == 'cancel':
            raise JobConflict('This sending job was permanently stopped; create a new precheck to send the remaining items')
        if previous and previous.state == 'stopped' and previous.result_metadata.get('control_mode') == 'pause':
            raise JobConflict('Resume the paused sending job')
        active = await self.session.scalar(select(Job).where(Job.owner_user_id == self.owner,
            Job.job_type == 'domain_worker_send', Job.state.in_(['queued', 'running', 'stopping'])).with_for_update())
        if active and active.state == 'queued' and active.business_day != business_day(self.now, active.timezone):
            active.state, active.stage = 'stopped', 'day_expired'
            active.lease_token = active.lease_expires_at = None
            active.version += 1
            active = None
        if active:
            if active.result_metadata.get('source_job_id') == str(source_id):
                return await self.get(active.id)
            raise JobConflict('Stop or finish the current sending batch first')
        summary = await self.summary(source_id)
        if not summary['checks']:
            raise JobConflict('No remaining eligible deliveries for today')
        accounts = [UUID(value) for value in operation.job.account_scope]
        mail = MailSettingsService(self.session, owner_user_id=self.owner, actor_user_id=self.actor,
            request_id=self.request_id, envelope=self.envelope)
        for account_id in accounts:
            account = await mail.get_account(account_id)
            if not account.enabled or not mail._read_secrets(account).get('smtp_password'):
                raise JobConflict('Selected sender account is disabled or has no SMTP password')
        preview = DomainWorkerPreviewService(self.session, self.store, owner=self.owner, actor=self.actor,
            timezone=operation.job.timezone, request_id=self.request_id, now=self.now)
        plans = PreviewService(self.session, self.store, owner_user_id=self.owner,
            actor_user_id=self.actor, request_id=self.request_id)
        items = []
        for candidate in summary['checks']:
            check_id = UUID(candidate['check_id'])
            exact = await preview.create(check_id, accounts, lifetime=timedelta(hours=24))
            await plans.approve(exact['id'], expected_revision=exact['revision'], confirmed=True, now=self.now)
            check = await OwnedRepository(self.session, Check, self.owner).get(check_id)
            rows = await self.deliveries().materialize(preview_id=exact['id'], expected_revision=exact['revision'],
                workflow='domain_worker', identities=[DeliveryBusinessIdentity(index,
                    daily_identity(check.business_day, check.domain)) for index in range(len(exact['deliveries']))], now=self.now)
            # A new explicit start can retry definitely-failed rows, preserving SENT/UNKNOWN.
            for row in rows:
                if row.state == 'failed':
                    await self.deliveries().retry_failed(row.id, expected_version=row.version, now=self.now)
            items.append(JobItemInput(str(check_id), len(items), {'check_id': str(check_id),
                'delivery_ids': list(dict.fromkeys(str(row.id) for row in rows))}))
        snapshot = await self.jobs().create_job(job_type='domain_worker_send', business_day=operation.job.business_day,
            timezone=operation.job.timezone, items=items, sender_account_ids=accounts)
        snapshot.job.result_metadata = {'source_job_id': str(source_id), 'batch_size': batch_size,
            'interval_seconds': interval_seconds, 'batch_processed': 0, 'next_run_at': None,
            'actor_authz_version': (await self.session.get(User, self.actor)).authz_version}
        operation.job.result_metadata = {**operation.job.result_metadata, 'workflow_open': False}
        await self.session.flush()
        return await self.get(snapshot.job.id)

    async def get(self, job_id):
        snapshot = await self.jobs().get_snapshot(job_id)
        if snapshot.job.job_type != 'domain_worker_send':
            raise JobConflict('Not a Domain Worker send job')
        check_ids = [UUID(item.result_metadata['check_id']) for item in snapshot.items]
        checks = {check.id: check for check in await self.session.scalars(select(Check).where(
            Check.owner_user_id == self.owner, Check.id.in_(check_ids)))}
        delivery_ids = list({UUID(value) for item in snapshot.items for value in item.result_metadata.get('delivery_ids', [])})
        by_id = {row.id: row for row in await self.session.scalars(select(Delivery).where(
            Delivery.owner_user_id == self.owner, Delivery.id.in_(delivery_ids)))}
        copy_states, attempts, counts = {}, {}, {}
        for attempt in await self.session.scalars(select(DeliveryAttempt).where(DeliveryAttempt.owner_user_id == self.owner,
            DeliveryAttempt.delivery_id.in_(delivery_ids)).order_by(DeliveryAttempt.attempt_number.desc())):
            attempts.setdefault(attempt.delivery_id, attempt)
            counts[attempt.delivery_id] = counts.get(attempt.delivery_id, 0) + 1
            if attempt.state == 'sent':
                copy_states.setdefault(attempt.delivery_id, attempt.sanitized_result.get('sent_copy_status', 'not_recorded'))
        rows = []
        for item in snapshot.items:
            check = checks.get(UUID(item.result_metadata['check_id']))
            deliveries = [by_id[UUID(value)] for value in item.result_metadata.get('delivery_ids', []) if UUID(value) in by_id]
            rows.append({'id': str(item.id), 'requested_url': check.requested_url if check else '', 'state': item.state,
                'reason_code': item.result_metadata.get('reason_code'),
                'deliveries': [{'id': str(row.id), 'recipient': row.recipient, 'sender_account_id': str(row.sender_account_id),
                                'channel': row.channel, 'state': row.state,
                                'sent_copy_status': copy_states.get(row.id, 'not_recorded'),
                                'attempt_count': counts.get(row.id, 0), 'message_id': row.message_id,
                                'last_error_code': (attempts[row.id].sanitized_result.get('reason_code') if row.id in attempts else item.result_metadata.get('reason_code')),
                                'smtp_stage': attempts[row.id].smtp_stage if row.id in attempts else None} for row in deliveries]})
        return {'id': str(snapshot.job.id), 'state': snapshot.job.state, 'business_day': str(snapshot.job.business_day),
            'control_mode': snapshot.job.result_metadata.get('control_mode'),
            'batch_size': snapshot.job.result_metadata['batch_size'],
            'interval_seconds': snapshot.job.result_metadata['interval_seconds'],
            'next_run_at': snapshot.job.result_metadata.get('next_run_at'), 'items': rows}

    async def control(self, job_id, action):
        original, tracked = self.store, _TrackedStore(self.store)
        self.store = tracked
        try:
            return await self._control(job_id, action)
        except Exception:
            for artifact in tracked.created:
                original.delete(storage_key=artifact.storage_key)
            raise
        finally:
            self.store = original

    async def _control(self, job_id, action):
        initial = await self.jobs().get_snapshot(job_id)
        await guard_batch(self.session, self.owner, self.now, self.timezone,
                          allowed_source=initial.job.result_metadata.get('source_job_id'))
        snapshot = await self.jobs().get_snapshot(job_id, for_update=True)
        await self.session.refresh(snapshot.job)
        if snapshot.job.job_type != 'domain_worker_send':
            raise JobConflict('Not a Domain Worker send job')
        if action in {'stop', 'pause', 'cancel'}:
            if snapshot.job.state == 'completed':
                return await self.get(job_id)
            if snapshot.job.result_metadata.get('control_mode') == 'cancel':
                return await self.get(job_id)
            mode = 'cancel' if action == 'cancel' else 'pause'
            snapshot.job.result_metadata = {**snapshot.job.result_metadata, 'control_mode': mode, 'next_run_at': None}
            await self.jobs().request_stop(job_id)
            if mode == 'cancel' and snapshot.job.state in {'partial', 'failed'}:
                snapshot.job.state = 'stopped'
                snapshot.job.version += 1
        else:
            if snapshot.job.result_metadata.get('control_mode') == 'cancel':
                raise JobConflict('This sending job was permanently stopped')
            active = await self.session.scalar(select(Job.id).where(Job.owner_user_id == self.owner,
                Job.job_type == 'domain_worker_send', Job.id != job_id, Job.state.in_(['queued', 'running', 'stopping'])).limit(1))
            if active:
                raise JobConflict('Stop or finish the current sending batch first')
            if snapshot.job.state in {'queued', 'running', 'stopping'}:
                return await self.get(job_id)
            if snapshot.job.business_day != business_day(self.now, snapshot.job.timezone):
                raise JobConflict('Run a new precheck for today')
            if action == 'resume':
                await self.jobs().resume(job_id)
            else:
                failed = []
                for item in snapshot.items:
                    if item.state not in {'failed', 'unknown'}:
                        continue
                    rows = [await self.deliveries().get(UUID(value)) for value in item.result_metadata.get('delivery_ids', [])]
                    if not any(row.state in {'failed', 'pending'} for row in rows):
                        continue
                    await self._refresh_retry_plan(snapshot.job, item, rows)
                    item.state = 'failed'  # Only item scheduling; UNKNOWN delivery rows stay immutable.
                    failed.append(item)
                await self.jobs().retry_failed(job_id, {item.id for item in failed})
            snapshot.job.result_metadata = {**snapshot.job.result_metadata, 'control_mode': None, 'next_run_at': None, 'batch_processed': 0,
                'actor_authz_version': (await self.session.get(User, self.actor)).authz_version}
        return await self.get(job_id)

    async def _refresh_retry_plan(self, job, item, rows):
        check_id = UUID(item.result_metadata['check_id'])
        preview = DomainWorkerPreviewService(self.session, self.store, owner=self.owner, actor=self.actor,
            timezone=job.timezone, request_id=self.request_id, now=self.now)
        # No network recheck. Validate existing evidence and refresh only the exact plan.
        exact = await preview.create(check_id, [UUID(value) for value in job.account_scope], lifetime=timedelta(hours=24))
        expected = {(str(row.sender_account_id), row.recipient, row.channel, row.draft_identity) for row in rows}
        actual = {(row['sender_account_id'], row['recipient'], row['channel'], row['draft_identity']) for row in exact['deliveries']}
        if expected != actual:
            raise JobConflict('Recipients changed; run a new precheck before sending')
        plans = PreviewService(self.session, self.store, owner_user_id=self.owner, actor_user_id=self.actor, request_id=self.request_id)
        await plans.approve(exact['id'], expected_revision=exact['revision'], confirmed=True, now=self.now)
        check = await OwnedRepository(self.session, Check, self.owner).get(check_id)
        materialized = await self.deliveries().materialize(preview_id=exact['id'], expected_revision=exact['revision'],
            workflow='domain_worker', identities=[DeliveryBusinessIdentity(index, daily_identity(check.business_day, check.domain))
                for index in range(len(exact['deliveries']))], now=self.now)
        for row in materialized:
            if row.state == 'failed':
                await self.deliveries().retry_failed(row.id, expected_version=row.version, now=self.now)


class DomainSendWorker:
    """Process one domain per claim; persist batch delay instead of sleeping a worker."""
    def __init__(self, sessions, store, sender, envelope, *, now=lambda: datetime.now(UTC), heartbeat_seconds=10, sent_copier=None):
        self.sessions, self.store, self.sender, self.envelope = sessions, store, sender, envelope
        self.now, self.heartbeat_seconds = now, heartbeat_seconds
        self.sent_copier = sent_copier

    async def run_next(self, token):
        async with self.sessions() as session, session.begin():
            repo = JobRepository(session)
            await DeliveryRecoveryRepository(session).mark_expired_unknown(now=self.now())
            await repo.recover_expired_stops(now=self.now(), job_types=frozenset({'domain_worker_send'}))
            job = await repo.claim_next(worker_token=token, lease_seconds=60,
                job_types=frozenset({'domain_worker_send'}), now=self.now())
            if job is None:
                return None
            await repo.recover_interrupted_items(job=job, worker_token=token, now=self.now())
            items = await repo.list_claimed_items(job, worker_token=token)
            owner, actor, job_id = job.owner_user_id, job.actor_user_id, job.id
            pending = next((item for item in items if item.state == 'pending'), None)
            if job.business_day != business_day(self.now(), job.timezone):
                job.state, job.stage = 'stopped', 'day_expired'
                job.lease_token = job.lease_expires_at = None
                job.version += 1
                return job_id
            item_id = None
            if pending:
                started = await repo.start_item(owner_user_id=owner, job_id=job_id, item_id=pending.id,
                    worker_token=token, expected_version=pending.version, stage='sending', now=self.now())
                item_id, version, metadata = started.id, started.version, dict(started.result_metadata)
        if item_id:
            task = asyncio.create_task(self._send_item(owner, actor, job_id, token, metadata))
            try:
                while not task.done():
                    done, _ = await asyncio.wait({task}, timeout=self.heartbeat_seconds)
                    if done:
                        break
                    async with self.sessions() as session, session.begin():
                        current = await OwnedRepository(session, Job, owner).get(job_id, for_update=True)
                        if not current or await JobRepository(session).heartbeat(job_id=job_id, worker_token=token,
                            expected_version=current.version, allow_stopping=True, now=self.now()) is None:
                            raise RuntimeError('Send worker lease lost')
                state, reason = await task
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            async with self.sessions() as session, session.begin():
                await JobRepository(session).checkpoint_item(owner_user_id=owner, job_id=job_id, item_id=item_id,
                    worker_token=token, expected_version=version, state=state, stage='completed',
                    result_metadata={**metadata, 'reason_code': reason}, now=self.now())
        async with self.sessions() as session, session.begin():
            repo = JobRepository(session)
            job = await OwnedRepository(session, Job, owner).get(job_id, for_update=True)
            if not job or job.lease_token != token:
                return job_id
            items = await repo.list_claimed_items(job, worker_token=token)
            if job.stop_requested:
                await repo.finalize_stop(owner_user_id=owner, job_id=job_id, worker_token=token, expected_version=job.version)
            elif any(item.state == 'pending' for item in items):
                count = job.result_metadata.get('batch_processed', 0) + int(item_id is not None)
                pause = count >= job.result_metadata['batch_size']
                job.result_metadata = {**job.result_metadata, 'batch_processed': 0 if pause else count,
                    'next_run_at': (self.now() + timedelta(seconds=job.result_metadata['interval_seconds'])).isoformat() if pause else None}
                job.state, job.stage = 'queued', 'batch_wait' if pause else 'sending'
                job.lease_token = job.lease_expires_at = None
                job.version += 1
            else:
                state = 'completed' if all(item.state == 'succeeded' for item in items) else 'partial'
                await repo.finalize_job(job_id=job_id, worker_token=token, expected_version=job.version,
                    state=state, stage='completed', result_metadata=job.result_metadata, now=self.now())
        return job_id

    async def _send_item(self, owner, actor, job_id, token, metadata):
        for value in metadata['delivery_ids']:
            try:
                async with self.sessions() as session, session.begin():
                    job = await OwnedRepository(session, Job, owner).get(job_id)
                    if not job or job.lease_token != token or job.lease_expires_at <= self.now():
                        return 'unknown', 'lease_lost'
                    user = await session.get(User, actor)
                    if not user or user.status != 'active' or user.authz_version != job.result_metadata.get('actor_authz_version'):
                        return 'failed', 'authorization_changed'
                    # Stop is cooperative: finish this domain, then take no next domain.
                    if job.business_day != business_day(self.now(), job.timezone):
                        return 'failed', 'day_expired'
                    svc = DeliveryService(session, self.store, owner_user_id=owner, actor_user_id=actor, request_id='domain-worker-send')
                    row = await svc.get(UUID(value))
                    if row.state == 'sent':
                        continue
                    if row.state != 'pending':
                        continue
                    mail = MailSettingsService(session, owner_user_id=owner, actor_user_id=actor,
                        request_id='domain-worker-send', envelope=self.envelope)
                    account = await mail.get_account(row.sender_account_id)
                    secrets = mail._read_secrets(account)
                    if not account.enabled or not secrets.get('smtp_password'):
                        return 'failed', 'account_unavailable'
                    spec = SmtpConnectionSpec(host=account.smtp_host, port=account.smtp_port, username=account.smtp_username,
                        password=secrets['smtp_password'], transport='implicit_tls' if account.smtp_port == 465 else account.smtp_transport,
                        timeout_seconds=60, proxy=mail._proxy_spec(account, secrets))
                    intent = await svc.claim_intent(row.id, expected_version=row.version, lease_seconds=300, now=self.now())
                    blobs = []
                    for binding in intent.artifacts:
                        artifact = await OwnedRepository(session, Artifact, owner).get(UUID(binding['artifact_id']))
                        data = self.store.read_verified(storage_key=artifact.storage_key,
                            expected_size=artifact.byte_size, expected_sha256=artifact.sha256)
                        blobs.append((binding, data))
                    message = compose_message(intent, blobs)
                # Intent committed above; no database transaction during SMTP.
                try:
                    outcome, stage, reason = await asyncio.to_thread(self.sender.send, spec, message)
                except Exception:
                    outcome, stage, reason = 'unknown', 'send_message', 'transport_outcome_uncertain'
                async with self.sessions() as session, session.begin():
                    await DeliveryService(session, self.store, owner_user_id=owner, actor_user_id=actor,
                        request_id='domain-worker-send').checkpoint(intent.delivery_id, attempt_id=intent.attempt_id,
                        claim_token=intent.claim_token, outcome=outcome, smtp_stage=stage,
                        result={'reason_code': reason, 'evidence_source': 'browser_or_manual'}, now=self.now())
                if outcome == 'sent' and self.sent_copier is not None:
                    from domaincheck.application.sent_copy import SentCopyService
                    await SentCopyService(self.sessions, self.store, self.sent_copier, self.envelope).save(
                        owner, actor, intent.delivery_id, message=message)
            except Exception:
                async with self.sessions() as session:
                    uncertain = await session.scalar(select(Delivery.id).where(Delivery.owner_user_id == owner,
                        Delivery.id.in_([UUID(value) for value in metadata['delivery_ids']]),
                        Delivery.state.in_(['sending', 'unknown'])).limit(1))
                return ('unknown', 'delivery_uncertain') if uncertain else ('failed', 'validation_failed')
        async with self.sessions() as session:
            rows = list(await session.scalars(select(Delivery).where(Delivery.owner_user_id == owner,
                Delivery.id.in_([UUID(value) for value in metadata['delivery_ids']]))))
            if any(row.state in {'unknown', 'sending'} for row in rows):
                return 'unknown', 'delivery_uncertain'
            return ('succeeded', 'completed') if all(row.state == 'sent' for row in rows) else ('failed', 'delivery_failed')

"""Owner scoped lifecycle and local-day delivery filtering for Domain Worker."""
import hashlib
from uuid import UUID

from sqlalchemy import cast, func, select

from domaincheck.application.jobs import JobConflict
from domaincheck.core.worker_precheck import valid_recipients
from domaincheck.infrastructure.cache import business_day
from domaincheck.infrastructure.db.models import Check, Delivery, DeliveryAttempt, Job, JobItem

BUSY_MESSAGE = 'Đang xử lý batch hiện tại. Vui lòng đợi hoàn thành trước khi kiểm tra danh sách mới.'
ACTIVE = ('queued', 'running', 'stopping')
TYPES = ('domain_worker_precheck', 'domain_worker_send')


async def lock_batches(session, owner):
    key = int.from_bytes(hashlib.sha256(f'{owner}:domain_worker_batch'.encode()).digest()[:8], 'big', signed=True)
    await session.execute(select(func.pg_advisory_xact_lock(key)))


async def recover_owner(session, owner, now):
    """Conservative recovery, never performs SMTP or resets uncertain deliveries."""
    jobs = list(await session.scalars(select(Job).where(Job.owner_user_id == owner,
        Job.job_type.in_(TYPES), Job.state.in_(ACTIVE)).with_for_update(skip_locked=True)))
    for job in jobs:
        expired_day = job.business_day != business_day(now, job.timezone)
        if job.state == 'queued':
            if expired_day:
                job.state, job.stage = 'stopped', 'day_expired'
                job.version += 1
            continue
        if job.lease_expires_at and job.lease_expires_at > now:
            continue
        items = list(await session.scalars(select(JobItem).where(JobItem.owner_user_id == owner,
            JobItem.job_id == job.id).with_for_update()))
        for item in items:
            if item.state != 'running':
                continue
            ids = [UUID(value) for value in item.result_metadata.get('delivery_ids', [])]
            deliveries = list(await session.scalars(select(Delivery).where(Delivery.owner_user_id == owner,
                Delivery.id.in_(ids)).with_for_update())) if ids else []
            for delivery in deliveries:
                if delivery.state != 'sending':
                    continue
                delivery.state, delivery.claim_token, delivery.claim_expires_at = 'unknown', None, None
                delivery.version += 1
                attempts = list(await session.scalars(select(DeliveryAttempt).where(
                    DeliveryAttempt.owner_user_id == owner, DeliveryAttempt.delivery_id == delivery.id,
                    DeliveryAttempt.state == 'intent').with_for_update()))
                for attempt in attempts:
                    attempt.state, attempt.smtp_stage, attempt.finished_at = 'unknown', 'checkpoint_missing', now
                    attempt.sanitized_result = {'reason_code': 'worker_lease_expired'}
            known_sent = bool(ids) and len(deliveries) == len(ids) and all(row.state == 'sent' for row in deliveries)
            known_unsent = bool(ids) and len(deliveries) == len(ids) and not any(row.state in {'sending', 'unknown'} for row in deliveries)
            item.state = 'succeeded' if known_sent else 'failed' if known_unsent else 'unknown'
            item.stage = 'recovered_checkpoint'
            item.result_metadata = {**item.result_metadata, 'reason_code': 'completed' if known_sent else 'worker_interrupted' if known_unsent else 'worker_lease_expired'}
            item.version += 1
        job.state = 'stopped' if job.stop_requested or expired_day else 'partial'
        if items and all(item.state == 'succeeded' for item in items):
            job.state = 'completed'
        job.stage = 'recovered_checkpoint'
        job.lease_token = job.lease_expires_at = None
        job.version += 1
    await session.flush()


async def current_batch(session, owner, now, timezone):
    running = await session.scalar(select(Job).where(Job.owner_user_id == owner, Job.job_type.in_(TYPES),
        Job.state.in_(ACTIVE)).order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
    if running:
        source = str(running.id) if running.job_type == 'domain_worker_precheck' else running.result_metadata.get('source_job_id')
        return {'current_id': source, 'send_job_id': str(running.id) if running.job_type == 'domain_worker_send' else None,
                'phase': 'precheck' if running.job_type == 'domain_worker_precheck' else 'sending', 'locked': True}
    paused = await session.scalar(select(Job).where(Job.owner_user_id == owner,
        Job.job_type == 'domain_worker_send', Job.state == 'stopped',
        Job.business_day == business_day(now, timezone), Job.result_metadata['control_mode'].astext == 'pause')
        .order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
    if paused:
        return {'current_id': paused.result_metadata['source_job_id'], 'send_job_id': str(paused.id),
                'phase': 'paused', 'locked': True}
    open_jobs = list(await session.scalars(select(Job).where(Job.owner_user_id == owner,
        Job.job_type == 'domain_worker_precheck', Job.business_day == business_day(now, timezone),
        Job.result_metadata['workflow_open'].astext == 'true', Job.state.in_(['completed', 'partial']))
        .order_by(Job.created_at.desc(), Job.id.desc())))
    for job in open_jobs:
        ready = list(await session.scalars(select(Check).join(JobItem,
            (Check.id == cast(JobItem.result_metadata['check_id'].astext, Check.id.type)) &
            (Check.owner_user_id == JobItem.owner_user_id)).where(JobItem.owner_user_id == owner,
            JobItem.job_id == job.id, JobItem.state == 'succeeded', Check.result['disposition'].astext == 'ready')))
        from domaincheck.application.worker_send import delivery_key
        for check in ready:
            keys = {delivery_key(check.business_day, check.domain, recipient)
                    for recipient in valid_recipients(check.result.get('recipients') or [])}
            accounts = [UUID(value) for value in job.account_scope]
            if not keys or not accounts:
                continue
            sent = set((await session.execute(select(Delivery.sender_account_id, Delivery.business_key).where(
                Delivery.owner_user_id == owner, Delivery.sender_account_id.in_(accounts),
                Delivery.business_key.in_(keys), Delivery.state == 'sent'))).all())
            if any((account, key) not in sent for account in accounts for key in keys):
                return {'current_id': str(job.id), 'send_job_id': None, 'phase': 'awaiting_send', 'locked': True}
    return {'current_id': None, 'send_job_id': None, 'phase': 'idle', 'locked': False}


async def guard_batch(session, owner, now, timezone, *, allowed_source=None):
    await lock_batches(session, owner)
    await recover_owner(session, owner, now)
    state = await current_batch(session, owner, now, timezone)
    if state['locked'] and state['current_id'] != (str(allowed_source) if allowed_source else None):
        raise JobConflict(BUSY_MESSAGE)
    return state


async def workspace_state(session, owner, now, timezone):
    await lock_batches(session, owner)
    await recover_owner(session, owner, now)
    state = await current_batch(session, owner, now, timezone)
    sources = list(await session.scalars(select(Job).where(Job.owner_user_id == owner,
        Job.job_type == 'domain_worker_precheck').order_by(Job.created_at.desc(), Job.id.desc()).limit(50)))
    sends = list(await session.scalars(select(Job).where(Job.owner_user_id == owner,
        Job.job_type == 'domain_worker_send', Job.result_metadata['source_job_id'].astext.in_([str(job.id) for job in sources]))
        .order_by(Job.created_at.desc(), Job.id.desc()))) if sources else []
    latest = {}
    for job in sends:
        latest.setdefault(job.result_metadata['source_job_id'], job)
    history = []
    for source in sources:
        send = latest.get(str(source.id))
        history.append({'id': str(source.id), 'created_at': source.created_at, 'business_day': str(source.business_day),
            'state': source.state, 'send_job_id': str(send.id) if send else None, 'send_state': send.state if send else None})
    if not state['current_id'] and history:
        state['current_id'] = history[0]['id']
        state['send_job_id'] = history[0]['send_job_id']
    return {**state, 'history': history}


async def filter_sent_inputs(session, owner, day, accounts, parsed):
    """Reuse today's no-email result or require SENT for all known account/routes."""
    from domaincheck.application.worker_send import delivery_key
    summary = {'input_count': len(parsed), 'unique_count': 0, 'queued_count': 0,
               'duplicate_count': 0, 'sent_count': 0, 'no_email_count': 0, 'processing_count': 0, 'excluded': []}
    unique, seen = [], set()
    for url, domain in parsed:
        if url in seen:
            summary['duplicate_count'] += 1
            summary['excluded'].append({'requested_url': url[:512], 'reason': 'duplicate'})
        else:
            seen.add(url); unique.append((url, domain))
    summary['unique_count'] = len(unique)
    domains = {domain for _, domain in unique}
    history = list(await session.scalars(select(Check).where(Check.owner_user_id == owner,
        Check.workflow == 'domain_worker', Check.business_day == day,
        Check.status.in_(['succeeded', 'failed', 'unknown']), Check.domain.in_(domains))
        .order_by(Check.updated_at.desc(), Check.created_at.desc(), Check.id.desc())))
    keys, latest = {}, {}
    for check in history:
        latest.setdefault(check.domain, check)
        if check.status != 'succeeded' or check.result.get('disposition') not in {
                'ready', 'excluded_already_sent', 'evidence_review', 'cloaking_review'}:
            continue
        keys.setdefault(check.domain, set()).update(delivery_key(day, check.domain, recipient)
            for recipient in valid_recipients(check.result.get('recipients') or []))
    all_keys = set().union(*keys.values()) if keys else set()
    rows = list(await session.scalars(select(Delivery).where(Delivery.owner_user_id == owner,
        Delivery.sender_account_id.in_(accounts), Delivery.business_key.in_(all_keys)))) if accounts and all_keys else []
    states = {(row.sender_account_id, row.business_key): row.state for row in rows}
    accepted = []
    for url, domain in unique:
        expected = [states.get((account, key)) for account in accounts for key in keys.get(domain, ())]
        reason = None
        if expected and all(state == 'sent' for state in expected):
            reason = 'already_sent'; summary['sent_count'] += 1
        elif expected and all(state in {'sent', 'sending', 'unknown'} for state in expected):
            reason = 'uncertain_delivery'; summary['processing_count'] += 1
        elif not keys.get(domain) and (previous := latest.get(domain)) is not None and previous.status == 'succeeded' and previous.result.get('disposition') == 'excluded_no_email' and not valid_recipients(previous.result.get('recipients') or []):
            reason = 'no_email'; summary['no_email_count'] += 1
        if reason:
            summary['excluded'].append({'requested_url': url[:512], 'reason': reason})
        else:
            accepted.append((url, domain))
    summary['queued_count'] = len(accepted)
    return accepted, summary

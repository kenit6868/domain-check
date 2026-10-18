"""Read-only dashboard aggregates. Never probe targets, sync mail or reconcile jobs."""
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import String, cast, distinct, func, select

from domaincheck.infrastructure.db.models import (
    Artifact, AuditEvent, CaseSource, Check, CloudflareRecord, Delivery, DeliveryAttempt, Job, JobItem,
    PreviewPlan, ReviewCase, ReviewSnapshot, User,
)

FEATURES = {
    'check_domain': ('check_domain.view', '/domain-check'),
    'quick_report': ('quick_report.view', '/quick-report'),
    'domain_worker': ('domain_worker.view', '/domain-worker'),
    'cloaking_review': ('cloaking_review.view', '/cloaking-review'),
    'domain_evidence_review': ('domain_evidence_review.view', '/domain-evidence-review'),
    'provider_replies': ('provider_replies.view', '/provider-replies'),
    'cloudflare_worker': ('cloudflare_worker.view', '/cloudflare-worker'),
}
ACTIVE_REVIEW = ('pending', 'failed', 'partial', 'ready', 'sending')


async def overview(session, actor, *, scope='own', days=7, user_id=None, feature=None, now=None, offset=0):
    if 'dashboard.view' not in actor.permissions:
        raise PermissionError('Permission denied')
    if scope not in {'own', 'all'} or days not in {1, 7, 30}:
        raise ValueError('Invalid dashboard range')
    if scope == 'all' and 'dashboard.view_all' not in actor.permissions:
        raise PermissionError('Permission denied')
    if scope == 'own' and user_id is not None and user_id != actor.user_id:
        raise PermissionError('Permission denied')
    allowed = set(FEATURES) if scope == 'all' else {key for key, (permission, _) in FEATURES.items() if permission in actor.permissions}
    if feature is not None:
        if feature not in FEATURES: raise ValueError('Unknown workflow')
        if feature not in allowed: raise PermissionError('Permission denied')
        allowed = {feature}
    now = now or datetime.now(UTC)
    zone = ZoneInfo(actor.timezone)
    today = now.astimezone(zone).date()
    first = today - timedelta(days=days - 1)
    start = datetime.combine(first, time.min, zone).astimezone(UTC)
    end = datetime.combine(today + timedelta(days=1), time.min, zone).astimezone(UTC)
    owner = actor.user_id if scope == 'own' else user_id

    def owned(model):
        return [model.owner_user_id == owner] if owner is not None else []

    def day_of(column):
        return cast(func.timezone(actor.timezone, column), Check.business_day.type)

    # Review email deliveries are historically stored under domain_worker.
    # Preview resource type provides the actual feature boundary.
    evidence_origin = select(CaseSource.id).join(ReviewCase, (ReviewCase.id == CaseSource.case_id) &
        (ReviewCase.owner_user_id == CaseSource.owner_user_id)).where(
        CaseSource.check_id == PreviewPlan.resource_id, CaseSource.owner_user_id == Delivery.owner_user_id,
        ReviewCase.case_type == 'domain_evidence').exists()
    from sqlalchemy import case
    delivery_workflow = case(
        (Artifact.kind == 'cloaking_review:draft', 'cloaking_review'),
        ((Delivery.workflow == 'domain_worker') & evidence_origin, 'domain_evidence_review'),
        else_=Delivery.workflow,
    )
    # Use one successful checkpoint per delivery, not attempt count/updated_at.
    sent = (select(DeliveryAttempt.delivery_id.label('delivery_id'),
        DeliveryAttempt.owner_user_id.label('owner'), func.min(DeliveryAttempt.finished_at).label('sent_at'))
        .where(DeliveryAttempt.state == 'sent').group_by(DeliveryAttempt.delivery_id, DeliveryAttempt.owner_user_id).subquery())
    base_delivery = select(Delivery.id, Delivery.owner_user_id, Delivery.state, sent.c.sent_at).join(
        PreviewPlan, (PreviewPlan.id == Delivery.preview_id) & (PreviewPlan.owner_user_id == Delivery.owner_user_id)
    ).join(Artifact, (Artifact.id == PreviewPlan.draft_artifact_id) & (Artifact.owner_user_id == Delivery.owner_user_id)
    ).outerjoin(sent, (sent.c.delivery_id == Delivery.id) & (sent.c.owner == Delivery.owner_user_id)).where(
        *owned(Delivery), delivery_workflow.in_(allowed))
    visible_deliveries = base_delivery.subquery()
    sent_rows = select(visible_deliveries).where(visible_deliveries.c.state == 'sent',
        visible_deliveries.c.sent_at >= start, visible_deliveries.c.sent_at < end).subquery()
    checks = select(Check.id, Check.owner_user_id, Check.requested_url, Check.business_day).where(
        *owned(Check), Check.workflow.in_(allowed), Check.status == 'succeeded',
        Check.business_day.between(first, today)).subquery()
    cf = select(CloudflareRecord.id, CloudflareRecord.owner_user_id, CloudflareRecord.business_day).where(
        *owned(CloudflareRecord), CloudflareRecord.business_day.between(first, today),
        CloudflareRecord.state == 'SUBMITTED', 'cloudflare_worker' in allowed).subquery()
    metrics = {}
    if allowed & {'check_domain', 'quick_report', 'domain_worker'}:
        metrics['checked_urls'] = int(await session.scalar(select(func.count(distinct(checks.c.requested_url)))) or 0)
    if allowed & {'domain_worker', 'cloaking_review', 'domain_evidence_review', 'provider_replies'}:
        metrics['sent_emails'] = int(await session.scalar(select(func.count()).select_from(sent_rows)) or 0)
        for state in ('failed', 'unknown'):
            metrics[state + '_deliveries'] = int(await session.scalar(select(func.count()).select_from(visible_deliveries).where(
                visible_deliveries.c.state == state)) or 0)
    if 'cloudflare_worker' in allowed:
        metrics['cloudflare_submitted'] = int(await session.scalar(select(func.count()).select_from(cf)) or 0)

    trend = {str(first + timedelta(days=i)): {'date': str(first + timedelta(days=i))} for i in range(days)}
    sent_day = day_of(sent_rows.c.sent_at)
    series = [
        ('checked_urls', select(checks.c.business_day, func.count(distinct(checks.c.requested_url))).group_by(checks.c.business_day)),
        ('sent_emails', select(sent_day, func.count()).group_by(sent_day)),
        ('cloudflare_submitted', select(cf.c.business_day, func.count()).group_by(cf.c.business_day)),
    ]
    for key, statement in series:
        if key not in metrics: continue
        for point in trend.values(): point[key] = 0
        for day, count in await session.execute(statement):
            if str(day) in trend: trend[str(day)][key] = count

    pending = []
    for kind, name in [('cloaking', 'cloaking_review'), ('domain_evidence', 'domain_evidence_review')]:
        if name not in allowed: continue
        valid_source = select(CaseSource.id).join(Check, (Check.id == CaseSource.check_id) &
            (Check.owner_user_id == CaseSource.owner_user_id)).where(
            CaseSource.case_id == ReviewCase.id, CaseSource.owner_user_id == ReviewCase.owner_user_id,
            Check.workflow == 'domain_worker', Check.status == 'succeeded', Check.business_day == today,
            Check.result['recipients'][0].astext.is_not(None),
            Check.result['disposition'].astext.in_(['cloaking_review'] if kind == 'cloaking' else ['evidence_review', 'ready'])
        ).exists()
        count = await session.scalar(select(func.count()).select_from(ReviewCase).where(
            *owned(ReviewCase), ReviewCase.case_type == kind, ReviewCase.business_day == today,
            ReviewCase.state.in_(ACTIVE_REVIEW), valid_source))
        pending.append({'feature': name, 'count': int(count or 0), 'href': FEATURES[name][1]})

    last_sync = None
    if 'provider_replies' in allowed:
        # Use the same reply ledger/preview binding, without opening private bodies.
        replied = select(Delivery.id).join(PreviewPlan, (PreviewPlan.id == Delivery.preview_id) &
            (PreviewPlan.owner_user_id == Delivery.owner_user_id)).where(
                Delivery.owner_user_id == ReviewCase.owner_user_id, PreviewPlan.resource_id == ReviewCase.id,
                PreviewPlan.resource_type == 'review_case', Delivery.workflow == 'provider_replies',
                Delivery.state.in_(['sent', 'sending', 'unknown'])).exists()
        count = await session.scalar(select(func.count()).select_from(ReviewCase).join(ReviewSnapshot,
            (ReviewSnapshot.context_key == cast(ReviewCase.id, String)) &
            (ReviewSnapshot.owner_user_id == ReviewCase.owner_user_id)).where(
            *owned(ReviewCase), ReviewCase.case_type == 'provider_reply', ReviewSnapshot.feature == 'provider_replies',
            ReviewCase.business_day == today, ReviewSnapshot.sanitized_state['needs_reply'].astext == 'true',
            ReviewSnapshot.sanitized_state['provider'].astext == 'cloudflare',
            func.coalesce(ReviewSnapshot.sanitized_state['sent_match_id'].astext, '') == '', ~replied))
        pending.append({'feature': 'provider_replies', 'count': int(count or 0), 'href': '/provider-replies'})
        last_sync = await session.scalar(select(func.max(AuditEvent.occurred_at)).where(
            *owned(AuditEvent), AuditEvent.action == 'provider_replies.sync', AuditEvent.outcome == 'success'))

    workers = []
    if 'domain_worker' in allowed:
        jobs = (await session.execute(select(Job, User.display_name).join(User, User.id == Job.owner_user_id).where(
            *owned(Job), Job.job_type.in_(['domain_worker_precheck', 'domain_worker_send']),
            (Job.state.in_(['queued', 'running', 'stopping'])) |
            ((Job.state == 'stopped') & (Job.business_day == today) & (Job.result_metadata['control_mode'].astext == 'pause'))
        ).order_by(Job.created_at.desc()).limit(20))).all()
        job_ids = [job.id for job, _ in jobs]
        counts = {identity: (total, done) for identity, total, done in await session.execute(select(
            JobItem.job_id, func.count(), func.count().filter(JobItem.state.in_([
                'succeeded', 'failed', 'excluded', 'review', 'ready', 'unknown', 'submitted', 'already_submitted', 'sent', 'partial'
            ]))).where(JobItem.job_id.in_(job_ids)).group_by(JobItem.job_id))} if job_ids else {}
        for job, name in jobs:
            total, done = counts.get(job.id, (0, 0))
            workers.append({'id': str(job.id), 'owner_name': name, 'feature': 'domain_worker', 'state': job.state,
                'total': total, 'completed': done, 'stale': job.state in {'running', 'stopping'} and
                    (job.heartbeat_at is None or now - job.heartbeat_at > timedelta(seconds=90)),
                'href': '/domain-worker' if job.owner_user_id == actor.user_id and 'domain_worker.view' in actor.permissions else None})
    if 'cloudflare_worker' in allowed:
        batches = (await session.execute(select(ReviewSnapshot, User.display_name).join(User, User.id == ReviewSnapshot.owner_user_id).where(
            *owned(ReviewSnapshot), ReviewSnapshot.feature == 'cloudflare_worker',
            ReviewSnapshot.sanitized_state['phase'].astext.in_(['CHECKING', 'SENDING', 'SEND_QUEUED', 'PAUSED', 'WAITING_FOR_SESSION'])
        ).order_by(ReviewSnapshot.created_at.desc()).limit(20))).all()
        record_ids = []
        for batch, _ in batches:
            for value in batch.sanitized_state.get('record_ids') or []:
                try: record_ids.append(UUID(value))
                except (TypeError, ValueError): continue
        records = {(row.owner_user_id, str(row.id)): row.state for row in await session.scalars(
            select(CloudflareRecord).where(*owned(CloudflareRecord), CloudflareRecord.id.in_(record_ids)))} if record_ids else {}
        for batch, name in batches:
            state = batch.sanitized_state
            heartbeat = state.get('heartbeat')
            try: stale = now - datetime.fromisoformat(heartbeat) > timedelta(seconds=90) if heartbeat else True
            except (TypeError, ValueError): stale = True
            ids = state.get('record_ids') or []
            done = len(ids) if state.get('phase') == 'CHECKING' else sum(
                records.get((batch.owner_user_id, value)) in {'SUBMITTED', 'ALREADY_SUBMITTED', 'FAILED', 'UNKNOWN', 'EXCLUDED'} for value in ids)
            workers.append({'id': str(batch.id), 'owner_name': name, 'feature': 'cloudflare_worker', 'state': state.get('phase'),
                'total': len(state.get('urls') or []), 'completed': done,
                'stale': stale and state.get('phase') in {'CHECKING', 'SENDING'},
                'href': '/cloudflare-worker' if batch.owner_user_id == actor.user_id and 'cloudflare_worker.view' in actor.permissions else None})

    users, user_total, pending_users = [], None, None
    if scope == 'all':
        user_total = int(await session.scalar(select(func.count()).select_from(User)) or 0)
        pending_users = int(await session.scalar(select(func.count()).select_from(User).where(User.status == 'pending')) or 0)
        roster = list(await session.scalars(select(User).where(*([User.id == user_id] if user_id else [])).order_by(User.display_name, User.id).offset(offset).limit(50)))
        ids = [row.id for row in roster]
        check_counts = dict((await session.execute(select(checks.c.owner_user_id, func.count(distinct(checks.c.requested_url))).where(checks.c.owner_user_id.in_(ids)).group_by(checks.c.owner_user_id))).all())
        sent_counts = dict((await session.execute(select(sent_rows.c.owner_user_id, func.count()).where(sent_rows.c.owner_user_id.in_(ids)).group_by(sent_rows.c.owner_user_id))).all())
        failed_counts = dict((await session.execute(select(visible_deliveries.c.owner_user_id, func.count()).where(visible_deliveries.c.owner_user_id.in_(ids), visible_deliveries.c.state.in_(['failed', 'unknown'])).group_by(visible_deliveries.c.owner_user_id))).all())
        for row in roster:
            users.append({'id': str(row.id), 'name': row.display_name, 'status': row.status,
                **({'checked_urls': check_counts.get(row.id, 0)} if 'checked_urls' in metrics else {}),
                **({'sent_emails': sent_counts.get(row.id, 0), 'attention': failed_counts.get(row.id, 0)} if 'sent_emails' in metrics else {})})

    # Audit access is independently authorized. Return no raw metadata or bodies.
    events = []
    if 'audit_logs.view' in actor.permissions and (scope == 'own' or 'audit_logs.view_all' in actor.permissions):
        for event in await session.scalars(select(AuditEvent).where(*owned(AuditEvent),
            AuditEvent.occurred_at >= start, AuditEvent.occurred_at < end).order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(8)):
            events.append({'id': str(event.id), 'action': event.action, 'outcome': event.outcome, 'at': event.occurred_at})
    return {'scope': scope, 'days': days, 'timezone': actor.timezone, 'start': first, 'end': today,
        'updated_at': now, 'features': sorted(allowed), 'metrics': metrics, 'trend': list(trend.values()),
        'pending': pending, 'workers': workers, 'users': users, 'user_total': user_total,
        'pending_users': pending_users, 'users_offset': offset, 'last_mail_sync': last_sync, 'events': events}

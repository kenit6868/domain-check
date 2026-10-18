"""Bounded audit metadata and durable, owner-scoped read model."""
import asyncio
import base64
import json
import logging
import re
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from domaincheck.infrastructure.db.models import AuditEvent, User

TOKEN = re.compile(r'^[a-zA-Z0-9_.:-]{1,100}$')
COUNTS = {'count', 'url_count', 'processed', 'failed_count', 'item_count', 'pending_count',
          'permission_count', 'role_count', 'http_status', 'attempt_number', 'image_count', 'imported', 'revision'}
CODES = {'reason_code', 'state', 'from', 'to', 'transport', 'job_type', 'mode', 'provider', 'smtp_stage'}
IDS = {'job_id', 'check_id', 'preview_id', 'attempt_id', 'sender_account_id', 'delivery_id'}
OUTCOMES = {'FAILED': 'failed', 'CHECK_FAILED': 'failed', 'UNKNOWN': 'unknown',
    'SUBMITTED': 'success', 'ALREADY_SUBMITTED': 'success', 'SENDING': 'pending',
    'SUBMITTING': 'pending', 'SEND_QUEUED': 'pending', 'QUEUED': 'pending', 'CANCELLED': 'stopped'}


def safe_metadata(values):
    result = {}
    if not isinstance(values, dict): return result
    for key, value in (values or {}).items():
        if key in COUNTS and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**9:
            result[key] = value
        elif key in CODES and isinstance(value, str) and TOKEN.fullmatch(value):
            result[key] = value
        elif key in IDS:
            try: result[key] = str(UUID(str(value)))
            except (ValueError, TypeError): pass
        elif key == 'fields' and isinstance(value, list):
            result[key] = [item for item in value[:40] if isinstance(item, str) and TOKEN.fullmatch(item)]
        elif key in {'added_permissions', 'removed_permissions'} and isinstance(value, list):
            from domaincheck.domain.authz import PERMISSIONS_BY_CODE, RETIRED_PERMISSION_CODES
            result[key] = [item for item in value[:200] if isinstance(item, str) and (item in PERMISSIONS_BY_CODE or item in RETIRED_PERMISSION_CODES)]
        elif key in {'added_role_ids', 'removed_role_ids'} and isinstance(value, list):
            valid = []
            for item in value[:100]:
                try: valid.append(str(UUID(str(item))))
                except ValueError: pass
            result[key] = valid
    return result


async def record(session, *, owner=None, actor=None, action, resource_type='operation',
                 resource_id=None, outcome='success', request_id='system', metadata=None, event_key=None):
    if not TOKEN.fullmatch(action) or not TOKEN.fullmatch(outcome):
        raise ValueError('Invalid audit event code')
    await session.execute(insert(AuditEvent).values(id=uuid4(), owner_user_id=owner,
        actor_user_id=actor, actor_type='user' if actor is not None else 'anonymous',
        action=action, resource_type=resource_type, resource_id=resource_id,
        outcome=outcome, request_id=request_id[:128], metadata_allowlist=safe_metadata(metadata),
        event_key=event_key).on_conflict_do_nothing(index_elements=['event_key']))


async def persist(request, **values):
    """Failure/security observations survive a rejected request transaction.

    This optional recorder never turns an accepted external side effect into a
    failure/retry. Critical intent/outcome events are written with their ledger.
    """
    try:
        async with asyncio.timeout(2):
            async with request.app.state.database.session_factory() as session, session.begin():
                await record(session, request_id=getattr(request.state, 'request_id', 'system'), **values)
    except Exception:
        logging.getLogger(__name__).warning('audit_event_persistence_unavailable')


def category_expression():
    return case((or_(AuditEvent.action.like('auth.%'), AuditEvent.action.like('security.%')), 'auth'),
        (or_(*[AuditEvent.action.like(prefix + '.%') for prefix in
               ('users', 'roles', 'mail_accounts', 'mail_profile')],
             AuditEvent.action == 'cloudflare_worker.settings'), 'admin'), else_='activity')


def outcome_expression():
    raw = case((AuditEvent.action == 'cloudflare_worker.checkpoint', func.coalesce(AuditEvent.metadata_allowlist['state'].astext, AuditEvent.outcome)),
               else_=AuditEvent.outcome)
    return case(OUTCOMES, value=raw, else_=raw)


def serialize(row, actor_name, owner_name):
    metadata = safe_metadata(row.metadata_allowlist)
    return {'id': str(row.id), 'occurred_at': row.occurred_at.isoformat(),
        'owner_user_id': str(row.owner_user_id) if row.owner_user_id else None,
        'actor_user_id': str(row.actor_user_id) if row.actor_user_id else None,
        'actor_name': actor_name, 'owner_name': owner_name, 'actor_type': row.actor_type,
        'action': row.action, 'resource_type': row.resource_type,
        'resource_id': str(row.resource_id) if row.resource_id else None,
        'outcome': OUTCOMES.get(metadata.get('state'), metadata.get('state', row.outcome))
            if row.action == 'cloudflare_worker.checkpoint' else OUTCOMES.get(row.outcome, row.outcome),
        'request_id': row.request_id,
        'metadata': metadata}


class AuditLogReader:
    def __init__(self, session, actor):
        self.session, self.actor = session, actor

    def query(self, scope):
        if self.actor.status != 'active' or 'audit_logs.view' not in self.actor.permissions:
            raise PermissionError('Audit view permission required')
        if scope == 'all' and 'audit_logs.view_all' not in self.actor.permissions:
            raise PermissionError('Global audit permission required')
        actor_user, owner_user = aliased(User), aliased(User)
        query = select(AuditEvent, actor_user.display_name.label('actor_name'), owner_user.display_name.label('owner_name')).outerjoin(
            actor_user, actor_user.id == AuditEvent.actor_user_id).outerjoin(
            owner_user, owner_user.id == AuditEvent.owner_user_id)
        if scope != 'all': query = query.where(AuditEvent.owner_user_id == self.actor.user_id)
        return query

    async def list(self, *, scope, category, start, end, user_id=None, feature=None,
                   action=None, outcome=None, request_id=None, cursor=None, limit=50, user=None):
        if start > end or (end - start).days > 366:
            raise ValueError('Date range must be ordered and at most 367 days')
        zone = ZoneInfo(self.actor.timezone)
        query = self.query(scope).where(
            category_expression() == category,
            AuditEvent.occurred_at >= datetime.combine(start, time.min, zone),
            AuditEvent.occurred_at < datetime.combine(end + timedelta(days=1), time.min, zone))
        if user_id: query = query.where(or_(AuditEvent.actor_user_id == user_id, AuditEvent.owner_user_id == user_id))
        if user:
            try:
                identity = UUID(user)
                query = query.where(or_(AuditEvent.actor_user_id == identity, AuditEvent.owner_user_id == identity))
            except ValueError:
                query = query.where(or_(query.selected_columns.actor_name.icontains(user, autoescape=True),
                                       query.selected_columns.owner_name.icontains(user, autoescape=True)))
        if feature: query = query.where(AuditEvent.action.startswith(feature + '.', autoescape=True))
        if action: query = query.where(AuditEvent.action == action)
        if outcome: query = query.where(outcome_expression() == outcome)
        if request_id: query = query.where(AuditEvent.request_id == request_id)
        if cursor:
            try:
                stamp, identity = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                stamp, identity = datetime.fromisoformat(stamp), UUID(identity)
                if stamp.tzinfo is None: raise ValueError('timezone')
            except Exception:
                raise ValueError('Invalid audit cursor') from None
            query = query.where(or_(AuditEvent.occurred_at < stamp,
                and_(AuditEvent.occurred_at == stamp, AuditEvent.id < identity)))
        rows = list(await self.session.execute(query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(limit + 1)))
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1][0]
            next_cursor = base64.urlsafe_b64encode(json.dumps([last.occurred_at.isoformat(), str(last.id)]).encode()).decode().rstrip('=')
        return {'items': [serialize(*row) for row in rows[:limit]], 'next_cursor': next_cursor}

    async def detail(self, identity, scope):
        row = (await self.session.execute(self.query(scope).where(AuditEvent.id == identity))).one_or_none()
        return serialize(*row) if row else None

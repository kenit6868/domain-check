"""Read-only audit history. No edit, delete or export endpoint."""
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from domaincheck.api.dependencies import get_db_session, require_permission
from domaincheck.application.audit_log import AuditLogReader

router = APIRouter(tags=['audit log'])


@router.get('/audit-logs')
async def list_events(response: Response,
    category: Literal['auth', 'activity', 'admin'] = 'auth', scope: Literal['own', 'all'] = 'own',
    start: str | None = Query(default=None, max_length=10), end: str | None = Query(default=None, max_length=10),
    user_id: UUID | None = None, feature: str | None = Query(default=None, max_length=80, pattern=r'^[a-z_]+$'),
    user: str | None = Query(default=None, max_length=100),
    action: str | None = Query(default=None, max_length=160), outcome: str | None = Query(default=None, max_length=40),
    request_id: str | None = Query(default=None, max_length=128), cursor: str | None = Query(default=None, max_length=240),
    limit: int = Query(default=50, ge=1, le=100),
    actor=Depends(require_permission('audit_logs.view')), session=Depends(get_db_session)):
    response.headers['Cache-Control'] = 'private, no-store'
    today = datetime.now(ZoneInfo(actor.timezone)).date()
    from datetime import date
    try:
        return await AuditLogReader(session, actor).list(scope=scope, category=category,
            start=date.fromisoformat(start) if start else today - timedelta(days=6),
            end=date.fromisoformat(end) if end else today, user_id=user_id, feature=feature,
            action=action, outcome=outcome, request_id=request_id, cursor=cursor, limit=limit, user=user)
    except PermissionError: raise HTTPException(403, 'Permission denied') from None
    except ValueError as exc: raise HTTPException(422, str(exc)) from None


@router.get('/audit-logs/{identity}')
async def detail(identity: UUID, response: Response, scope: Literal['own', 'all'] = 'own',
    actor=Depends(require_permission('audit_logs.view')), session=Depends(get_db_session)):
    response.headers['Cache-Control'] = 'private, no-store'
    try: result = await AuditLogReader(session, actor).detail(identity, scope)
    except PermissionError: raise HTTPException(403, 'Permission denied') from None
    if result is None: raise HTTPException(404, 'Audit event not found')
    return result

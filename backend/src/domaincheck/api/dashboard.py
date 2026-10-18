from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from domaincheck.api.dependencies import get_db_session, require_permission
from domaincheck.application.dashboard import overview

router = APIRouter(tags=['dashboard'])


@router.get('/dashboard')
async def dashboard(request: Request, response: Response,
    scope: Literal['own', 'all'] = 'own', days: int = Query(default=7, ge=1, le=30),
    user_id: UUID | None = None, feature: str | None = Query(default=None, max_length=60),
    offset: int = Query(default=0, ge=0, le=1000000),
    actor=Depends(require_permission('dashboard.view')), session=Depends(get_db_session)):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        return await overview(session, actor, scope=scope, days=days, user_id=user_id,
            feature=feature, offset=offset, now=request.app.state.clock.now())
    except PermissionError: raise HTTPException(403, 'Permission denied') from None
    except ValueError: raise HTTPException(422, 'Invalid dashboard filter') from None

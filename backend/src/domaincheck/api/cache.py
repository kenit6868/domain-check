from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from domaincheck.api.dependencies import require_csrf
from domaincheck.application.auth import AuthenticatedUser
from domaincheck.infrastructure.cache import (
    CACHE_CLEAR_PERMISSIONS,
    CacheUnavailable,
    business_day,
)


router = APIRouter(tags=["cache"])


class CacheClearInput(BaseModel):
    feature: Literal["quick-report", "cloudflare-worker", "domain-worker"]
    business_day: date | None = None


class CacheClearResponse(BaseModel):
    feature: str
    business_day: date
    deleted_count: int


@router.post("/cache/clear", response_model=CacheClearResponse)
async def clear_cache(
    body: CacheClearInput,
    request: Request,
    actor: AuthenticatedUser = Depends(require_csrf),
) -> CacheClearResponse:
    permission = CACHE_CLEAR_PERMISSIONS.get(body.feature)
    if actor.status != "active" or permission is None or permission not in actor.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Permission denied")
    selected_day = body.business_day or business_day(request.app.state.clock.now(), actor.timezone)
    try:
        deleted = await request.app.state.scoped_cache.clear_feature_day(
            actor.user_id, body.feature, selected_day
        )
    except CacheUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return CacheClearResponse(
        feature=body.feature, business_day=selected_day, deleted_count=deleted
    )

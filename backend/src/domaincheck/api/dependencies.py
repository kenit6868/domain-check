from __future__ import annotations

import hmac

from collections.abc import Callable

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.application.auth import AuthenticatedUser, AuthenticationError, AuthService
from domaincheck.infrastructure.auth.sessions import hash_token


async def get_db_session(request: Request):
    async with request.app.state.database.session_factory() as session:
        async with session.begin():
            yield session


async def current_user(
    request: Request,
    dc_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> AuthenticatedUser:
    if not dc_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    try:
        user = await AuthService(session).authenticate(dc_session)
        request.state.audit_actor = user
        return user
    except AuthenticationError as exc:
        if exc.user_id and exc.session_id:
            from domaincheck.application.audit_log import persist
            await persist(request, owner=exc.user_id, actor=exc.user_id,
                action='auth.' + exc.reason_code, resource_type='session', resource_id=exc.session_id,
                outcome='denied', metadata={'reason_code': exc.reason_code},
                event_key=f'session-terminal:{exc.session_id}')
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


async def require_csrf(
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> AuthenticatedUser:
    origin = request.headers.get("origin")
    if origin not in request.app.state.settings.allowed_origins:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origin is not allowed")
    if not csrf_header or not hmac.compare_digest(hash_token(csrf_header), user.csrf_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF validation failed")
    return user


def require_permission(code: str, *, csrf: bool = False, configured_profile: bool = False) -> Callable:
    """Build an API dependency that enforces a registered action permission.

    Read endpoints use the authenticated session dependency. Mutating endpoints
    additionally pass through the Origin/CSRF boundary.
    """

    dependency = require_csrf if csrf else current_user

    async def check(user: AuthenticatedUser = Depends(dependency),
                    session: AsyncSession = Depends(get_db_session)) -> AuthenticatedUser:
        if user.status != "active" or code not in user.permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Permission denied")
        if configured_profile:
            from domaincheck.application.profile_readiness import missing_report_profile
            from domaincheck.infrastructure.db.models import UserSettings
            missing = missing_report_profile(await session.get(UserSettings, user.user_id))
            if missing:
                raise HTTPException(status.HTTP_409_CONFLICT,
                    "Complete your organization profile before using Quick Report: " + ", ".join(missing))
        return user

    return check


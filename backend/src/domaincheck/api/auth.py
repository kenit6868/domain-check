from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.api.dependencies import current_user, get_db_session, require_csrf
from domaincheck.application.auth import AuthenticatedUser, AuthenticationError, AuthService
from domaincheck.infrastructure.auth.oidc import OIDCError
from domaincheck.application.audit_log import persist


router = APIRouter(tags=["auth"])


class MeResponse(BaseModel):
    id: str
    display_name: str
    telegram_username: str | None
    status: str
    timezone: str
    permissions: list[str]
    roles: list[str]


class TelegramAuthStatusResponse(BaseModel):
    configured: bool
    issuer: str
    redirect_uri: str


@router.get("/auth/telegram/start")
async def telegram_start(request: Request, return_path: str | None = Query(default=None)):
    oidc = request.app.state.oidc
    if oidc is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Telegram login is not configured")
    authorization = await oidc.create_authorization_request(return_path=return_path)
    response = RedirectResponse(authorization.authorization_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        "dc_oidc_binding",
        authorization.browser_binding,
        max_age=request.app.state.settings.oidc_state_ttl_seconds,
        httponly=True,
        secure=request.app.state.settings.environment == "production",
        samesite="lax",
        path="/api/v1/auth/telegram/callback",
    )
    return response


@router.get("/auth/telegram/status", response_model=TelegramAuthStatusResponse)
async def telegram_status(request: Request) -> TelegramAuthStatusResponse:
    settings = request.app.state.settings
    return TelegramAuthStatusResponse(
        configured=request.app.state.oidc is not None,
        issuer=settings.telegram_issuer,
        redirect_uri=settings.telegram_redirect_uri,
    )


@router.get("/auth/telegram/callback")
async def telegram_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_db_session),
):
    if error or not code or not state:
        await persist(request, action='auth.login_failed', outcome='denied', metadata={'reason_code': 'provider_denied_or_missing_parameters'})
        response = RedirectResponse("/login?login_error=telegram_denied", status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie("dc_oidc_binding", path="/api/v1/auth/telegram/callback")
        return response
    binding = request.cookies.get("dc_oidc_binding")
    if not binding or request.app.state.oidc is None:
        await persist(request, action='auth.login_failed', outcome='denied', metadata={'reason_code': 'binding_expired_or_missing'})
        response = RedirectResponse('/login?login_error=telegram_expired', status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie('dc_oidc_binding', path='/api/v1/auth/telegram/callback')
        return response
    try:
        claims, _return_path = await request.app.state.oidc.exchange_code(
            code=code, state=state, browser_binding=binding
        )
        login = await AuthService(session, request.state.request_id).login_oidc_identity(
            issuer=str(claims["iss"]),
            subject=str(claims["sub"]),
            display_name=str(claims.get("name") or claims.get("preferred_username") or "Telegram user"),
            telegram_username=(str(claims["preferred_username"]) if claims.get("preferred_username") else None),
            timezone=request.app.state.settings.default_timezone,
        )
    except (OIDCError, AuthenticationError, KeyError) as exc:
        owner = exc.user_id if isinstance(exc, AuthenticationError) else None
        await persist(request, owner=owner, actor=owner, action='auth.login_failed', outcome='denied',
            metadata={'reason_code': exc.reason_code if isinstance(exc, AuthenticationError) else 'oidc_validation_failed'})
        response = RedirectResponse("/login?login_error=telegram_callback", status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie("dc_oidc_binding", path="/api/v1/auth/telegram/callback")
        return response
    destination = "/"
    response = RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
    secure = request.app.state.settings.environment == "production"
    response.delete_cookie("dc_oidc_binding", path="/api/v1/auth/telegram/callback")
    response.set_cookie("dc_session", login.token, expires=login.expires_at, httponly=True, secure=secure, samesite="lax", path="/")
    response.set_cookie("dc_csrf", login.csrf_token, expires=login.expires_at, httponly=False, secure=secure, samesite="lax", path="/")
    return response


@router.get("/me", response_model=MeResponse)
async def me(user: AuthenticatedUser = Depends(current_user)) -> MeResponse:
    return MeResponse(
        id=str(user.user_id), display_name=user.display_name,
        telegram_username=user.telegram_username, status=user.status,
        timezone=user.timezone, permissions=sorted(user.permissions),
        roles=list(user.roles),
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    user: AuthenticatedUser = Depends(require_csrf),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    await AuthService(session, request.state.request_id).revoke(user.session_id)
    response.delete_cookie("dc_session", path="/")
    response.delete_cookie("dc_csrf", path="/")

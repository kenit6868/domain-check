"""Explicit owned precheck, batch control, and public Account ID settings."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from domaincheck.api.dependencies import require_permission
from domaincheck.application.cloudflare_worker import CloudflareConflict, CloudflareService, account_id
from domaincheck.cloudflare_runner import dispatch

router = APIRouter(tags=['cloudflare worker'])


def service(request, actor):
    return CloudflareService(request.app.state.database.session_factory, request.app.state.artifact_store,
        actor.user_id, actor.timezone, request.app.state.clock.now)


def error(exc):
    if isinstance(exc, LookupError): return HTTPException(404, 'Cloudflare batch not found')
    if isinstance(exc, CloudflareConflict): return HTTPException(409, str(exc))
    return HTTPException(409, 'Cloudflare operation failed; reload and inspect batch status')


class DashboardSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: str = Field(default='', max_length=32)


@router.get('/settings/cloudflare-dashboard')
async def dashboard(request: Request, response: Response, actor=Depends(require_permission('mail_accounts.view_own'))):
    response.headers['Cache-Control'] = 'private, no-store'
    return await service(request, actor).settings()


@router.put('/settings/cloudflare-dashboard')
async def save_dashboard(body: DashboardSettings, request: Request,
    actor=Depends(require_permission('mail_accounts.manage_own', csrf=True))):
    try: return await service(request, actor).settings(account_id(body.account_id))
    except Exception as exc: raise error(exc) from None


@router.get('/cloudflare-worker')
async def workspace(request: Request, response: Response, actor=Depends(require_permission('cloudflare_worker.view'))):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        return {**await service(request, actor).view(), 'submit_enabled': request.app.state.settings.cloudflare_submit_enabled}
    except Exception as exc: raise error(exc) from None


class PrecheckInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    urls: list[Annotated[str, Field(max_length=8192)]] = Field(min_length=1, max_length=200)


@router.post('/cloudflare-worker/precheck')
async def precheck(body: PrecheckInput, request: Request,
    actor=Depends(require_permission('cloudflare_worker.precheck', csrf=True, configured_profile=True))):
    svc = service(request, actor)
    try:
        identity = await svc.prepare(body.urls)
        bridge = getattr(request.app.state, 'cloudflare_dispatch', None) or dispatch
        try:
            await bridge(request.app.state.settings, {'owner': str(actor.user_id), 'batch_id': str(identity),
                'timezone': actor.timezone, 'checking': True})
        except Exception:
            await svc.dispatch_failed(identity, checking=True)
            raise CloudflareConflict('Cloudflare runner is unavailable; start domaincheck-cloudflare-runner') from None
        return await svc.view(identity)
    except Exception as exc: raise error(exc) from None


class SendInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cookie: SecretStr = Field(default_factory=lambda: SecretStr(''), max_length=32768)
    delay_seconds: int = Field(default=30, ge=0, le=300)
    confirmed: bool = False


async def send_command(identity, body, request, actor, *, action='start', retry=False):
    svc = service(request, actor)
    try:
        await svc.view(identity)  # Verify owner before forwarding a Cookie to the private runner.
        if not request.app.state.settings.cloudflare_submit_enabled:
            raise CloudflareConflict('Cloudflare submissions are disabled')
        bridge = getattr(request.app.state, 'cloudflare_dispatch', None) or dispatch
        await bridge(request.app.state.settings, {'owner': str(actor.user_id), 'batch_id': str(identity),
            'timezone': actor.timezone, 'cookie': body.cookie.get_secret_value(), 'action': action,
            'delay': body.delay_seconds, 'confirmed': body.confirmed, 'retry': retry})
        return await svc.view(identity)
    except Exception as exc: raise error(exc) from None


@router.post('/cloudflare-worker/{identity}/start')
async def start(identity: UUID, body: SendInput, request: Request,
    actor=Depends(require_permission('cloudflare_worker.start', csrf=True, configured_profile=True))):
    return await send_command(identity, body, request, actor)


@router.post('/cloudflare-worker/{identity}/retry')
async def retry(identity: UUID, body: SendInput, request: Request,
    actor=Depends(require_permission('cloudflare_worker.retry', csrf=True, configured_profile=True))):
    return await send_command(identity, body, request, actor, retry=True)


@router.post('/cloudflare-worker/{identity}/resume')
async def resume(identity: UUID, body: SendInput, request: Request,
    actor=Depends(require_permission('cloudflare_worker.resume', csrf=True, configured_profile=True))):
    return await send_command(identity, body, request, actor, action='resume')


@router.post('/cloudflare-worker/{identity}/{action}')
async def control(identity: UUID, action: Literal['pause', 'cancel'], request: Request,
    actor=Depends(require_permission('cloudflare_worker.stop', csrf=True))):
    try:
        svc = service(request, actor)
        await svc.command(identity, action)
        return await svc.view(identity)
    except Exception as exc: raise error(exc) from None


@router.delete('/cloudflare-worker/cache')
async def clear(request: Request, actor=Depends(require_permission('cloudflare_worker.clear_cache', csrf=True))):
    try:
        await service(request, actor).clear()
        return {'cleared': True}
    except Exception as exc: raise error(exc) from None

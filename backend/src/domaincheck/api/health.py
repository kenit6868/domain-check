"""Authenticated aggregate service health, without job/user/connection metadata."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import text

from domaincheck.api.dependencies import current_user
from domaincheck.workers.runtime import HEALTH_PREFIX

router = APIRouter(tags=['health'])
WORKER_PERMISSIONS = {
    'quick_report': 'quick_report.view',
    'check_domain': 'check_domain.view',
    'domain_worker_precheck': 'domain_worker.view',
    'domain_worker_send': 'domain_worker.view',
    'browser_evidence': 'check_domain.view',
    'cloudflare_runner': 'cloudflare_worker.view',
}


async def worker_health(redis, names):
    result = []
    for name in names:
        ready = draining = 0
        async for key in redis.scan_iter(match=f'{HEALTH_PREFIX}{name}:*', count=100):
            value = await redis.get(key)
            ready += value == 'ready'
            draining += value == 'draining'
        result.append({'name': name, 'state': 'ready' if ready else 'draining' if draining else 'offline',
                       'slots': ready})
    return result


@router.get('/health/workers')
async def workers(request: Request, response: Response, actor=Depends(current_user)):
    if actor.status != 'active':
        raise HTTPException(403, 'Active account required')
    response.headers['Cache-Control'] = 'private, no-store'
    settings = request.app.state.settings
    names = [name for name, permission in WORKER_PERMISSIONS.items()
             if permission in actor.permissions and (name != 'browser_evidence' or settings.browser_capture_enabled)]
    try:
        services = await asyncio.wait_for(worker_health(request.app.state.redis, names), timeout=3)
    except Exception:
        services = [{'name': name, 'state': 'unknown', 'slots': 0} for name in names]
    return {'services': services}


@router.get('/health/ready')
async def ready(request: Request):
    async def check():
        async with request.app.state.database.engine.connect() as connection:
            await connection.execute(text('SELECT 1'))
        await request.app.state.redis.ping()
    try:
        await asyncio.wait_for(check(), timeout=3)
    except Exception:
        raise HTTPException(503, 'Backend dependencies unavailable') from None
    return {'status': 'ready'}

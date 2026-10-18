"""Private, authenticated HTTP bridge to the one process holding Dashboard cookies."""
from __future__ import annotations

import hashlib
import hmac
from contextlib import asynccontextmanager
from uuid import UUID

import anyio
import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import text
from redis.asyncio import Redis

from domaincheck.application.cloudflare_runner import CloudflareRunner
from domaincheck.application.cloudflare_worker import CloudflareConflict
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.db.session import Database
from domaincheck.settings import Settings
from domaincheck.workers.runtime import publish_health


def bridge_token(settings):
    if not settings.secret_master_key:
        raise CloudflareConflict('Configure the backend master key before starting the Cloudflare runner')
    return hmac.new(settings.secret_master_key.get_secret_value().encode(), b'cloudflare-runner-v1', hashlib.sha256).hexdigest()


class RunnerCommand(BaseModel):
    owner: UUID
    batch_id: UUID
    timezone: str
    checking: bool = False
    cookie: SecretStr = Field(default_factory=lambda: SecretStr(''), max_length=32768)
    action: str = 'start'
    delay: int = Field(default=30, ge=0, le=300)
    confirmed: bool = False
    retry: bool = False


async def dispatch(settings, payload):
    token = bridge_token(settings)
    def call():
        try:
            response = requests.post(settings.cloudflare_runner_url.rstrip('/') + '/run',
                headers={'Authorization': f'Bearer {token}'}, json=payload, timeout=15, allow_redirects=False)
        except requests.RequestException:
            raise CloudflareConflict('Cloudflare runner is unavailable; start domaincheck-cloudflare-runner') from None
        if response.status_code != 200:
            # Only accept fixed application error text; never relay untrusted proxy/HTTP bodies.
            code = response.json().get('code') if response.headers.get('Content-Type', '').startswith('application/json') else None
            messages = {
                'settings_account_mismatch': 'Account ID trong Cookie khác Account ID đã lưu tại Cài đặt mail. Kiểm tra lại tài khoản Cloudflare.',
                'batch_account_mismatch': 'Batch này đã gắn với tài khoản Cloudflare khác. Dùng Cookie cùng tài khoản hoặc tạo lượt kiểm tra mới.',
                'cookie_account_missing': 'Chưa cấu hình Cloudflare Account ID và Cookie không có curr-account. Nhập Account ID trong Cài đặt mail hoặc dùng Cookie đầy đủ có curr-account.',
                'cookie_account_invalid': 'Chưa cấu hình Account ID và không đọc được ID duy nhất từ curr-account. Nhập Account ID trong Cài đặt mail hoặc dùng Cookie có curr-account hợp lệ.',
                'cookie_invalid': 'Nhập Cookie hợp lệ trên một dòng để tiếp tục.',
                'command_rejected': 'Batch cannot start: check profile, permission, confirmation and eligible rows',
                'submissions_disabled': 'Cloudflare submissions are disabled on the runner'}
            raise CloudflareConflict(messages.get(code, 'Cloudflare runner rejected the request'))
    await anyio.to_thread.run_sync(call)


def create_runner_app(settings=None, manager=None):
    settings = settings or Settings()
    token = bridge_token(settings)
    database = Database(settings.database_url)
    manager = manager or CloudflareRunner(database.session_factory, FilesystemArtifactStore(settings.artifact_root),
        enabled=settings.cloudflare_submit_enabled, slots=settings.cloudflare_runner_slots)

    @asynccontextmanager
    async def lifespan(app):
        # One runner for this database even when several API processes share it.
        async with database.engine.connect() as connection:
            claimed = await connection.scalar(text('SELECT pg_try_advisory_lock(7348291741283)'))
            if not claimed: raise RuntimeError('A Cloudflare runner is already active for this database')
            await connection.commit()
            redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3)
            try:
                async with publish_health(redis, 'cloudflare_runner'):
                    yield
            finally:
                await manager.close()
                await redis.aclose()
                await connection.execute(text('SELECT pg_advisory_unlock(7348291741283)'))
                await connection.commit()
        await database.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get('/health/live')
    async def live():
        return {'status': 'ok'}

    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({'code': 'command_rejected'}, status_code=422)

    async def authorize(authorization: str = Header(default='')):
        if not hmac.compare_digest(authorization, f'Bearer {token}'):
            raise HTTPException(403, 'Forbidden')

    @app.post('/run', dependencies=[Depends(authorize)])
    async def run(body: RunnerCommand):
        try:
            await manager.attach(body.owner, body.batch_id, body.timezone, cookie=body.cookie.get_secret_value(),
                checking=body.checking, action=body.action, delay=body.delay, confirmed=body.confirmed, retry=body.retry)
            return {'accepted': True}
        except Exception as exc:
            # Never put Cookie, upstream response, URL, or exception text in the HTTP result.
            code = exc.code if isinstance(exc, CloudflareConflict) else 'command_rejected'
            from fastapi.responses import JSONResponse
            return JSONResponse({'code': code}, status_code=409)

    return app


def main():
    import uvicorn
    from domaincheck.settings import get_settings
    settings = get_settings()
    uvicorn.run(create_runner_app(settings), host=settings.cloudflare_runner_host, port=8789, access_log=False)


if __name__ == '__main__': main()

"""Single dedicated runner. Session cookies exist only in this process's memory."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from domaincheck.application.cloudflare_worker import CloudflareConflict, CloudflareService, digest
from domaincheck.infrastructure.db.models import Artifact, CloudflareRecord, Permission, RolePermission, User, UserRole


class CloudflareRunner:
    def __init__(self, factory, store, *, checker=None, submitter=None, enabled=False, now=None, slots=2):
        if not 1 <= slots <= 4:
            raise ValueError('Cloudflare runner slots must be between 1 and 4')
        self.capacity = asyncio.Semaphore(slots)
        self.factory, self.store = factory, store
        self.checker, self.submitter, self.enabled = checker, submitter, enabled
        self.now = now or (lambda: datetime.now(UTC))
        self.tasks, self.cookies, self.attach_locks = {}, {}, {}
        self.runner_id = str(uuid4())

    def service(self, owner, timezone):
        return CloudflareService(self.factory, self.store, owner, timezone, self.now)

    async def allowed(self, session, owner, permission):
        user = await session.get(User, owner)
        if user is None or user.status != 'active': return False
        return bool(await session.scalar(select(Permission.id).join(RolePermission,
            RolePermission.permission_id == Permission.id).join(UserRole, UserRole.role_id == RolePermission.role_id)
            .where(UserRole.user_id == owner, Permission.code == permission).limit(1)))

    async def attach(self, owner, identity, timezone, *, cookie='', checking=False,
                     action='start', delay=30, confirmed=False, retry=False):
        lock = self.attach_locks.setdefault(identity, asyncio.Lock())
        async with lock:
            service = self.service(owner, timezone)
            async with self.factory() as session:
                row = await service.batch(session, identity)
                await service.recover(session, row)
                data = row.sanitized_state
                if data.get('runner_id') not in {None, self.runner_id} and data['phase'] in {'CHECKING', 'SENDING', 'SEND_QUEUED', 'PAUSED', 'WAITING_FOR_SESSION'} and data.get('heartbeat'):
                    raise CloudflareConflict('Another runner holds this batch')
                permission = 'cloudflare_worker.precheck' if checking else ('cloudflare_worker.retry' if retry else
                    'cloudflare_worker.resume' if action == 'resume' else 'cloudflare_worker.start')
                if not await self.allowed(session, owner, permission): raise CloudflareConflict('Permission denied')
                if checking and row.sanitized_state['phase'] != 'CHECKING': raise CloudflareConflict('Precheck is closed')
            if not checking and not self.enabled: raise CloudflareConflict('Cloudflare submissions are disabled', code='submissions_disabled')
            resolved = None
            cookie = cookie or self.cookies.get(identity, '')
            if cookie:
                from domaincheck.engine.cloudflare_dashboard_session import CloudflareDashboardConfig, CloudflareDashboardSessionError, _account_id_from_cookie, _headers
                import re
                configured = (await service.settings())['account_id']
                try: _headers(cookie)
                except CloudflareDashboardSessionError:
                    raise CloudflareConflict('Cookie is empty or contains invalid line breaks', code='cookie_invalid') from None
                cookie_id = _account_id_from_cookie(cookie)
                cookie_value = cookie.split(':', 1)[1].strip() if cookie.strip().lower().startswith('cookie:') else cookie
                # A parser limitation is not evidence that the configured account differs.
                if not configured and re.search(r'(?:^|;\s*)curr-account=', cookie_value, re.I) and not cookie_id:
                    raise CloudflareConflict('Cookie curr-account is ambiguous or invalid', code='cookie_account_invalid')
                if not configured and not cookie_id:
                    raise CloudflareConflict('No configured Account ID and Cookie has no curr-account', code='cookie_account_missing')
                if configured and cookie_id and configured.lower() != cookie_id.lower():
                    raise CloudflareConflict('Cookie account differs from Account ID in Mail settings', code='settings_account_mismatch')
                resolved = CloudflareDashboardConfig.from_mapping({'cloudflare_account_id': configured}, cookie).account_id.lower()
                async with self.factory() as session:
                    row = await service.batch(session, identity)
                    existing = row.sanitized_state.get('account_id')
                    if existing and existing != resolved:
                        raise CloudflareConflict('This batch is bound to another Cloudflare account', code='batch_account_mismatch')
            if not checking:
                if not cookie and identity not in self.cookies:
                    raise CloudflareConflict('Enter a new Cloudflare Cookie to continue', code='cookie_invalid')
                # Validate Cookie before releasing a paused runner or queueing any requests.
                await service.command(identity, action, delay=delay, confirmed=confirmed, retry=retry)
                previous = self.tasks.get(identity)
                if previous and not previous.done() and previous.get_name() == 'cf-precheck':
                    await asyncio.shield(previous)
                if cookie: self.cookies[identity] = cookie
                if resolved:
                    async with self.factory() as session, session.begin():
                        row = await service.batch(session, identity)
                        row.sanitized_state = {**row.sanitized_state, 'account_id': resolved}
            if identity not in self.tasks or self.tasks[identity].done():
                async with self.factory() as session, session.begin():
                    row = await service.batch(session, identity)
                    row.sanitized_state = {**row.sanitized_state, 'runner_id': self.runner_id,
                        'heartbeat': self.now().isoformat()}
                self.tasks[identity] = asyncio.create_task(self.run(owner, identity, timezone, checking), name='cf-precheck' if checking else 'cf-send')

    async def pulse(self, service, identity, *, current=None):
        async with self.factory() as session, session.begin():
            row = await service.batch(session, identity)
            if row.sanitized_state.get('runner_id') != self.runner_id:
                raise CloudflareConflict('Runner claim changed')
            row.sanitized_state = {**row.sanitized_state, 'heartbeat': self.now().isoformat()}

    async def heartbeat(self, service, identity):
        while True:
            await asyncio.sleep(10)
            await self.pulse(service, identity)

    async def run(self, owner, identity, timezone, checking):
        service = self.service(owner, timezone)
        heartbeat = asyncio.create_task(self.heartbeat(service, identity))
        try:
            if checking: await self.precheck(service, identity)
            else: await self.send(service, identity)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Fixed codes only. Third-party exceptions can contain Cookie or server content.
            async with self.factory() as session, session.begin():
                row = await service.batch(session, identity)
                if row.sanitized_state.get('runner_id') != self.runner_id: return
                for record in await service.rows(session, row):
                    if record.state == 'SUBMITTING':
                        record.state = 'UNKNOWN'
                        record.sanitized_result = {**record.sanitized_result, 'error': 'runner_failed_after_intent'}
                row.sanitized_state = {**row.sanitized_state, 'phase': 'INTERRUPTED' if checking else 'WAITING_FOR_SESSION',
                    'error': 'runner_failed', 'heartbeat': None, 'current_id': None}
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self.cookies.pop(identity, None)

    async def precheck(self, service, identity):
        async with self.factory() as session:
            row = await service.batch(session, identity)
            targets = row.sanitized_state['urls']
            cfg, profile_version = await service.config(session)
        from domaincheck.engine import phishing_toolkit as toolkit
        from domaincheck.core.cloudflare_check import check
        checker = self.checker or check
        for url in targets:
            async with self.capacity:
                async with self.factory() as session, session.begin():
                    row = await service.batch(session, identity)
                    if row.sanitized_state['phase'] != 'CHECKING' or row.sanitized_state.get('runner_id') != self.runner_id: return
                    if row.business_day != service.day() or not await self.allowed(session, service.owner, 'cloudflare_worker.precheck'):
                        row.sanitized_state = {**row.sanitized_state, 'phase': 'INTERRUPTED', 'error': 'scope_changed'}
                        return
                    row.sanitized_state = {**row.sanitized_state, 'current_id': url}
                    prior = await session.scalar(select(CloudflareRecord).where(CloudflareRecord.owner_user_id == service.owner,
                        CloudflareRecord.canonical_url_key == digest(url), CloudflareRecord.state.in_(['SUBMITTED', 'ALREADY_SUBMITTED', 'UNKNOWN', 'SUBMITTING']))
                        .order_by(CloudflareRecord.created_at.desc()).limit(1))
                    if prior:
                        row.sanitized_state = {**row.sanitized_state,
                            'record_ids': [*row.sanitized_state['record_ids'], str(prior.id)],
                            'excluded_record_ids': [*row.sanitized_state.get('excluded_record_ids', []), str(prior.id)],
                            'processed': row.sanitized_state['processed'] + 1}
                        continue
                try:
                    result = await asyncio.to_thread(checker, url, cfg)
                    state = 'READY' if result.get('cloudflare') else 'NOT_CLOUDFLARE'
                    error = None
                    if result.get('_error'): state, error = 'CHECK_FAILED', 'precheck_failed'
                except Exception:
                    state, error = 'CHECK_FAILED', 'precheck_failed'
                draft = toolkit.generate_cloudflare_report_text(url.split('/')[2], cfg, url) if state == 'READY' else ''
                raw = json.dumps({'draft': draft, 'config': cfg, 'profile_version': profile_version}, ensure_ascii=False).encode()
                blob = self.store.store_preview_payload(owner_user_id=service.owner, preview_id=uuid4(), data=raw)
                try:
                    async with self.factory() as session, session.begin():
                        await service.lock(session)
                        row = await service.batch(session, identity)
                        if row.sanitized_state['phase'] != 'CHECKING' or row.sanitized_state.get('runner_id') != self.runner_id:
                            self.store.delete(storage_key=blob.storage_key)
                            return
                        record = await session.scalar(select(CloudflareRecord).where(CloudflareRecord.owner_user_id == service.owner,
                            CloudflareRecord.business_day == row.business_day, CloudflareRecord.canonical_url_key == digest(url)))
                        session.add(Artifact(id=blob.artifact_id, owner_user_id=service.owner, kind='cloudflare_worker:payload',
                            mime_type=blob.mime_type, byte_size=blob.byte_size, sha256=blob.sha256, storage_key=blob.storage_key, immutable=True))
                        if record is None:
                            record = CloudflareRecord(id=uuid4(), owner_user_id=service.owner, full_url=url,
                                canonical_url_key=digest(url), business_day=row.business_day, report_version=digest(draft),
                                idempotency_key=digest(f'{row.business_day}:{url}'), request_hash=blob.sha256, state=state)
                            session.add(record)
                        record.state, record.report_version, record.request_hash = state, digest(draft), blob.sha256
                        record.sanitized_result = {'artifact_id': str(blob.artifact_id), 'storage_key': blob.storage_key,
                            'sha256': blob.sha256, 'byte_size': blob.byte_size, 'attempts': 0, 'error': error}
                        row.sanitized_state = {**row.sanitized_state, 'record_ids': [*row.sanitized_state['record_ids'], str(record.id)],
                            'processed': row.sanitized_state['processed'] + 1,
                            'excluded_record_ids': [*row.sanitized_state.get('excluded_record_ids', []), *([str(record.id)] if state != 'READY' else [])]}
                except BaseException:
                    self.store.delete(storage_key=blob.storage_key)
                    raise
        async with self.factory() as session, session.begin():
            row = await service.batch(session, identity)
            if row.sanitized_state['phase'] == 'CHECKING':
                row.sanitized_state = {**row.sanitized_state, 'phase': 'CHECKED', 'current_id': None}

    async def send(self, service, identity):
        while True:
            # Release capacity between records and while paused/delayed; consent,
            # ownership and profile bindings are rechecked inside each step.
            async with self.capacity:
                result = await self._send_step(service, identity)
            if result is None:
                return
            if result == 'waiting':
                await asyncio.sleep(.5)

    async def _send_step(self, service, identity):
        from domaincheck.engine.cloudflare_dashboard_session import (submit_dashboard_report, CloudflareDashboardSessionError,
            CloudflareDashboardRateLimitError, CloudflareDashboardUnknownError, CloudflareDashboardDuplicateError,
            CloudflareDashboardError)
        submitter = self.submitter or submit_dashboard_report
        waiting = False
        async with self.factory() as session, session.begin():
            await service.lock(session)
            row = await service.batch(session, identity)
            data = dict(row.sanitized_state)
            if data.get('runner_id') != self.runner_id: return
            if data['phase'] in {'CANCELLED', 'COMPLETED', 'INTERRUPTED'}: return
            if row.business_day != service.day():
                row.sanitized_state = {**data, 'phase': 'CANCELLED', 'error': 'day_changed'}
                return
            if not await self.allowed(session, service.owner, data.get('permission', 'cloudflare_worker.start')):
                row.sanitized_state = {**data, 'phase': 'CANCELLED', 'error': 'permission_revoked'}
                return
            records = await service.rows(session, row)
            next_at = data.get('next_run_at')
            waiting = data['phase'] in {'PAUSED', 'WAITING_FOR_SESSION'} or (next_at and datetime.fromisoformat(next_at) > self.now())
            if not waiting:
                record = next((record for record in records if record.state == 'QUEUED'), None)
                if record is None:
                    row.sanitized_state = {**data, 'phase': 'COMPLETED', 'current_id': None, 'next_run_at': None}
                    return
                if identity not in self.cookies:
                    row.sanitized_state = {**data, 'phase': 'WAITING_FOR_SESSION', 'error': 'cookie_required'}
                    waiting = True
                else:
                    payload = await service.verified_payload(session, record)
                    _, version = await service.config(session)
                    if payload['profile_version'] != version:
                        row.sanitized_state = {**data, 'phase': 'CANCELLED', 'error': 'profile_changed_recheck'}
                        return
                    cookie, target, draft = self.cookies[identity], record.full_url, payload['draft']
                    cfg = {**payload['config'], 'cloudflare_account_id': data['account_id']}
                    record.state = 'SUBMITTING'
                    record.sanitized_result = {**record.sanitized_result, 'attempts': record.sanitized_result.get('attempts', 0) + 1, 'error': None}
                    record_id = record.id
                    row.sanitized_state = {**data, 'phase': 'SENDING', 'current_id': str(record_id), 'next_run_at': None}
        if waiting:
            return 'waiting'
        # Intent is committed before network I/O; no blind retry after a lost response.
        state, error, report_id, pause = 'SUBMITTED', None, None, None
        try:
            result = await asyncio.to_thread(submitter, target, draft, cfg, cookie)
            report_id = str(result.get('report_id') or '')[:500]
        except CloudflareDashboardSessionError:
            state, error, pause = 'QUEUED', 'cookie_rejected', 'WAITING_FOR_SESSION'
            self.cookies.pop(identity, None)
        except CloudflareDashboardRateLimitError:
            state, error, pause = 'QUEUED', 'provider_rate_limit_or_challenge', 'PAUSED'
        except CloudflareDashboardDuplicateError:
            state = 'ALREADY_SUBMITTED'
        except CloudflareDashboardUnknownError:
            state, error, pause = 'UNKNOWN', 'submission_unconfirmed', 'PAUSED'
        except CloudflareDashboardError:
            state, error = 'FAILED', 'provider_rejected'
        except Exception:
            state, error, pause = 'UNKNOWN', 'submission_unconfirmed', 'PAUSED'
        async with self.factory() as session, session.begin():
            row = await service.batch(session, identity)
            record = await session.get(CloudflareRecord, record_id)
            record.state, record.provider_report_id = state, report_id
            record.sanitized_result = {**record.sanitized_result, 'error': error}
            service.audit(session, 'checkpoint', record_id, {'state': state})
            data = dict(row.sanitized_state)
            if data.get('runner_id') != self.runner_id: return
            phase = data['phase'] if data['phase'] in {'CANCELLED', 'PAUSED'} else (pause or 'SENDING')
            row.sanitized_state = {**data, 'phase': phase, 'current_id': None,
                'error': error, 'next_run_at': (self.now() + timedelta(seconds=data['delay_seconds'])).isoformat()}
        return 'continue'

    async def close(self):
        for task in self.tasks.values(): task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.cookies.clear()


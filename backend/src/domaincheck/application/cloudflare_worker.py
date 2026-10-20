"""Owned Cloudflare Dashboard plans and durable state; cookies never enter this layer."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from domaincheck.application.profile_readiness import missing_report_profile
from domaincheck.core.investigation import validate_target
from domaincheck.core.cloudflare_input import parse_target_input
from domaincheck.infrastructure.db.models import Artifact, AuditEvent, CloudflareRecord, ReviewSnapshot, UserSettings

FEATURE = 'cloudflare_worker'
PROTECTED = {'SUBMITTED', 'ALREADY_SUBMITTED', 'UNKNOWN', 'SUBMITTING'}
ACTIVE = {'CHECKING', 'SENDING', 'SEND_QUEUED', 'PAUSED', 'WAITING_FOR_SESSION'}


class CloudflareConflict(ValueError):
    def __init__(self, message, *, code='command_rejected'):
        super().__init__(message)
        self.code = code


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def account_id(value):
    value = str(value or '').strip()
    if value and not re.fullmatch('[0-9a-fA-F]{32}', value):
        raise CloudflareConflict('Cloudflare Account ID must contain 32 hexadecimal characters')
    return value.lower()


class CloudflareService:
    def __init__(self, factory, store, owner, timezone='Asia/Bangkok', now=None):
        self.factory, self.store, self.owner = factory, store, owner
        self.timezone = timezone
        self.now = now or (lambda: datetime.now(UTC))

    def day(self):
        return self.now().astimezone(ZoneInfo(self.timezone)).date()

    async def lock(self, session):
        key = int.from_bytes(hashlib.sha256(f'cf:{self.owner}'.encode()).digest()[:8], signed=True)
        await session.execute(select(func.pg_advisory_xact_lock(key)))

    def audit(self, session, action, identity, metadata=None):
        session.add(AuditEvent(owner_user_id=self.owner, actor_user_id=self.owner,
            action=f'cloudflare_worker.{action}', resource_type='cloudflare_batch', resource_id=identity,
            outcome='success', metadata_allowlist=metadata or {}, request_id='cloudflare-worker'))

    async def settings(self, value=None):
        async with self.factory() as session, session.begin():
            await self.lock(session)
            row = await session.scalar(select(ReviewSnapshot).where(ReviewSnapshot.owner_user_id == self.owner,
                ReviewSnapshot.feature == 'cloudflare_settings', ReviewSnapshot.context_key == 'dashboard'))
            if value is not None:
                value = account_id(value)
                if row is None:
                    row = ReviewSnapshot(owner_user_id=self.owner, feature='cloudflare_settings', context_key='dashboard', sanitized_state={})
                    session.add(row)
                row.sanitized_state = {'account_id': value}
                self.audit(session, 'settings', None)
            return {'account_id': (row.sanitized_state.get('account_id', '') if row else '')}

    async def config(self, session):
        profile = await session.get(UserSettings, self.owner)
        if profile is None or missing_report_profile(profile):
            raise CloudflareConflict('Complete brand, contact name and email in Mail settings first')
        return {key: getattr(profile, key) for key in ('brand_name', 'contact_name', 'contact_email', 'company_name')}, profile.version

    async def batch(self, session, identity):
        row = await session.scalar(select(ReviewSnapshot).where(ReviewSnapshot.id == identity,
            ReviewSnapshot.owner_user_id == self.owner, ReviewSnapshot.feature == FEATURE).with_for_update())
        if row is None:
            raise LookupError('Cloudflare batch not found')
        return row

    async def rows(self, session, batch):
        ids = [UUID(value) for value in batch.sanitized_state.get('record_ids', [])]
        records = list(await session.scalars(select(CloudflareRecord).where(CloudflareRecord.owner_user_id == self.owner,
            CloudflareRecord.id.in_(ids)))) if ids else []
        order = {value: i for i, value in enumerate(ids)}
        return sorted(records, key=lambda row: order[row.id])

    async def recover(self, session, row):
        data = dict(row.sanitized_state)
        if data['phase'] not in ACTIVE or not data.get('heartbeat'):
            return
        if self.now() - datetime.fromisoformat(data['heartbeat']) <= timedelta(seconds=90):
            return
        for record in await self.rows(session, row):
            if record.state == 'SUBMITTING':
                record.state = 'UNKNOWN'
                record.sanitized_result = {**record.sanitized_result, 'error': 'interrupted_after_intent'}
        data.update(phase='INTERRUPTED' if data['phase'] == 'CHECKING' else 'WAITING_FOR_SESSION',
            error='runner_interrupted', current_id=None, heartbeat=None, runner_id=None)
        row.sanitized_state = data

    async def prepare(self, values):
        parsed, invalid, duplicates = parse_target_input('\n'.join(values))
        targets = []
        for raw in parsed:
            try: url, _ = validate_target(raw)
            except ValueError: invalid.append(raw[:500]); continue
            if url in targets: duplicates.append(url)
            else: targets.append(url)
        if not targets: raise CloudflareConflict('No valid public HTTP(S) targets')
        if len(targets) > 200: raise CloudflareConflict('Maximum 200 URLs per batch')
        async with self.factory() as session, session.begin():
            await self.lock(session)
            await self.config(session)
            previous = list(await session.scalars(select(ReviewSnapshot).where(ReviewSnapshot.owner_user_id == self.owner,
                ReviewSnapshot.feature == FEATURE)))
            for row in previous:
                await self.recover(session, row)
                if row.business_day != self.day() and row.sanitized_state['phase'] in ACTIVE:
                    for record in await self.rows(session, row):
                        if record.state == 'SUBMITTING':
                            record.state = 'UNKNOWN'
                            record.sanitized_result = {**record.sanitized_result, 'error': 'interrupted_after_intent'}
                    row.sanitized_state = {**row.sanitized_state, 'phase': 'CANCELLED', 'error': 'day_changed'}
                if row.sanitized_state['phase'] in ACTIVE:
                    raise CloudflareConflict('Finish or cancel the current Cloudflare batch first')
            identity = uuid4()
            epoch = await self.cache_epoch(session)
            row = ReviewSnapshot(id=identity, owner_user_id=self.owner, feature=FEATURE, context_key=str(identity),
                business_day=self.day(), sanitized_state={'phase': 'CHECKING', 'urls': targets,
                'invalid': invalid, 'duplicates': duplicates, 'record_ids': [], 'processed': 0, 'cache_epoch': epoch,
                'excluded_record_ids': [], 'heartbeat': self.now().isoformat(), 'delay_seconds': 30, 'current_id': None, 'error': None})
            session.add(row)
            self.audit(session, 'precheck', identity, {'url_count': len(targets)})
        return identity

    async def view(self, identity=None):
        async with self.factory() as session, session.begin():
            await self.lock(session)
            if identity:
                row = await self.batch(session, identity)
            else:
                row = await session.scalar(select(ReviewSnapshot).where(ReviewSnapshot.owner_user_id == self.owner,
                    ReviewSnapshot.feature == FEATURE, ReviewSnapshot.business_day == self.day())
                    .order_by(ReviewSnapshot.created_at.desc()).limit(1).with_for_update())
            if row is None: return {'batch': None, 'records': []}
            await self.recover(session, row)
            records = []
            for record in await self.rows(session, row):
                data = record.sanitized_result
                records.append({'id': str(record.id), 'url': record.full_url, 'state': record.state,
                    'attempts': data.get('attempts', 0), 'error': data.get('error'), 'report_id': record.provider_report_id,
                    'draft': (await self.verified_payload(session, record))['draft'] if data.get('artifact_id') else '',
                    'excluded': str(record.id) in row.sanitized_state.get('excluded_record_ids', [])})
            fields = {'phase', 'urls', 'invalid', 'duplicates', 'processed', 'delay_seconds', 'current_id', 'error', 'next_run_at', 'account_id'}
            return {'batch': {'id': str(row.id), 'day': str(row.business_day),
                **{key: value for key, value in row.sanitized_state.items() if key in fields}}, 'records': records}

    def load_payload(self, data):
        return json.loads(self.store.read_verified(storage_key=data['storage_key'],
            expected_size=data['byte_size'], expected_sha256=data['sha256']))

    async def verified_payload(self, session, record):
        data = record.sanitized_result
        artifact = await session.get(Artifact, UUID(data['artifact_id']))
        if (artifact is None or artifact.owner_user_id != self.owner or artifact.kind != 'cloudflare_worker:payload'
            or artifact.sha256 != record.request_hash or artifact.sha256 != data['sha256']
            or artifact.byte_size != data['byte_size'] or artifact.storage_key != data['storage_key']):
            raise CloudflareConflict('Report artifact binding changed; recheck before sending')
        payload = self.load_payload(data)
        if digest(payload['draft']) != record.report_version:
            raise CloudflareConflict('Report content changed; recheck before sending')
        return payload

    async def command(self, identity, action, *, delay=30, confirmed=False, retry=False):
        async with self.factory() as session, session.begin():
            await self.lock(session)
            row = await self.batch(session, identity)
            await self.recover(session, row)
            data = dict(row.sanitized_state)
            phase = data['phase']
            if action in {'pause', 'cancel'}:
                if phase in {'CANCELLED', 'COMPLETED'}: return
                if action == 'pause' and phase not in {'SENDING', 'SEND_QUEUED', 'PAUSED', 'WAITING_FOR_SESSION'}:
                    raise CloudflareConflict('Only sending batches can be paused')
                data['phase'] = 'CANCELLED' if action == 'cancel' else 'PAUSED'
            else:
                if row.business_day != self.day(): raise CloudflareConflict('Recheck URLs for the current day')
                if data.get('cache_epoch', '') != await self.cache_epoch(session):
                    raise CloudflareConflict('Cache was cleared; recheck URLs before sending')
                if phase in {'CANCELLED', 'CHECKING', 'INTERRUPTED'}: raise CloudflareConflict('Batch cannot be started')
                if phase in {'SENDING', 'SEND_QUEUED'}: raise CloudflareConflict('Batch is already sending')
                if not confirmed: raise CloudflareConflict('Review the reports and confirm submission')
                if not 0 <= delay <= 300: raise CloudflareConflict('Delay must be between 0 and 300 seconds')
                records = await self.rows(session, row)
                eligible = {'READY', 'QUEUED'} | ({'FAILED'} if retry else set())
                selected = [record for record in records if record.state in eligible]
                if not selected: raise CloudflareConflict('No eligible reports; accepted/unknown reports cannot be retried')
                _, version = await self.config(session)
                for record in selected:
                    payload = await self.verified_payload(session, record)
                    if payload['profile_version'] != version: raise CloudflareConflict('Profile changed; recheck before sending')
                    record.state = 'QUEUED'
                data.update(phase='SEND_QUEUED', delay_seconds=delay, confirmed=True,
                    permission='cloudflare_worker.retry' if retry else 'cloudflare_worker.resume' if action == 'resume' else 'cloudflare_worker.start',
                    heartbeat=self.now().isoformat(), error=None)
            row.sanitized_state = data
            self.audit(session, 'retry' if retry else action, identity)

    async def dispatch_failed(self, identity, checking=False):
        async with self.factory() as session, session.begin():
            row = await self.batch(session, identity)
            if row.sanitized_state['phase'] == ('CHECKING' if checking else 'SEND_QUEUED'):
                row.sanitized_state = {**row.sanitized_state, 'phase': 'INTERRUPTED' if checking else 'WAITING_FOR_SESSION',
                    'error': 'runner_unavailable', 'heartbeat': None}

    async def cache_epoch(self, session, *, reset=False):
        row = await session.scalar(select(ReviewSnapshot).where(
            ReviewSnapshot.owner_user_id == self.owner,
            ReviewSnapshot.feature == 'cloudflare_cache', ReviewSnapshot.context_key == str(self.day())))
        if reset:
            if row is None:
                row = ReviewSnapshot(owner_user_id=self.owner, feature='cloudflare_cache',
                    context_key=str(self.day()), business_day=self.day(), sanitized_state={})
                session.add(row)
            row.sanitized_state = {'epoch': str(uuid4())}
        return row.sanitized_state.get('epoch', '') if row else ''

    async def clear(self):
        async with self.factory() as session, session.begin():
            await self.lock(session)
            rows = list(await session.scalars(select(ReviewSnapshot).where(ReviewSnapshot.owner_user_id == self.owner,
                ReviewSnapshot.feature == FEATURE, ReviewSnapshot.business_day == self.day())))
            if any(row.sanitized_state['phase'] in ACTIVE for row in rows):
                raise CloudflareConflict('Stop the current batch before clearing')
            await self.cache_epoch(session, reset=True)
            for row in rows:
                # Hide editable workspace, preserve records and accepted/unknown audit history.
                row.business_day = None
            self.audit(session, 'clear_cache', None)


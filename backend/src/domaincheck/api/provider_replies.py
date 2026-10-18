"""Explicit owned mailbox operations; GET never opens IMAP or sends SMTP."""
import asyncio
import json
from functools import partial
from datetime import date, datetime
from time import monotonic
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.api.dependencies import get_db_session, require_permission
from domaincheck.application.provider_replies import ProviderRepliesService, ProviderReplySender
from domaincheck.application.previews import PreviewConflict
from domaincheck.application.mail_settings import MailSettingsNotFound
from domaincheck.application.sent_copy import LegacySentCopyAdapter
from domaincheck.application.worker_review import review_lock_key
from domaincheck.core.mail.sending import SmtpReportSender
from domaincheck.core.provider_mail import ProviderMailboxAdapter
from domaincheck.core.review_capture import capture_review
from domaincheck.core.evidence import CaptureFailed, capture_failure_code
from domaincheck.infrastructure.db.models import Artifact, AuditEvent, ReviewSnapshot
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.ports.artifacts import ArtifactIntegrityError, ImageUpload

router = APIRouter(prefix='/provider-replies', tags=['provider replies'])


def service(session, actor, request):
    return ProviderRepliesService(session, request.app.state.artifact_store, owner=actor.user_id, actor=actor.user_id,
        timezone=actor.timezone, now=request.app.state.clock.now(), envelope=request.app.state.secret_envelope)


def adapter(request):
    return getattr(request.app.state, 'provider_mail_adapter', None) or ProviderMailboxAdapter(request.app.state.settings.mail_egress_endpoints)


def failure(exc):
    if isinstance(exc, CaptureFailed):
        code = capture_failure_code(exc)
        descriptions = {'browser_timeout': 'Hết thời gian chụp (tối đa 180 giây).',
            'browser_runtime_missing': 'Backend chưa có Playwright.', 'browser_executable_missing': 'Backend chưa có Chromium.',
            'browser_navigation_failed': 'Browser không mở được trang hoặc kết nối bị ngắt.',
            'headed_browser_unavailable': 'Máy chạy backend không có desktop để mở browser. Tắt chế độ mở cửa sổ để chạy ngầm.',
            'terminal_page': 'Trang trả về cảnh báo hoặc lỗi, chưa có bằng chứng hợp lệ.'}
        return HTTPException(409, f"Chụp bằng chứng thất bại [{code}]. {descriptions.get(code, 'Không tạo được bộ ảnh/manifest hợp lệ. Có thể thử lại hoặc upload bằng chứng. Ảnh cũ vẫn được giữ. Thao tác này không gửi email.')}")
    if isinstance(exc, (LookupError, MailSettingsNotFound)): return HTTPException(404, 'Provider reply not found')
    if isinstance(exc, (FileNotFoundError, ArtifactIntegrityError)): return HTTPException(409, 'Evidence or original message changed; refresh and create a new preview')
    if isinstance(exc, PreviewConflict): return HTTPException(409, str(exc))
    if isinstance(exc, ValueError): return HTTPException(422, str(exc))
    return HTTPException(409, 'Mail operation failed; check account configuration, connection and allowed endpoints')


def scope_dates(start, end):
    if end < start or (end-start).days > 31: raise ValueError('Choose a valid date range of at most 32 days')


@router.get('/accounts')
async def accounts(request: Request, response: Response, session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('provider_replies.view'))):
    response.headers['Cache-Control'] = 'private, no-store'
    rows = await service(session, actor, request).mail_settings().list_accounts()
    return [{'id': row.id, 'sender_email': row.smtp_username, 'enabled': row.enabled,
             'imap_configured': bool((row.imap_config or {}).get('host'))} for row in rows]


@router.get('')
async def messages(account_id: UUID, date_from: date, date_to: date, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session), actor=Depends(require_permission('provider_replies.view'))):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        scope_dates(date_from, date_to)
        rows = await service(session, actor, request).list(account_id, date_from, date_to)
        sync = await session.scalar(select(ReviewSnapshot).where(ReviewSnapshot.owner_user_id == actor.user_id,
            ReviewSnapshot.feature == 'provider_replies_sync', ReviewSnapshot.context_key == str(account_id)))
        return {'items': rows, 'last_sync': sync.sanitized_state if sync else None}
    except Exception as exc: raise failure(exc) from exc


class Sync(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID
    date_from: date
    date_to: date
    unread_only: bool = False
    limit: int = Field(default=100, ge=1, le=500)
    sync_id: UUID | None = None


async def save_progress(request, owner, account_id, state, *, start=False):
    async with request.app.state.database.session_factory() as progress_session, progress_session.begin():
        snapshot = await progress_session.scalar(select(ReviewSnapshot).where(ReviewSnapshot.owner_user_id == owner,
            ReviewSnapshot.feature == 'provider_replies_progress', ReviewSnapshot.context_key == str(account_id)).with_for_update())
        state = {**state, 'updated_at': request.app.state.clock.now().isoformat()}
        if snapshot and not start and snapshot.sanitized_state.get('sync_id') != state['sync_id']: return
        if snapshot and not start and snapshot.sanitized_state.get('status') != 'running' and state['status'] == 'running': return
        if snapshot: snapshot.sanitized_state = state
        else: progress_session.add(ReviewSnapshot(owner_user_id=owner, feature='provider_replies_progress',
            context_key=str(account_id), sanitized_state=state))


@router.get('/sync-progress')
async def sync_progress(account_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session), actor=Depends(require_permission('provider_replies.view'))):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        await service(session, actor, request).mail_settings().get_account(account_id)
        snapshot = await session.scalar(select(ReviewSnapshot).where(ReviewSnapshot.owner_user_id == actor.user_id,
            ReviewSnapshot.feature == 'provider_replies_progress', ReviewSnapshot.context_key == str(account_id)))
        state = snapshot.sanitized_state if snapshot else None
        if state and state['status'] == 'running' and (request.app.state.clock.now() - datetime.fromisoformat(state['updated_at'])).total_seconds() > 300:
            state = {**state, 'status': 'interrupted', 'stage': 'interrupted'}
        return {'progress': state}
    except Exception as exc: raise failure(exc) from exc


@router.post('/sync')
async def sync(body: Sync, request: Request, response: Response, session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('provider_replies.sync', csrf=True))):
    response.headers['Cache-Control'] = 'private, no-store'
    started = False
    progress_state = {'sync_id': str(body.sync_id or uuid4()), 'status': 'running', 'stage': 'connecting',
        'folder': '', 'processed': 0, 'total': 0, 'folder_index': 0, 'folder_total': 0, 'folders': []}
    try:
        scope_dates(body.date_from, body.date_to)
        svc = service(session, actor, request)
        account, spec = await svc.connection(body.account_id)
        key = review_lock_key(actor.user_id, body.account_id)
        if not await session.scalar(select(func.pg_try_advisory_xact_lock(key))):
            raise PreviewConflict('This account is already syncing in another session')
        await save_progress(request, actor.user_id, body.account_id, progress_state, start=True)
        started = True
        loop = asyncio.get_running_loop()
        last = [0.0, '']
        def report(update):
            progress_state.update(update)
            if update['stage'] == 'reading' and update['folder'] == last[1] and monotonic() - last[0] < 0.5: return
            last[:] = [monotonic(), update['folder']]
            asyncio.run_coroutine_threadsafe(save_progress(request, actor.user_id, body.account_id, dict(progress_state)), loop).result(timeout=15)
        result = await asyncio.to_thread(adapter(request).sync, spec,
            {**(account.imap_config or {}), 'sender_email': account.smtp_username},
            body.date_from, body.date_to, actor.timezone, body.limit, body.unread_only, progress=report)
        imported = await svc.ingest(body.account_id, result)
        snapshot = await session.scalar(select(ReviewSnapshot).where(ReviewSnapshot.owner_user_id == actor.user_id,
            ReviewSnapshot.feature == 'provider_replies_sync', ReviewSnapshot.context_key == str(account.id)).with_for_update())
        state = {**imported, 'synced_at': request.app.state.clock.now().isoformat(),
            'date_from': str(body.date_from), 'date_to': str(body.date_to)}
        if snapshot: snapshot.sanitized_state = state
        else: session.add(ReviewSnapshot(owner_user_id=actor.user_id, feature='provider_replies_sync', context_key=str(account.id), sanitized_state=state))
        await session.flush()
        await session.commit()
        await save_progress(request, actor.user_id, body.account_id, {**progress_state, 'status': 'completed',
            'stage': 'completed', 'folders': imported['folders'], 'imported': imported['imported']})
        return state
    except Exception as exc:
        if started:
            await save_progress(request, actor.user_id, body.account_id, {**progress_state, 'status': 'failed', 'stage': 'failed'})
        raise failure(exc) from exc


@router.get('/{case_id}')
async def detail(case_id: UUID, request: Request, response: Response, session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('provider_replies.view'))):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        result = await service(session, actor, request).detail(case_id)
        result['capture_enabled'] = request.app.state.settings.browser_capture_enabled
        if 'provider_replies.preview' not in actor.permissions: result['preview'] = None
        return result
    except Exception as exc: raise failure(exc) from exc


@router.post('/{case_id}/seen')
async def seen(case_id: UUID, request: Request, response: Response, session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('provider_replies.mark_seen', csrf=True))):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        svc = service(session, actor, request); _, snapshot = await svc.context(case_id)
        _, spec = await svc.connection(UUID(snapshot.sanitized_state['account_id']))
        await asyncio.to_thread(adapter(request).mark_seen, spec, snapshot.sanitized_state['location'])
        snapshot.sanitized_state = {**snapshot.sanitized_state, 'seen': True}
        session.add(AuditEvent(owner_user_id=actor.user_id, actor_user_id=actor.user_id, action='provider_replies.mark_seen',
            resource_type='review_case', resource_id=case_id, outcome='success', request_id='provider-replies', metadata_allowlist={}))
        return {'seen': True}
    except Exception as exc: raise failure(exc) from exc


class ReplyDetails(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reported_url: str = Field(default='', max_length=8192)
    official_url: str = Field(default='', max_length=8192)
    evidence: str = Field(default='', max_length=10000)
    subject: str = Field(default='', max_length=998)
    body: str = Field(default='', max_length=32000)
    legal_approved: bool = False


@router.post('/{case_id}/previews', status_code=201)
async def preview(case_id: UUID, request: Request, response: Response, details: str = Form(...),
    files: list[UploadFile] = File(default=[]), session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('provider_replies.preview', csrf=True, configured_profile=True))):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        data = ReplyDetails.model_validate(json.loads(details))
        if len(files) > 3: raise ValueError('Choose 1-3 PNG/JPEG images')
        uploads = []
        for file in files:
            raw = await file.read(10*1024*1024+1)
            if not 0 < len(raw) <= 10*1024*1024 or file.content_type not in {'image/png', 'image/jpeg'}:
                raise ValueError('Images must be PNG/JPEG and at most 10 MB each')
            uploads.append(ImageUpload('evidence.png', file.content_type, raw))
        return await service(session, actor, request).preview(case_id, data.model_dump(), uploads)
    except Exception as exc: raise failure(exc) from exc


class ReplyCapture(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reported_url: str = Field(default='', max_length=8192)
    show_browser: bool = False


@router.post('/{case_id}/capture')
async def capture(case_id: UUID, body: ReplyCapture, request: Request, response: Response, session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('check_domain.capture', csrf=True, configured_profile=True))):
    response.headers['Cache-Control'] = 'private, no-store'
    if 'provider_replies.preview' not in actor.permissions: raise HTTPException(403, 'Permission denied')
    if not request.app.state.settings.browser_capture_enabled: raise HTTPException(409, 'Browser capture is disabled; upload images instead')
    try:
        return await service(session, actor, request).capture(case_id,
            getattr(request.app.state, 'provider_reply_capture', None) or partial(capture_review, headed=body.show_browser), body.reported_url)
    except Exception as exc: raise failure(exc) from exc


@router.get('/{case_id}/images/{artifact_id}')
async def image(case_id: UUID, artifact_id: UUID, request: Request, session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('provider_replies.view'))):
    try:
        svc = service(session, actor, request); case, snapshot = await svc.context(case_id)
        _, images = await svc.evidence(case, snapshot)
        if artifact_id not in {image.id for image in images}: raise LookupError('Image not found')
        artifact = await OwnedRepository(session, Artifact, actor.user_id).get(artifact_id)
        raw = request.app.state.artifact_store.read_verified(storage_key=artifact.storage_key, expected_size=artifact.byte_size, expected_sha256=artifact.sha256)
        return Response(raw, media_type=artifact.mime_type, headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
    except Exception as exc: raise failure(exc) from exc


class Send(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_id: UUID
    revision: int = Field(ge=1)
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed: bool = False


@router.post('/{case_id}/send')
async def send(case_id: UUID, body: Send, request: Request, response: Response,
    actor=Depends(require_permission('provider_replies.send', csrf=True, configured_profile=True))):
    response.headers['Cache-Control'] = 'private, no-store'
    settings = request.app.state.settings
    sender = ProviderReplySender(request.app.state.database.session_factory, request.app.state.artifact_store,
        getattr(request.app.state, 'provider_reply_sender', None) or SmtpReportSender(settings.mail_egress_endpoints),
        request.app.state.secret_envelope, now=request.app.state.clock.now,
        sent_copier=getattr(request.app.state, 'provider_reply_sent_copier', None) or LegacySentCopyAdapter(settings.mail_egress_endpoints))
    try: return await sender.send(actor.user_id, actor.user_id, actor.timezone, case_id, **body.model_dump())
    except Exception as exc: raise failure(exc) from exc

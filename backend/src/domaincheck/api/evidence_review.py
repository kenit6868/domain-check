"""Normal evidence review: exact preview and synchronous direct SMTP, no worker job."""
import json
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.api.dependencies import get_db_session, require_permission
from domaincheck.application.evidence_review import EvidenceReviewSender, EvidenceReviewService
from domaincheck.application.previews import PreviewConflict, PreviewNotFound
from domaincheck.core.mail.sending import SmtpReportSender
from domaincheck.application.sent_copy import ImapSentCopyAdapter
from domaincheck.infrastructure.db.models import Artifact
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.ports.artifacts import ArtifactIntegrityError, ArtifactValidationError, ImageUpload

router = APIRouter(prefix='/domain-worker/reviews/evidence', tags=['evidence review'])


def service(session, actor, request):
    return EvidenceReviewService(session, request.app.state.artifact_store, owner=actor.user_id,
        actor=actor.user_id, timezone=actor.timezone, now=request.app.state.clock.now(), request_id=request.state.request_id)


def failure(exc):
    if isinstance(exc, (PreviewNotFound, LookupError)):
        return HTTPException(404, 'Evidence review resource not found')
    if isinstance(exc, (ArtifactIntegrityError, FileNotFoundError)):
        return HTTPException(409, 'Evidence or signature changed; create a new preview')
    return HTTPException(409 if isinstance(exc, PreviewConflict) else 422, str(exc))


@router.get('/{case_id}')
async def detail(case_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session), actor=Depends(require_permission('domain_evidence_review.view'))):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        result = await service(session, actor, request).detail(case_id)
        if 'domain_evidence_review.preview' not in actor.permissions:
            result['preview'] = None
        return result
    except (ValueError, LookupError, FileNotFoundError, ArtifactIntegrityError) as exc:
        raise failure(exc) from exc


@router.get('/{case_id}/images/{artifact_id}')
async def image(case_id: UUID, artifact_id: UUID, request: Request,
    session: AsyncSession = Depends(get_db_session), actor=Depends(require_permission('domain_evidence_review.view'))):
    try:
        review = service(session, actor, request)
        _, checks = await review.context(case_id)
        _, images = await review.previews()._evidence(checks[0])
        if artifact_id not in {image.id for image in images}:
            raise LookupError('Image does not belong to this case')
        artifact = await OwnedRepository(session, Artifact, actor.user_id).get(artifact_id)
        data = request.app.state.artifact_store.read_verified(storage_key=artifact.storage_key,
            expected_size=artifact.byte_size, expected_sha256=artifact.sha256)
        return Response(content=data, media_type=artifact.mime_type,
                        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
    except (ValueError, LookupError, FileNotFoundError, ArtifactIntegrityError) as exc:
        raise failure(exc) from exc


@router.post('/{case_id}/previews', status_code=201)
async def preview(case_id: UUID, request: Request, response: Response, account_ids: str = Form(...),
    files: list[UploadFile] = File(default=[]), session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('domain_evidence_review.preview', csrf=True, configured_profile=True))):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        if files and 'domain_evidence_review.upload' not in actor.permissions:
            raise HTTPException(403, 'Permission denied')
        values = json.loads(account_ids)
        if not isinstance(values, list) or not 1 <= len(values) <= 100 or len(set(values)) != len(values):
            raise ValueError('Choose 1–100 distinct source accounts')
        accounts = [UUID(value) for value in values]
        if len(files) > 3:
            raise ValueError('Choose 1–3 PNG/JPEG images')
        uploads = []
        for index, file in enumerate(files):
            data = await file.read(10 * 1024 * 1024 + 1)
            if not data or len(data) > 10 * 1024 * 1024 or file.content_type not in {'image/png', 'image/jpeg'}:
                raise ValueError('Images must be PNG/JPEG, non-empty and at most 10 MB each')
            uploads.append(ImageUpload(f'review-{index}.png' if file.content_type == 'image/png' else f'review-{index}.jpg', file.content_type, data))
        return await service(session, actor, request).preview(case_id, accounts, uploads)
    except (ValueError, TypeError, LookupError, FileNotFoundError, ArtifactIntegrityError, ArtifactValidationError) as exc:
        raise failure(exc) from exc


class Send(BaseModel):
    model_config = ConfigDict(extra='forbid')
    check_id: UUID
    preview_id: UUID
    revision: int = Field(ge=1)
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed: bool = False


@router.post('/{case_id}/send')
async def send(case_id: UUID, body: Send, request: Request, response: Response,
    actor=Depends(require_permission('domain_evidence_review.send', csrf=True, configured_profile=True))):
    response.headers['Cache-Control'] = 'private, no-store'
    settings = request.app.state.settings
    sender = EvidenceReviewSender(request.app.state.database.session_factory, request.app.state.artifact_store,
        getattr(request.app.state, 'evidence_review_sender', None) or SmtpReportSender(settings.mail_egress_endpoints),
        request.app.state.secret_envelope, now=request.app.state.clock.now,
        sent_copier=getattr(request.app.state, 'evidence_review_sent_copier', None) or ImapSentCopyAdapter(settings.mail_egress_endpoints))
    try:
        return await sender.send(owner=actor.user_id, actor=actor.user_id, timezone=actor.timezone, case_id=case_id, **body.model_dump())
    except (ValueError, LookupError, FileNotFoundError, ArtifactIntegrityError) as exc:
        raise failure(exc) from exc

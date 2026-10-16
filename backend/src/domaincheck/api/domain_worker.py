from __future__ import annotations

from uuid import UUID
from typing import Annotated
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from domaincheck.api.dependencies import get_db_session, require_permission
from domaincheck.application.domain_worker import DomainWorkerService, WorkerNotFound
from domaincheck.application.jobs import JobConflict, JobNotFound
from domaincheck.core.worker_precheck import email_examples
from domaincheck.infrastructure.cache import business_day
from domaincheck.infrastructure.db.models import Check, Job, JobItem, SenderAccount, UserSettings
from domaincheck.infrastructure.db.owned import OwnedRepository

router = APIRouter(prefix="/domain-worker", tags=["domain worker"])


class SendBatchCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    batch_size: int = Field(default=5, ge=1, le=100)
    interval_seconds: int = Field(default=300, ge=0, le=86400)
    confirmed: bool = False


def send_service(session, actor, request):
    from domaincheck.application.worker_send import WorkerSendService
    return WorkerSendService(session, request.app.state.artifact_store, owner=actor.user_id,
        actor=actor.user_id, timezone=actor.timezone, request_id=request.state.request_id,
        now=request.app.state.clock.now(), envelope=request.app.state.secret_envelope)


async def send_action(session, actor, request, response, action, *args, **kwargs):
    from domaincheck.application.previews import PreviewConflict, PreviewNotFound
    from domaincheck.application.deliveries import DeliveryConflict
    from domaincheck.application.mail_settings import MailSettingsConflict
    from domaincheck.ports.artifacts import ArtifactIntegrityError
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        return await getattr(send_service(session, actor, request), action)(*args, **kwargs)
    except (WorkerNotFound, JobNotFound, PreviewNotFound) as exc:
        raise HTTPException(404, 'Domain Worker resource not found') from exc
    except (JobConflict, PreviewConflict, DeliveryConflict, MailSettingsConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ArtifactIntegrityError, FileNotFoundError) as exc:
        raise HTTPException(409, 'Evidence or signature artifact is unavailable or changed') from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/prechecks/{operation_id}/send-summary')
async def send_summary(operation_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session), actor=Depends(require_permission('domain_worker.view'))):
    return await send_action(session, actor, request, response, 'summary', operation_id)


@router.post('/prechecks/{operation_id}/send', status_code=202)
async def start_send(operation_id: UUID, body: SendBatchCreate, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('domain_worker.start', csrf=True, configured_profile=True))):
    return await send_action(session, actor, request, response, 'start', operation_id, **body.model_dump())


@router.get('/send-jobs/{job_id}')
async def get_send_job(job_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session), actor=Depends(require_permission('domain_worker.view'))):
    return await send_action(session, actor, request, response, 'get', job_id)


@router.post('/send-jobs/{job_id}/stop')
async def stop_send_job(job_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session), actor=Depends(require_permission('domain_worker.stop', csrf=True))):
    return await send_action(session, actor, request, response, 'control', job_id, 'stop')


@router.post('/send-jobs/{job_id}/resume')
async def resume_send_job(job_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('domain_worker.start', csrf=True, configured_profile=True))):
    return await send_action(session, actor, request, response, 'control', job_id, 'resume')


@router.post('/send-jobs/{job_id}/retry')
async def retry_send_job(job_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('domain_worker.retry', csrf=True, configured_profile=True))):
    return await send_action(session, actor, request, response, 'control', job_id, 'retry')


@router.get("/readiness")
async def readiness(session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission("domain_worker.view"))):
    from domaincheck.application.profile_readiness import missing_report_profile
    missing = missing_report_profile(await session.get(UserSettings, actor.user_id))
    return {"ready": not missing, "missing": missing}


class PrecheckCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targets: list[Annotated[str, Field(min_length=1, max_length=8192)]] = Field(min_length=1, max_length=100)
    sender_account_ids: list[UUID] = Field(min_length=1, max_length=100)


@router.get('/accounts')
async def sender_accounts(response: Response, session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('domain_worker.view'))):
    accounts = await OwnedRepository(session, SenderAccount, actor.user_id).list(
        criteria=(SenderAccount.enabled.is_(True),),
        order_by=(SenderAccount.is_default.desc(), SenderAccount.created_at, SenderAccount.id), limit=500)
    response.headers['Cache-Control'] = 'private, no-store'
    return [{'id': account.id, 'label': account.label, 'sender_email': account.smtp_username} for account in accounts]


class RetryItems(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_ids: list[UUID] = Field(min_length=1, max_length=100)


def service(session, actor, request):
    return DomainWorkerService(session, owner=actor.user_id, actor=actor.user_id,
        timezone=actor.timezone, request_id=request.state.request_id, now=request.app.state.clock.now())


def serialize(operation):
    checks = []
    for check, item in zip(operation.checks, operation.items, strict=True):
        complete = item.state == "succeeded" and check.status == "succeeded"
        result = check.result if complete else {}
        checks.append({"id": check.id, "item_id": item.id, "requested_url": check.requested_url,
            "domain": check.domain, "status": item.state,
            "disposition": result.get("disposition", "precheck_error" if item.state == "failed" else item.state),
            "recipients": result.get("recipients", []),
            "cloaking_verdict": (result.get("cloaking") or {}).get("verdict"),
            "case_id": result.get("case_id"), "evidence_set_id": result.get("evidence_set_id"),
            "evidence_reason": result.get("evidence_reason"),
            "evidence_failure_code": result.get("evidence_failure_code"),
            "browser_verification_pending": bool(result.get("browser_verification_pending"))})
    return {"id": operation.job.id, "state": operation.job.state, "stage": operation.job.stage,
            "business_day": operation.job.business_day.isoformat(),
            "sender_account_ids": getattr(operation.job, 'account_scope', None) or [], "checks": checks}


@router.post("/prechecks", status_code=202)
async def create_precheck(body: PrecheckCreate, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission("domain_worker.precheck", csrf=True, configured_profile=True))):
    try:
        if len(set(body.sender_account_ids)) != len(body.sender_account_ids):
            raise ValueError('Choose distinct sender accounts')
        operation = await service(session, actor, request).create(body.targets, body.sender_account_ids)
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    response.headers["Cache-Control"] = "private, no-store"
    return serialize(operation)


@router.get("/prechecks/{operation_id}")
async def get_precheck(operation_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session), actor=Depends(require_permission("domain_worker.view"))):
    try:
        operation = await service(session, actor, request).get(operation_id)
    except WorkerNotFound as exc:
        raise HTTPException(404, "Domain Worker operation not found") from exc
    response.headers["Cache-Control"] = "private, no-store"
    return {**serialize(operation), 'is_current_day': operation.job.business_day ==
        business_day(request.app.state.clock.now(), operation.job.timezone)}


async def control(operation_id, action, request, response, session, actor, item_ids=None):
    svc = service(session, actor, request)
    try:
        operation = await svc.get(operation_id)
        if action != "stop" and operation.job.business_day != business_day(svc.now, actor.timezone):
            raise JobConflict("Run a new precheck for the current day")
        if action == "stop":
            await svc.jobs().request_stop(operation_id)
        elif action == "resume":
            await svc.jobs().resume(operation_id)
        else:
            if operation.job.state in {"queued", "running", "stopping"}:
                raise JobConflict("Wait until the current operation stops")
            await svc.jobs().retry_failed(operation_id, set(item_ids))
        response.headers["Cache-Control"] = "private, no-store"
        return serialize(await svc.get(operation_id))
    except (WorkerNotFound, JobNotFound) as exc:
        raise HTTPException(404, "Domain Worker operation not found") from exc
    except (JobConflict, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/prechecks/{operation_id}/stop")
async def stop(operation_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session), actor=Depends(require_permission("domain_worker.stop", csrf=True))):
    return await control(operation_id, "stop", request, response, session, actor)


@router.post("/prechecks/{operation_id}/resume")
async def resume(operation_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission("domain_worker.precheck", csrf=True, configured_profile=True))):
    return await control(operation_id, "resume", request, response, session, actor)


@router.post("/prechecks/{operation_id}/retry")
async def retry(operation_id: UUID, body: RetryItems, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission("domain_worker.retry", csrf=True, configured_profile=True))):
    return await control(operation_id, "retry", request, response, session, actor, body.item_ids)


@router.get("/checks/{check_id}/drafts")
async def drafts(check_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission("domain_worker.view", configured_profile=True))):
    check = await OwnedRepository(session, Check, actor.user_id).get(check_id)
    if check is None or check.workflow != "domain_worker":
        raise HTTPException(404, "Domain Worker check not found")
    if (check.status != "succeeded" or check.result.get("disposition") not in {"ready", "evidence_review"}
            or check.business_day != business_day(request.app.state.clock.now(), actor.timezone)):
        raise HTTPException(409, "A current-day normal report precheck is required")
    completed_item = await session.scalar(select(JobItem.id).join(Job,
        (Job.id == JobItem.job_id) & (Job.owner_user_id == JobItem.owner_user_id)).where(
        JobItem.owner_user_id == actor.user_id, Job.job_type == "domain_worker_precheck",
        JobItem.result_metadata["check_id"].astext == str(check.id), JobItem.state == "succeeded"))
    if completed_item is None:
        raise HTTPException(409, "Precheck item must complete before viewing drafts")
    profile = await session.get(UserSettings, actor.user_id)
    config = {field: getattr(profile, field, "") if profile else "" for field in
        ("brand_name", "contact_name", "contact_email", "company_name", "business_registration_no", "signature_role")}
    response.headers["Cache-Control"] = "private, no-store"
    try:
        examples = email_examples(check.domain, check.requested_url, check.result, config)
    except RuntimeError as exc:
        raise HTTPException(503, "Email templates need a backend package reinstall and API restart") from exc
    from domaincheck.application.signature_logo import load_profile_logo, logo_response
    from domaincheck.core.email_content import html_report_body
    from domaincheck.ports.artifacts import ArtifactIntegrityError
    try:
        logo = await load_profile_logo(session, request.app.state.artifact_store, actor.user_id, profile)
    except (ArtifactIntegrityError, FileNotFoundError) as exc:
        raise HTTPException(409, "Signature logo failed integrity validation") from exc
    for example in examples:
        example['html_body'] = html_report_body(example['body'], logo.content_id) if logo else None
    return {"check_id": check.id, "advisory": True, "drafts": examples, "signature_logo": logo_response(logo)}


class ExactPreviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sender_account_ids: list[UUID] = Field(min_length=1, max_length=20)


async def exact_preview_action(action, check_id, request, response, session, actor, **kwargs):
    from domaincheck.application.domain_worker_preview import DomainWorkerPreviewService
    from domaincheck.application.previews import PreviewConflict, PreviewNotFound
    from domaincheck.ports.artifacts import ArtifactIntegrityError
    svc = DomainWorkerPreviewService(session, request.app.state.artifact_store,
        owner=actor.user_id, actor=actor.user_id, timezone=actor.timezone,
        request_id=request.state.request_id, now=request.app.state.clock.now())
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return await getattr(svc, action)(check_id, **kwargs)
    except PreviewNotFound as exc:
        raise HTTPException(404, str(exc), headers={"Cache-Control": "private, no-store"}) from exc
    except PreviewConflict as exc:
        raise HTTPException(409, str(exc), headers={"Cache-Control": "private, no-store"}) from exc
    except (ArtifactIntegrityError, FileNotFoundError) as exc:
        raise HTTPException(409, "Preview evidence or logo failed integrity validation",
                            headers={"Cache-Control": "private, no-store"}) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc), headers={"Cache-Control": "private, no-store"}) from exc
    except RuntimeError as exc:
        raise HTTPException(503, "Email templates need a backend package reinstall and API restart",
                            headers={"Cache-Control": "private, no-store"}) from exc


@router.get("/checks/{check_id}/preview-options")
async def preview_options(check_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission("domain_worker.preview", configured_profile=True))):
    return await exact_preview_action("options", check_id, request, response, session, actor)


@router.post("/checks/{check_id}/previews", status_code=201)
async def create_exact_preview(check_id: UUID, body: ExactPreviewCreate, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission("domain_worker.preview", csrf=True, configured_profile=True))):
    return await exact_preview_action("create", check_id, request, response, session, actor,
                                      account_ids=body.sender_account_ids)


@router.get("/checks/{check_id}/previews/{preview_id}")
async def get_exact_preview(check_id: UUID, preview_id: UUID, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission("domain_worker.preview", configured_profile=True))):
    return await exact_preview_action("get", check_id, request, response, session, actor, preview_id=preview_id)


@router.get('/reviews/cloaking')
async def cloaking_reviews(request: Request, response: Response, session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('cloaking_review.view'))):
    from domaincheck.application.worker_review import list_reviews
    response.headers['Cache-Control'] = 'private, no-store'
    return await list_reviews(session, actor.user_id, actor.timezone, request.app.state.clock.now(), 'cloaking')


@router.get('/reviews/evidence')
async def evidence_reviews(request: Request, response: Response, session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('domain_evidence_review.view'))):
    from domaincheck.application.worker_review import list_reviews
    response.headers['Cache-Control'] = 'private, no-store'
    return await list_reviews(session, actor.user_id, actor.timezone, request.app.state.clock.now(), 'domain_evidence')


@router.post('/reviews/evidence/{case_id}/images', status_code=201)
async def upload_review(case_id: UUID, request: Request, response: Response, files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_db_session),
    actor=Depends(require_permission('domain_evidence_review.upload', csrf=True))):
    from domaincheck.application.worker_review import upload_review_images
    from domaincheck.ports.artifacts import ArtifactValidationError, ImageUpload
    if not 1 <= len(files) <= 3:
        raise HTTPException(422, 'Upload 1–3 PNG/JPEG images')
    uploads = []
    limit = request.app.state.settings.artifact_max_file_bytes
    for index, file in enumerate(files):
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(422, 'Evidence image exceeds the size limit')
        extension = 'png' if file.content_type == 'image/png' else 'jpg'
        uploads.append(ImageUpload(f'manual-{index + 1}.{extension}', file.content_type or '', data))
    try:
        result = await upload_review_images(session, request.app.state.artifact_store,
            actor.user_id, actor.timezone, request.app.state.clock.now(), case_id, uploads)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    response.headers['Cache-Control'] = 'private, no-store'
    return result

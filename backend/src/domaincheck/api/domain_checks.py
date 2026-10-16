"""Owned full Check Domain API. Checking is asynchronous; this API never sends reports."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.api.dependencies import get_db_session, require_permission
from domaincheck.application.auth import AuthenticatedUser
from domaincheck.application.domain_checks import DomainCheckNotFound, DomainCheckOperation, DomainCheckService
from domaincheck.application.evidence import EvidenceConflict, EvidenceNotFound, EvidenceService, EvidenceSummary
from domaincheck.application.jobs import JobNotFound, JobService
from domaincheck.core.report_guidance import build_report_guidance
from domaincheck.ports.artifacts import ArtifactValidationError, ImageUpload


router = APIRouter(prefix="/domain-checks", tags=["domain checks"])


class DomainCheckCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(min_length=1)
    submit_vt: bool = False


class DomainCheckItemResponse(BaseModel):
    id: UUID
    requested_url: str
    domain: str
    status: str
    result: dict
    cache_provenance: dict


class DomainCheckOperationResponse(BaseModel):
    id: UUID
    state: str
    stage: str
    business_day: str
    check: DomainCheckItemResponse


def serialize(operation: DomainCheckOperation) -> DomainCheckOperationResponse:
    return DomainCheckOperationResponse(
        id=operation.job.id, state=operation.job.state, stage=operation.job.stage,
        business_day=operation.job.business_day.isoformat(),
        check=DomainCheckItemResponse(id=operation.checks[0].id,
            requested_url=operation.checks[0].requested_url, domain=operation.checks[0].domain,
            status="unknown" if operation.item_states[0] == "unknown" else operation.checks[0].status,
            result={} if operation.item_states[0] == "unknown" or operation.checks[0].status in {"pending", "running"}
                else operation.checks[0].result,
            cache_provenance=operation.checks[0].cache_provenance),
    )


def service(session: AsyncSession, actor: AuthenticatedUser, request: Request) -> DomainCheckService:
    return DomainCheckService(session, owner_user_id=actor.user_id, actor_user_id=actor.user_id,
        timezone=actor.timezone, request_id=request.state.request_id,
        now=request.app.state.clock.now())


def evidence_service(request: Request, actor: AuthenticatedUser) -> EvidenceService:
    return EvidenceService(request.app.state.database.session_factory,
        request.app.state.artifact_store, owner_user_id=actor.user_id,
        actor_user_id=actor.user_id, request_id=request.state.request_id)


def evidence_response(item: EvidenceSummary) -> dict:
    return {"id": item.id, "check_id": item.check_id, "requested_url": item.requested_url,
        "landing_url": item.landing_url, "destination_url": item.destination_url,
        "capture_mode": item.capture_mode, "manifest": item.manifest,
        "artifact_ids": item.artifact_ids}


@router.post("/checks", response_model=DomainCheckOperationResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_checks(body: DomainCheckCreate, request: Request, response: Response,
                        session: AsyncSession = Depends(get_db_session),
                        actor: AuthenticatedUser = Depends(require_permission("check_domain.check", csrf=True))):
    if body.submit_vt:
        if "check_domain.submit_vt" not in actor.permissions:
            raise HTTPException(403, "VirusTotal submission permission denied")
        key = request.app.state.settings.vt_api_key
        if not key or not key.get_secret_value().strip():
            raise HTTPException(409, "VirusTotal submission is not configured")
    try:
        result = await service(session, actor, request).create(body.target, submit_vt=body.submit_vt)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    response.headers["Cache-Control"] = "private, no-store"
    return serialize(result)


@router.get("/checks/{operation_id}", response_model=DomainCheckOperationResponse)
async def get_checks(operation_id: UUID, request: Request, response: Response,
                     session: AsyncSession = Depends(get_db_session),
                     actor: AuthenticatedUser = Depends(require_permission("check_domain.view"))):
    try:
        result = await service(session, actor, request).get(operation_id)
    except DomainCheckNotFound as exc:
        raise HTTPException(404, "Check Domain operation not found") from exc
    response.headers["Cache-Control"] = "private, no-store"
    return serialize(result)


@router.get("/checks/{check_id}/report-guidance")
async def report_guidance(check_id: UUID, response: Response,
                          session: AsyncSession = Depends(get_db_session),
                          actor: AuthenticatedUser = Depends(require_permission("check_domain.view"))):
    from domaincheck.infrastructure.db.models import Check, UserSettings
    from domaincheck.infrastructure.db.owned import OwnedRepository

    check = await OwnedRepository(session, Check, actor.user_id).get(check_id)
    if check is None or check.workflow != "check_domain":
        raise HTTPException(404, "Check Domain check not found")
    if check.status != "succeeded":
        raise HTTPException(409, "Check Domain check is not complete")
    profile = await session.get(UserSettings, actor.user_id)
    config = {field: getattr(profile, field, "") if profile else "" for field in
              ("brand_name", "contact_name", "contact_email", "company_name",
               "business_registration_no", "signature_role")}
    response.headers["Cache-Control"] = "private, no-store"
    return build_report_guidance(domain=check.domain, target_url=check.requested_url,
                                 result=check.result, profile=config)


@router.post("/checks/{check_id}/evidence/upload", status_code=201)
async def upload_evidence(check_id: UUID, request: Request, response: Response,
                          files: list[UploadFile] = File(...),
                          actor: AuthenticatedUser = Depends(require_permission("check_domain.upload", csrf=True))):
    if not 1 <= len(files) <= 3:
        raise HTTPException(422, "Upload between one and three images")
    limit = request.app.state.settings.artifact_max_file_bytes
    uploads = []
    for index, file in enumerate(files):
        content = await file.read(limit + 1)
        if len(content) > limit:
            raise HTTPException(422, "Evidence image exceeds configured size limit")
        extension = ".png" if file.content_type == "image/png" else ".jpg"
        uploads.append(ImageUpload(f"manual-{index + 1}{extension}",
                                   file.content_type or "", content))
    try:
        item = await evidence_service(request, actor).save_images(check_id, uploads,
            mode="manual_upload", manifest={"evidence_type": "manual_upload",
                                           "navigation_verified": False, "terminal": False})
    except EvidenceNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except EvidenceConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    response.headers["Cache-Control"] = "private, no-store"
    return evidence_response(item)


@router.post("/checks/{check_id}/evidence/capture", status_code=202)
async def request_capture(check_id: UUID, request: Request, response: Response,
                          actor: AuthenticatedUser = Depends(require_permission("check_domain.capture", csrf=True))):
    if not request.app.state.settings.browser_capture_enabled:
        raise HTTPException(503, "Browser capture is disabled")
    from domaincheck.core.networking import EgressProxy, EgressDenied
    proxy = request.app.state.settings.browser_egress_proxy_url
    if proxy:
        try:
            EgressProxy.parse(proxy.get_secret_value())
        except EgressDenied as exc:
            raise HTTPException(503, "Browser capture egress proxy is invalid") from exc
    try:
        job = await evidence_service(request, actor).create_capture_job(check_id)
    except EvidenceNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except EvidenceConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    response.headers["Cache-Control"] = "private, no-store"
    return {"job_id": job.id, "state": job.state}


@router.get("/captures/{job_id}")
async def get_capture_job(job_id: UUID, request: Request, response: Response,
                          session: AsyncSession = Depends(get_db_session),
                          actor: AuthenticatedUser = Depends(require_permission("check_domain.view"))):
    try:
        snapshot = await JobService(session, owner_user_id=actor.user_id,
            actor_user_id=actor.user_id, request_id=request.state.request_id).get_snapshot(job_id)
    except JobNotFound as exc:
        raise HTTPException(404, "Capture job not found") from exc
    if snapshot.job.job_type != "browser_evidence":
        raise HTTPException(404, "Capture job not found")
    response.headers["Cache-Control"] = "private, no-store"
    return {"job_id": snapshot.job.id, "state": snapshot.job.state,
            "stage": snapshot.job.stage, "check_id": snapshot.items[0].result_metadata.get("check_id")}


@router.get("/checks/{check_id}/evidence")
async def list_evidence(check_id: UUID, request: Request, response: Response,
                        actor: AuthenticatedUser = Depends(require_permission("check_domain.view"))):
    try:
        rows = await evidence_service(request, actor).list_for_check(check_id)
    except EvidenceNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    response.headers["Cache-Control"] = "private, no-store"
    return [evidence_response(item) for item in rows]

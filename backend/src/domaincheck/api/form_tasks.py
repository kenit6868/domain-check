from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.api.dependencies import get_db_session, require_permission
from domaincheck.application.auth import AuthenticatedUser
from domaincheck.application.form_tasks import (
    FormTaskConflict, FormTaskError, FormTaskNotFound, FormTaskService,
)


router = APIRouter(tags=["form tasks"])


class ExtensionDeviceRegistration(BaseModel):
    device_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    extension_version: str = Field(min_length=1, max_length=40)


class ExtensionDeviceResponse(BaseModel):
    registered: bool
    extension_version: str


class FormTaskCreate(BaseModel):
    provider: Literal["google_gsb", "microsoft_smartscreen", "cloudflare", "chongluadao", "coccoc_safe", "godaddy_phishing", "registry_co_phishing", "xyz_registry_abuse"]
    device_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class FormTaskCreatedResponse(BaseModel):
    id: UUID
    capability_token: str
    provider: str
    form_url: str
    target_url: str
    mode: Literal["fill_only"]
    state: str
    expires_at: datetime


class FormTaskExchangeRequest(BaseModel):
    capability_token: str = Field(min_length=40, max_length=200)
    device_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class FormTaskExchangeResponse(BaseModel):
    id: UUID
    status_token: str
    provider: str
    form_url: str
    target_url: str
    draft: str
    mode: Literal["fill_only"]
    threat_type: str
    threat_category: str
    expires_at: datetime
    contact_email: str
    contact_name: str
    brand_name: str
    report_type: str


class FormTaskStatusUpdate(BaseModel):
    status_token: str = Field(min_length=40, max_length=200)
    state: Literal["WORKING", "FILLED", "NEEDS_MANUAL", "FAILED"]
    result: str = Field(default="", max_length=1000)
    adapter_id: str = Field(default="", max_length=60)
    adapter_version: str = Field(default="", max_length=30)


class FormTaskStatusResponse(BaseModel):
    id: UUID
    provider: str
    target_url: str
    form_url: str
    mode: Literal["fill_only"]
    state: str
    expires_at: datetime
    status: dict | None


def _status_response(row) -> FormTaskStatusResponse:
    state = row.sanitized_state
    return FormTaskStatusResponse(
        id=row.id, provider=state["provider"], target_url=state["target_url"],
        form_url=state["form_url"], mode=state["mode"], state=state["state"],
        expires_at=datetime.fromisoformat(state["expires_at"]), status=state.get("status"),
    )


def _extension_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and not origin.startswith("chrome-extension://"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Extension origin is required")


@router.post("/extension/devices", response_model=ExtensionDeviceResponse)
async def register_extension_device(
    body: ExtensionDeviceRegistration,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedUser = Depends(require_permission("quick_report.open_form", csrf=True, configured_profile=True)),
) -> ExtensionDeviceResponse:
    try:
        row = await FormTaskService(
            session, owner_user_id=actor.user_id, actor_user_id=actor.user_id,
            request_id=request.state.request_id, now=request.app.state.clock.now(),
            ttl_seconds=request.app.state.settings.form_task_ttl_seconds,
        ).register_device(body.device_id, body.extension_version)
    except FormTaskError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return ExtensionDeviceResponse(
        registered=True, extension_version=row.sanitized_state["extension_version"],
    )


@router.post(
    "/quick-reports/checks/{check_id}/form-tasks",
    response_model=FormTaskCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_form_task(
    check_id: UUID,
    body: FormTaskCreate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedUser = Depends(require_permission("quick_report.open_form", csrf=True, configured_profile=True)),
) -> FormTaskCreatedResponse:
    service = FormTaskService(
        session, owner_user_id=actor.user_id, actor_user_id=actor.user_id,
        request_id=request.state.request_id, now=request.app.state.clock.now(),
        ttl_seconds=request.app.state.settings.form_task_ttl_seconds,
    )
    try:
        created = await service.create(check_id, provider=body.provider, device_id=body.device_id)
    except FormTaskNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except FormTaskConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except FormTaskError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return FormTaskCreatedResponse(
        id=created.id, capability_token=created.capability_token,
        provider=created.provider, form_url=created.form_url,
        target_url=created.target_url, mode="fill_only", state=created.state,
        expires_at=created.expires_at,
    )


@router.post("/form-tasks/exchange", response_model=FormTaskExchangeResponse)
async def exchange_form_task(
    body: FormTaskExchangeRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> FormTaskExchangeResponse:
    _extension_origin(request)
    try:
        exchanged = await FormTaskService.exchange(
            session, capability_token=body.capability_token, device_id=body.device_id,
            now=request.app.state.clock.now(),
        )
    except FormTaskNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except FormTaskConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except FormTaskError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    payload = exchanged.payload
    return FormTaskExchangeResponse(
        id=exchanged.id, status_token=exchanged.status_token,
        provider=payload.provider, form_url=payload.form_url,
        target_url=payload.target_url, draft=payload.draft, mode="fill_only",
        threat_type=payload.threat_type, threat_category=payload.threat_category,
        expires_at=exchanged.expires_at,
        contact_email=payload.contact_email, contact_name=payload.contact_name,
        brand_name=payload.brand_name, report_type=payload.report_type,
    )


@router.post("/form-tasks/{task_id}/status", response_model=FormTaskStatusResponse)
async def update_form_task_status(
    task_id: UUID,
    body: FormTaskStatusUpdate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> FormTaskStatusResponse:
    _extension_origin(request)
    try:
        row = await FormTaskService.update_status(
            session, task_id=task_id, status_token=body.status_token,
            state_name=body.state, result=body.result, adapter_id=body.adapter_id,
            adapter_version=body.adapter_version, now=request.app.state.clock.now(),
        )
    except FormTaskNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except FormTaskConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except FormTaskError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _status_response(row)


@router.get("/form-tasks/{task_id}", response_model=FormTaskStatusResponse)
async def get_form_task(
    task_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedUser = Depends(require_permission("quick_report.view")),
) -> FormTaskStatusResponse:
    try:
        row = await FormTaskService(
            session, owner_user_id=actor.user_id, actor_user_id=actor.user_id,
            request_id=request.state.request_id, now=request.app.state.clock.now(),
            ttl_seconds=request.app.state.settings.form_task_ttl_seconds,
        ).get(task_id)
    except FormTaskNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _status_response(row)

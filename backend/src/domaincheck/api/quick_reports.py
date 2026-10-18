from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.api.dependencies import get_db_session, require_permission
from domaincheck.application.auth import AuthenticatedUser
from domaincheck.application.quick_report_actions import ActionConflict, QuickReportActions
from domaincheck.core.form_tasks import PROVIDER_FORMS, available_providers
from domaincheck.core.quick_report_routes import report_forms
from domaincheck.application.quick_reports import (
    QuickReportNotFound, QuickReportOperation, QuickReportService,
)


router = APIRouter(prefix="/quick-reports", tags=["quick reports"])


@router.get("/readiness")
async def quick_report_readiness(
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedUser = Depends(require_permission("quick_report.view")),
):
    from domaincheck.application.profile_readiness import missing_report_profile
    from domaincheck.infrastructure.db.models import UserSettings
    missing = missing_report_profile(await session.get(UserSettings, actor.user_id))
    return {"ready": not missing, "missing": missing}


def actions(request, actor):
    return QuickReportActions(request.app.state.database.session_factory, request.app.state.artifact_store,
        owner=actor.user_id, timezone=actor.timezone, request_id=request.state.request_id,
        now=request.app.state.clock.now, adapter=getattr(request.app.state, "netcraft_adapter", None))


class NetcraftSubmit(BaseModel):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=1)
    confirmed: bool
    retry: bool = False


def action_error(exc):
    if isinstance(exc, QuickReportNotFound):
        raise HTTPException(404, "Quick Report resource not found") from exc
    raise HTTPException(409, str(exc)) from exc


@router.post("/checks/{check_id}/netcraft/preview")
async def netcraft_preview(check_id: UUID, request: Request,
    actor: AuthenticatedUser = Depends(require_permission("quick_report.submit_netcraft", csrf=True, configured_profile=True))):
    try:
        result = await actions(request, actor).preview(check_id)
        return result | {"submit_enabled": request.app.state.settings.netcraft_submit_enabled}
    except (QuickReportNotFound, ActionConflict) as exc:
        action_error(exc)


@router.get("/netcraft/{submission_id}")
async def netcraft_status(submission_id: UUID, request: Request,
    actor: AuthenticatedUser = Depends(require_permission("quick_report.submit_netcraft"))):
    try:
        return await actions(request, actor).get(submission_id)
    except (QuickReportNotFound, ActionConflict) as exc:
        action_error(exc)


@router.get("/checks/{check_id}/netcraft")
async def netcraft_for_check(check_id: UUID, request: Request,
    actor: AuthenticatedUser = Depends(require_permission("quick_report.submit_netcraft"))):
    try:
        result = await actions(request, actor).find(check_id)
        return result | {"submit_enabled": request.app.state.settings.netcraft_submit_enabled} if result else None
    except (QuickReportNotFound, ActionConflict) as exc:
        action_error(exc)


@router.post("/netcraft/{submission_id}/submit")
async def netcraft_submit(submission_id: UUID, body: NetcraftSubmit, request: Request,
    actor: AuthenticatedUser = Depends(require_permission("quick_report.submit_netcraft", csrf=True, configured_profile=True))):
    if not request.app.state.settings.netcraft_submit_enabled:
        raise HTTPException(503, "Netcraft submission is disabled by the operator; preview is available")
    try:
        return await actions(request, actor).submit(submission_id, **body.model_dump())
    except (QuickReportNotFound, ActionConflict) as exc:
        action_error(exc)


@router.get("/checks/{operation_id}/exports/nictop")
async def export_nictop(operation_id: UUID, request: Request,
    actor: AuthenticatedUser = Depends(require_permission("quick_report.export", configured_profile=True))):
    try:
        content = await actions(request, actor).export_nictop(operation_id)
    except (QuickReportNotFound, ActionConflict) as exc:
        action_error(exc)
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="nictop-{operation_id}.xlsx"',
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class QuickReportCreate(BaseModel):
    targets: list[str] = Field(min_length=1, max_length=100)


class QuickReportCheckResponse(BaseModel):
    id: UUID
    requested_url: str
    domain: str
    status: str
    result: dict
    cache_provenance: dict
    providers: list[dict[str, str]]
    report_forms: list[dict]


class QuickReportOperationResponse(BaseModel):
    id: UUID
    state: str
    stage: str
    business_day: str
    checks: list[QuickReportCheckResponse]
    duplicates: list[str]


def response(operation: QuickReportOperation) -> QuickReportOperationResponse:
    return QuickReportOperationResponse(
        id=operation.job.id,
        state=operation.job.state,
        stage=operation.job.stage,
        business_day=operation.job.business_day.isoformat(),
        checks=[QuickReportCheckResponse(
            id=row.id, requested_url=row.requested_url, domain=row.domain,
            status=row.status, result=row.result, cache_provenance=row.cache_provenance,
            providers=[{"id": key, "label": PROVIDER_FORMS[key][0], "form_url": PROVIDER_FORMS[key][1]}
                for key in available_providers(row.domain, row.result)] if row.status == "succeeded" else [],
            report_forms=report_forms(row.domain, row.result) if row.status == 'succeeded' else [],
        ) for row in operation.checks],
        duplicates=list(operation.duplicates),
    )


@router.get('/daily')
async def daily_checks(request: Request, http_response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedUser = Depends(require_permission('quick_report.view')),
):
    from domaincheck.infrastructure.cache import business_day
    from sqlalchemy import select
    from domaincheck.infrastructure.db.models import Job, JobItem
    now = request.app.state.clock.now()
    checks = await QuickReportService(session, owner_user_id=actor.user_id,
        actor_user_id=actor.user_id, timezone=actor.timezone,
        request_id=request.state.request_id, now=now).daily_checks()
    linked = set(await session.scalars(select(JobItem.result_metadata['check_id'].as_string())
        .join(Job, Job.id == JobItem.job_id)
        .where(JobItem.owner_user_id == actor.user_id, Job.owner_user_id == actor.user_id,
            Job.job_type == 'quick_report',
            JobItem.result_metadata['check_id'].as_string().in_([str(row.id) for row in checks]))))
    http_response.headers['Cache-Control'] = 'private, no-store'
    return {'business_day': business_day(now, actor.timezone).isoformat(),
        'checks': [QuickReportCheckResponse(id=row.id, requested_url=row.requested_url,
            domain=row.domain, status='not_queued' if row.status in {'pending', 'running'} and str(row.id) not in linked else row.status, result=row.result,
            cache_provenance=row.cache_provenance,
            providers=[{'id': key, 'label': PROVIDER_FORMS[key][0], 'form_url': PROVIDER_FORMS[key][1]}
                for key in available_providers(row.domain, row.result)] if row.status == 'succeeded' else [],
            report_forms=report_forms(row.domain, row.result) if row.status == 'succeeded' else []) for row in checks]}


@router.get('/checks/{check_id}/form-drafts')
async def form_drafts(check_id: UUID, response: Response, include_providers: bool = False,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedUser = Depends(require_permission('quick_report.open_form', configured_profile=True)),
):
    import importlib
    from domaincheck.infrastructure.db.models import Check, UserSettings
    from domaincheck.infrastructure.db.owned import OwnedRepository
    check = await OwnedRepository(session, Check, actor.user_id).get(check_id)
    if check is None or check.workflow != 'quick_report':
        raise HTTPException(404, 'Quick Report check not found')
    if check.status != 'succeeded':
        raise HTTPException(409, 'Quick Report check is not complete')
    profile = await session.get(UserSettings, actor.user_id)
    config = {field: getattr(profile, field) for field in ('brand_name', 'contact_name', 'contact_email', 'company_name', 'business_registration_no', 'signature_role')}
    config['registry_contact'] = check.result.get('registry_contact')
    toolkit = importlib.import_module('domaincheck.engine.phishing_toolkit')
    drafts = []
    if include_providers:
        from domaincheck.core.form_tasks import QuickReportFormTaskAdapter
        adapter = QuickReportFormTaskAdapter()
        for provider in available_providers(check.domain, check.result):
            if provider not in {'google_gsb', 'microsoft_smartscreen', 'chongluadao', 'coccoc_safe', 'cloudflare'}:
                continue
            task = adapter.build(provider, target_url=check.requested_url, domain=check.domain, config=config)
            drafts.append({'channel': 'infrastructure' if provider == 'cloudflare' else 'general',
                'label': PROVIDER_FORMS[provider][0], 'form_url': task.form_url,
                'autofill_provider': provider, 'note': '', 'draft': task.draft})
        drafts.append({'channel': 'general', 'label': 'Netcraft', 'form_url': 'https://report.netcraft.com/',
            'autofill_provider': None, 'note': 'netcraft',
            'draft': toolkit.generate_community_report_text(check.domain, {'brand_name': config['brand_name']}, check.requested_url)})
    for form in report_forms(check.domain, check.result):
        if include_providers and form['autofill_provider']:
            draft = adapter.build(form['autofill_provider'], target_url=check.requested_url, domain=check.domain, config=config).draft
        elif form['channel'] == 'registrar':
            draft = toolkit.get_webform_draft_text(check.domain, form['label'], form['form_url'], config, target_url=check.requested_url)
        else:
            draft = toolkit.get_registry_webform_draft_text(check.domain, toolkit._static_registry_lookup(check.domain), config, target_url=check.requested_url)
        drafts.append(form | {'draft': draft})
    response.headers['Cache-Control'] = 'private, no-store'
    return drafts


@router.post("/checks", response_model=QuickReportOperationResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_checks(
    body: QuickReportCreate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedUser = Depends(require_permission("quick_report.check", csrf=True, configured_profile=True)),
) -> QuickReportOperationResponse:
    try:
        operation = await QuickReportService(
            session, owner_user_id=actor.user_id, actor_user_id=actor.user_id,
            timezone=actor.timezone, request_id=request.state.request_id,
            now=request.app.state.clock.now(),
        ).create(body.targets)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return response(operation)


@router.get("/checks/{operation_id}", response_model=QuickReportOperationResponse)
async def get_checks(
    operation_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedUser = Depends(require_permission("quick_report.view")),
) -> QuickReportOperationResponse:
    try:
        operation = await QuickReportService(
            session, owner_user_id=actor.user_id, actor_user_id=actor.user_id,
            timezone=actor.timezone, request_id=request.state.request_id,
            now=request.app.state.clock.now(),
        ).get(operation_id)
    except (QuickReportNotFound, ValueError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quick Report operation not found")
    return response(operation)

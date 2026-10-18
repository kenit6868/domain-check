from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from domaincheck.api.auth import router as auth_router
from domaincheck.api.admin import router as admin_router
from domaincheck.api.artifacts import router as artifacts_router
from domaincheck.api.cache import router as cache_router
from domaincheck.api.mail_settings import router as mail_settings_router
from domaincheck.api.quick_reports import router as quick_reports_router
from domaincheck.api.domain_checks import router as domain_checks_router
from domaincheck.api.domain_worker import router as domain_worker_router
from domaincheck.api.evidence_review import router as evidence_review_router
from domaincheck.api.cloaking_review import router as cloaking_review_router
from domaincheck.api.provider_replies import router as provider_replies_router
from domaincheck.api.cloudflare_worker import router as cloudflare_worker_router
from domaincheck.api.link_status import router as link_status_router
from domaincheck.api.audit_log import router as audit_log_router
from domaincheck.api.dashboard import router as dashboard_router
from domaincheck.api.form_tasks import router as form_tasks_router
from domaincheck.api.health import router as health_router
from domaincheck.application.context import SystemClock
from domaincheck.core.mail import AllowlistedMailConnectionTester, StandardMailConnectionTester
from domaincheck.infrastructure.auth.oidc import RedisOIDCStateStore, TelegramOIDCClient
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.cache import ScopedRedisCache
from domaincheck.infrastructure.db.session import Database
from domaincheck.infrastructure.db.seeds import sync_permission_catalog
from domaincheck.infrastructure.secrets import SecretEnvelope
from domaincheck.settings import Settings, get_settings


class LiveResponse(BaseModel):
    status: str
    service: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    field_errors: list[dict[str, str]] = Field(default_factory=list)
    request_id: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            # Run after schema migrations, before accepting traffic. Upsert new
            # codes/initial roles and retire obsolete codes without resetting
            # existing role selections. The seed serializes API replicas/CLI.
            async with application.state.database.session_factory() as session, session.begin():
                await sync_permission_catalog(session)
            yield
        finally:
            await application.state.database.dispose()
            await application.state.redis.aclose()

    app = FastAPI(
        title="Domain Check Tool API",
        version="0.1.0",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url=f"{settings.api_prefix}/docs" if settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.clock = SystemClock()
    app.state.database = Database(settings.database_url)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.scoped_cache = ScopedRedisCache(app.state.redis)
    app.state.artifact_store = FilesystemArtifactStore(
        settings.artifact_root,
        max_file_bytes=settings.artifact_max_file_bytes,
        max_dimension=settings.artifact_max_dimension,
        max_pixels=settings.artifact_max_pixels,
    )
    app.state.oidc = None
    app.state.secret_envelope = None
    app.state.mail_connection_tester = AllowlistedMailConnectionTester(
        StandardMailConnectionTester(), settings.mail_egress_endpoints)
    if settings.secret_master_key:
        app.state.secret_envelope = SecretEnvelope.from_base64_keys(
            {settings.secret_key_version: settings.secret_master_key.get_secret_value()},
            settings.secret_key_version,
        )
    if settings.telegram_client_id and settings.telegram_client_secret:
        app.state.oidc = TelegramOIDCClient(
            issuer=settings.telegram_issuer,
            client_id=settings.telegram_client_id,
            client_secret=settings.telegram_client_secret.get_secret_value(),
            redirect_uri=settings.telegram_redirect_uri,
            state_store=RedisOIDCStateStore(app.state.redis),
            state_ttl_seconds=settings.oidc_state_ttl_seconds,
        )

    @app.middleware("http")
    async def request_identity(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if supplied and len(supplied) <= 128 and supplied.isascii() else str(uuid4())
        request.state.request_id = request_id
        from domaincheck.api.audit_capture import capture_command
        try:
            response = await call_next(request)
        except Exception:
            await capture_command(request, SimpleNamespace(status_code=500))
            raise
        response.headers["X-Request-ID"] = request_id
        await capture_command(request, response)
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request could not be completed"
        body = ErrorResponse(
            code=f"http_{exc.status_code}", message=message,
            request_id=getattr(request.state, "request_id", str(uuid4())),
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"], "type": item["type"]}
            for item in exc.errors()
        ]
        body = ErrorResponse(
            code="validation_error", message="Request validation failed", field_errors=fields,
            request_id=getattr(request.state, "request_id", str(uuid4())),
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.get(f"{settings.api_prefix}/health/live", response_model=LiveResponse, tags=["health"])
    async def live() -> LiveResponse:
        return LiveResponse(status="ok", service="domaincheck-api")

    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(health_router, prefix=settings.api_prefix)
    app.include_router(audit_log_router, prefix=settings.api_prefix)
    app.include_router(dashboard_router, prefix=settings.api_prefix)
    app.include_router(admin_router, prefix=settings.api_prefix)
    app.include_router(artifacts_router, prefix=settings.api_prefix)
    app.include_router(cache_router, prefix=settings.api_prefix)
    app.include_router(mail_settings_router, prefix=settings.api_prefix)
    app.include_router(quick_reports_router, prefix=settings.api_prefix)
    app.include_router(domain_checks_router, prefix=settings.api_prefix)
    app.include_router(domain_worker_router, prefix=settings.api_prefix)
    app.include_router(evidence_review_router, prefix=settings.api_prefix)
    app.include_router(cloaking_review_router, prefix=settings.api_prefix)
    app.include_router(provider_replies_router, prefix=settings.api_prefix)
    app.include_router(cloudflare_worker_router, prefix=settings.api_prefix)
    app.include_router(link_status_router, prefix=settings.api_prefix)
    app.include_router(form_tasks_router, prefix=settings.api_prefix)
    return app


app = create_app()


from __future__ import annotations

from dataclasses import asdict
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.api.dependencies import get_db_session, require_permission
from domaincheck.application.auth import AuthenticatedUser
from domaincheck.application.mail_settings import (
    MailAccountValues,
    MailSettingsConflict,
    MailSettingsNotFound,
    MailSettingsService,
)
from domaincheck.core.mail import ConnectionTestResult


router = APIRouter(tags=["mail settings"])


@router.get('/settings/profile/logo')
async def get_signature_logo(request: Request,
    actor=Depends(require_permission('mail_accounts.view_own')),
    session: AsyncSession = Depends(get_db_session)):
    from domaincheck.application.signature_logo import load_profile_logo
    from domaincheck.infrastructure.db.models import UserSettings
    from domaincheck.ports.artifacts import ArtifactIntegrityError
    try:
        logo = await load_profile_logo(session, request.app.state.artifact_store, actor.user_id,
            await session.get(UserSettings, actor.user_id))
    except (ArtifactIntegrityError, FileNotFoundError) as exc:
        raise HTTPException(409, 'Signature logo failed integrity validation') from exc
    return Response(logo.data, media_type=logo.mime_type, headers={'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'"})


@router.put('/settings/profile/logo')
async def upload_signature_logo(request: Request, file: UploadFile = File(...),
    actor=Depends(require_permission('mail_accounts.manage_own', csrf=True))):
    from domaincheck.application.signature_logo import save_profile_logo
    from domaincheck.ports.artifacts import ArtifactValidationError, ImageUpload
    limit = min(request.app.state.settings.artifact_max_file_bytes, 2 * 1024 * 1024)
    data = await file.read(limit + 1)
    if len(data) > limit or file.content_type not in {'image/png', 'image/jpeg'}:
        raise HTTPException(422, 'Use one PNG/JPEG logo, maximum 2 MB')
    try:
        artifact_id = await save_profile_logo(request.app.state.database.session_factory,
            request.app.state.artifact_store, actor.user_id,
            ImageUpload('signature-logo.png' if file.content_type == 'image/png' else 'signature-logo.jpg', file.content_type, data),
            getattr(request.state, 'request_id', 'signature-logo'))
    except ArtifactValidationError as exc:
        raise HTTPException(422, 'Logo must be a valid PNG/JPEG image within the image limits') from exc
    return {'logo_artifact_id': artifact_id}


@router.delete('/settings/profile/logo', status_code=204)
async def reset_signature_logo(request: Request,
    actor=Depends(require_permission('mail_accounts.manage_own', csrf=True))):
    from domaincheck.application.signature_logo import reset_profile_logo
    await reset_profile_logo(request.app.state.database.session_factory, actor.user_id,
        getattr(request.state, 'request_id', 'signature-logo'))
    return Response(status_code=204)


@router.get('/settings/mail-connectivity')
async def mail_connectivity_status(request: Request,
    actor=Depends(require_permission('mail_accounts.view_own')),
    session: AsyncSession = Depends(get_db_session)):
    endpoints = {item.strip().casefold() for item in request.app.state.settings.mail_egress_endpoints}
    accounts = await _service(request, session, actor).list_accounts()
    return {'enabled': request.app.state.settings.mail_connectivity_enabled, 'accounts': [
        {'id': account.id,
         'smtp_allowed': f'{account.smtp_host}:{account.smtp_port}'.casefold() in endpoints,
         'imap_allowed': f'{account.imap_config.get("host", "")}:{account.imap_config.get("port", 993)}'.casefold() in endpoints,
         'proxy_allowed': not account.proxy_config.get('host') or f'{account.proxy_config.get("host")}:{account.proxy_config.get("port")}'.casefold() in endpoints}
        for account in accounts]}


@router.post("/settings/profile/logo/default")
async def apply_default_logo(request: Request,
    actor: AuthenticatedUser = Depends(require_permission("mail_accounts.manage_own", csrf=True))):
    from domaincheck.application.signature_logo import import_bundled_logo
    from domaincheck.ports.artifacts import ArtifactIntegrityError
    try:
        artifact_id = await import_bundled_logo(request.app.state.database.session_factory,
            request.app.state.artifact_store, actor.user_id, getattr(request.state, 'request_id', 'signature-logo'))
    except (ArtifactIntegrityError, FileNotFoundError) as exc:
        raise HTTPException(409, "Signature logo is unavailable or failed integrity validation") from exc
    return {"logo_artifact_id": artifact_id}


class ImapInput(BaseModel):
    reply_mailboxes: list[str] | None = Field(default=None, max_length=30)
    host: str = Field(default="", max_length=500)
    port: int = Field(default=993, ge=1, le=65535)
    username: str = Field(default="", max_length=500)
    mailbox: str = Field(default="INBOX", max_length=500)
    sent_mailbox: str = Field(default="Sent", max_length=500)
    junk_mailbox: str = Field(default="Junk", max_length=500)
    cloudflare_mailbox: str = Field(default="2-Cloudflare", max_length=500)
    evidence_mailbox: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_reply_mailboxes(self):
        if self.reply_mailboxes is not None:
            names = []
            for value in self.reply_mailboxes:
                name = value.strip()
                if not name or len(name) > 500 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                    raise ValueError("Use valid non-empty IMAP folder names (maximum 500 characters)")
                if name not in names:
                    names.append(name)
            self.reply_mailboxes = names
        return self


class ProxyInput(BaseModel):
    scheme: Literal["socks5", "socks4", "http"] | None = None
    host: str = Field(default="", max_length=500)
    port: int | None = Field(default=None, ge=1, le=65535)


class SignatureInput(BaseModel):
    contact_name: str = Field(default="", max_length=300)
    contact_email: str = Field(default="", max_length=500)
    signature_role: str = Field(default="", max_length=300)


class MailAccountBase(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    smtp_host: str = Field(min_length=1, max_length=500)
    smtp_port: int = Field(ge=1, le=65535)
    smtp_username: str = Field(min_length=1, max_length=500)
    smtp_transport: Literal["implicit_tls", "starttls", "plain"] | None = None
    enabled: bool = True
    is_default: bool = False
    imap: ImapInput = Field(default_factory=ImapInput)
    proxy: ProxyInput = Field(default_factory=ProxyInput)
    signature: SignatureInput = Field(default_factory=SignatureInput)

    @model_validator(mode="after")
    def transport_default(self):
        if self.smtp_transport is None:
            self.smtp_transport = "implicit_tls" if self.smtp_port == 465 else "starttls"
        if self.proxy.host and (not self.proxy.scheme or not self.proxy.port):
            raise ValueError("Proxy scheme and port are required when proxy host is set")
        return self


class MailAccountCreate(MailAccountBase):
    smtp_password: str | None = Field(default=None, min_length=1, max_length=4096)
    imap_password: str | None = Field(default=None, min_length=1, max_length=4096)
    proxy_username: str | None = Field(default=None, max_length=1000)
    proxy_password: str | None = Field(default=None, min_length=1, max_length=4096)


class MailAccountUpdate(MailAccountCreate):
    pass


class MailAccountResponse(BaseModel):
    id: UUID
    label: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_transport: str
    enabled: bool
    is_default: bool
    imap: ImapInput
    proxy: ProxyInput
    signature: SignatureInput
    has_smtp_password: bool
    has_imap_password: bool
    has_proxy_credentials: bool
    version: int


class ProfileInput(BaseModel):
    brand_name: str = Field(default="", max_length=300)
    contact_name: str = Field(default="", max_length=300)
    contact_email: str = Field(default="", max_length=500)
    company_name: str = Field(default="", max_length=500)
    business_registration_no: str = Field(default="", max_length=200)
    signature_role: str = Field(default="", max_length=300)


class ProfileResponse(ProfileInput):
    logo_artifact_id: UUID | None
    version: int


class ConnectionTestResponse(BaseModel):
    status: Literal["success", "failed"]
    protocol: Literal["smtp", "imap"]
    transport: str
    code: str
    message: str
    elapsed_ms: int


def _service(request: Request, session: AsyncSession, actor: AuthenticatedUser) -> MailSettingsService:
    return MailSettingsService(
        session, owner_user_id=actor.user_id, actor_user_id=actor.user_id,
        request_id=getattr(request.state, "request_id", str(uuid4())),
        envelope=request.app.state.secret_envelope,
    )


def _values(body: MailAccountBase) -> MailAccountValues:
    return MailAccountValues(
        label=body.label.strip(), smtp_host=body.smtp_host.strip(), smtp_port=body.smtp_port,
        smtp_username=body.smtp_username.strip(), smtp_transport=body.smtp_transport or "starttls",
        enabled=body.enabled, is_default=body.is_default,
        imap_config=body.imap.model_dump(), proxy_config=body.proxy.model_dump(),
        signature_overrides=body.signature.model_dump(),
    )


def _secrets(body: MailAccountCreate) -> dict[str, str | None]:
    return {
        "smtp_password": body.smtp_password,
        "imap_password": body.imap_password,
        "proxy_username": body.proxy_username,
        "proxy_password": body.proxy_password,
    }


def _response(account) -> MailAccountResponse:
    fields = set((account.secret_metadata or {}).get("fields", []))
    return MailAccountResponse(
        id=account.id, label=account.label, smtp_host=account.smtp_host,
        smtp_port=account.smtp_port, smtp_username=account.smtp_username,
        smtp_transport=account.smtp_transport, enabled=account.enabled,
        is_default=account.is_default, imap=ImapInput.model_validate(account.imap_config),
        proxy=ProxyInput.model_validate(account.proxy_config),
        signature=SignatureInput.model_validate(account.signature_overrides),
        has_smtp_password="smtp_password" in fields,
        has_imap_password="imap_password" in fields,
        has_proxy_credentials=bool({"proxy_username", "proxy_password"} & fields),
        version=account.version,
    )


def _raise(exc: Exception) -> None:
    if isinstance(exc, MailSettingsNotFound):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, MailSettingsConflict):
        messages = {
            "Mail secret encryption is not configured": (503, "Máy chủ chưa cấu hình khóa mã hóa mật khẩu mail. Quản trị viên cần cấu hình DOMAINCHECK_SECRET_MASTER_KEY và khởi động lại backend."),
            "Mail secret key is unavailable": (503, "Khóa giải mã mật khẩu mail chưa sẵn sàng. Liên hệ quản trị viên kiểm tra cấu hình khóa."),
            "SMTP password is not configured": (409, "Tài khoản chưa có mật khẩu SMTP. Mở Sửa để bổ sung."),
            "IMAP host, username and password are required": (409, "Cần cấu hình host, username và mật khẩu IMAP trước khi kiểm tra."),
            "Mail account is disabled": (409, "Tài khoản mail đang tắt. Bật lại trước khi sử dụng."),
            "Proxy configuration is incomplete": (409, "Cấu hình proxy chưa đầy đủ. Kiểm tra scheme, host và port."),
        }
        code, message = messages.get(str(exc), (409, "Cấu hình tài khoản mail chưa phù hợp. Kiểm tra thông tin và thử lại."))
        raise HTTPException(code, message) from exc
    if isinstance(exc, IntegrityError):
        original = getattr(exc.orig, "__cause__", None) or exc.orig
        constraint = getattr(original, "constraint_name", None)
        if constraint == "uq_sender_accounts_owner_user_id_normalized_identity":
            raise HTTPException(409, "Tài khoản mail này đã tồn tại (cùng SMTP host, port và username). Hãy mở Sửa ở tài khoản đã có; nếu tài khoản đang tắt, có thể bật lại trong form sửa.") from exc
        raise HTTPException(409, "Không thể lưu do dữ liệu tài khoản xung đột. Kiểm tra tài khoản mặc định hoặc tải lại danh sách trước khi thử lại.") from exc
    raise HTTPException(422, str(exc)) from exc


@router.get("/mail-accounts", response_model=list[MailAccountResponse])
async def list_mail_accounts(
    request: Request,
    actor: AuthenticatedUser = Depends(require_permission("mail_accounts.view_own")),
    session: AsyncSession = Depends(get_db_session),
) -> list[MailAccountResponse]:
    accounts = await _service(request, session, actor).list_accounts()
    return [_response(item) for item in accounts]


@router.post("/mail-accounts", response_model=MailAccountResponse, status_code=201)
async def create_mail_account(
    body: MailAccountCreate, request: Request,
    actor: AuthenticatedUser = Depends(require_permission("mail_accounts.manage_own", csrf=True)),
    session: AsyncSession = Depends(get_db_session),
) -> MailAccountResponse:
    try:
        account = await _service(request, session, actor).create_account(_values(body), _secrets(body))
    except (MailSettingsConflict, IntegrityError, ValueError) as exc:
        _raise(exc)
    return _response(account)


@router.get("/mail-accounts/{account_id}", response_model=MailAccountResponse)
async def get_mail_account(
    account_id: UUID, request: Request,
    actor: AuthenticatedUser = Depends(require_permission("mail_accounts.view_own")),
    session: AsyncSession = Depends(get_db_session),
) -> MailAccountResponse:
    try:
        return _response(await _service(request, session, actor).get_account(account_id))
    except MailSettingsNotFound as exc:
        _raise(exc)


@router.patch("/mail-accounts/{account_id}", response_model=MailAccountResponse)
async def update_mail_account(
    account_id: UUID, body: MailAccountUpdate, request: Request,
    actor: AuthenticatedUser = Depends(require_permission("mail_accounts.manage_own", csrf=True)),
    session: AsyncSession = Depends(get_db_session),
) -> MailAccountResponse:
    secret_fields = {"smtp_password", "imap_password", "proxy_username", "proxy_password"}
    changed = secret_fields & body.model_fields_set
    try:
        account = await _service(request, session, actor).update_account(
            account_id, _values(body), _secrets(body), changed
        )
    except (MailSettingsConflict, MailSettingsNotFound, IntegrityError, ValueError) as exc:
        _raise(exc)
    return _response(account)


@router.delete("/mail-accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_mail_account(
    account_id: UUID, request: Request,
    actor: AuthenticatedUser = Depends(require_permission("mail_accounts.manage_own", csrf=True)),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    try:
        await _service(request, session, actor).disable_account(account_id)
    except MailSettingsNotFound as exc:
        _raise(exc)
    return Response(status_code=204)


def _connection_response(result: ConnectionTestResult) -> ConnectionTestResponse:
    return ConnectionTestResponse(**asdict(result))


@router.post("/mail-accounts/{account_id}/test-smtp", response_model=ConnectionTestResponse)
async def test_smtp_connection(
    account_id: UUID, request: Request,
    actor: AuthenticatedUser = Depends(require_permission("mail_accounts.test_own", csrf=True)),
    session: AsyncSession = Depends(get_db_session),
) -> ConnectionTestResponse:
    if not request.app.state.settings.mail_connectivity_enabled:
        raise HTTPException(503, "Chức năng kiểm tra kết nối mail chưa được quản trị viên bật trên server.")
    try:
        result = await _service(request, session, actor).test_smtp_connection(
            account_id, request.app.state.mail_connection_tester
        )
    except (MailSettingsConflict, MailSettingsNotFound, ValueError) as exc:
        _raise(exc)
    return _connection_response(result)


@router.post("/mail-accounts/{account_id}/test-imap", response_model=ConnectionTestResponse)
async def test_imap_connection(
    account_id: UUID, request: Request,
    actor: AuthenticatedUser = Depends(require_permission("mail_accounts.test_own", csrf=True)),
    session: AsyncSession = Depends(get_db_session),
) -> ConnectionTestResponse:
    if not request.app.state.settings.mail_connectivity_enabled:
        raise HTTPException(503, "Chức năng kiểm tra kết nối mail chưa được quản trị viên bật trên server.")
    try:
        result = await _service(request, session, actor).test_imap_connection(
            account_id, request.app.state.mail_connection_tester
        )
    except (MailSettingsConflict, MailSettingsNotFound, ValueError) as exc:
        _raise(exc)
    return _connection_response(result)


@router.get("/settings/profile", response_model=ProfileResponse)
async def get_profile(
    request: Request,
    actor: AuthenticatedUser = Depends(require_permission("mail_accounts.view_own")),
    session: AsyncSession = Depends(get_db_session),
) -> ProfileResponse:
    profile = await _service(request, session, actor).get_profile()
    return ProfileResponse(
        brand_name=profile.brand_name, contact_name=profile.contact_name,
        contact_email=profile.contact_email, company_name=profile.company_name,
        business_registration_no=profile.business_registration_no,
        signature_role=profile.signature_role, logo_artifact_id=profile.logo_artifact_id,
        version=profile.version,
    )


@router.patch("/settings/profile", response_model=ProfileResponse)
async def update_profile(
    body: ProfileInput, request: Request,
    actor: AuthenticatedUser = Depends(require_permission("mail_accounts.manage_own", csrf=True)),
    session: AsyncSession = Depends(get_db_session),
) -> ProfileResponse:
    profile = await _service(request, session, actor).update_profile(body.model_dump())
    return ProfileResponse(**body.model_dump(), logo_artifact_id=profile.logo_artifact_id, version=profile.version)

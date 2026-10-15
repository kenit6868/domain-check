from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.api.dependencies import get_db_session, require_permission
from domaincheck.application.admin import AdminConflict, AdminNotFound, AdminService
from domaincheck.application.auth import AuthenticatedUser
from domaincheck.domain.authz import PERMISSION_CATALOG


router = APIRouter(tags=["administration"])


class RoleSummary(BaseModel):
    id: UUID
    key: str
    name: str
    system: bool


class UserItem(BaseModel):
    id: UUID
    display_name: str
    telegram_username: str | None
    identity_provider: str | None
    status: str
    timezone: str
    last_login_at: str | None
    roles: list[RoleSummary]


class UserPage(BaseModel):
    items: list[UserItem]
    total: int
    offset: int
    limit: int


class StatusUpdate(BaseModel):
    status: str


class RoleAssignment(BaseModel):
    role_ids: list[UUID] = Field(default_factory=list, max_length=100)


class PermissionItem(BaseModel):
    code: str
    description: str
    safe_for_default_user: bool


class RoleDetail(RoleSummary):
    description: str
    permissions: list[str]
    version: int


class RoleCreate(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    permissions: list[str] = Field(default_factory=list, max_length=200)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    permissions: list[str] | None = Field(default=None, max_length=200)


def _service(session: AsyncSession, actor: AuthenticatedUser, request_id: str | None) -> AdminService:
    return AdminService(session, actor_user_id=actor.user_id, request_id=request_id or str(uuid4()))


def _raise_admin_error(exc: Exception) -> None:
    if isinstance(exc, AdminNotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if isinstance(exc, AdminConflict):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/users", response_model=UserPage)
async def list_users(
    search: str | None = Query(default=None, max_length=200),
    user_status: str | None = Query(default=None, alias="status", pattern="^(pending|active|disabled)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    actor: AuthenticatedUser = Depends(require_permission("users.view")),
    session: AsyncSession = Depends(get_db_session),
) -> UserPage:
    service = _service(session, actor, None)
    page = await service.list_users(search=search, status=user_status, offset=offset, limit=limit)
    roles = await service.roles_for_users([item.id for item in page.items])
    identities = await service.identities_for_users([item.id for item in page.items])
    return UserPage(
        items=[
            UserItem(
                id=item.id, display_name=item.display_name, telegram_username=item.telegram_username,
                identity_provider=identities[item.id].provider if item.id in identities else None,
                status=item.status, timezone=item.timezone,
                last_login_at=item.last_login_at.isoformat() if item.last_login_at else None,
                roles=[RoleSummary(id=role.id, key=role.key, name=role.name, system=role.system) for role in roles[item.id]],
            ) for item in page.items
        ],
        total=page.total, offset=page.offset, limit=page.limit,
    )


@router.patch("/users/{user_id}/status", response_model=UserItem)
async def update_user_status(
    user_id: UUID,
    body: StatusUpdate,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
    actor: AuthenticatedUser = Depends(require_permission("users.manage", csrf=True)),
    session: AsyncSession = Depends(get_db_session),
) -> UserItem:
    service = _service(session, actor, request_id)
    try:
        item = await service.set_user_status(user_id, body.status)
    except (AdminConflict, AdminNotFound, ValueError) as exc:
        _raise_admin_error(exc)
    roles = await service.roles_for_users([item.id])
    identities = await service.identities_for_users([item.id])
    return UserItem(
        id=item.id, display_name=item.display_name, telegram_username=item.telegram_username,
        identity_provider=identities[item.id].provider if item.id in identities else None,
        status=item.status, timezone=item.timezone,
        last_login_at=item.last_login_at.isoformat() if item.last_login_at else None,
        roles=[RoleSummary(id=role.id, key=role.key, name=role.name, system=role.system) for role in roles[item.id]],
    )


@router.put("/users/{user_id}/roles", status_code=status.HTTP_204_NO_CONTENT)
async def assign_user_roles(
    user_id: UUID,
    body: RoleAssignment,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
    actor: AuthenticatedUser = Depends(require_permission("users.assign_roles", csrf=True)),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    try:
        await _service(session, actor, request_id).assign_user_roles(
            user_id, set(body.role_ids), actor.permissions
        )
    except (AdminConflict, AdminNotFound, ValueError) as exc:
        _raise_admin_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/revoke-sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_user_sessions(
    user_id: UUID,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
    actor: AuthenticatedUser = Depends(require_permission("users.manage", csrf=True)),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    try:
        await _service(session, actor, request_id).revoke_user_sessions(user_id)
    except (AdminNotFound, ValueError) as exc:
        _raise_admin_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/permissions", response_model=list[PermissionItem])
async def list_permissions(
    _: AuthenticatedUser = Depends(require_permission("roles.view")),
) -> list[PermissionItem]:
    return [
        PermissionItem(
            code=item.code,
            description=item.description,
            safe_for_default_user=item.safe_for_default_user,
        )
        for item in PERMISSION_CATALOG
    ]


@router.get("/roles", response_model=list[RoleDetail])
async def list_roles(
    actor: AuthenticatedUser = Depends(require_permission("roles.view")),
    session: AsyncSession = Depends(get_db_session),
) -> list[RoleDetail]:
    service = _service(session, actor, None)
    roles = await service.list_roles()
    permissions = await service.permissions_for_roles([role.id for role in roles])
    return [
        RoleDetail(
            id=role.id, key=role.key, name=role.name, description=role.description,
            system=role.system, permissions=permissions[role.id], version=role.version,
        ) for role in roles
    ]


@router.post("/roles", response_model=RoleDetail, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreate,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
    actor: AuthenticatedUser = Depends(require_permission("roles.manage", csrf=True)),
    session: AsyncSession = Depends(get_db_session),
) -> RoleDetail:
    service = _service(session, actor, request_id)
    try:
        role = await service.create_role(
            key=body.key, name=body.name, description=body.description,
            permission_codes=set(body.permissions), actor_permissions=actor.permissions,
        )
    except (AdminConflict, AdminNotFound, ValueError) as exc:
        _raise_admin_error(exc)
    permissions = await service.permissions_for_roles([role.id])
    return RoleDetail(id=role.id, key=role.key, name=role.name, description=role.description,
                      system=role.system, permissions=permissions[role.id], version=role.version)


@router.patch("/roles/{role_id}", response_model=RoleDetail)
async def update_role(
    role_id: UUID,
    body: RoleUpdate,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
    actor: AuthenticatedUser = Depends(require_permission("roles.manage", csrf=True)),
    session: AsyncSession = Depends(get_db_session),
) -> RoleDetail:
    service = _service(session, actor, request_id)
    try:
        role = await service.update_role(
            role_id, name=body.name, description=body.description,
            permission_codes=set(body.permissions) if body.permissions is not None else None,
            actor_permissions=actor.permissions,
        )
    except (AdminConflict, AdminNotFound, ValueError) as exc:
        _raise_admin_error(exc)
    permissions = await service.permissions_for_roles([role.id])
    return RoleDetail(id=role.id, key=role.key, name=role.name, description=role.description,
                      system=role.system, permissions=permissions[role.id], version=role.version)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: UUID,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
    actor: AuthenticatedUser = Depends(require_permission("roles.manage", csrf=True)),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    try:
        await _service(session, actor, request_id).delete_role(role_id)
    except (AdminConflict, AdminNotFound, ValueError) as exc:
        _raise_admin_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

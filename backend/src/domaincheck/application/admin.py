from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.domain.authz import validate_permission_codes
from domaincheck.infrastructure.db.models import (
    AuditEvent,
    AuthIdentity,
    Permission,
    Role,
    RolePermission,
    Session,
    User,
    UserRole,
)


class AdminConflict(ValueError):
    pass


class AdminNotFound(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Page:
    items: list
    total: int
    offset: int
    limit: int


class AdminService:
    """Transactional identity and role administration.

    All mutating methods assume the caller already opened a database
    transaction and enforced the matching API permission.
    """

    _LAST_ADMIN_LOCK = 0x44435F41444D494E

    def __init__(self, session: AsyncSession, *, actor_user_id: UUID, request_id: str):
        self._session = session
        self._actor_user_id = actor_user_id
        self._request_id = request_id[:128] or "unknown"

    async def list_users(self, *, search: str | None, status: str | None, offset: int, limit: int) -> Page:
        filters = []
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(or_(User.display_name.ilike(pattern), User.telegram_username.ilike(pattern)))
        if status:
            filters.append(User.status == status)
        total = int(await self._session.scalar(select(func.count()).select_from(User).where(*filters)) or 0)
        users = list(await self._session.scalars(
            select(User).where(*filters).order_by(User.created_at.desc(), User.id).offset(offset).limit(limit)
        ))
        return Page(users, total, offset, limit)

    async def roles_for_users(self, user_ids: list[UUID]) -> dict[UUID, list[Role]]:
        result = {user_id: [] for user_id in user_ids}
        if not user_ids:
            return result
        rows = await self._session.execute(
            select(UserRole.user_id, Role).join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(user_ids)).order_by(Role.name)
        )
        for user_id, role in rows:
            result[user_id].append(role)
        return result

    async def identities_for_users(self, user_ids: list[UUID]) -> dict[UUID, AuthIdentity]:
        if not user_ids:
            return {}
        rows = list(await self._session.scalars(
            select(AuthIdentity).where(AuthIdentity.user_id.in_(user_ids), AuthIdentity.provider == "telegram")
        ))
        return {row.user_id: row for row in rows}

    async def list_roles(self) -> list[Role]:
        return list(await self._session.scalars(select(Role).order_by(Role.system.desc(), Role.name, Role.id)))

    async def permissions_for_roles(self, role_ids: list[UUID]) -> dict[UUID, list[str]]:
        result = {role_id: [] for role_id in role_ids}
        if not role_ids:
            return result
        rows = await self._session.execute(
            select(RolePermission.role_id, Permission.code)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(RolePermission.role_id.in_(role_ids)).order_by(Permission.code)
        )
        for role_id, code in rows:
            result[role_id].append(code)
        return result

    async def set_user_status(self, target_user_id: UUID, new_status: str) -> User:
        if new_status not in {"pending", "active", "disabled"}:
            raise ValueError("Invalid user status")
        await self._lock_admin_invariant()
        user = await self._session.scalar(select(User).where(User.id == target_user_id).with_for_update())
        if user is None:
            raise AdminNotFound("User not found")
        if user.status == "active" and new_status != "active" and await self._has_admin_role(user.id):
            await self._ensure_another_active_admin(user.id)
        old_status = user.status
        if old_status == new_status:
            return user
        user.status = new_status
        user.authz_version += 1
        user.version += 1
        if new_status == "active":
            await self._ensure_default_user_role(user.id)
        await self._revoke_user_sessions(user.id)
        self._audit(
            owner_user_id=user.id, action="users.status.update", resource_type="user",
            resource_id=user.id, metadata={"from": old_status, "to": new_status},
        )
        await self._session.flush()
        return user

    async def assign_user_roles(self, target_user_id: UUID, role_ids: set[UUID], actor_permissions: frozenset[str]) -> None:
        await self._lock_admin_invariant()
        user = await self._session.scalar(select(User).where(User.id == target_user_id).with_for_update())
        if user is None:
            raise AdminNotFound("User not found")
        roles = list(await self._session.scalars(select(Role).where(Role.id.in_(role_ids)))) if role_ids else []
        if len(roles) != len(role_ids):
            raise AdminNotFound("One or more roles were not found")
        await self._ensure_actor_may_grant(roles, actor_permissions)
        old_role_ids = set(await self._session.scalars(select(UserRole.role_id).where(UserRole.user_id == user.id)))
        admin_role = await self._admin_role()
        if admin_role.id in old_role_ids and admin_role.id not in role_ids and user.status == "active":
            await self._ensure_another_active_admin(user.id)
        await self._session.execute(delete(UserRole).where(UserRole.user_id == user.id))
        self._session.add_all(
            UserRole(user_id=user.id, role_id=role_id, assigned_by_user_id=self._actor_user_id)
            for role_id in sorted(role_ids, key=str)
        )
        user.authz_version += 1
        user.version += 1
        await self._revoke_user_sessions(user.id)
        self._audit(
            owner_user_id=user.id, action="users.roles.replace", resource_type="user",
            resource_id=user.id, metadata={"role_count": len(role_ids)},
        )
        await self._session.flush()

    async def revoke_user_sessions(self, target_user_id: UUID) -> None:
        user = await self._session.scalar(select(User).where(User.id == target_user_id).with_for_update())
        if user is None:
            raise AdminNotFound("User not found")
        user.authz_version += 1
        user.version += 1
        await self._revoke_user_sessions(user.id)
        self._audit(
            owner_user_id=user.id, action="users.sessions.revoke", resource_type="user",
            resource_id=user.id, metadata={},
        )
        await self._session.flush()

    async def create_role(self, *, key: str, name: str, description: str, permission_codes: set[str], actor_permissions: frozenset[str]) -> Role:
        validate_permission_codes(permission_codes)
        normalized_key = key.strip().lower()
        if not normalized_key or not all(char.isalnum() or char in "-_" for char in normalized_key):
            raise ValueError("Role key must contain only letters, numbers, '-' or '_'")
        if await self._session.scalar(select(Role.id).where(Role.key == normalized_key)):
            raise AdminConflict("Role key already exists")
        role = Role(key=normalized_key, name=name.strip(), description=description.strip(), system=False)
        self._session.add(role)
        await self._session.flush()
        await self._replace_role_permissions(role, permission_codes)
        self._audit(
            owner_user_id=self._actor_user_id, action="roles.create", resource_type="role",
            resource_id=role.id, metadata={"key": role.key, "permission_count": len(permission_codes)},
        )
        await self._session.flush()
        return role

    async def update_role(self, role_id: UUID, *, name: str | None, description: str | None, permission_codes: set[str] | None, actor_permissions: frozenset[str]) -> Role:
        role = await self._session.scalar(select(Role).where(Role.id == role_id).with_for_update())
        if role is None:
            raise AdminNotFound("Role not found")
        if role.key == "admin":
            raise AdminConflict("The administrator role is managed by the permission catalog")
        if role.system:
            if name is not None and name.strip() != role.name:
                raise AdminConflict("System role identity cannot be modified")
            if description is not None and description.strip() != role.description:
                raise AdminConflict("System role identity cannot be modified")
        if permission_codes is not None:
            validate_permission_codes(permission_codes)
            await self._replace_role_permissions(role, permission_codes)
        if name is not None:
            role.name = name.strip()
        if description is not None:
            role.description = description.strip()
        role.version += 1
        affected = list(await self._session.scalars(
            select(User).join(UserRole, UserRole.user_id == User.id)
            .where(UserRole.role_id == role.id).with_for_update()
        ))
        for user in affected:
            user.authz_version += 1
            user.version += 1
            await self._revoke_user_sessions(user.id)
        self._audit(
            owner_user_id=self._actor_user_id, action="roles.update", resource_type="role",
            resource_id=role.id, metadata={"permission_count": len(permission_codes) if permission_codes is not None else None},
        )
        await self._session.flush()
        return role

    async def delete_role(self, role_id: UUID) -> None:
        role = await self._session.scalar(select(Role).where(Role.id == role_id).with_for_update())
        if role is None:
            raise AdminNotFound("Role not found")
        if role.system:
            raise AdminConflict("System roles cannot be deleted")
        assignments = int(await self._session.scalar(select(func.count()).select_from(UserRole).where(UserRole.role_id == role.id)) or 0)
        if assignments:
            raise AdminConflict("Role is assigned to users")
        await self._session.delete(role)
        self._audit(
            owner_user_id=self._actor_user_id, action="roles.delete", resource_type="role",
            resource_id=role.id, metadata={"key": role.key},
        )
        await self._session.flush()

    async def _lock_admin_invariant(self) -> None:
        await self._session.execute(select(func.pg_advisory_xact_lock(self._LAST_ADMIN_LOCK)))

    async def _admin_role(self) -> Role:
        role = await self._session.scalar(select(Role).where(Role.key == "admin"))
        if role is None:
            raise AdminConflict("Admin system role is not seeded")
        return role

    async def _has_admin_role(self, user_id: UUID) -> bool:
        return bool(await self._session.scalar(
            select(UserRole.user_id).join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user_id, Role.key == "admin")
        ))

    async def _ensure_another_active_admin(self, excluded_user_id: UUID) -> None:
        count = int(await self._session.scalar(
            select(func.count()).select_from(UserRole)
            .join(User, User.id == UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.key == "admin", User.status == "active", User.id != excluded_user_id)
        ) or 0)
        if count == 0:
            raise AdminConflict("The last active administrator cannot be removed or disabled")

    async def _ensure_default_user_role(self, user_id: UUID) -> None:
        role = await self._session.scalar(select(Role).where(Role.key == "user"))
        if role is None:
            raise AdminConflict("Default user role is not seeded")
        existing = await self._session.get(UserRole, (user_id, role.id))
        if existing is None:
            self._session.add(UserRole(user_id=user_id, role_id=role.id, assigned_by_user_id=self._actor_user_id))

    async def _ensure_actor_may_grant(self, roles: list[Role], actor_permissions: frozenset[str]) -> None:
        role_ids = [role.id for role in roles]
        if not role_ids:
            return
        codes = set(await self._session.scalars(
            select(Permission.code).join(RolePermission).where(RolePermission.role_id.in_(role_ids))
        ))
        await self._ensure_actor_may_grant_codes(codes, actor_permissions)

    @staticmethod
    async def _ensure_actor_may_grant_codes(codes: set[str], actor_permissions: frozenset[str]) -> None:
        if "roles.manage" in actor_permissions:
            return
        if not codes.issubset(actor_permissions):
            raise AdminConflict("Cannot grant permissions the actor does not hold")

    async def _replace_role_permissions(self, role: Role, codes: set[str]) -> None:
        permissions = list(await self._session.scalars(select(Permission).where(Permission.code.in_(codes)))) if codes else []
        if len(permissions) != len(codes):
            raise ValueError("Permission catalog is not synchronized")
        await self._session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
        self._session.add_all(RolePermission(role_id=role.id, permission_id=item.id) for item in permissions)

    async def _revoke_user_sessions(self, user_id: UUID) -> None:
        await self._session.execute(
            update(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC), version=Session.version + 1)
        )

    def _audit(self, *, owner_user_id: UUID, action: str, resource_type: str, resource_id: UUID | None, metadata: dict) -> None:
        self._session.add(AuditEvent(
            owner_user_id=owner_user_id, actor_user_id=self._actor_user_id,
            action=action, resource_type=resource_type, resource_id=resource_id,
            outcome="success", metadata_allowlist=metadata, request_id=self._request_id,
        ))

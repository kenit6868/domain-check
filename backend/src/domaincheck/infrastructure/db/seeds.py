from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.domain.authz import DEFAULT_USER_PERMISSIONS, PERMISSION_CATALOG
from domaincheck.infrastructure.db.models import Permission, Role, RolePermission, Session, User, UserRole


ADMIN_PERMISSION_CODES = frozenset(item.code for item in PERMISSION_CATALOG)


async def sync_permission_catalog(session: AsyncSession, *, reset_user_permissions: bool = False) -> None:
    """Idempotently synchronize known permissions and protected system roles.

    Unknown DB permissions are not silently deleted because an existing custom
    role may still reference them; they grant no API behavior unless code also
    registers and enforces the permission.
    """

    for definition in PERMISSION_CATALOG:
        statement = insert(Permission).values(
            code=definition.code,
            description=definition.description,
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[Permission.code],
                set_={"description": definition.description},
            )
        )

    user_role_exists = await session.scalar(select(Role.id).where(Role.key == "user")) is not None
    roles: dict[str, Role] = {}
    for key, name, description in (
        ("user", "User", "Default account role; permissions are selected by an administrator."),
        ("admin", "Administrator", "Full application permissions with normal owner isolation."),
    ):
        await session.execute(
            insert(Role)
            .values(key=key, name=name, description=description, system=True)
            .on_conflict_do_update(
                index_elements=[Role.key],
                set_={"name": name, "description": description, "system": True},
            )
        )
        role = await session.scalar(select(Role).where(Role.key == key))
        assert role is not None
        roles[key] = role

    # Admin follows the code-owned catalog and is refreshed on every deploy.
    # Existing user selections survive normal deploy-time syncs.
    admin_role = roles["admin"]
    permission_ids = list(
        await session.scalars(
            select(Permission.id).where(Permission.code.in_(ADMIN_PERMISSION_CODES))
        )
    )
    await session.execute(delete(RolePermission).where(RolePermission.role_id == admin_role.id))
    session.add_all(
        RolePermission(role_id=admin_role.id, permission_id=permission_id)
        for permission_id in permission_ids
    )
    if not user_role_exists or reset_user_permissions:
        user_role = roles["user"]
        await session.execute(delete(RolePermission).where(RolePermission.role_id == user_role.id))
        user_permission_ids = list(await session.scalars(
            select(Permission.id).where(Permission.code.in_(DEFAULT_USER_PERMISSIONS))
        ))
        session.add_all(RolePermission(role_id=user_role.id, permission_id=value) for value in user_permission_ids)
        if reset_user_permissions:
            user_role.version += 1
            members = select(UserRole.user_id).where(UserRole.role_id == user_role.id)
            await session.execute(update(User).where(User.id.in_(members)).values(
                authz_version=User.authz_version + 1, version=User.version + 1,
            ))
            await session.execute(update(Session).where(
                Session.user_id.in_(members), Session.revoked_at.is_(None),
            ).values(revoked_at=datetime.now(UTC), version=Session.version + 1))
    await session.flush()


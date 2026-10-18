from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.domain.authz import DEFAULT_USER_PERMISSIONS, PERMISSION_CATALOG, RETIRED_PERMISSION_CODES
from domaincheck.infrastructure.db.models import Permission, Role, RolePermission, Session, User, UserRole


ADMIN_PERMISSION_CODES = frozenset(item.code for item in PERMISSION_CATALOG)


async def sync_permission_catalog(session: AsyncSession, *, reset_user_permissions: bool = False, grant_dashboard: bool = False) -> None:
    """Idempotently synchronize known permissions and protected system roles.

    Retire only explicitly removed, inert codes. Preserve unknown rows and all
    selections of active permissions on existing roles, including admin/user.
    """

    # API replicas/CLI may seed concurrently on first deployment.
    await session.execute(select(func.pg_advisory_xact_lock(0x52424143)))
    retired_ids = list(await session.scalars(select(Permission.id).where(
        Permission.code.in_(RETIRED_PERMISSION_CODES - ADMIN_PERMISSION_CODES))))
    if retired_ids:
        affected_roles = list(await session.scalars(select(RolePermission.role_id).where(
            RolePermission.permission_id.in_(retired_ids)).distinct()))
        await session.execute(delete(RolePermission).where(RolePermission.permission_id.in_(retired_ids)))
        await session.execute(delete(Permission).where(Permission.id.in_(retired_ids)))
        if affected_roles:
            await session.execute(update(Role).where(Role.id.in_(affected_roles)).values(version=Role.version + 1))
        # Removed codes had no active operation. Auth reads permissions from DB
        # on every request; keep sessions and in-flight worker authz versions.

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

    existing_roles = set(await session.scalars(select(Role.key).where(Role.key.in_(("user", "admin")))))
    user_role_exists = "user" in existing_roles
    roles: dict[str, Role] = {}
    for key, name, description in (
        ("user", "User", "Default account role; permissions are selected by an administrator."),
        ("admin", "Administrator", "Administrator permissions can be edited in role settings."),
    ):
        await session.execute(
            insert(Role)
            .values(key=key, name=name, description=description, system=True)
            .on_conflict_do_update(
                index_elements=[Role.key],
                set_={"system": True},
            )
        )
        role = await session.scalar(select(Role).where(Role.key == key))
        assert role is not None
        roles[key] = role

    # Seed full admin access once. Later syncs retain operator choices for both
    # system roles, including names and descriptions.
    admin_role = roles["admin"]
    permission_ids = list(
        await session.scalars(
            select(Permission.id).where(Permission.code.in_(ADMIN_PERMISSION_CODES))
        )
    )
    if "admin" not in existing_roles:
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
    if grant_dashboard:
        # Explicit first rollout only; ordinary startup must retain later choices.
        for key, codes in [('user', {'dashboard.view'}), ('admin', {'dashboard.view', 'dashboard.view_all'})]:
            ids = list(await session.scalars(select(Permission.id).where(Permission.code.in_(codes))))
            changed = False
            for identity in ids:
                result = await session.execute(insert(RolePermission).values(role_id=roles[key].id,
                    permission_id=identity).on_conflict_do_nothing().returning(RolePermission.permission_id))
                changed = bool(result.scalar_one_or_none()) or changed
            if changed: roles[key].version += 1
    await session.flush()


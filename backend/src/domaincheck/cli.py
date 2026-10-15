from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select

from domaincheck.infrastructure.db.models import AuthIdentity, Permission, Role, RolePermission, User, UserRole
from domaincheck.infrastructure.db.seeds import sync_permission_catalog
from domaincheck.infrastructure.db.session import Database
from domaincheck.settings import get_settings


async def sync_rbac(*, reset_user_permissions: bool = False) -> None:
    """Synchronize the code-owned permission catalog and protected roles."""

    settings = get_settings()
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session, session.begin():
            await sync_permission_catalog(session, reset_user_permissions=reset_user_permissions)
            role_count = int(await session.scalar(select(func.count()).select_from(Role)) or 0)
            permission_count = int(await session.scalar(select(func.count()).select_from(Permission)) or 0)
            binding_count = int(await session.scalar(select(func.count()).select_from(RolePermission)) or 0)
        print(
            "RBAC catalog synchronized: "
            f"{role_count} roles, {permission_count} permissions, {binding_count} bindings"
        )
    finally:
        await database.dispose()


async def bootstrap_admin(subject: str | None) -> None:
    """Promote one existing Telegram subject through an explicit local command."""

    settings = get_settings()
    stable_subject = subject or settings.bootstrap_telegram_subject
    if not stable_subject:
        raise SystemExit(
            "Telegram subject is required via --telegram-subject or "
            "DOMAINCHECK_BOOTSTRAP_TELEGRAM_SUBJECT"
        )
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session, session.begin():
            await sync_permission_catalog(session)
            identity = await session.scalar(
                select(AuthIdentity).where(
                    AuthIdentity.issuer == settings.telegram_issuer,
                    AuthIdentity.subject == stable_subject,
                ).with_for_update()
            )
            if identity is None:
                raise SystemExit("No existing Telegram identity matches that issuer and subject")
            user = await session.scalar(select(User).where(User.id == identity.user_id).with_for_update())
            admin_role = await session.scalar(select(Role).where(Role.key == "admin"))
            user_role = await session.scalar(select(Role).where(Role.key == "user"))
            if user is None or admin_role is None or user_role is None:
                raise SystemExit("Identity or seeded system roles are unavailable")
            user.status = "active"
            user.authz_version += 1
            user.version += 1
            if await session.get(UserRole, (user.id, admin_role.id)) is None:
                session.add(UserRole(user_id=user.id, role_id=admin_role.id, assigned_by_user_id=user.id))
            # A bootstrap administrator already receives the entire catalog via
            # the admin role; remove the default user role if an earlier version
            # assigned both roles.
            default_assignment = await session.get(UserRole, (user.id, user_role.id))
            if default_assignment is not None:
                await session.delete(default_assignment)
            print(f"Administrator bootstrap completed for user id {user.id}")
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="domaincheck-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync = subparsers.add_parser("sync-rbac")
    sync.add_argument("--reset-user-permissions", action="store_true", help="Reset user role to all non-administration permissions")
    bootstrap = subparsers.add_parser("bootstrap-admin")
    bootstrap.add_argument("--telegram-subject", help="Stable OIDC subject, never a Telegram username")
    arguments = parser.parse_args()
    if arguments.command == "sync-rbac":
        asyncio.run(sync_rbac(reset_user_permissions=arguments.reset_user_permissions))
    elif arguments.command == "bootstrap-admin":
        asyncio.run(bootstrap_admin(arguments.telegram_subject))


if __name__ == "__main__":
    main()

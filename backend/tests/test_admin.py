from __future__ import annotations

import asyncio
import os
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select

from domaincheck.api.dependencies import require_permission
from domaincheck.application.admin import AdminConflict, AdminService
from domaincheck.application.auth import AuthenticatedUser
from domaincheck.infrastructure.db.models import AuditEvent, Permission, Role, RolePermission, User, UserRole
from domaincheck.infrastructure.db.seeds import ADMIN_PERMISSION_CODES, sync_permission_catalog
from domaincheck.infrastructure.db.session import Database


TEST_DATABASE_URL = os.environ.get("DOMAINCHECK_TEST_DATABASE_URL")


class PermissionDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_permission_dependency_defaults_to_deny(self):
        dependency = require_permission("users.view")
        user = AuthenticatedUser(
            user_id=uuid4(), display_name="Fixture", telegram_username=None,
            status="active", timezone="Asia/Bangkok", permissions=frozenset(),
            session_id=uuid4(), csrf_hash=b"x" * 32,
        )
        with self.assertRaises(HTTPException) as raised:
            await dependency(user)
        self.assertEqual(raised.exception.status_code, 403)


@unittest.skipUnless(TEST_DATABASE_URL, "requires isolated PostgreSQL test database")
class AdminDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = Database(TEST_DATABASE_URL or "")

    async def asyncTearDown(self):
        await self.database.dispose()

    async def _seed_admin(self, session, name: str) -> tuple[User, Role]:
        await sync_permission_catalog(session)
        admin_role = await session.scalar(select(Role).where(Role.key == "admin"))
        assert admin_role is not None
        user = User(display_name=name, status="active", timezone="Asia/Bangkok")
        session.add(user)
        await session.flush()
        session.add(UserRole(user_id=user.id, role_id=admin_role.id, assigned_by_user_id=user.id))
        await session.flush()
        return user, admin_role

    async def test_activation_assigns_default_role_and_writes_audit(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            actor, _ = await self._seed_admin(session, f"actor-{uuid4()}")
            target = User(display_name="Pending", status="pending", timezone="Asia/Bangkok")
            session.add(target)
            await session.flush()
            await AdminService(session, actor_user_id=actor.id, request_id="req-activate").set_user_status(
                target.id, "active"
            )
            role_keys = set(await session.scalars(
                select(Role.key).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == target.id)
            ))
            audit = await session.scalar(select(AuditEvent).where(AuditEvent.request_id == "req-activate"))
            self.assertEqual(target.status, "active")
            self.assertIn("user", role_keys)
            user_permission_count = int(await session.scalar(
                select(func.count()).select_from(RolePermission)
                .join(Role, Role.id == RolePermission.role_id).where(Role.key == "user")
            ) or 0)
            from domaincheck.domain.authz import DEFAULT_USER_PERMISSIONS
            self.assertEqual(user_permission_count, len(DEFAULT_USER_PERMISSIONS))
            self.assertEqual(audit.metadata_allowlist, {"from": "pending", "to": "active"})
            await transaction.rollback()

    async def test_user_and_admin_permissions_and_labels_are_editable(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            actor, admin_role = await self._seed_admin(session, f"role-admin-{uuid4()}")
            user_role = await session.scalar(select(Role).where(Role.key == "user"))
            assert user_role is not None
            service = AdminService(session, actor_user_id=actor.id, request_id="req-user-role")
            await service.update_role(
                user_role.id,
                name=user_role.name,
                description=user_role.description,
                permission_codes={"quick_report.view", "quick_report.check"},
                actor_permissions=ADMIN_PERMISSION_CODES,
            )
            codes = set(await session.scalars(
                select(Permission.code).join(RolePermission).where(RolePermission.role_id == user_role.id)
            ))
            self.assertEqual(codes, {"quick_report.view", "quick_report.check"})
            target = User(display_name="Role assignment target", status="active", timezone="Asia/Bangkok")
            session.add(target)
            await session.flush()
            with self.assertRaises(AdminConflict):
                await service.assign_user_roles(
                    target.id, {user_role.id}, frozenset({"users.assign_roles"})
                )
            await service.update_role(admin_role.id, name="Custom administrator", description="Selected access",
                permission_codes={"roles.view", "roles.manage", "domain_worker.preview"},
                actor_permissions=ADMIN_PERMISSION_CODES)
            codes = await service.permissions_for_roles([admin_role.id])
            self.assertEqual(set(codes[admin_role.id]), {"roles.view", "roles.manage", "domain_worker.preview"})
            await sync_permission_catalog(session)
            codes = await service.permissions_for_roles([admin_role.id])
            self.assertEqual(set(codes[admin_role.id]), {"roles.view", "roles.manage", "domain_worker.preview"})
            self.assertEqual(admin_role.name, "Custom administrator")
            self.assertEqual(admin_role.description, "Selected access")
            await transaction.rollback()

    async def test_role_save_materializes_a_new_code_missing_from_database_catalog(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            actor, _ = await self._seed_admin(session, f"catalog-admin-{uuid4()}")
            permission = await session.scalar(select(Permission).where(Permission.code == 'domain_worker.preview'))
            await session.execute(delete(RolePermission).where(RolePermission.permission_id == permission.id))
            await session.delete(permission); await session.flush()
            role = await session.scalar(select(Role).where(Role.key == 'user'))
            service = AdminService(session, actor_user_id=actor.id, request_id='catalog-repair')
            await service.update_role(role.id, name=None, description=None,
                permission_codes={'domain_worker.preview'}, actor_permissions=ADMIN_PERMISSION_CODES)
            self.assertEqual((await service.permissions_for_roles([role.id]))[role.id], ['domain_worker.preview'])
            self.assertIsNotNone(await session.scalar(select(Permission.id).where(Permission.code == 'domain_worker.preview')))
            with self.assertRaises(ValueError):
                await service.update_role(role.id, name=None, description=None,
                    permission_codes={'unregistered.action'}, actor_permissions=ADMIN_PERMISSION_CODES)
            await transaction.rollback()

    async def test_last_active_admin_cannot_be_disabled_or_unassigned(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            actor, _ = await self._seed_admin(session, f"last-admin-{uuid4()}")
            service = AdminService(session, actor_user_id=actor.id, request_id="req-last-admin")
            with self.assertRaises(AdminConflict):
                await service.set_user_status(actor.id, "disabled")
            with self.assertRaises(AdminConflict):
                await service.assign_user_roles(actor.id, set(), ADMIN_PERMISSION_CODES)
            await transaction.rollback()

    async def test_assignment_cannot_grant_permissions_actor_does_not_hold(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            actor, _ = await self._seed_admin(session, f"limited-admin-{uuid4()}")
            target = User(display_name="Target", status="active", timezone="Asia/Bangkok")
            session.add(target)
            await session.flush()
            service = AdminService(session, actor_user_id=actor.id, request_id="req-escalation")
            dangerous = await service.create_role(
                key=f"sender-{uuid4()}", name="Sender", description="fixture",
                permission_codes={"check_domain.send"}, actor_permissions=ADMIN_PERMISSION_CODES,
            )
            with self.assertRaises(AdminConflict):
                await service.assign_user_roles(
                    target.id, {dangerous.id}, frozenset({"users.assign_roles"})
                )
            await transaction.rollback()

    async def test_two_concurrent_admin_removals_leave_one_active_admin(self):
        async with self.database.session_factory() as session, session.begin():
            first, admin_role = await self._seed_admin(session, f"concurrent-a-{uuid4()}")
            second = User(display_name=f"concurrent-b-{uuid4()}", status="active", timezone="Asia/Bangkok")
            session.add(second)
            await session.flush()
            session.add(UserRole(user_id=second.id, role_id=admin_role.id, assigned_by_user_id=first.id))
            first_id, second_id = first.id, second.id

        async def disable(actor_id, target_id):
            try:
                async with self.database.session_factory() as session, session.begin():
                    await AdminService(session, actor_user_id=actor_id, request_id=str(uuid4())).set_user_status(
                        target_id, "disabled"
                    )
                return "disabled"
            except AdminConflict:
                return "protected"

        outcomes = await asyncio.gather(disable(first_id, second_id), disable(second_id, first_id))
        self.assertCountEqual(outcomes, ["disabled", "protected"])
        async with self.database.session_factory() as session:
            active_admins = int(await session.scalar(
                select(func.count()).select_from(UserRole)
                .join(User, User.id == UserRole.user_id).join(Role, Role.id == UserRole.role_id)
                .where(Role.key == "admin", User.status == "active")
            ) or 0)
            self.assertGreaterEqual(active_admins, 1)


if __name__ == "__main__":
    unittest.main()

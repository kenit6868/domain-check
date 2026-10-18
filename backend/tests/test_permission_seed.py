"""Current permission catalog, retirement and deployment seed regressions."""
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest
from uuid import uuid4

from sqlalchemy import delete, select

from domaincheck.domain.authz import PERMISSIONS_BY_CODE, RETIRED_PERMISSION_CODES, validate_permission_codes
from domaincheck.infrastructure.db.models import Permission, Role, RolePermission, User, UserRole
from domaincheck.infrastructure.db.seeds import sync_permission_catalog
from domaincheck.infrastructure.db.session import Database


class CatalogTests(unittest.TestCase):
    def test_retired_codes_are_not_available_for_new_grants(self):
        self.assertTrue(RETIRED_PERMISSION_CODES.isdisjoint(PERMISSIONS_BY_CODE))
        for code in RETIRED_PERMISSION_CODES:
            with self.subTest(code=code), self.assertRaises(ValueError):
                validate_permission_codes({code})


class StartupSeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_seeds_before_traffic_and_disposes_on_failure(self):
        from domaincheck.main import create_app
        from domaincheck.settings import Settings

        @asynccontextmanager
        async def transaction(): yield

        session = SimpleNamespace(begin=transaction)

        @asynccontextmanager
        async def factory(): yield session

        for failure in (None, RuntimeError('seed failure')):
            app = create_app(Settings(_env_file=None))
            # Dispose unused real clients before substituting the test doubles.
            await app.state.database.dispose(); await app.state.redis.aclose()
            db = SimpleNamespace(session_factory=factory, dispose=AsyncMock())
            redis = SimpleNamespace(aclose=AsyncMock())
            app.state.database, app.state.redis = db, redis
            with patch('domaincheck.main.sync_permission_catalog', new_callable=AsyncMock) as seed:
                seed.side_effect = failure
                if failure:
                    with self.assertRaises(RuntimeError):
                        async with app.router.lifespan_context(app):
                            self.fail('A failed seed must not serve traffic')
                else:
                    async with app.router.lifespan_context(app):
                        seed.assert_awaited_once_with(session)
                        db.dispose.assert_not_awaited()
                db.dispose.assert_awaited_once(); redis.aclose.assert_awaited_once()


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class PermissionSeedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(os.environ['DOMAINCHECK_TEST_DATABASE_URL'])

    async def asyncTearDown(self):
        await self.db.dispose()

    async def test_retirement_preserves_active_choices_custom_rows_and_worker_versions(self):
        async with self.db.session_factory() as session, session.begin() as transaction:
            await sync_permission_catalog(session)
            roles = list(await session.scalars(select(Role).where(Role.key.in_(['admin', 'user']))))
            custom = Role(key=f'cleanup-{uuid4()}', name='Selected role', description='Keep choices', system=False)
            member = User(display_name='Retirement fixture', status='active', timezone='Asia/Bangkok')
            unknown = Permission(code=f'future_{uuid4().hex}.view', description='Other release')
            retired = [Permission(code=code, description='Obsolete') for code in RETIRED_PERMISSION_CODES]
            session.add_all([custom, member, unknown, *retired]); await session.flush()
            roles.append(custom)
            active = await session.scalar(select(Permission).where(Permission.code == 'quick_report.view'))
            session.add_all([RolePermission(role_id=custom.id, permission_id=active.id),
                             RolePermission(role_id=custom.id, permission_id=unknown.id),
                             UserRole(user_id=member.id, role_id=custom.id, assigned_by_user_id=member.id)])
            for role in roles:
                session.add_all(RolePermission(role_id=role.id, permission_id=value.id) for value in retired)
            await session.flush()
            versions = {role.id: role.version for role in roles}
            authz = member.authz_version

            async def bindings():
                return set(await session.execute(select(RolePermission.role_id, Permission.code)
                    .join(Permission, Permission.id == RolePermission.permission_id)
                    .where(Permission.code.not_in(RETIRED_PERMISSION_CODES))))

            before = await bindings()
            await sync_permission_catalog(session)
            self.assertEqual(await bindings(), before)
            self.assertFalse(list(await session.scalars(select(Permission.code).where(Permission.code.in_(RETIRED_PERMISSION_CODES)))))
            self.assertIsNotNone(await session.get(Permission, unknown.id))
            for role in roles:
                await session.refresh(role)
                self.assertEqual(role.version, versions[role.id] + 1)
            await session.refresh(member)
            self.assertEqual(member.authz_version, authz)
            await sync_permission_catalog(session)
            self.assertEqual(await bindings(), before)
            for role in roles:
                await session.refresh(role)
                self.assertEqual(role.version, versions[role.id] + 1)
            await transaction.rollback()

    async def test_sync_materializes_new_catalog_entry_without_granting_existing_roles(self):
        async with self.db.session_factory() as session, session.begin() as transaction:
            await sync_permission_catalog(session)
            permission = await session.scalar(select(Permission).where(Permission.code == 'domain_worker.preview'))
            await session.execute(delete(RolePermission).where(RolePermission.permission_id == permission.id))
            await session.delete(permission); await session.flush()
            before = set(await session.execute(select(RolePermission.role_id, RolePermission.permission_id)))
            await sync_permission_catalog(session)
            self.assertIsNotNone(await session.scalar(select(Permission.id).where(Permission.code == 'domain_worker.preview')))
            self.assertEqual(set(await session.execute(select(RolePermission.role_id, RolePermission.permission_id))), before)
            await transaction.rollback()

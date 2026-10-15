from __future__ import annotations

import os
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from domaincheck.api.artifacts import download_artifact
from domaincheck.application.auth import AuthenticationError, AuthService
from domaincheck.application.auth import AuthenticatedUser
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.auth.sessions import hash_token
from domaincheck.infrastructure.db.models import Artifact, Check, Permission, Role, RolePermission, User, UserRole
from domaincheck.infrastructure.db.repositories import ArtifactRepository, TargetRepository
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.infrastructure.db.seeds import ADMIN_PERMISSION_CODES, sync_permission_catalog
from domaincheck.infrastructure.db.session import Database
from domaincheck.ports.artifacts import ImageUpload


TEST_DATABASE_URL = os.environ.get("DOMAINCHECK_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "requires isolated PostgreSQL test database")
class DatabaseIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = Database(TEST_DATABASE_URL or "")

    async def asyncTearDown(self):
        await self.database.dispose()

    async def test_target_repository_never_returns_another_owner_target(self):
        async with self.database.session_factory() as session, session.begin():
            owner_a = User(display_name="A", status="active", timezone="Asia/Bangkok")
            owner_b = User(display_name="B", status="active", timezone="Asia/Bangkok")
            session.add_all([owner_a, owner_b])
            await session.flush()
            target = await TargetRepository(session, owner_a.id).create(
                full_url_identity="https://example.test/login", hostname="example.test"
            )
            self.assertIsNotNone(await TargetRepository(session, owner_a.id).get(target.id))
            self.assertIsNone(await TargetRepository(session, owner_b.id).get(target.id))
            await session.rollback()

    async def test_owned_repository_scopes_list_count_update_and_delete(self):
        async with self.database.session_factory() as session, session.begin():
            owner_a = User(display_name="A", status="active", timezone="Asia/Bangkok")
            owner_b = User(display_name="B", status="active", timezone="Asia/Bangkok")
            session.add_all([owner_a, owner_b])
            await session.flush()
            same_url = "https://same.example.test/login?flow=1"
            target_a = await TargetRepository(session, owner_a.id).create(
                full_url_identity=same_url, hostname="same.example.test"
            )
            target_b = await TargetRepository(session, owner_b.id).create(
                full_url_identity=same_url, hostname="same.example.test"
            )
            owned_a = OwnedRepository(session, type(target_a), owner_a.id)
            owned_b = OwnedRepository(session, type(target_b), owner_b.id)
            self.assertEqual(await owned_a.count(), 1)
            self.assertEqual([item.id for item in await owned_a.list()], [target_a.id])
            self.assertIsNone(await owned_a.get(target_b.id))
            self.assertFalse(await owned_a.update(target_b.id, {"hostname": "leak.test"}))
            self.assertFalse(await owned_a.delete(target_b.id))
            self.assertTrue(await owned_a.update(
                target_a.id, {"hostname": "updated.example.test"}, expected_version=1
            ))
            self.assertFalse(await owned_a.update(
                target_a.id, {"hostname": "stale.example.test"}, expected_version=1
            ))
            await session.refresh(target_a)
            await session.refresh(target_b)
            self.assertEqual(target_a.hostname, "updated.example.test")
            self.assertEqual(target_b.hostname, "same.example.test")
            with self.assertRaises(ValueError):
                await owned_a.update(target_a.id, {"owner_user_id": owner_b.id})
            await session.rollback()

    async def test_owner_scope_survives_reuse_of_the_same_database_connection(self):
        database = Database(TEST_DATABASE_URL or "", pool_size=1, max_overflow=0)
        try:
            async with database.session_factory() as session, session.begin():
                owner_a = User(display_name="Pool A", status="active", timezone="Asia/Bangkok")
                owner_b = User(display_name="Pool B", status="active", timezone="Asia/Bangkok")
                session.add_all([owner_a, owner_b])
                await session.flush()
                target_a = await TargetRepository(session, owner_a.id).create(
                    full_url_identity="https://pool.example.test/a", hostname="pool.example.test"
                )
                target_b = await TargetRepository(session, owner_b.id).create(
                    full_url_identity="https://pool.example.test/b", hostname="pool.example.test"
                )
                owner_a_id, owner_b_id = owner_a.id, owner_b.id
                target_a_id, target_b_id = target_a.id, target_b.id

            async with database.session_factory() as session:
                pid_a = await session.scalar(select(func.pg_backend_pid()))
                self.assertIsNotNone(await TargetRepository(session, owner_a_id).get(target_a_id))
                self.assertIsNone(await TargetRepository(session, owner_a_id).get(target_b_id))
            async with database.session_factory() as session:
                pid_b = await session.scalar(select(func.pg_backend_pid()))
                self.assertEqual(pid_a, pid_b)
                self.assertIsNotNone(await TargetRepository(session, owner_b_id).get(target_b_id))
                self.assertIsNone(await TargetRepository(session, owner_b_id).get(target_a_id))

            async with database.session_factory() as session, session.begin():
                from sqlalchemy import delete
                from domaincheck.infrastructure.db.models import Target
                await session.execute(delete(Target).where(Target.id.in_([target_a_id, target_b_id])))
                await session.execute(delete(User).where(User.id.in_([owner_a_id, owner_b_id])))
        finally:
            await database.dispose()

    async def test_artifact_repository_never_returns_another_owner_artifact(self):
        async with self.database.session_factory() as session, session.begin():
            owner_a = User(display_name="A", status="active", timezone="Asia/Bangkok")
            owner_b = User(display_name="B", status="active", timezone="Asia/Bangkok")
            session.add_all([owner_a, owner_b])
            await session.flush()
            artifact = Artifact(
                owner_user_id=owner_a.id,
                evidence_set_id=None,
                kind="check_domain:evidence",
                mime_type="image/png",
                byte_size=8,
                sha256="0" * 64,
                storage_key=f"users/{owner_a.id}/fixture/{uuid4()}.png",
                original_name="fixture.png",
                immutable=True,
            )
            session.add(artifact)
            await session.flush()
            self.assertIsNotNone(await ArtifactRepository(session, owner_a.id).get(artifact.id))
            self.assertIsNone(await ArtifactRepository(session, owner_b.id).get(artifact.id))
            await session.rollback()

    async def test_artifact_download_enforces_owner_permission_and_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FilesystemArtifactStore(Path(directory))
            image = io.BytesIO()
            Image.new("RGB", (2, 2), color=(1, 2, 3)).save(image, format="PNG")
            async with self.database.session_factory() as session, session.begin():
                owner_a = User(display_name="A", status="active", timezone="Asia/Bangkok")
                owner_b = User(display_name="B", status="active", timezone="Asia/Bangkok")
                session.add_all([owner_a, owner_b])
                await session.flush()
                stored = store.store_evidence_images(
                    owner_user_id=owner_a.id,
                    evidence_set_id=uuid4(),
                    uploads=[ImageUpload("fixture.png", "image/png", image.getvalue())],
                )[0]
                artifact = Artifact(
                    id=stored.artifact_id,
                    owner_user_id=owner_a.id,
                    evidence_set_id=None,
                    kind="check_domain:evidence",
                    mime_type=stored.mime_type,
                    byte_size=stored.byte_size,
                    sha256=stored.sha256,
                    storage_key=stored.storage_key,
                    original_name=stored.original_name,
                    immutable=True,
                )
                session.add(artifact)
                await session.flush()
                request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(artifact_store=store)))

                def actor(user_id, permissions):
                    return AuthenticatedUser(
                        user_id=user_id, display_name="Fixture", telegram_username=None,
                        status="active", timezone="Asia/Bangkok", permissions=frozenset(permissions),
                        session_id=uuid4(), csrf_hash=b"x" * 32,
                    )

                response = await download_artifact(
                    artifact.id, request, actor(owner_a.id, {"check_domain.view"}), session
                )
                self.assertEqual(response.body, image.getvalue())
                self.assertEqual(response.headers["cache-control"], "private, no-store")
                with self.assertRaises(HTTPException) as denied:
                    await download_artifact(
                        artifact.id, request, actor(owner_a.id, {"quick_report.view"}), session
                    )
                self.assertEqual(denied.exception.status_code, 403)
                with self.assertRaises(HTTPException) as hidden:
                    await download_artifact(
                        artifact.id, request, actor(owner_b.id, {"check_domain.view"}), session
                    )
                self.assertEqual(hidden.exception.status_code, 404)
                await session.rollback()

    async def test_composite_foreign_key_rejects_cross_owner_check_target(self):
        async with self.database.session_factory() as session:
            async with session.begin():
                owner_a = User(display_name="A", status="active", timezone="Asia/Bangkok")
                owner_b = User(display_name="B", status="active", timezone="Asia/Bangkok")
                session.add_all([owner_a, owner_b])
                await session.flush()
                target = await TargetRepository(session, owner_a.id).create(
                    full_url_identity="https://example.test/login", hostname="example.test"
                )
                cross_owner = Check(
                    owner_user_id=owner_b.id,
                    actor_user_id=owner_b.id,
                    target_id=target.id,
                    requested_url=target.full_url_identity,
                    workflow="quick_report",
                    workflow_key="fixture",
                    domain="example.test",
                    business_day=date(2026, 10, 15),
                    timezone="Asia/Bangkok",
                    engine_version="fixture",
                    status="pending",
                    result={},
                    cache_provenance={},
                )
                session.add(cross_owner)
                with self.assertRaises(IntegrityError):
                    await session.flush()

    async def test_oidc_login_creates_pending_user_and_persists_only_token_hash(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            login = await AuthService(session).login_oidc_identity(
                issuer="https://issuer.test", subject=f"subject-{uuid4()}",
                display_name="Fixture User", telegram_username="fixture",
                timezone="Asia/Bangkok",
            )
            self.assertEqual(login.user.status, "pending")
            authenticated = await AuthService(session).authenticate(login.token)
            self.assertEqual(authenticated.user_id, login.user.id)
            self.assertEqual(authenticated.permissions, frozenset())
            from domaincheck.infrastructure.db.models import Session
            persisted = await session.get(Session, authenticated.session_id)
            self.assertEqual(persisted.token_hash, hash_token(login.token))
            self.assertNotIn(login.token.encode(), persisted.token_hash)
            await transaction.rollback()

    async def test_authz_version_invalidates_existing_session(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            login = await AuthService(session).login_oidc_identity(
                issuer="https://issuer.test", subject=f"subject-{uuid4()}",
                display_name="Active User", telegram_username=None,
                timezone="Asia/Bangkok",
            )
            login.user.status = "active"
            permission = Permission(code=f"fixture.view.{uuid4()}", description="fixture")
            role = Role(key=f"fixture-{uuid4()}", name="Fixture", description="", system=False)
            session.add_all([permission, role])
            await session.flush()
            session.add_all([
                RolePermission(role_id=role.id, permission_id=permission.id),
                UserRole(user_id=login.user.id, role_id=role.id, assigned_by_user_id=login.user.id),
            ])
            await session.flush()
            authenticated = await AuthService(session).authenticate(login.token)
            self.assertEqual(authenticated.permissions, frozenset({permission.code}))
            login.user.authz_version += 1
            await session.flush()
            with self.assertRaises(AuthenticationError):
                await AuthService(session).authenticate(login.token)
            await transaction.rollback()

    async def test_permission_seed_makes_admin_full_and_preserves_user_selection(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            await sync_permission_catalog(session)
            user_role = await session.scalar(select(Role).where(Role.key == "user"))
            admin_role = await session.scalar(select(Role).where(Role.key == "admin"))
            selected_permission = await session.scalar(
                select(Permission).where(Permission.code == "quick_report.view")
            )
            assert user_role is not None and admin_role is not None and selected_permission is not None
            await session.execute(delete(RolePermission).where(RolePermission.role_id == user_role.id))
            session.add(RolePermission(role_id=user_role.id, permission_id=selected_permission.id))
            await session.flush()
            await sync_permission_catalog(session)
            from domaincheck.domain.authz import PERMISSION_CATALOG
            count = await session.scalar(select(func.count()).select_from(Permission))
            admin_codes = set(await session.scalars(
                select(Permission.code).join(RolePermission).where(RolePermission.role_id == admin_role.id)
            ))
            user_codes = set(await session.scalars(
                select(Permission.code).join(RolePermission).where(RolePermission.role_id == user_role.id)
            ))
            self.assertGreaterEqual(count, len(PERMISSION_CATALOG))
            self.assertEqual(ADMIN_PERMISSION_CODES, {item.code for item in PERMISSION_CATALOG})
            self.assertEqual(admin_codes, ADMIN_PERMISSION_CODES)
            self.assertEqual(user_codes, {"quick_report.view"})
            from domaincheck.domain.authz import DEFAULT_USER_PERMISSIONS
            await sync_permission_catalog(session, reset_user_permissions=True)
            await sync_permission_catalog(session, reset_user_permissions=True)
            reset_codes = set(await session.scalars(
                select(Permission.code).join(RolePermission).where(RolePermission.role_id == user_role.id)
            ))
            self.assertEqual(reset_codes, DEFAULT_USER_PERMISSIONS)
            self.assertEqual(reset_codes, ADMIN_PERMISSION_CODES - {
                "users.view", "users.manage", "users.assign_roles", "roles.view", "roles.manage",
            })
            await transaction.rollback()


from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.infrastructure.auth.sessions import generate_session_token, hash_token
from domaincheck.application.audit_log import record
from domaincheck.infrastructure.db.models import (
    AuthIdentity,
    Permission,
    Role,
    RolePermission,
    Session,
    User,
    UserRole,
)


class AuthenticationError(ValueError):
    def __init__(self, message, *, reason_code='authentication_failed', user_id=None, session_id=None):
        super().__init__(message)
        self.reason_code, self.user_id, self.session_id = reason_code, user_id, session_id


@dataclass(frozen=True, slots=True, repr=False)
class LoginSession:
    token: str
    csrf_token: str
    user: User
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: UUID
    display_name: str
    telegram_username: str | None
    status: str
    timezone: str
    permissions: frozenset[str]
    session_id: UUID
    csrf_hash: bytes
    roles: tuple[str, ...] = ()


class AuthService:
    def __init__(self, session: AsyncSession, request_id='auth'):
        self._session = session
        self._request_id = request_id

    @staticmethod
    def _advisory_key(issuer: str, subject: str) -> int:
        raw = hashlib.sha256(f"{issuer}\0{subject}".encode()).digest()[:8]
        return int.from_bytes(raw, signed=True)

    async def login_oidc_identity(
        self,
        *,
        issuer: str,
        subject: str,
        display_name: str,
        telegram_username: str | None,
        timezone: str,
        session_lifetime: timedelta = timedelta(days=7),
    ) -> LoginSession:
        if not issuer or not subject:
            raise AuthenticationError("OIDC identity is incomplete")
        await self._session.execute(
            select(func.pg_advisory_xact_lock(self._advisory_key(issuer, subject)))
        )
        identity = await self._session.scalar(
            select(AuthIdentity).where(
                AuthIdentity.issuer == issuer,
                AuthIdentity.subject == subject,
            )
        )
        if identity is None:
            user = User(
                display_name=display_name or "Telegram user",
                telegram_username=telegram_username,
                status="pending",
                timezone=timezone,
            )
            self._session.add(user)
            await self._session.flush()
            identity = AuthIdentity(
                user_id=user.id,
                provider="telegram",
                issuer=issuer,
                subject=subject,
            )
            self._session.add(identity)
        else:
            user = await self._session.get(User, identity.user_id)
            if user is None:
                raise AuthenticationError("OIDC identity has no user")
            user.display_name = display_name or user.display_name
            user.telegram_username = telegram_username
        if user.status == "disabled":
            raise AuthenticationError("User is disabled", reason_code='account_disabled', user_id=user.id)
        now = datetime.now(UTC)
        user.last_login_at = now
        raw_token = generate_session_token()
        raw_csrf = generate_session_token()
        expires_at = now + session_lifetime
        session = Session(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            csrf_hash=hash_token(raw_csrf),
            expires_at=expires_at,
            authz_version=user.authz_version,
        )
        self._session.add(session)
        await self._session.flush()
        await record(self._session, owner=user.id, actor=user.id, action='auth.login_success',
            resource_type='session', resource_id=session.id, request_id=self._request_id,
            metadata={'provider': 'telegram', 'state': user.status}, event_key=f'login:{session.id}')
        return LoginSession(raw_token, raw_csrf, user, expires_at)

    async def authenticate(self, raw_token: str) -> AuthenticatedUser:
        observed = datetime.now(UTC)
        row = (
            await self._session.execute(
                select(Session, User)
                .join(User, User.id == Session.user_id)
                .where(
                    Session.token_hash == hash_token(raw_token),
                )
            )
        ).one_or_none()
        if row is None:
            raise AuthenticationError("Session is invalid or expired")
        session, user = row
        if session.revoked_at is not None or user.status == "disabled" or session.authz_version != user.authz_version:
            raise AuthenticationError("Session authorization is stale", reason_code='session_revoked', user_id=user.id, session_id=session.id)
        if session.expires_at <= observed:
            raise AuthenticationError("Session is invalid or expired", reason_code='session_expired', user_id=user.id, session_id=session.id)
        permissions: frozenset[str]
        if user.status != "active":
            permissions = frozenset()
        else:
            codes = await self._session.scalars(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .join(UserRole, UserRole.role_id == RolePermission.role_id)
                .where(UserRole.user_id == user.id)
            )
            permissions = frozenset(codes)
        roles = tuple(await self._session.scalars(
            select(Role.name).join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id).order_by(Role.key, Role.id)
        ))
        return AuthenticatedUser(
            user_id=user.id,
            display_name=user.display_name,
            telegram_username=user.telegram_username,
            status=user.status,
            timezone=user.timezone,
            permissions=permissions,
            session_id=session.id,
            csrf_hash=session.csrf_hash,
            roles=roles,
        )

    async def revoke(self, session_id: UUID) -> None:
        session = await self._session.get(Session, session_id)
        if session is not None and session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            session.version += 1
            await record(self._session, owner=session.user_id, actor=session.user_id, action='auth.logout',
                resource_type='session', resource_id=session.id, request_id=self._request_id,
                event_key=f'session-terminal:{session.id}')
            await self._session.flush()


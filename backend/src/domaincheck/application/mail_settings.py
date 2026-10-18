from __future__ import annotations

from dataclasses import asdict, dataclass
from uuid import UUID

import anyio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.core.mail import (
    ConnectionTestResult,
    ImapConnectionSpec,
    MailConnectionTester,
    ProxySpec,
    SmtpConnectionSpec,
)
from domaincheck.infrastructure.db.models import AuditEvent, SenderAccount, UserSettings
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.infrastructure.secrets import EncryptedValue, SecretEnvelope


class MailSettingsNotFound(ValueError):
    pass


class MailSettingsConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MailAccountValues:
    label: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_transport: str
    enabled: bool
    is_default: bool
    imap_config: dict
    proxy_config: dict
    signature_overrides: dict


class MailSettingsService:
    _DEFAULT_LOCK_NAMESPACE = 0x44435F4D41494C00

    def __init__(
        self, session: AsyncSession, *, owner_user_id: UUID, actor_user_id: UUID,
        request_id: str, envelope: SecretEnvelope | None,
    ):
        self._session = session
        self._owner_user_id = owner_user_id
        self._actor_user_id = actor_user_id
        self._request_id = request_id[:128] or "unknown"
        self._envelope = envelope

    async def list_accounts(self) -> list[SenderAccount]:
        return await OwnedRepository(self._session, SenderAccount, self._owner_user_id).list(
            order_by=(SenderAccount.is_default.desc(), SenderAccount.created_at, SenderAccount.id)
        )

    async def get_account(self, account_id: UUID, *, for_update: bool = False) -> SenderAccount:
        account = await OwnedRepository(
            self._session, SenderAccount, self._owner_user_id
        ).get(account_id, for_update=for_update)
        if account is None:
            raise MailSettingsNotFound("Mail account not found")
        return account

    async def create_account(self, values: MailAccountValues, secret_updates: dict[str, str | None]) -> SenderAccount:
        account = SenderAccount(
            owner_user_id=self._owner_user_id,
            normalized_identity=self._identity(values.smtp_host, values.smtp_port, values.smtp_username),
            **asdict(values),
        )
        if values.is_default:
            await self._clear_default_accounts()
        self._session.add(account)
        await self._session.flush()
        if any(value is not None for value in secret_updates.values()):
            self._store_secrets(account, {key: value for key, value in secret_updates.items() if value is not None})
        self._audit("mail_accounts.create", account.id, {"transport": account.smtp_transport})
        await self._session.flush()
        return account

    async def update_account(
        self, account_id: UUID, values: MailAccountValues, secret_updates: dict[str, str | None],
        changed_secret_fields: set[str],
    ) -> SenderAccount:
        account = await self.get_account(account_id, for_update=True)
        if values.is_default and not account.is_default:
            await self._clear_default_accounts(except_id=account.id)
        for field, value in asdict(values).items():
            setattr(account, field, value)
        account.normalized_identity = self._identity(values.smtp_host, values.smtp_port, values.smtp_username)
        if changed_secret_fields:
            secrets = self._read_secrets(account)
            for field in changed_secret_fields:
                value = secret_updates[field]
                if value is None:
                    secrets.pop(field, None)
                else:
                    secrets[field] = value
            self._store_secrets(account, secrets)
        account.version += 1
        self._audit("mail_accounts.update", account.id, {"transport": account.smtp_transport,
            'fields': sorted(set(asdict(values)) | changed_secret_fields)})
        await self._session.flush()
        return account

    async def disable_account(self, account_id: UUID) -> None:
        account = await self.get_account(account_id, for_update=True)
        account.enabled = False
        account.is_default = False
        account.version += 1
        self._audit("mail_accounts.disable", account.id, {})
        await self._session.flush()

    async def test_smtp_connection(
        self, account_id: UUID, tester: MailConnectionTester,
    ) -> ConnectionTestResult:
        account = await self.get_account(account_id)
        self._require_testable(account)
        secrets = self._read_secrets(account)
        password = secrets.get("smtp_password")
        if not password:
            raise MailSettingsConflict("SMTP password is not configured")
        spec = SmtpConnectionSpec(
            host=account.smtp_host, port=account.smtp_port, username=account.smtp_username,
            password=password, transport=account.smtp_transport,
            proxy=self._proxy_spec(account, secrets),
        )
        result = await anyio.to_thread.run_sync(tester.test_smtp, spec)
        self._audit_connection(account.id, result)
        await self._session.flush()
        return result

    async def test_imap_connection(
        self, account_id: UUID, tester: MailConnectionTester,
    ) -> ConnectionTestResult:
        account = await self.get_account(account_id)
        self._require_testable(account)
        secrets = self._read_secrets(account)
        password = secrets.get("imap_password")
        config = account.imap_config or {}
        host = str(config.get("host") or "").strip()
        username = str(config.get("username") or account.smtp_username).strip()
        if not host or not username or not password:
            raise MailSettingsConflict("IMAP host, username and password are required")
        spec = ImapConnectionSpec(
            host=host, port=int(config.get("port") or 993), username=username, password=password,
        )
        result = await anyio.to_thread.run_sync(tester.test_imap, spec)
        self._audit_connection(account.id, result)
        await self._session.flush()
        return result

    async def get_profile(self) -> UserSettings:
        profile = await self._session.get(UserSettings, self._owner_user_id)
        if profile is None:
            profile = UserSettings(owner_user_id=self._owner_user_id)
            self._session.add(profile)
            await self._session.flush()
        return profile

    async def update_profile(self, values: dict[str, str]) -> UserSettings:
        profile = await self.get_profile()
        for field, value in values.items():
            setattr(profile, field, value)
        profile.version += 1
        self._audit("mail_profile.update", self._owner_user_id, {"fields": sorted(values)})
        await self._session.flush()
        return profile

    async def _clear_default_accounts(self, except_id: UUID | None = None) -> None:
        lock_key = self._DEFAULT_LOCK_NAMESPACE ^ (self._owner_user_id.int & 0x7FFF_FFFF_FFFF_FFFF)
        from sqlalchemy import func
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))
        statement = update(SenderAccount).where(
            SenderAccount.owner_user_id == self._owner_user_id,
            SenderAccount.is_default.is_(True),
        )
        if except_id is not None:
            statement = statement.where(SenderAccount.id != except_id)
        await self._session.execute(statement.values(is_default=False, version=SenderAccount.version + 1))

    def _read_secrets(self, account: SenderAccount) -> dict[str, str]:
        if account.encrypted_secret is None:
            return {}
        if self._envelope is None or account.secret_nonce is None or account.key_version is None:
            raise MailSettingsConflict("Mail secret key is unavailable")
        decoded = self._envelope.decrypt_json(
            EncryptedValue(account.encrypted_secret, account.secret_nonce, account.key_version),
            associated_data=self._associated_data(account.id),
        )
        return {str(key): str(value) for key, value in decoded.items()}

    def _store_secrets(self, account: SenderAccount, secrets: dict[str, str]) -> None:
        if not secrets:
            account.encrypted_secret = None
            account.secret_nonce = None
            account.key_version = None
            account.secret_metadata = {}
            return
        if self._envelope is None:
            raise MailSettingsConflict("Mail secret encryption is not configured")
        encrypted = self._envelope.encrypt_json(secrets, associated_data=self._associated_data(account.id))
        account.encrypted_secret = encrypted.ciphertext
        account.secret_nonce = encrypted.nonce
        account.key_version = encrypted.key_version
        account.secret_metadata = {"fields": sorted(secrets)}

    def _associated_data(self, account_id: UUID) -> bytes:
        return f"{self._owner_user_id}:sender-account:{account_id}".encode()

    @staticmethod
    def _require_testable(account: SenderAccount) -> None:
        if not account.enabled:
            raise MailSettingsConflict("Mail account is disabled")

    @staticmethod
    def _proxy_spec(account: SenderAccount, secrets: dict[str, str]) -> ProxySpec | None:
        config = account.proxy_config or {}
        host = str(config.get("host") or "").strip()
        if not host:
            return None
        scheme = str(config.get("scheme") or "")
        port = config.get("port")
        if scheme not in {"socks5", "socks4", "http"} or not port:
            raise MailSettingsConflict("Proxy configuration is incomplete")
        return ProxySpec(
            scheme=scheme, host=host, port=int(port),
            username=secrets.get("proxy_username"), password=secrets.get("proxy_password"),
        )

    def _audit_connection(self, account_id: UUID, result: ConnectionTestResult) -> None:
        self._session.add(AuditEvent(
            owner_user_id=self._owner_user_id, actor_user_id=self._actor_user_id,
            action=f"mail_accounts.test_{result.protocol}", resource_type="sender_account",
            resource_id=account_id, outcome=result.status,
            metadata_allowlist={"code": result.code, "transport": result.transport},
            request_id=self._request_id,
        ))

    @staticmethod
    def _identity(host: str, port: int, username: str) -> str:
        return f"{username.strip().casefold()}@{host.strip().casefold()}:{port}"

    def _audit(self, action: str, resource_id: UUID, metadata: dict) -> None:
        self._session.add(AuditEvent(
            owner_user_id=self._owner_user_id, actor_user_id=self._actor_user_id,
            action=action, resource_type="sender_account", resource_id=resource_id,
            outcome="success", metadata_allowlist=metadata, request_id=self._request_id,
        ))

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.core.form_tasks import PROVIDER_FORMS, available_providers, ProviderFormTask, QuickReportFormTaskAdapter
from domaincheck.infrastructure.db.models import AuditEvent, Check, ReviewSnapshot, UserSettings
from domaincheck.infrastructure.db.owned import OwnedRepository


DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
PROVIDERS = frozenset(PROVIDER_FORMS)
TERMINAL_STATES = frozenset({"FILLED", "NEEDS_MANUAL", "FAILED"})
STATUS_STATES = frozenset({"WORKING", *TERMINAL_STATES})


class FormTaskError(ValueError):
    pass


class FormTaskNotFound(FormTaskError):
    pass


class FormTaskConflict(FormTaskError):
    pass


@dataclass(frozen=True, slots=True)
class FormTaskCreated:
    id: UUID
    capability_token: str
    provider: str
    form_url: str
    target_url: str
    mode: str
    state: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class FormTaskExchange:
    id: UUID
    status_token: str
    payload: ProviderFormTask
    expires_at: datetime


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _task_token(task_id: UUID) -> str:
    return f"{task_id}.{secrets.token_urlsafe(32)}"


def _token_task_id(token: str) -> UUID:
    try:
        prefix, secret = token.split(".", 1)
        if len(secret) < 32:
            raise ValueError
        return UUID(prefix)
    except (ValueError, AttributeError) as exc:
        raise FormTaskNotFound("Form task was not found") from exc


def _device_context(device_id: str) -> str:
    if not DEVICE_ID_PATTERN.fullmatch(device_id):
        raise FormTaskError("Extension device ID is invalid")
    return f"device:{_digest(device_id)}"


def _task_context(task_id: UUID) -> str:
    return f"task:{task_id}"


class FormTaskService:
    def __init__(self, session: AsyncSession, *, owner_user_id: UUID, actor_user_id: UUID,
                 request_id: str, now: datetime, ttl_seconds: int = 300,
                 adapter: QuickReportFormTaskAdapter | None = None) -> None:
        self._session = session
        self._owner = owner_user_id
        self._actor = actor_user_id
        self._request_id = request_id
        self._now = now
        self._ttl = ttl_seconds
        self._adapter = adapter or QuickReportFormTaskAdapter()

    async def register_device(self, device_id: str, extension_version: str) -> ReviewSnapshot:
        context = _device_context(device_id)
        version = extension_version.strip()[:40]
        if not version:
            raise FormTaskError("Extension version is required")
        row = await self._session.scalar(select(ReviewSnapshot).where(
            ReviewSnapshot.owner_user_id == self._owner,
            ReviewSnapshot.feature == "extension_device",
            ReviewSnapshot.context_key == context,
        ).with_for_update())
        state = {
            "device_id_hash": _digest(device_id), "extension_version": version,
            "last_seen_at": self._now.isoformat(),
        }
        if row is None:
            row = ReviewSnapshot(
                owner_user_id=self._owner, feature="extension_device",
                context_key=context, sanitized_state=state,
            )
            self._session.add(row)
            await self._session.flush()
        else:
            row.sanitized_state = state
            row.version += 1
        await self._audit("extension.device.register", "extension_device", row.id, "success", {
            "extension_version": version,
        })
        return row

    async def create(self, check_id: UUID, *, provider: str, device_id: str) -> FormTaskCreated:
        if provider not in PROVIDERS:
            raise FormTaskError("Unsupported Quick Report form provider")
        device_context = _device_context(device_id)
        device = await self._session.scalar(select(ReviewSnapshot.id).where(
            ReviewSnapshot.owner_user_id == self._owner,
            ReviewSnapshot.feature == "extension_device",
            ReviewSnapshot.context_key == device_context,
        ))
        if device is None:
            raise FormTaskError("Extension device is not registered for this account")
        check = await OwnedRepository(self._session, Check, self._owner).get(check_id)
        if check is None or check.workflow != "quick_report":
            raise FormTaskNotFound("Quick Report check was not found")
        if check.status != "succeeded":
            raise FormTaskConflict("Quick Report check is not complete")
        if provider not in available_providers(check.domain, check.result):
            raise FormTaskConflict("Provider does not match this check's routing")
        settings = await self._session.get(UserSettings, self._owner)
        config = {
            "brand_name": settings.brand_name if settings else "",
            "contact_name": settings.contact_name if settings else "",
            "contact_email": settings.contact_email if settings else "",
            "company_name": settings.company_name if settings else "",
            "business_registration_no": settings.business_registration_no if settings else "",
            "signature_role": settings.signature_role if settings else "",
            "registry_contact": check.result.get("registry_contact"),
        }
        payload = self._adapter.build(
            provider, target_url=check.requested_url, domain=check.domain, config=config,
        )
        if payload.mode != "fill_only":
            raise FormTaskError("Quick Report tasks must be fill-only")
        task_id = uuid4()
        capability = _task_token(task_id)
        expires_at = self._now + timedelta(seconds=self._ttl)
        state = {
            "kind": "form_task", "task_id": str(task_id), "check_id": str(check.id),
            "provider": payload.provider, "target_url": payload.target_url,
            "form_url": payload.form_url, "mode": payload.mode,
            "payload": {
                "draft": payload.draft, "threat_type": payload.threat_type,
                "threat_category": payload.threat_category,
                "contact_email": payload.contact_email, "contact_name": payload.contact_name,
                "brand_name": payload.brand_name, "report_type": payload.report_type,
            },
            "device_id_hash": _digest(device_id),
            "capability_hash": _digest(capability), "status_token_hash": "pending_exchange",
            "state": "CREATED", "expires_at": expires_at.isoformat(),
            "consumed_at": None, "status": None,
        }
        row = ReviewSnapshot(
            id=task_id, owner_user_id=self._owner, feature="form_task",
            context_key=_task_context(task_id), business_day=check.business_day,
            sanitized_state=state,
        )
        self._session.add(row)
        await self._session.flush()
        await self._audit("quick_report.form_task.create", "form_task", task_id, "success", {
            "provider": provider, "mode": "fill_only", "check_id": str(check.id),
        })
        return FormTaskCreated(
            task_id, capability, provider, payload.form_url, payload.target_url,
            payload.mode, "CREATED", expires_at,
        )

    async def get(self, task_id: UUID) -> ReviewSnapshot:
        row = await OwnedRepository(self._session, ReviewSnapshot, self._owner).get(task_id)
        if row is None or row.feature != "form_task":
            raise FormTaskNotFound("Form task was not found")
        return row

    async def _audit(self, action: str, resource_type: str, resource_id: UUID,
                     outcome: str, metadata: dict) -> None:
        self._session.add(AuditEvent(
            owner_user_id=self._owner, actor_user_id=self._actor, action=action,
            resource_type=resource_type, resource_id=resource_id, outcome=outcome,
            metadata_allowlist=metadata, request_id=self._request_id,
        ))

    @staticmethod
    async def exchange(session: AsyncSession, *, capability_token: str, device_id: str,
                       now: datetime) -> FormTaskExchange:
        task_id = _token_task_id(capability_token)
        _device_context(device_id)
        row = await session.scalar(select(ReviewSnapshot).where(
            ReviewSnapshot.id == task_id,
            ReviewSnapshot.feature == "form_task",
            ReviewSnapshot.context_key == _task_context(task_id),
        ).with_for_update())
        if row is None:
            raise FormTaskNotFound("Form task was not found")
        state = dict(row.sanitized_state)
        expires_at = datetime.fromisoformat(str(state["expires_at"]))
        if now >= expires_at:
            state["state"] = "EXPIRED"
            row.sanitized_state = state
            row.version += 1
            raise FormTaskConflict("Form task has expired")
        if state.get("consumed_at") is not None or state.get("state") != "CREATED":
            raise FormTaskConflict("Form task capability was already used")
        if not hmac.compare_digest(str(state.get("capability_hash", "")), _digest(capability_token)):
            raise FormTaskNotFound("Form task was not found")
        if not hmac.compare_digest(str(state.get("device_id_hash", "")), _digest(device_id)):
            raise FormTaskNotFound("Form task was not found")
        status_token = _task_token(task_id)
        state["status_token_hash"] = _digest(status_token)
        state["capability_hash"] = "consumed"
        state["consumed_at"] = now.isoformat()
        state["state"] = "EXCHANGED"
        row.sanitized_state = state
        row.version += 1
        payload_data = dict(state["payload"])
        payload = ProviderFormTask(
            provider=str(state["provider"]), form_url=str(state["form_url"]),
            target_url=str(state["target_url"]), draft=str(payload_data.get("draft", "")),
            mode=str(state["mode"]), threat_type=str(payload_data.get("threat_type", "")),
            threat_category=str(payload_data.get("threat_category", "")),
            contact_email=str(payload_data.get("contact_email", "")),
            contact_name=str(payload_data.get("contact_name", "")),
            brand_name=str(payload_data.get("brand_name", "")),
            report_type=str(payload_data.get("report_type", "phishing")),
        )
        return FormTaskExchange(task_id, status_token, payload, expires_at)

    @staticmethod
    async def update_status(session: AsyncSession, *, task_id: UUID, status_token: str,
                            state_name: str, result: str, adapter_id: str,
                            adapter_version: str, now: datetime) -> ReviewSnapshot:
        if state_name not in STATUS_STATES:
            raise FormTaskError("Form task status is invalid")
        row = await session.scalar(select(ReviewSnapshot).where(
            ReviewSnapshot.id == task_id,
            ReviewSnapshot.feature == "form_task",
            ReviewSnapshot.context_key == _task_context(task_id),
        ).with_for_update())
        if row is None:
            raise FormTaskNotFound("Form task was not found")
        state = dict(row.sanitized_state)
        expires_at = datetime.fromisoformat(str(state["expires_at"]))
        if now >= expires_at:
            raise FormTaskConflict("Form task has expired")
        if not hmac.compare_digest(str(state.get("status_token_hash", "")), _digest(status_token)):
            raise FormTaskNotFound("Form task was not found")
        current = str(state.get("state", ""))
        if current in TERMINAL_STATES and current != state_name:
            raise FormTaskConflict("Form task is already terminal")
        state["state"] = state_name
        state["status"] = {
            "state": state_name, "result": " ".join(result.split())[:300],
            "adapter_id": adapter_id.strip()[:60],
            "adapter_version": adapter_version.strip()[:30], "updated_at": now.isoformat(),
        }
        row.sanitized_state = state
        row.version += 1
        return row

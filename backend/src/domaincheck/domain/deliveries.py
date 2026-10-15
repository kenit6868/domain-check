from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID


DELIVERY_PENDING = "pending"
DELIVERY_SENDING = "sending"
DELIVERY_SENT = "sent"
DELIVERY_FAILED = "failed"
DELIVERY_UNKNOWN = "unknown"
DELIVERY_STATES = frozenset({
    DELIVERY_PENDING, DELIVERY_SENDING, DELIVERY_SENT, DELIVERY_FAILED, DELIVERY_UNKNOWN,
})
DELIVERY_RETRYABLE_STATES = frozenset({DELIVERY_FAILED})

_CODE = re.compile(r"^[a-z][a-z0-9_:-]{0,79}$")
_FORBIDDEN_RESULT_KEYS = frozenset({
    "authorization", "body", "cookie", "credential", "html", "password",
    "proxy_password", "secret", "session_token", "smtp_password", "token",
})


@dataclass(frozen=True, slots=True)
class DeliveryBusinessIdentity:
    """Server-side workflow identity for one exact preview delivery.

    ``business_identity`` must come from the workflow adapter, never the web
    client. It deliberately excludes preview IDs and content fingerprints so
    editing a draft cannot bypass the sent-delivery ledger.
    """

    delivery_index: int
    business_identity: str


@dataclass(frozen=True, slots=True)
class DeliveryIntent:
    delivery_id: UUID
    attempt_id: UUID
    attempt_number: int
    claim_token: str
    message_id: str
    exact_delivery: dict
    artifacts: tuple[dict, ...]


def validate_workflow(value: str) -> str:
    if not _CODE.fullmatch(value):
        raise ValueError("Delivery workflow must be a lowercase stable code")
    return value


def business_key(*, workflow: str, business_identity: str, recipient: str, channel: str,
                 draft_identity: str) -> str:
    validate_workflow(workflow)
    identity = business_identity.strip()
    if not identity or len(identity.encode("utf-8")) > 2048:
        raise ValueError("Delivery business identity is invalid")
    payload = {
        "business_identity": identity,
        "channel": channel,
        "draft_identity": draft_identity,
        "recipient": recipient.strip().lower(),
        "workflow": workflow,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stable_message_id(delivery_id: UUID) -> str:
    return f"<{delivery_id.hex}@delivery.domaincheck.invalid>"


def validate_attempt_result(value: Mapping[str, Any], *, max_bytes: int = 16_384) -> dict[str, Any]:
    """Keep attempt metadata useful without accepting message or secret data."""

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                if str(key).strip().casefold() in _FORBIDDEN_RESULT_KEYS:
                    raise ValueError("Delivery result contains a forbidden field")
                visit(child)
        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            for child in node:
                visit(child)
        elif node is not None and not isinstance(node, (str, int, float, bool)):
            raise ValueError("Delivery result must be JSON-compatible")

    copied = dict(value)
    visit(copied)
    try:
        encoded = json.dumps(copied, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Delivery result must be JSON-compatible") from exc
    if len(encoded) > max_bytes:
        raise ValueError("Delivery result exceeds the configured limit")
    return copied

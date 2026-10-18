from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID


PREVIEW_SCHEMA_VERSION = 1
PREVIEW_FEATURES = frozenset({
    "check_domain",
    "cloaking_review",
    "domain_evidence_review",
    "domain_worker",
    "lead_triage",
    "provider_replies",
    "quick_report",
})
_EMAIL = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


@dataclass(frozen=True, slots=True)
class PreparedDelivery:
    sender_account_id: UUID
    recipient: str
    channel: str
    draft_identity: str
    subject: str
    text_body: str
    html_body: str | None = None
    sender_email: str | None = None
    sender_name: str = ""
    sender_label: str = ""
    in_reply_to: str | None = None
    references: str | None = None


@dataclass(frozen=True, slots=True)
class ExactPreview:
    preview_id: UUID
    revision: int
    fingerprint: str
    payload: dict


def validate_prepared_deliveries(deliveries: list[PreparedDelivery]) -> None:
    if not 1 <= len(deliveries) <= 100:
        raise ValueError("A preview must contain between 1 and 100 deliveries")
    seen: set[tuple[UUID, str, str]] = set()
    total_bytes = 0
    for delivery in deliveries:
        recipient = delivery.recipient.strip().lower()
        if len(recipient) > 500 or not _EMAIL.fullmatch(recipient):
            raise ValueError("Preview recipient is invalid")
        if not _CODE.fullmatch(delivery.channel):
            raise ValueError("Preview channel is invalid")
        if not delivery.draft_identity or len(delivery.draft_identity) > 128:
            raise ValueError("Draft identity must contain 1 to 128 characters")
        if not delivery.subject.strip() or len(delivery.subject) > 998 or any(c in delivery.subject for c in '\r\n'):
            raise ValueError("Preview subject is invalid")
        if delivery.sender_email is not None and (
            not _EMAIL.fullmatch(delivery.sender_email) or any(c in delivery.sender_name for c in '\r\n')
        ):
            raise ValueError("Preview sender is invalid")
        if not delivery.text_body.strip():
            raise ValueError("Preview text body is required")
        for header in (delivery.in_reply_to, delivery.references):
            if header is not None and (len(header) > 998 or any(c in header for c in '\r\n')
                                       or not re.fullmatch(r'(?:<[^<>\s]+>\s*)+', header)):
                raise ValueError('Reply thread header is invalid')
        key = (delivery.sender_account_id, recipient, delivery.draft_identity)
        if key in seen:
            raise ValueError("Preview contains a duplicate delivery identity")
        seen.add(key)
        total_bytes += len(delivery.subject.encode("utf-8"))
        total_bytes += len(delivery.text_body.encode("utf-8"))
        total_bytes += len((delivery.html_body or "").encode("utf-8"))
    if total_bytes > 4 * 1024 * 1024:
        raise ValueError("Preview content exceeds 4 MiB")


def canonical_payload_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def fingerprint_payload(payload: dict) -> str:
    return hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()

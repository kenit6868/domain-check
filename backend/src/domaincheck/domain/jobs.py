from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


JOB_TERMINAL_STATES = frozenset({"completed", "failed", "partial", "stopped"})
JOB_RESUMABLE_STATES = frozenset({"stopped", "failed", "partial"})
ITEM_RETRYABLE_STATES = frozenset({"failed"})
ITEM_FINAL_STATES = frozenset({
    "succeeded", "failed", "excluded", "review", "ready", "unknown",
    "submitted", "already_submitted", "sent", "partial",
})

_CODE = re.compile(r"^[a-z][a-z0-9_:-]{0,79}$")
_FORBIDDEN_METADATA_KEYS = frozenset({
    "authorization", "body", "cookie", "credential", "html", "password",
    "proxy_password", "secret", "session_token", "smtp_password", "token",
})


def validate_code(value: str, *, label: str) -> str:
    if not _CODE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase stable code")
    return value


def validate_result_metadata(value: Mapping[str, Any], *, max_bytes: int = 65_536) -> dict[str, Any]:
    """Validate sanitized checkpoint metadata without echoing rejected values."""

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                normalized = str(key).strip().casefold()
                if normalized in _FORBIDDEN_METADATA_KEYS:
                    raise ValueError("Job metadata contains a forbidden field")
                visit(child)
        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            for child in node:
                visit(child)
        elif node is not None and not isinstance(node, (str, int, float, bool)):
            raise ValueError("Job metadata must be JSON-compatible")

    copied = dict(value)
    visit(copied)
    try:
        encoded = json.dumps(copied, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Job metadata must be JSON-compatible") from exc
    if len(encoded) > max_bytes:
        raise ValueError("Job metadata exceeds the configured limit")
    return copied

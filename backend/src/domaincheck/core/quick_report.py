from __future__ import annotations

import importlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol


QUICK_REPORT_ENGINE_VERSION = "legacy-run-cdn-check-v2-registrar-abuse"
_BLOCKED_KEYS = frozenset({
    "body", "content", "cookie", "credential", "html", "password", "proxy",
    "raw_html", "secret", "session", "storage_key", "token",
})
_PATH_KEYS = frozenset({"path", "paths", "file", "files", "screenshot_path", "screenshot_paths"})


class QuickReportAdapter(Protocol):
    def run(self, target: str, config: dict) -> dict: ...


class DomainQuickReportAdapter:
    """Use the packaged checking engine with explicitly resolved configuration."""

    @staticmethod
    def validate_runtime() -> None:
        for module in ("requests", "whois", "dns.resolver", "ipwhois"):
            try:
                importlib.import_module(module)
            except ImportError as exc:
                raise RuntimeError("Quick Report worker dependencies are missing; install backend dependencies before starting the worker") from exc
        # Import before claiming jobs so packaging failures cannot fail user items.
        importlib.import_module("domaincheck.engine.phishing_toolkit")

    def run(self, target: str, config: dict) -> dict:
        toolkit = importlib.import_module("domaincheck.engine.phishing_toolkit")
        return toolkit.run_cdn_check(target, config)


def sanitize_quick_report_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return JSON-only investigation data without bodies, credentials or local paths."""

    def clean(node: Any, key: str = "") -> Any:
        normalized = key.strip().casefold()
        if normalized in _BLOCKED_KEYS or normalized in _PATH_KEYS or normalized.endswith("_path"):
            return None
        if isinstance(node, Mapping):
            return {
                str(child_key): cleaned
                for child_key, child in node.items()
                if (cleaned := clean(child, str(child_key))) is not None
            }
        if isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            return [clean(child) for child in node]
        if node is None or isinstance(node, (bool, int, float)):
            return node
        if isinstance(node, str):
            return node[:20_000]
        return str(node)[:2_000]

    allowed = {
        key: value.get(key)
        for key in (
            "domain", "cloudflare", "cdn_detected", "registrar", "registry_contact",
            "report_recipients", "cloaking", "_error",
        )
        if key in value
    }
    result = clean(allowed)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 512 * 1024:
        raise ValueError("Quick Report result exceeds the persistence limit")
    return result

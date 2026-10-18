"""Read-only web adapter around the characterized full investigation pipeline."""
from __future__ import annotations

import importlib
import ipaddress
import json
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit


ENGINE_VERSION = "legacy-run-check-v1"
RESULT_FIELDS = (
    "domain", "cert", "whois", "cloudflare", "cdn_detected", "origin_ip_scan",
    "origin_ip_whois", "registry_contact", "virustotal", "virustotal_submit",
    "safebrowsing", "reputation", "ca_note", "http_check", "domain_age_days",
    "mx_records", "registrar_abuse_email_source", "registrar_abuse_email_used",
    "cloaking",
)
BLOCKED_KEYS = frozenset({
    "body", "content", "cookie", "credential", "draft", "drafts", "error",
    "html", "password", "proxy", "raw_html", "secret", "session", "token",
    "evidence_path", "screenshot_path", "screenshot_paths", "file_path", "local_path",
    "path", "paths", "file", "files", "log_row",
})


def validate_target(value: str) -> tuple[str, str]:
    from domaincheck.application.quick_reports import canonical_target

    raw = value.strip()
    original = urlsplit(raw if "://" in raw else f"https://{raw}")
    if original.username or original.password or original.fragment:
        raise ValueError("Check Domain target cannot contain userinfo or a fragment")
    canonical, host = canonical_target(value)
    parsed = urlsplit(canonical)
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Check Domain target cannot contain userinfo or a fragment")
    if parsed.port not in (None, 80, 443):
        raise ValueError("Check Domain target port must be 80 or 443")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.endswith((".local", ".localhost", ".internal", ".test")):
            raise ValueError("Check Domain target must be a public domain") from None
    else:
        raise ValueError("Check Domain target must be a domain, not an IP address")
    return canonical, host


def sanitize_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Whitelist investigation fields and remove file paths, errors and raw content."""
    def clean(node: Any, key: str = "") -> Any:
        lower = key.casefold()
        if lower in BLOCKED_KEYS or lower.endswith(("_path", "_file")):
            return None
        if isinstance(node, Mapping):
            return {str(k): item for k, v in node.items()
                    if (item := clean(v, str(k))) is not None}
        if isinstance(node, (tuple, list)):
            return [item for v in node if (item := clean(v)) is not None]
        if node is None or isinstance(node, (bool, int, float)):
            return node
        if isinstance(node, str):
            return node[:4000]
        return str(node)[:1000]

    result = clean({key: value[key] for key in RESULT_FIELDS if key in value})
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 512 * 1024:
        raise ValueError("Check Domain result exceeds persistence limit")
    return result


class InvestigationAdapter(Protocol):
    def run(self, target: str, *, submit_vt: bool, config: dict) -> dict: ...


class DomainInvestigationAdapter:
    @staticmethod
    def validate_runtime() -> None:
        from domaincheck.core.quick_report import DomainQuickReportAdapter
        DomainQuickReportAdapter.validate_runtime()

    def run(self, target: str, *, submit_vt: bool, config: dict) -> dict:
        toolkit = importlib.import_module("domaincheck.engine.phishing_toolkit")
        return toolkit.run_check(target, submit_vt, config, persist=False,
                                 create_drafts=False, capture_cloaking_evidence=False)

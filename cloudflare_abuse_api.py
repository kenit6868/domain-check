"""Minimal Cloudflare Abuse Reports API client.

Secrets are accepted in memory from config and are never persisted or included
in returned diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


API_BASE = "https://api.cloudflare.com/client/v4"
# Cloudflare uses the abuse form identifier in both the route and the payload.
# Using the UI-friendly label ``phishing`` here returns HTTP 405.
PHISHING_REPORT_PARAM = "abuse_phishing"
DEFAULT_TIMEOUT = 30


class CloudflareAbuseApiError(RuntimeError):
    """A sanitized Cloudflare API failure."""


@dataclass(frozen=True)
class CloudflareAbuseConfig:
    api_token: str
    account_id: str

    @classmethod
    def from_mapping(cls, cfg: dict) -> "CloudflareAbuseConfig":
        token = str(cfg.get("cloudflare_api_token") or "").strip()
        account_id = str(cfg.get("cloudflare_account_id") or "").strip()
        if not token:
            raise CloudflareAbuseApiError("Thiếu cloudflare.api_token trong config.ini.")
        if not account_id:
            raise CloudflareAbuseApiError("Thiếu cloudflare.account_id trong config.ini.")
        if len(account_id) > 32 or not all(ch in "0123456789abcdefABCDEF" for ch in account_id):
            raise CloudflareAbuseApiError("Cloudflare Account ID không hợp lệ.")
        return cls(api_token=token, account_id=account_id)


def _headers(config: CloudflareAbuseConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.api_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _decode_response(response: requests.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or data.get("success") is False:
        messages = []
        for item in data.get("errors") or []:
            if isinstance(item, dict) and item.get("message"):
                messages.append(str(item["message"]))
        detail = "; ".join(messages)[:500] or response.reason or "Cloudflare API error"
        raise CloudflareAbuseApiError(f"Cloudflare API HTTP {response.status_code}: {detail}")
    return data


def verify_access(cfg: dict, *, session: Any = requests) -> dict:
    """Verify token and read Abuse Reports without changing external state."""
    config = CloudflareAbuseConfig.from_mapping(cfg)
    try:
        verify = session.get(
            f"{API_BASE}/user/tokens/verify",
            headers=_headers(config),
            timeout=DEFAULT_TIMEOUT,
        )
    except Exception as exc:
        raise CloudflareAbuseApiError(
            f"Không thể kết nối Cloudflare API: {type(exc).__name__}"
        ) from None
    verify_data = _decode_response(verify)
    status = str((verify_data.get("result") or {}).get("status") or "")
    if status.lower() != "active":
        raise CloudflareAbuseApiError("Cloudflare API token không ở trạng thái active.")
    try:
        listing = session.get(
            f"{API_BASE}/accounts/{config.account_id}/abuse-reports",
            headers=_headers(config),
            params={"per_page": 1},
            timeout=DEFAULT_TIMEOUT,
        )
    except Exception as exc:
        raise CloudflareAbuseApiError(
            f"Không thể đọc Abuse Reports API: {type(exc).__name__}"
        ) from None
    list_data = _decode_response(listing)
    return {
        "ok": True,
        "token_status": status,
        "report_count": int((list_data.get("result_info") or {}).get("total_count") or 0),
    }


def build_phishing_payload(target_url: str, draft: str, cfg: dict) -> dict:
    email = str(cfg.get("contact_email") or "").strip()
    name = str(cfg.get("contact_name") or "").strip()
    brand = str(cfg.get("brand_name") or "").strip()
    justification = str(draft or "").strip()
    if "@" not in email:
        raise CloudflareAbuseApiError("Email liên hệ trong config.ini không hợp lệ.")
    if not name or name.startswith("["):
        raise CloudflareAbuseApiError("Thiếu tên người báo cáo trong config.ini.")
    if len(justification) < 20:
        raise CloudflareAbuseApiError("Nội dung bằng chứng phải có ít nhất 20 ký tự.")
    return {
        "act": "abuse_phishing",
        "email": email,
        "email2": email,
        "host_notification": "send",
        "justification": justification[:5000],
        "name": name[:255],
        "owner_notification": "send",
        "urls": target_url,
        "company": brand[:100] if brand and not brand.startswith("[") else "",
        "original_work": brand[:255] if brand and not brand.startswith("[") else "",
        "title": "Phishing and brand impersonation report",
    }


def submit_phishing_report(
    target_url: str, draft: str, cfg: dict, *, session: Any = requests,
) -> dict:
    """Submit one operator-approved phishing report."""
    config = CloudflareAbuseConfig.from_mapping(cfg)
    payload = {key: value for key, value in build_phishing_payload(target_url, draft, cfg).items() if value}
    try:
        response = session.post(
            f"{API_BASE}/accounts/{config.account_id}/abuse-reports/{PHISHING_REPORT_PARAM}",
            headers=_headers(config),
            json=payload,
            timeout=DEFAULT_TIMEOUT,
        )
    except Exception as exc:
        raise CloudflareAbuseApiError(
            f"Không thể gửi Cloudflare API: {type(exc).__name__}"
        ) from None
    data = _decode_response(response)
    result = data.get("result")
    report_id = str(data.get("abuse_rand") or "")
    if isinstance(result, dict):
        report_id = report_id or str(result.get("abuse_rand") or result.get("id") or "")
        result_text = str(result.get("result") or "success")
    else:
        result_text = str(result or "success")
    return {
        "ok": True,
        "report_id": report_id,
        "result": result_text,
        "http_status": int(response.status_code),
    }

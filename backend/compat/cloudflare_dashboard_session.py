"""Cloudflare Dashboard session adapter for operator-approved abuse reports.

The operator-provided Dashboard ``Cookie`` is accepted per call, used only for
that request, and never returned or persisted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

import requests

from cloudflare_abuse_api import build_phishing_payload


DASHBOARD_API_BASE = "https://dash.cloudflare.com/api/v4"
DEFAULT_TIMEOUT = 30


class CloudflareDashboardError(RuntimeError):
    """A definite, sanitized Dashboard submission failure."""


class CloudflareDashboardSessionError(CloudflareDashboardError):
    """The supplied Dashboard session was rejected or has expired."""


class CloudflareDashboardRateLimitError(CloudflareDashboardError):
    """Cloudflare asked the operator to wait or complete verification."""


class CloudflareDashboardUnknownError(CloudflareDashboardError):
    """The request was sent but no conclusive response was received."""


class CloudflareDashboardDuplicateError(CloudflareDashboardError):
    """Cloudflare confirms that this URL was submitted recently."""


@dataclass(frozen=True)
class CloudflareDashboardConfig:
    account_id: str

    @classmethod
    def from_mapping(cls, cfg: dict, cookie_value: str = "") -> "CloudflareDashboardConfig":
        account_id = str(cfg.get("cloudflare_account_id") or "").strip()
        if not account_id:
            account_id = _account_id_from_cookie(cookie_value)
        if not account_id:
            raise CloudflareDashboardError(
                "Không tìm thấy Cloudflare Account ID trong Cookie curr-account."
            )
        if not re.fullmatch(r"[0-9a-fA-F]{32}", account_id):
            raise CloudflareDashboardError("Cloudflare Account ID không hợp lệ.")
        return cls(account_id=account_id)


def _account_id_from_cookie(cookie_value: str) -> str:
    """Read only curr-account, preferring explicit account fields over unrelated IDs.

    Decode bounded URL/JSON wrappers. Never scan authentication/session cookies
    for hex-looking tokens or choose an arbitrary ID from ambiguous data.
    """
    value = str(cookie_value or "").strip()
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    values = re.findall(r"(?:^|;\s*)curr-account=([^;]*)", value, flags=re.I)
    if not values: return ""

    def valid(candidate):
        return isinstance(candidate, str) and re.fullmatch(r"[0-9a-fA-F]{32}", candidate.strip())

    def extract(raw):
        if len(raw) > 32768: return ""
        current = raw.strip()
        for _ in range(6):
            if not isinstance(current, str): break
            current = current.strip()
            if valid(current): return current.lower()
            try:
                current = json.loads(current)
                continue
            except (ValueError, TypeError):
                decoded = unquote(current)
                if decoded == current: return ""
                current = decoded
        if not isinstance(current, dict): return ""
        explicit = [current[key] for key in ('accountId', 'account_id', 'accountTag', 'account_tag') if key in current]
        nested = current.get('account')
        if valid(nested): explicit.append(nested)
        if isinstance(nested, dict):
            explicit.extend(nested[key] for key in ('id', 'accountId', 'account_id', 'tag') if key in nested)
        candidates = {candidate.strip().lower() for candidate in explicit if valid(candidate)}
        if explicit:
            return next(iter(candidates)) if len(candidates) == 1 else ""
        if 'id' in current:
            return current['id'].strip().lower() if valid(current['id']) else ""
        # Preserve legacy dictionaries containing exactly one unambiguous ID.
        candidates = {candidate.strip().lower() for candidate in current.values() if valid(candidate)}
        return next(iter(candidates)) if len(candidates) == 1 else ""

    extracted = [extract(raw) for raw in values]
    return extracted[0] if all(extracted) and len(set(extracted)) == 1 else ""


def _headers(cookie_value: str) -> dict[str, str]:
    value = str(cookie_value or "").strip()
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    if not value:
        raise CloudflareDashboardSessionError("Chưa nhập Cookie của phiên Cloudflare.")
    if "\r" in value or "\n" in value:
        raise CloudflareDashboardSessionError("Cookie Cloudflare phải nằm trên một dòng.")
    return {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://dash.cloudflare.com",
        "Referer": "https://dash.cloudflare.com/",
        "x-cross-site-security": "dash",
        "Cookie": value,
    }


def build_dashboard_payload(target_url: str, draft: str, cfg: dict) -> dict:
    """Build the observed Dashboard payload without copying browser telemetry."""
    official = build_phishing_payload(target_url, draft, cfg)
    country = str(cfg.get("cloudflare_reported_country") or "VN").strip().upper()
    if len(country) != 2 or not country.isalpha():
        country = "VN"
    return {
        "act": "abuse_phishing",
        "name": official["name"],
        "email": official["email"],
        "email2": official["email2"],
        "justification": official["justification"],
        "original_work": None,
        "title": "",
        "company": official.get("company", ""),
        "tele": "",
        "urls": official["urls"],
        "reported_country": country,
        "reported_user_agent": "",
        "comments": "",
        "host_notification": "send-anon",
        "owner_notification": "send-anon",
        "agree": 0,
    }


def _response_data(response: requests.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def submit_dashboard_report(
    target_url: str,
    draft: str,
    cfg: dict,
    cookie_value: str,
    *,
    session: Any = requests,
) -> dict:
    """Submit one report through the observed Dashboard endpoint.

    A transport exception is deliberately classified as unknown because the
    server may have accepted the request before the response was lost.
    """
    config = CloudflareDashboardConfig.from_mapping(cfg, cookie_value)
    headers = _headers(cookie_value)
    payload = build_dashboard_payload(target_url, draft, cfg)
    try:
        response = session.post(
            f"{DASHBOARD_API_BASE}/accounts/{config.account_id}/abuse-reports/abuse_phishing",
            headers=headers,
            json=payload,
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=False,
        )
    except Exception as exc:
        raise CloudflareDashboardUnknownError(
            f"Không nhận được phản hồi xác nhận từ Cloudflare ({type(exc).__name__}); cần đối chiếu trước khi gửi lại."
        ) from None

    data = _response_data(response)
    errors = data.get("errors") or []
    messages = [
        str(item.get("message")) for item in errors
        if isinstance(item, dict) and item.get("message")
    ]
    codes = {
        str(item.get("code")) for item in errors
        if isinstance(item, dict) and item.get("code") is not None
    }
    response_message = str(data.get("msg") or "").strip()
    detail = response_message[:500] or "; ".join(messages)[:500] or response.reason or "Cloudflare Dashboard error"
    if response.status_code in {401, 403} or "10000" in codes or "authentication" in detail.lower():
        raise CloudflareDashboardSessionError(
            "Cookie Cloudflare bị từ chối hoặc đã hết hạn; hãy nhập Cookie mới."
        )
    if response.status_code == 429 or any(word in detail.lower() for word in ("rate limit", "captcha", "challenge", "verification")):
        raise CloudflareDashboardRateLimitError(
            "Cloudflare yêu cầu chờ hoặc xác minh thủ công; batch đã tạm dừng."
        )
    error_code = str(data.get("error_code") or data.get("err_code") or "").strip().lower()
    if error_code == "dedupe":
        raise CloudflareDashboardDuplicateError(detail)
    if response.status_code >= 400 or data.get("success") is False:
        raise CloudflareDashboardError(
            f"Cloudflare Dashboard HTTP {response.status_code}: {detail}"
        )

    result = data.get("result")
    report_id = str(data.get("abuse_rand") or "")
    if isinstance(result, dict):
        report_id = report_id or str(result.get("abuse_rand") or result.get("id") or "")
        result_text = str(result.get("result") or "")
    else:
        result_text = str(result or "")
    confirmed = data.get("success") is True or (
        result_text.strip().lower() == "success" and bool(report_id)
    )
    if not confirmed:
        raise CloudflareDashboardUnknownError(
            "Cloudflare không trả result=success kèm mã report; cần đối chiếu trước khi gửi lại."
        )
    return {
        "ok": True,
        "report_id": report_id,
        "result": result_text,
        "http_status": int(response.status_code),
    }

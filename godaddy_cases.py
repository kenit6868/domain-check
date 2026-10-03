"""Local metadata for GoDaddy phishing reports submitted by an operator."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import urlsplit

import phishing_toolkit as pt

CASE_PATH = pt._runtime_path("godaddy_cases.json")
STATUS_URL = "https://legalportal.godaddy.com/abuse/status"
STATUSES = {"submitted", "acknowledged", "in_review", "more_info", "resolved", "closed", "unknown"}
STATUS_LABELS = {
    "submitted": "Đã gửi form", "acknowledged": "Đã tiếp nhận",
    "in_review": "Đang xử lý", "more_info": "Cần bổ sung",
    "resolved": "Đã xử lý", "closed": "Đã đóng", "unknown": "Chưa rõ",
}
_LOCK = threading.RLock()
_CASE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{3,79}\Z")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _account(value: str) -> str:
    return str(value or "").strip().lower()


def _case_id(value: str) -> str:
    value = str(value or "").strip()
    if not _CASE_ID_RE.fullmatch(value):
        raise ValueError("Case ID phải dài 4–80 ký tự chữ, số, dấu chấm, gạch hoặc slash.")
    return value


def _url(value: str) -> str:
    value = str(value or "").strip()
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Cần full URL HTTP(S) hợp lệ của trang đã báo cáo.")
    return value


def _submitted_at(value: str | None) -> str:
    if not value:
        return _now()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Thời điểm gửi không hợp lệ.") from exc
    if parsed.tzinfo is None:
        raise ValueError("Thời điểm gửi phải có múi giờ.")
    return parsed.astimezone(timezone.utc).isoformat()


def _read() -> list[dict]:
    try:
        with open(CASE_PATH, encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or not isinstance(data.get("cases"), list) or not all(
            isinstance(row, dict) for row in data["cases"]
        ):
            raise ValueError("File case GoDaddy có cấu trúc sai; dữ liệu hiện có chưa bị ghi đè.")
        return data["cases"]
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("File case GoDaddy bị lỗi; dữ liệu hiện có chưa bị ghi đè.") from exc


def _write(records: list[dict]) -> None:
    os.makedirs(os.path.dirname(CASE_PATH), exist_ok=True)
    temp = f"{CASE_PATH}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(temp, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "cases": records}, stream, ensure_ascii=False, indent=2)
        os.replace(temp, CASE_PATH)
    finally:
        try:
            os.remove(temp)
        except FileNotFoundError:
            pass


def list_cases(account: str | None = None, target_url: str | None = None) -> list[dict]:
    with _LOCK:
        records = _read()
    if account is not None:
        records = [row for row in records if row.get("account") == _account(account)]
    if target_url is not None:
        records = [row for row in records if row.get("target_url") == target_url]
    return sorted(records, key=lambda row: row.get("submitted_at", ""), reverse=True)


def record_case(account: str, case_id: str, target_url: str, *, submitted_at: str | None = None) -> dict:
    account, case_id, target_url = _account(account), _case_id(case_id), _url(target_url)
    submitted_at = _submitted_at(submitted_at)
    if not account or "@" not in account:
        raise ValueError("Cần email người báo cáo đã dùng trên form GoDaddy.")
    with _LOCK:
        records = _read()
        key = (account, case_id.lower(), target_url)
        for row in records:
            if (row.get("account"), str(row.get("case_id", "")).lower(), row.get("target_url")) == key:
                return dict(row)
        row = {
            "account": account, "case_id": case_id, "target_url": target_url,
            "submitted_at": submitted_at, "status": "submitted",
            "status_source": "operator", "status_checked_at": "", "last_mail_at": "",
            "last_mail_type": "", "last_message_id": "", "last_mail_key": "",
        }
        records.append(row)
        _write(records)
        return dict(row)


def record_portal_status(account: str, case_id: str, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError("Trạng thái portal không hợp lệ.")
    with _LOCK:
        records = _read()
        matches = [row for row in records if row.get("account") == _account(account)
                   and str(row.get("case_id", "")).lower() == _case_id(case_id).lower()]
        if not matches:
            raise ValueError("Không tìm thấy Case ID trong tài khoản này.")
        for row in matches:
            row.update(status=status, status_source="portal_manual", status_checked_at=_now())
        _write(records)
        return dict(matches[0])


def _mail_timestamp(mail) -> str:
    for value in (getattr(mail, "server_date", ""), getattr(mail, "date", "")):
        try:
            return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            pass
    return _now()


def sync_mail(account: str, mails: list) -> int:
    """Link only exact account + case ID from a GoDaddy sender; save no mail body."""
    changed = 0
    with _LOCK:
        records = _read()
        for mail in sorted(mails, key=_mail_timestamp):
            sender_domain = parseaddr(getattr(mail, "sender", ""))[1].lower().split("@")[-1]
            if sender_domain != "godaddy.com" and not sender_domain.endswith(".godaddy.com"):
                continue
            if _account(getattr(mail, "account", "")) != _account(account):
                continue
            ticket = str(getattr(mail, "ticket", "") or "").strip().lower()
            if not ticket:
                continue
            kind = str(getattr(mail, "request_type", "") or "")
            status = ("more_info" if kind in {
                "legal_evidence", "identity", "screenshot", "full_url",
                "official_url", "technical_evidence", "clarification",
            } else "acknowledged" if kind == "acknowledgement" else
                "resolved" if kind == "resolved" else "")
            for row in records:
                if row.get("account") != _account(account) or str(row.get("case_id", "")).lower() != ticket:
                    continue
                timestamp = _mail_timestamp(mail)
                mail_key = "|".join((str(getattr(mail, "source_mailbox", "") or ""),
                                     str(getattr(mail, "uid", "") or ""),
                                     str(getattr(mail, "message_id", "") or "")))
                if row.get("last_mail_key") == mail_key or row.get("last_mail_at", "") > timestamp:
                    continue
                row.update(last_mail_at=timestamp, last_mail_type=kind,
                           last_message_id=str(getattr(mail, "message_id", "") or "")[:255],
                           last_mail_key=mail_key[:400])
                if status and row.get("status_checked_at", "") < timestamp:
                    row.update(status=status, status_source="email")
                changed += 1
        if changed:
            _write(records)
    return changed


def followup_draft(row: dict) -> str:
    return (
        f"Subject: Follow-up on GoDaddy phishing report {row['case_id']}\n\n"
        "Dear GoDaddy Abuse Team,\n\n"
        f"Please review the status of case {row['case_id']} concerning the reported URL below.\n"
        f"Reported URL: {row['target_url']}\n"
        f"Original report date (UTC): {row['submitted_at']}\n\n"
        "[Add the current observed behavior and evidence here before sending.]\n"
        "Please let us know if further evidence is needed and take appropriate "
        "mitigation action if the abuse is confirmed.\n\n"
        "Regards,\n[Reporter name]"
    )


def followup_due(row: dict, days: int, now: datetime | None = None) -> bool:
    if row.get("status") in {"more_info", "resolved", "closed"}:
        return False
    latest = max(row.get("submitted_at", ""), row.get("last_mail_at", ""),
                 row.get("status_checked_at", ""))
    try:
        observed = datetime.fromisoformat(latest)
    except (TypeError, ValueError):
        return False
    return (now or datetime.now(timezone.utc)) >= observed + timedelta(days=max(1, days))

"""One-shot account-scoped mail and report statistics.

The Streamlit menu uses this coordinator instead of asking the operator to
visit Sent Mail Evidence and Provider Replies separately.  A sync is still an
explicit action: it reads the selected account's IMAP Inbox/Sent/Junk folders,
indexes Sent attachments in memory, fetches inbound provider messages for the
same date range, and stores only a compact statistics snapshot.  The existing
Provider Replies page keeps its own filtering/reply workflow unchanged.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import uuid
from datetime import date, datetime, timezone
from typing import Callable

import mail_statistics
import phishing_toolkit as pt
import provider_replies
import report_statistics
import sent_mail_evidence
import browser_evidence


# A Streamlit hot reload can retain these modules from an older source tree.
# General Statistics calls the new range counter and timeout-aware IMAP helper,
# so load their current APIs before the first explicit sync.
if getattr(mail_statistics, "MODULE_VERSION", 0) < 9:
    mail_statistics = importlib.reload(mail_statistics)
if getattr(provider_replies, "MODULE_VERSION", 0) < 6:
    provider_replies = importlib.reload(provider_replies)
if getattr(sent_mail_evidence, "MODULE_VERSION", 0) < 2:
    sent_mail_evidence = importlib.reload(sent_mail_evidence)
if getattr(report_statistics, "MODULE_VERSION", 0) < 3:
    report_statistics = importlib.reload(report_statistics)


MODULE_VERSION = 2
CACHE_VERSION = 2
CACHE_PATH = pt._runtime_path("general_statistics_cache.json")


def account_key(value) -> str:
    return str(value or "").strip().lower()


def _cache_key(account: str, date_from: date, date_to: date) -> str:
    return f"{account_key(account)}|{date_from.isoformat()}|{date_to.isoformat()}"


def _read_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {"version": CACHE_VERSION, "snapshots": {}}
    if not isinstance(value, dict) or value.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "snapshots": {}}
    snapshots = value.get("snapshots")
    return {"version": CACHE_VERSION, "snapshots": snapshots if isinstance(snapshots, dict) else {}}


def _write_cache(data: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    temp_path = f"{CACHE_PATH}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, CACHE_PATH)
    finally:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass


def _json_safe(value):
    """Keep snapshots JSON-serialisable without persisting message bodies."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def save_snapshot(snapshot: dict) -> None:
    account = str(snapshot.get("account") or "").strip()
    date_from = date.fromisoformat(str(snapshot["date_from"]))
    date_to = date.fromisoformat(str(snapshot["date_to"]))
    if not account:
        return
    data = _read_cache()
    data["snapshots"][_cache_key(account, date_from, date_to)] = _json_safe(snapshot)
    _write_cache(data)


def load_snapshot(account: str, date_from: date, date_to: date) -> dict | None:
    value = _read_cache()["snapshots"].get(_cache_key(account, date_from, date_to))
    if not isinstance(value, dict):
        return None
    if account_key(value.get("account")) != account_key(account):
        return None
    return dict(value)


def clear_snapshots(account: str | None = None) -> bool:
    data = _read_cache()
    snapshots = data["snapshots"]
    if account is None:
        removed = bool(snapshots)
        snapshots.clear()
    else:
        wanted = account_key(account)
        keys = [
            key for key, value in snapshots.items()
            if account_key(value.get("account")) == wanted
        ]
        removed = bool(keys)
        for key in keys:
            snapshots.pop(key, None)
    if removed:
        _write_cache(data)
    return removed


def _emit(progress_callback, stage: str, position: int, total: int) -> None:
    if progress_callback:
        progress_callback(stage, position, total)


def _safe_error_text(value, account: dict | None = None, *, max_length: int = 500) -> str:
    """Return short display text without copying account values to cache/UI."""
    text = str(value or "")
    for key in ("password", "username", "imap_host", "host"):
        value = str((account or {}).get(key) or "").strip()
        if value:
            text = text.replace(value, "[redacted]")
    text = re.sub(
        r"(?i)\b(password|pass|token|secret|authorization)\s*([=:])\s*[^\s,;]+",
        r"\1\2[redacted]",
        text,
    )
    return text[:max_length]


def _error_record(stage: str, exc: Exception, account: dict | None = None) -> dict:
    return {"stage": stage, "error": _safe_error_text(exc, account)}


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _safe_url(value, account: dict | None = None) -> str:
    text = _safe_error_text(value, account, max_length=2_000)
    try:
        return browser_evidence.redact_url(text)
    except (AttributeError, TypeError, ValueError):
        return text


def _mail_value(mail, key: str, default=""):
    if isinstance(mail, dict):
        return mail.get(key, default)
    return getattr(mail, key, default)


def _sanitize_folder_statistics(rows, relevant_mails, account: dict) -> list[dict]:
    """Whitelist folder-level aggregates; never persist raw IMAP exceptions."""
    relevant_by_mailbox = {}
    for mail in relevant_mails or []:
        mailbox = str(_mail_value(mail, "source_mailbox", "INBOX") or "INBOX").strip().lower()
        relevant_by_mailbox[mailbox] = relevant_by_mailbox.get(mailbox, 0) + 1

    result = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("folder") or "Khác").strip()
        if label not in {"Inbox", "Thư rác"}:
            label = "Khác"
        mailbox = str(raw.get("mailbox") or ("INBOX" if label == "Inbox" else "")).strip().lower()
        result.append({
            "folder": label,
            "matched": relevant_by_mailbox.get(mailbox, 0),
            "status": _safe_error_text(raw.get("status") or "—", account, max_length=300),
        })
    return result


def _sanitize_summary_rows(rows, label_key: str, account: dict) -> list[dict]:
    result = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        result.append({
            label_key: _safe_error_text(row.get(label_key) or "—", account, max_length=500),
            "sent": _safe_int(row.get("sent")),
            "success": _safe_int(row.get("success")),
            "replies": _safe_int(row.get("replies")),
            "resolved": _safe_int(row.get("resolved")),
            "takedown_rate": _safe_float(row.get("takedown_rate")),
        })
    return result


def _sanitize_links(rows, account: dict) -> list[dict]:
    result = []
    text_keys = (
        "domain", "to", "account", "channel", "provider", "draft", "subject", "sent_at",
        "send_status", "evidence", "reply_provider", "reply_subject", "reply_at",
        "reply_outcome", "reply_outcome_key", "ticket",
    )
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cleaned = {
            key: _safe_error_text(row.get(key) or "—", account, max_length=1_000)
            for key in text_keys
        }
        cleaned["target_url"] = _safe_url(row.get("target_url") or "", account) or "—"
        cleaned["evidence_images"] = _safe_int(row.get("evidence_images"))
        result.append(cleaned)
    return result


def _sanitize_report(report, account: dict) -> dict:
    """Persist only the explicit aggregate/view fields used by this dashboard."""
    source = report if isinstance(report, dict) else {}
    evidence_source = source.get("evidence") if isinstance(source.get("evidence"), dict) else {}
    outcome_source = source.get("outcomes") if isinstance(source.get("outcomes"), dict) else {}
    return {
        "version": _safe_int(source.get("version")),
        "sent_total": _safe_int(source.get("sent_total")),
        "sent_success": _safe_int(source.get("sent_success")),
        "sent_failed": _safe_int(source.get("sent_failed")),
        "linked_reply_total": _safe_int(source.get("linked_reply_total")),
        "reply_total": _safe_int(source.get("reply_total")),
        "resolved_total": _safe_int(source.get("resolved_total")),
        "response_rate": _safe_float(source.get("response_rate")),
        "takedown_rate": _safe_float(source.get("takedown_rate")),
        "evidence": {
            key: _safe_int(evidence_source.get(key))
            for key in ("automatic", "manual", "mixed", "none", "unknown", "with_images")
        },
        "outcomes": {
            _safe_error_text(key, account, max_length=100): _safe_int(value)
            for key, value in outcome_source.items()
        },
        "by_channel": _sanitize_summary_rows(source.get("by_channel"), "channel_label", account),
        "by_provider": _sanitize_summary_rows(source.get("by_provider"), "provider_label", account),
        "by_subject": _sanitize_summary_rows(source.get("by_subject"), "subject", account),
        "by_draft": _sanitize_summary_rows(source.get("by_draft"), "draft", account),
        "links": _sanitize_links(source.get("links"), account),
        "warnings": [
            _safe_error_text(value, account, max_length=500)
            for value in (source.get("warnings") or [])
            if str(value or "").strip()
        ],
    }


def sync_account_general_statistics(
    account: dict,
    date_from: date,
    date_to: date,
    *,
    local_tz=None,
    progress_callback: Callable[[str, int, int], None] | None = None,
    timeout: int = 60,
    count_mail_fn=None,
    sent_sync_fn=None,
    sent_loader_fn=None,
    provider_fetch_fn=None,
    report_builder_fn=None,
) -> dict:
    """Synchronise all mail sources and return/save one statistics snapshot.

    Each source is isolated.  A Sent parser or provider mailbox failure is
    reported in ``errors`` while counts and any available report data remain
    usable.  The function never sends SMTP or submits an external report.
    """
    if date_from > date_to:
        raise ValueError("Từ ngày không được lớn hơn Đến ngày")
    account_name = str(account.get("username") or "").strip()
    if not account_name or not account.get("imap_host") or not account.get("password"):
        raise ValueError("Tài khoản thiếu cấu hình IMAP")
    local_tz = local_tz or datetime.now().astimezone().tzinfo
    count_mail_fn = count_mail_fn or mail_statistics.count_account_mail_range
    sent_sync_fn = sent_sync_fn or sent_mail_evidence.sync_account_sent_mail
    sent_loader_fn = sent_loader_fn or sent_mail_evidence.load_cached_records
    provider_fetch_fn = provider_fetch_fn or provider_replies.fetch_provider_mail_all_folders
    report_builder_fn = report_builder_fn or report_statistics.build_account_report

    errors = []
    _emit(progress_callback, "mail", 0, 4)
    try:
        mail_counts = count_mail_fn(
            account, date_from, date_to, local_tz, timeout=timeout,
        )
    except Exception as exc:
        mail_counts = {
            "account": account_name, "received": 0, "sent": 0, "junk": 0,
            "status": "error", "error": _safe_error_text(exc, account),
            "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        }
        errors.append(_error_record("mail_counts", exc, account))
    _emit(progress_callback, "mail", 1, 4)

    sent_result = {"success": False, "matched": 0, "with_images": 0, "errors": []}
    try:
        sent_result = sent_sync_fn(
            account, date_from, date_to, local_tz=local_tz, timeout=timeout,
        ) or sent_result
        if not isinstance(sent_result, dict):
            raise TypeError("Sent synchroniser returned an invalid result")
    except Exception as exc:
        sent_result = {"success": False, "matched": 0, "with_images": 0, "errors": []}
        errors.append(_error_record("sent_mail", exc, account))
    try:
        sent_records = sent_loader_fn(account_name, date_from, date_to, local_tz)
    except Exception as exc:
        sent_records = []
        errors.append(_error_record("sent_cache", exc, account))
    _emit(progress_callback, "sent", 2, 4)

    provider_mails = []
    folder_statistics = []
    try:
        provider_mails, folder_statistics = provider_fetch_fn(
            account,
            date_from=date_from,
            date_to=date_to,
            progress_callback=None,
            timeout=timeout,
        )
        provider_mails = provider_mails or []
        folder_statistics = folder_statistics or []
    except Exception as exc:
        errors.append(_error_record("provider_replies", exc, account))
        # The report can still use a previously synchronised local cache.  It
        # is explicitly labelled as a fallback so the UI never implies that
        # the current IMAP sync succeeded.
        try:
            provider_mails = provider_replies.load_mail_cache(account_name)
        except Exception:
            provider_mails = []
    relevant_provider_mails = []
    for mail in provider_mails:
        try:
            if report_statistics.is_relevant_provider_reply(mail):
                relevant_provider_mails.append(mail)
        except (AttributeError, TypeError, ValueError):
            continue
    sanitized_folder_statistics = _sanitize_folder_statistics(
        folder_statistics, relevant_provider_mails, account,
    )
    _emit(progress_callback, "replies", 3, 4)

    try:
        report = report_builder_fn(
            account_name,
            date_from,
            date_to,
            sent_mail_records=sent_records,
            provider_mails=relevant_provider_mails,
            local_tz=local_tz,
        )
    except Exception as exc:
        errors.append(_error_record("report", exc, account))
        report = {
            "sent_total": 0, "sent_success": 0, "sent_failed": 0,
            "linked_reply_total": 0, "reply_total": 0, "resolved_total": 0,
            "response_rate": 0.0, "takedown_rate": 0.0,
            "evidence": {"automatic": 0, "manual": 0, "mixed": 0, "none": 0, "unknown": 0},
            "by_channel": [], "by_provider": [], "by_subject": [], "by_draft": [],
            "outcomes": {}, "links": [], "warnings": [_safe_error_text(exc, account)],
        }

    # ``sent_result[errors]`` contains per-message parse failures and is safe
    # to expose as a count; the full message/body never enters this snapshot.
    parse_errors = sent_result.get("errors") if isinstance(sent_result, dict) else []
    if parse_errors:
        errors.append({"stage": "sent_parse", "count": len(parse_errors)})
    status = "complete" if not errors else "partial"
    snapshot = {
        "version": MODULE_VERSION,
        "status": status,
        "account": account_name,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "mail": {
            "received": _safe_int(mail_counts.get("received")),
            "sent": _safe_int(mail_counts.get("sent")),
            "junk": _safe_int(mail_counts.get("junk")),
            "status": _safe_error_text(mail_counts.get("status") or "error", account, max_length=100),
            "error": _safe_error_text(mail_counts.get("error") or "", account),
        },
        "sent_sync": {
            "scanned": _safe_int(sent_result.get("scanned")),
            "matched": _safe_int(sent_result.get("matched")),
            "with_images": _safe_int(sent_result.get("with_images")),
            "errors": len(parse_errors),
            "success": bool(sent_result.get("success")),
        },
        "reply_sync": {
            "matched": len(relevant_provider_mails),
            "folders": sanitized_folder_statistics,
            "used_cache_fallback": bool(errors and any(item.get("stage") == "provider_replies" for item in errors)),
        },
        "report": _sanitize_report(report, account),
        "errors": errors,
    }
    save_snapshot(snapshot)
    _emit(progress_callback, "done", 4, 4)
    return snapshot


def summary_metrics(snapshot: dict) -> dict:
    """Calculate the user-facing comparison ratios from one snapshot."""
    mail = snapshot.get("mail") if isinstance(snapshot, dict) else {}
    report = snapshot.get("report") if isinstance(snapshot, dict) else {}
    evidence = report.get("evidence") if isinstance(report, dict) else {}
    inbox = int(mail.get("received", 0) or 0)
    sent = int(mail.get("sent", 0) or 0)
    junk = int(mail.get("junk", 0) or 0)
    total_incoming = inbox + junk
    report_total = int(report.get("sent_total", 0) or 0)
    report_success = int(report.get("sent_success", 0) or 0)
    replies = int(report.get("linked_reply_total", 0) or 0)
    resolved = int(report.get("resolved_total", 0) or 0)
    with_images = int(evidence.get("with_images", 0) or 0)
    return {
        "inbox": inbox,
        "sent": sent,
        "junk": junk,
        "total_incoming": total_incoming,
        "spam_rate": round(junk / total_incoming * 100, 2) if total_incoming else 0.0,
        "report_total": report_total,
        "report_success": report_success,
        "send_success_rate": round(report_success / report_total * 100, 2) if report_total else 0.0,
        "replies": replies,
        "resolved": resolved,
        "response_rate": round(replies / report_success * 100, 2) if report_success else 0.0,
        "takedown_rate": round(resolved / report_success * 100, 2) if report_success else 0.0,
        "with_images": with_images,
        "evidence_rate": round(with_images / report_success * 100, 2) if report_success else 0.0,
    }

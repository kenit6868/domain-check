"""Read-only IMAP message counts for one local calendar day."""

from __future__ import annotations

import imaplib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

from provider_replies import _parse_imap_list_line
from phishing_toolkit import _runtime_path


_INTERNALDATE_RE = re.compile(rb'INTERNALDATE "([^"]+)"', re.I)
_KNOWN_SENT_FOLDERS = ("Sent", "Sent Items", "Sent Messages", "INBOX.Sent")
_KNOWN_JUNK_FOLDERS = ("Junk", "Spam", "Junk Email", "INBOX.Junk", "INBOX.Spam", "[Gmail]/Spam")
CACHE_PATH = _runtime_path("mail_statistics_cache.json")
CACHE_VERSION = 1
MODULE_VERSION = 6
JOB_DIR = _runtime_path("mail_statistics_jobs")
ACTIVE_JOB_STATES = {"queued", "running"}


def _atomic_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    finally:
        try: os.remove(temp_path)
        except FileNotFoundError: pass


def create_statistics_job(selected_day: date, accounts: list[dict]) -> str:
    """Persist a credential-free background job and return its job.json path."""
    job_id = f"{selected_day.isoformat()}_{uuid.uuid4().hex}"
    job_dir = os.path.join(JOB_DIR, job_id)
    job = {
        "version": 1, "job_id": job_id, "selected_day": selected_day.isoformat(),
        "accounts": [str(account.get("username") or "") for account in accounts],
        "created_at": datetime.now().astimezone().isoformat(),
    }
    _atomic_json(os.path.join(job_dir, "job.json"), job)
    _atomic_json(os.path.join(job_dir, "status.json"), {"state": "queued", "error": ""})
    return os.path.join(job_dir, "job.json")


def launch_statistics_job(job_path: str):
    """Launch a detached statistics worker without a visible Windows console."""
    job_path = os.path.abspath(job_path)
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--mail-statistics-job", job_path]
    else:
        command = [sys.executable, os.path.abspath(__file__), "--run-job", job_path]
    try:
        return subprocess.Popen(
            command, cwd=os.path.dirname(os.path.abspath(__file__)),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        _atomic_json(os.path.join(os.path.dirname(job_path), "status.json"), {
            "state": "failed", "error": str(exc), "completed_at": datetime.now().astimezone().isoformat(),
        })
        raise


def latest_statistics_job(selected_day: date) -> dict | None:
    """Return the newest persisted status for one local day."""
    if not os.path.isdir(JOB_DIR):
        return None
    candidates = []
    for name in os.listdir(JOB_DIR):
        job_path = os.path.join(JOB_DIR, name, "job.json")
        status_path = os.path.join(JOB_DIR, name, "status.json")
        try:
            with open(job_path, encoding="utf-8") as handle: job = json.load(handle)
            if job.get("selected_day") != selected_day.isoformat(): continue
            with open(status_path, encoding="utf-8") as handle: status = json.load(handle)
            candidates.append((os.path.getmtime(status_path), {**status, "job_id": job.get("job_id", name)}))
        except (OSError, ValueError, TypeError):
            continue
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _read_statistics_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as handle:
            value = json.load(handle)
        if value.get("version") != CACHE_VERSION or not isinstance(value.get("days"), dict):
            return {"version": CACHE_VERSION, "days": {}}
        return value
    except (OSError, ValueError, TypeError, AttributeError):
        return {"version": CACHE_VERSION, "days": {}}


def load_cached_statistics(selected_day: date) -> list[dict]:
    """Load one day's sanitized result rows; invalid cache entries are ignored."""
    record = _read_statistics_cache()["days"].get(selected_day.isoformat(), {})
    rows = record.get("results") if isinstance(record, dict) else None
    if not isinstance(rows, list):
        return []
    valid = []
    for row in rows:
        if not isinstance(row, dict) or row.get("status") not in {"ok", "error", "not_configured"}:
            continue
        try:
            valid.append({
                "account": str(row.get("account") or ""),
                "received": int(row.get("received", 0)),
                "sent": int(row.get("sent", 0)),
                "junk": int(row.get("junk", 0)),
                "status": row["status"],
                "error": str(row.get("error") or ""),
            })
        except (TypeError, ValueError):
            continue
    return valid


def save_cached_statistics(selected_day: date, results: list[dict]) -> None:
    """Atomically cache one day's non-secret result rows."""
    data = _read_statistics_cache()
    sanitized = [{
        "account": str(row.get("account") or ""),
        "received": int(row.get("received", 0)),
        "sent": int(row.get("sent", 0)),
        "junk": int(row.get("junk", 0)),
        "status": str(row.get("status") or "error"),
        "error": str(row.get("error") or ""),
    } for row in results]
    data["days"][selected_day.isoformat()] = {
        "updated_at": datetime.now().astimezone().isoformat(),
        "results": sanitized,
    }
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    temp_path = f"{CACHE_PATH}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, CACHE_PATH)
    finally:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass


def clear_cached_statistics(selected_day: date) -> bool:
    """Remove only the selected local day from the persistent cache."""
    data = _read_statistics_cache()
    removed = data["days"].pop(selected_day.isoformat(), None) is not None
    if not removed:
        return False
    if not data["days"]:
        try:
            os.remove(CACHE_PATH)
        except FileNotFoundError:
            pass
        return True
    temp_path = f"{CACHE_PATH}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, CACHE_PATH)
    finally:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
    return True


def _message_local_date(fetch_metadata, local_tz):
    """Return an IMAP INTERNALDATE converted to the requested local timezone."""
    raw = fetch_metadata[0] if isinstance(fetch_metadata, tuple) else fetch_metadata
    if not isinstance(raw, bytes):
        return None
    match = _INTERNALDATE_RE.search(raw)
    if not match:
        return None
    try:
        value = parsedate_to_datetime(match.group(1).decode("ascii"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=local_tz)
        return value.astimezone(local_tz).date()
    except (TypeError, ValueError, UnicodeDecodeError, OverflowError):
        return None


def _special_mailbox(conn, account, config_keys, special_flag, known_folders, fallback):
    configured = next((str(account.get(key) or "").strip() for key in config_keys if account.get(key)), "")
    if configured:
        return configured
    status, folders = conn.list()
    if status != "OK":
        raise RuntimeError("Không đọc được danh sách thư mục IMAP")
    parsed = [_parse_imap_list_line(line) for line in (folders or [])]
    flagged = next((mailbox for flags, _, mailbox in parsed if special_flag in flags.lower() and mailbox), "")
    if flagged:
        return flagged
    names = [mailbox for _, _, mailbox in parsed if mailbox]
    return next(
        (actual for actual in names for known in known_folders if actual.lower() == known.lower()),
        fallback,
    )


def _sent_mailbox(conn, account):
    return _special_mailbox(
        conn, account, ("imap_sent_mailbox",), "\\sent", _KNOWN_SENT_FOLDERS, "Sent",
    )


def _junk_mailbox(conn, account):
    return _special_mailbox(
        conn, account, ("imap_junk_mailbox", "imap_spam_mailbox"), "\\junk", _KNOWN_JUNK_FOLDERS, "Junk",
    )


def _quoted_mailbox(mailbox):
    """Quote mailbox names so Gmail paths containing spaces form one IMAP argument."""
    value = str(mailbox).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def _count_mailbox(conn, mailbox, selected_day, local_tz):
    status, _ = conn.select(_quoted_mailbox(mailbox), readonly=True)
    if status != "OK":
        raise RuntimeError(f"Không mở được thư mục {mailbox}")
    # Server-side SEARCH uses the IMAP server's calendar date. Widen it and
    # apply the exact local-day filter to INTERNALDATE below.
    since = (selected_day - timedelta(days=1)).strftime("%d-%b-%Y")
    before = (selected_day + timedelta(days=2)).strftime("%d-%b-%Y")
    status, data = conn.uid("search", None, "SINCE", since, "BEFORE", before)
    if status != "OK":
        raise RuntimeError(f"Không tìm được email trong thư mục {mailbox}")
    count = 0
    for uid in (data[0].split() if data and data[0] else []):
        status, payload = conn.uid("fetch", uid, "(INTERNALDATE)")
        if status != "OK" or not payload:
            continue
        # With metadata-only FETCH, real IMAP servers commonly return a plain
        # bytes item. A tuple is normally used only when a message literal/body
        # is included. Accept both response shapes.
        message_day = next((
            parsed for parsed in (
                _message_local_date(item, local_tz)
                for item in payload if isinstance(item, (bytes, tuple))
            ) if parsed is not None
        ), None)
        if message_day == selected_day:
            count += 1
    return count


def count_account_mail(account: dict, selected_day: date, local_tz, timeout: int = 30) -> dict:
    """Count Inbox, Sent, and Junk messages without changing flags."""
    host = account.get("imap_host")
    username = str(account.get("username") or "").strip()
    if not host or not username or not account.get("password"):
        raise ValueError("Tài khoản thiếu cấu hình IMAP")
    conn = imaplib.IMAP4_SSL(host, int(account.get("imap_port", 993)), timeout=timeout)
    try:
        conn.login(username, account["password"])
        sent_mailbox = _sent_mailbox(conn, account)
        junk_mailbox = _junk_mailbox(conn, account)
        received = _count_mailbox(
            conn, str(account.get("imap_mailbox") or "INBOX"), selected_day, local_tz,
        )
        sent = _count_mailbox(conn, sent_mailbox, selected_day, local_tz)
        junk = _count_mailbox(conn, junk_mailbox, selected_day, local_tz)
        return {
            "account": username, "received": received, "sent": sent, "junk": junk,
            "status": "ok", "error": "",
        }
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def count_account_incoming(account: dict, date_from: date, date_to: date, local_tz, timeout: int = 30) -> dict:
    """Count only Inbox and Junk over an inclusive local-date range."""
    host = account.get("imap_host")
    username = str(account.get("username") or "").strip()
    if not host or not username or not account.get("password"):
        raise ValueError("Tài khoản thiếu cấu hình IMAP")
    if date_from > date_to:
        raise ValueError("Từ ngày không được lớn hơn Đến ngày")
    conn = imaplib.IMAP4_SSL(host, int(account.get("imap_port", 993)), timeout=timeout)
    try:
        conn.login(username, account["password"])
        inbox_mailbox = str(account.get("imap_mailbox") or "INBOX")
        junk_mailbox = _junk_mailbox(conn, account)
        received = junk = 0
        current_day = date_from
        while current_day <= date_to:
            received += _count_mailbox(conn, inbox_mailbox, current_day, local_tz)
            junk += _count_mailbox(conn, junk_mailbox, current_day, local_tz)
            current_day += timedelta(days=1)
        return {
            "account": username, "received": received, "junk": junk,
            "status": "ok", "error": "", "inbox_mailbox": inbox_mailbox,
            "junk_mailbox": junk_mailbox,
        }
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def daily_mail_statistics(accounts: list[dict], selected_day: date, local_tz) -> list[dict]:
    """Return isolated per-account results so one broken mailbox cannot hide others."""
    results = []
    for account in accounts:
        username = str(account.get("username") or "Tài khoản chưa đặt tên")
        if not account.get("imap_host"):
            results.append({
                "account": username, "received": 0, "sent": 0, "junk": 0,
                "status": "not_configured", "error": "",
            })
            continue
        try:
            results.append(count_account_mail(account, selected_day, local_tz))
        except Exception as exc:
            results.append({
                "account": username, "received": 0, "sent": 0, "junk": 0,
                "status": "error", "error": str(exc),
            })
    return results


def run_statistics_job(job_path: str) -> None:
    """Execute one persisted job and checkpoint completion for the UI."""
    job_path = os.path.abspath(job_path)
    status_path = os.path.join(os.path.dirname(job_path), "status.json")
    try:
        with open(job_path, encoding="utf-8") as handle:
            job = json.load(handle)
        _atomic_json(status_path, {"state": "running", "error": "", "started_at": datetime.now().astimezone().isoformat()})
        from phishing_toolkit import load_config
        configured = list(load_config().get("smtp_accounts", []))
        requested = set(job.get("accounts") or [])
        accounts = [account for account in configured if str(account.get("username") or "") in requested]
        selected_day = date.fromisoformat(job["selected_day"])
        results = daily_mail_statistics(accounts, selected_day, datetime.now().astimezone().tzinfo)
        save_cached_statistics(selected_day, results)
        _atomic_json(status_path, {
            "state": "complete", "error": "", "completed_at": datetime.now().astimezone().isoformat(),
            "result_count": len(results),
        })
    except Exception as exc:
        _atomic_json(status_path, {
            "state": "failed", "error": str(exc), "completed_at": datetime.now().astimezone().isoformat(),
        })


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--run-job":
        run_statistics_job(sys.argv[2])
    else:
        raise SystemExit("Usage: mail_statistics.py --run-job <job.json>")

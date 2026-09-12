#!/usr/bin/env python3
"""Background worker: check domains in batches, generate drafts, then email reports."""

import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

import phishing_toolkit as pt
import browser_evidence
import cloaking_review_queue as review_queue
from domain_utils import extract_domains_from_text


WORKER_DIR = pt._runtime_path("worker_jobs")
CLOAKING_WORKER_DIR = pt._runtime_path("cloaking_send_jobs")
ACTIVE_JOB_STATES = {"prechecking", "running", "waiting"}
# Log riêng các domain đã được worker check nhưng KHÔNG tìm được email report nào để gửi
# (registrar chỉ nhận web form, chưa tra được abuse email...). Tách khỏi sent_log.csv vì
# đây không phải 1 lần gửi thành/thất bại — chỉ là "đã thử, không có gì để gửi". Dùng để
# Trang Domain Worker (nút Lọc domain) tự động bỏ qua, tránh dò lại abuse email vô ích
# trong cùng 1 ngày cho domain đã biết chắc không gửi được.
NO_EMAIL_LOG_PATH = pt._runtime_path("no_email_log.csv")
PRECHECK_CACHE_PATH = pt._runtime_path("domain_precheck_cache.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: str, data: dict):
    # Windows có thể khóa status.json trong khoảnh khắc Streamlit/antivirus đang
    # đọc file. Dùng temp name riêng và retry để một lock thoáng qua không làm
    # chết toàn bộ worker job.
    tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        last_error = None
        for attempt in range(30):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(min(0.05 * (attempt + 1), 0.5))
        raise last_error
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


@contextmanager
def _exclusive_file_lock(path: str, *, timeout: float = 15.0):
    """Cross-process advisory lock for small JSON state files on Windows/Unix."""
    lock_path = f"{path}.lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    handle = open(lock_path, "a+b")
    if os.path.getsize(lock_path) == 0:
        handle.write(b"0")
        handle.flush()
    deadline = time.monotonic() + max(0.1, timeout)
    locked = False
    try:
        while time.monotonic() < deadline:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except (BlockingIOError, OSError):
                time.sleep(0.05)
        if not locked:
            raise TimeoutError(f"Không lấy được lock state: {os.path.basename(path)}")
        yield
    finally:
        if locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return default


def launch_job_process(job_path: str):
    """Launch one persisted worker job without opening a visible console window."""
    job_path = os.path.abspath(job_path)
    stop_path = os.path.join(os.path.dirname(job_path), "stop.requested")
    try:
        os.remove(stop_path)
    except FileNotFoundError:
        pass
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--worker-job", job_path]
    else:
        command = [sys.executable, os.path.abspath(__file__), job_path]
    return subprocess.Popen(
        command,
        cwd=pt.BASE_DIR,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )


def is_cloaking_review_job_dir(job_dir: str) -> bool:
    """Identify new and legacy review-send jobs without relying on their parent folder."""
    try:
        with open(os.path.join(job_dir, "job.json"), encoding="utf-8") as job_file:
            job = json.load(job_file)
    except (OSError, ValueError, TypeError):
        return os.path.basename(os.path.abspath(job_dir)).startswith("review_")
    return bool(
        job.get("review_queue_ids")
        or job.get("review_decision")
        or str(job.get("job_id") or "").startswith("review_")
    )


def _find_active_job_dir(root: str, *, review_job: bool) -> str | None:
    if not os.path.isdir(root):
        return None
    candidates = []
    for name in os.listdir(root):
        job_dir = os.path.join(root, name)
        if not os.path.isdir(job_dir) or is_cloaking_review_job_dir(job_dir) != review_job:
            continue
        try:
            with open(os.path.join(job_dir, "status.json"), encoding="utf-8") as status_file:
                state = json.load(status_file).get("state")
        except (OSError, ValueError, TypeError):
            continue
        if state in ACTIVE_JOB_STATES:
            candidates.append(job_dir)
    return max(candidates, key=os.path.getmtime) if candidates else None


def find_active_job_dir() -> str | None:
    """Return only an active primary Domain Worker job (never a review-send job)."""
    return _find_active_job_dir(WORKER_DIR, review_job=False)


def find_active_cloaking_job_dir() -> str | None:
    """Find legacy review-send jobs for migration/diagnostics only."""
    candidates = []
    for root in dict.fromkeys([CLOAKING_WORKER_DIR, WORKER_DIR]):
        active = _find_active_job_dir(root, review_job=True)
        if active:
            candidates.append(active)
    return max(candidates, key=os.path.getmtime) if candidates else None


def create_cloaking_review_job(
    queue_ids: list[str], *, decision: str, allowed_accounts: list[str],
    batch_size: int = 5, interval_seconds: int = 0,
) -> str:
    """Create a legacy review job; the current UI must use direct preview/send."""
    if decision not in {"confirmed_cloaking", "not_cloaking"}:
        raise ValueError("Unsupported cloaking review decision")
    if not queue_ids:
        raise ValueError("Select at least one cloaking review item")
    if not allowed_accounts:
        raise ValueError("Select at least one SMTP account")
    items = []
    seen_targets = set()
    for queue_id in dict.fromkeys(queue_ids):
        item = review_queue.load_item(queue_id)
        if not item or item.get("state") not in review_queue.ACTIVE_STATES:
            raise ValueError(f"Review item is no longer selectable: {queue_id}")
        target = str(item.get("target_url") or "").strip()
        if not target:
            raise ValueError(f"Review item has no target URL: {queue_id}")
        if target in seen_targets:
            raise ValueError(f"Select only one pending record for duplicate URL: {target}")
        seen_targets.add(target)
        items.append(item)

    os.makedirs(CLOAKING_WORKER_DIR, exist_ok=True)
    job_id = "review_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    job_dir = os.path.join(CLOAKING_WORKER_DIR, job_id)
    os.makedirs(job_dir)
    targets = [item["target_url"] for item in items]
    queue_id_by_target = {item["target_url"]: item["queue_id"] for item in items}
    operator_evidence = {
        item["target_url"]: (item.get("result") or {}).get("cloaking_result", {}).get("operator_evidence")
        for item in items
        if (item.get("result") or {}).get("cloaking_result", {}).get("operator_evidence")
    }
    job = {
        "job_id": job_id,
        "created_at": _now(),
        "domains": targets,
        "batch_size": max(1, int(batch_size)),
        "interval_seconds": max(0, int(interval_seconds)),
        "include_vncert": False,
        "allowed_accounts": list(dict.fromkeys(allowed_accounts)),
        "force_precheck": False,
        "precheck_only": False,
        "preflight_version": 2,
        "retry_targets": targets,
        "approved_cloaking_targets": targets if decision == "confirmed_cloaking" else [],
        "force_normal_targets": targets if decision == "not_cloaking" else [],
        "operator_cloaking_evidence": operator_evidence,
        "review_queue_ids": queue_id_by_target,
        "review_decision": decision,
    }
    ready = []
    for item in items:
        prepared = dict(item.get("prepared") or {})
        prepared.setdefault("target_url", item["target_url"])
        prepared.setdefault("domain", item.get("domain") or pt.normalize_domain(item["target_url"]))
        prepared.setdefault("recipients", [])
        ready.append(prepared)
    _atomic_json(os.path.join(job_dir, "job.json"), job)
    _atomic_json(os.path.join(job_dir, "preflight.json"), {
        "version": 2, "ready": ready,
        "excluded_no_email": [], "excluded_already_sent": [],
    })
    review_queue.mark_selected_for_send(
        [item["queue_id"] for item in items],
        state=(
            review_queue.QUEUED_CLOAKING
            if decision == "confirmed_cloaking"
            else review_queue.QUEUED_NORMAL
        ),
        decision=decision, send_job_id=job_id,
        attempt_accounts=allowed_accounts,
    )
    return os.path.join(job_dir, "job.json")


def _append_event(path: str, event: dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": _now(), **event}, ensure_ascii=False) + "\n")


def _log_no_email(domain: str, target_url: str):
    """Ghi 1 dòng vào no_email_log.csv khi 1 domain check xong nhưng không có draft nào
    có email hợp lệ để gửi. Best-effort — lỗi ghi file không được phá job (giống log_sent)."""
    is_new = not os.path.exists(NO_EMAIL_LOG_PATH)
    try:
        with open(NO_EMAIL_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "domain", "target_url"])
            if is_new:
                writer.writeheader()
            writer.writerow({"timestamp": _now(), "domain": domain, "target_url": target_url})
    except OSError:
        pass


def _should_stop(stop_path: str) -> bool:
    return os.path.exists(stop_path)


def _load_precheck_cache(local_day: date | None = None, local_tz=None) -> dict[str, dict]:
    """Load reusable recipient prechecks from the current local day only."""
    local_tz = local_tz or datetime.now().astimezone().tzinfo
    local_day = local_day or datetime.now(local_tz).date()
    try:
        with open(PRECHECK_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {}
    valid = {}
    changed = False
    for domain, entry in entries.items():
        try:
            checked_at = datetime.fromisoformat(str(entry.get("checked_at", "")).replace("Z", "+00:00"))
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            if checked_at.astimezone(local_tz).date() == local_day:
                cleaned = dict(entry)
                if "manual_forms" in cleaned:
                    cleaned.pop("manual_forms", None)
                    changed = True
                    entries[domain] = cleaned
                recipients = cleaned.get("recipients") or []
                kept = [
                    item for item in recipients
                    if isinstance(item, dict)
                    and not pt.is_blocked_report_recipient(str(item.get("email") or ""))
                ]
                if len(kept) != len(recipients):
                    changed = True
                    cleaned["recipients"] = kept
                    entries[domain] = cleaned
                valid[domain] = cleaned
        except (AttributeError, TypeError, ValueError):
            continue
    if changed:
        _atomic_json(PRECHECK_CACHE_PATH, {"version": 2, "entries": entries})
    return valid


def _save_precheck_cache(entries: dict[str, dict]):
    """Persist the current in-memory daily cache atomically."""
    os.makedirs(os.path.dirname(PRECHECK_CACHE_PATH), exist_ok=True)
    _atomic_json(PRECHECK_CACHE_PATH, {"version": 2, "entries": entries})


def _interruptible_wait(seconds: int, stop_path: str, status_path: str, status: dict) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _should_stop(stop_path):
            return True
        status["next_batch_in_seconds"] = max(0, int(deadline - time.time()))
        _atomic_json(status_path, status)
        time.sleep(min(2, max(0.1, deadline - time.time())))
    status["next_batch_in_seconds"] = 0
    return False


def _successfully_reported_domain_accounts_today(
    local_day: date | None = None,
    local_tz=None,
) -> set[tuple[str, str]]:
    """Return successful ``(domain, sender account)`` pairs for the local day.

    Rows written before the account column was introduced are intentionally
    ignored: they cannot prove which sender already reported the domain.
    """
    reported = set()
    if not os.path.exists(pt.SENT_LOG_PATH):
        return reported
    local_tz = local_tz or datetime.now().astimezone().tzinfo
    local_day = local_day or datetime.now(local_tz).date()
    try:
        with pt.sent_log_lock():
            if not os.path.exists(pt.SENT_LOG_PATH):
                return reported
            with open(pt.SENT_LOG_PATH, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    success = str(row.get("success", "")).strip().lower()
                    domain = pt.normalize_domain(str(row.get("domain", "")).strip()).lower().rstrip(".")
                    account = str(row.get("account", "")).strip().lower()
                    timestamp = str(row.get("timestamp", "")).strip()
                    try:
                        sent_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        if sent_at.tzinfo is None:
                            sent_at = sent_at.replace(tzinfo=timezone.utc)
                        sent_day = sent_at.astimezone(local_tz).date()
                    except (TypeError, ValueError):
                        continue
                    if domain and account and sent_day == local_day and success in {"true", "1", "yes"}:
                        reported.add((domain, account))
    except (OSError, csv.Error):
        # Không chặn job nếu file log đang bị Excel khóa hoặc có một dòng lỗi.
        pass
    return reported


def _successfully_sent_deliveries_today(
    local_day: date | None = None,
    local_tz=None,
) -> set[tuple[str, str, str, str]]:
    """Return successful (domain, account, draft, recipient) deliveries for today."""
    deliveries = set()
    if not os.path.exists(pt.SENT_LOG_PATH):
        return deliveries
    local_tz = local_tz or datetime.now().astimezone().tzinfo
    local_day = local_day or datetime.now(local_tz).date()
    try:
        with pt.sent_log_lock():
            with open(pt.SENT_LOG_PATH, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if str(row.get("success", "")).strip().lower() not in {"true", "1", "yes"}:
                        continue
                    try:
                        sent_at = datetime.fromisoformat(
                            str(row.get("timestamp", "")).strip().replace("Z", "+00:00")
                        )
                        if sent_at.tzinfo is None:
                            sent_at = sent_at.replace(tzinfo=timezone.utc)
                        if sent_at.astimezone(local_tz).date() != local_day:
                            continue
                    except (TypeError, ValueError):
                        continue
                    domain = pt.normalize_domain(str(row.get("domain", ""))).lower().rstrip(".")
                    account = str(row.get("account", "")).strip().lower()
                    draft = str(row.get("draft_file", "")).strip().lower()
                    recipient = str(row.get("to", "")).strip().lower()
                    if domain and account and draft and recipient:
                        deliveries.add((domain, account, draft, recipient))
    except (OSError, csv.Error):
        pass
    return deliveries


def build_preflight_delivery_preview(
    ready_items: list[dict], account_names: list[str], *, include_vncert: bool = False,
) -> dict:
    """Estimate delivery scope from persisted preflight without touching SMTP.

    Draft generation still happens inside the worker, so this preview deliberately
    describes recipient/account routes rather than claiming an exact final body.
    """
    sent_deliveries = _successfully_sent_deliveries_today()
    rows = []
    for prepared in ready_items or []:
        domain = str(prepared.get("domain") or "").strip().lower().rstrip(".")
        target_url = str(prepared.get("target_url") or "").strip()
        if not domain or not target_url:
            continue
        for recipient in prepared.get("recipients") or []:
            if not isinstance(recipient, dict):
                continue
            channel = str(recipient.get("channel") or "").strip().lower()
            email = str(recipient.get("email") or "").strip().lower()
            if channel not in {"registrar", "registry", "hosting"} or not email:
                continue
            draft = f"{domain}_{channel}_report.txt"
            for account in account_names or []:
                account_name = str(account or "").strip().lower()
                if not account_name:
                    continue
                key = (domain, account_name, draft.lower(), email)
                rows.append({
                    "target_url": target_url,
                    "domain": domain,
                    "account": account_name,
                    "recipient": email,
                    "channel": channel,
                    "draft": draft,
                    "status": "already_sent" if key in sent_deliveries else "pending",
                })
    return {
        "rows": rows,
        "total": len(rows),
        "pending": sum(row["status"] == "pending" for row in rows),
        "already_sent": sum(row["status"] == "already_sent" for row in rows),
        "include_vncert": bool(include_vncert),
    }


def _delivery_error_code(stage: str, error: str) -> str:
    stage = str(stage or "").strip().lower()
    message = str(error or "").lower()
    if stage == "authenticate":
        return "SMTP_AUTH_FAILED"
    if stage in {"connect", "starttls"}:
        return "SMTP_CONNECTION_FAILED"
    if stage == "send" and any(token in message for token in ("recipient", "refused", "550", "553")):
        return "RECIPIENT_REJECTED"
    if stage == "send":
        return "SMTP_SEND_FAILED"
    return "DELIVERY_FAILED"


def _successfully_reported_domain_accounts() -> set[tuple[str, str]]:
    """Return every successful ``(domain, sender account)`` pair in the sent cache."""
    reported = set()
    if not os.path.exists(pt.SENT_LOG_PATH):
        return reported
    try:
        with pt.sent_log_lock():
            if not os.path.exists(pt.SENT_LOG_PATH):
                return reported
            with open(pt.SENT_LOG_PATH, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    success = str(row.get("success", "")).strip().lower()
                    domain = pt.normalize_domain(str(row.get("domain", "")).strip()).lower().rstrip(".")
                    account = str(row.get("account", "")).strip().lower()
                    if domain and account and success in {"true", "1", "yes"}:
                        reported.add((domain, account))
    except (OSError, csv.Error):
        pass
    return reported


def stop_job_process(job_dir: str) -> tuple[bool, str]:
    """Request a stop and terminate the dedicated worker process/process group."""
    status_path = os.path.join(job_dir, "status.json")
    stop_path = os.path.join(job_dir, "stop.requested")
    with open(stop_path, "w", encoding="utf-8") as f:
        f.write(_now())
    try:
        with open(status_path, encoding="utf-8") as f:
            status = json.load(f)
    except (OSError, ValueError):
        return False, "Không đọc được PID của worker; đã lưu yêu cầu dừng."
    pid = status.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False, "Worker chưa ghi PID; đã lưu yêu cầu dừng."
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                text=True, timeout=10, check=False,
            )
            if completed.returncode != 0:
                raise OSError((completed.stderr or completed.stdout).strip())
        else:
            os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Đã lưu yêu cầu dừng nhưng không thể kết thúc process: {exc}"
    status.update({
        "state": "stopped", "current_domain": None, "next_batch_in_seconds": 0,
        "finished_at": _now(), "stop_forced": True,
    })
    _atomic_json(status_path, status)
    _append_event(os.path.join(job_dir, "events.jsonl"), {
        "type": "job_force_stopped", "processed": status.get("processed", 0),
    })
    return True, "Đã dừng hẳn worker. Các email gửi thành công trước đó đã nằm trong cache."


def _send_domain_drafts(
    domain: str, drafts: list, cfg: dict, include_vncert: bool,
    events_path: str, stop_path: str | None = None, prepared_drafts: list | None = None,
    sent_deliveries: set[tuple[str, str, str, str]] | None = None,
    attachments: list[str] | None = None,
    require_browser_evidence: bool = False,
    target_url: str = "",
) -> tuple[dict, set[str], bool]:
    summary = {"drafts_total": len(drafts), "drafts_sendable": 0, "sent_ok": 0, "sent_failed": 0, "already_sent": 0, "sent_to": []}
    successful_accounts = set()
    sent_deliveries = sent_deliveries if sent_deliveries is not None else set()
    evidence_meta = pt.evidence_log_metadata(attachments)
    items = prepared_drafts if prepared_drafts is not None else [{"path": path} for path in drafts]
    for item in items:
        if stop_path and _should_stop(stop_path):
            return summary, successful_accounts, True
        path = item["path"]
        filename = os.path.basename(path)
        if not include_vncert and filename.endswith("_vncert_report.txt"):
            _append_event(events_path, {"type": "draft_skipped", "domain": domain, "draft": filename, "reason": "vncert_disabled"})
            continue
        parsed = item.get("parsed")
        if parsed is None:
            try:
                parsed = pt.parse_draft_email(path)
            except Exception as exc:
                summary["sent_failed"] += 1
                summary["sent_to"].append({
                    "to": "", "draft": filename, "account": "", "ok": False,
                    "status": "failed", "stage": "draft",
                    "error_code": "DRAFT_FAILED", "error": str(exc),
                })
                _append_event(events_path, {"type": "draft_error", "domain": domain, "draft": filename, "error": str(exc)})
                continue
        if not parsed.get("to"):
            _append_event(events_path, {"type": "draft_skipped", "domain": domain, "draft": filename, "reason": "no_email_recipient"})
            continue

        delivery_errors = pt.validate_report_delivery(
            parsed, target_url=target_url, attachments=attachments,
            require_browser_evidence=require_browser_evidence,
        )
        if delivery_errors:
            summary["sent_failed"] += 1
            error = "; ".join(delivery_errors)
            summary["sent_to"].append({
                "to": parsed.get("to") or "", "draft": filename,
                "account": "", "ok": False, "status": "failed",
                "stage": "validation", "error_code": "DRAFT_VALIDATION_FAILED",
                "error": error,
            })
            _append_event(events_path, {
                "type": "draft_error", "domain": domain, "draft": filename,
                "error": error,
            })
            continue

        summary["drafts_sendable"] += 1
        accounts = cfg.get("smtp_accounts") or []
        proxies = cfg.get("smtp_proxies") or []
        for index, account_cfg in enumerate(accounts):
            if stop_path and _should_stop(stop_path):
                return summary, successful_accounts, True
            proxy = proxies[index % len(proxies)] if proxies else None
            account_username = str(account_cfg.get("username") or "").strip().lower()
            delivery_key = (
                domain.lower().rstrip("."), account_username, filename.lower(),
                str(parsed["to"]).strip().lower(),
            )
            if delivery_key in sent_deliveries:
                summary["already_sent"] += 1
                summary["sent_to"].append({
                    "to": parsed["to"],
                    "draft": filename,
                    "account": account_username,
                    "ok": True,
                    "status": "already_sent",
                    "error": "",
                })
                _append_event(events_path, {
                    "type": "draft_skipped", "domain": domain, "draft": filename,
                    "to": parsed["to"], "account": account_username,
                    "reason": "already_sent_today",
                })
                continue
            send_args = (
                parsed["to"], parsed["subject"],
                pt.personalize_email_body(parsed["body"], cfg, account_cfg),
                account_cfg, proxy,
            )
            if attachments:
                result = pt.send_report_email_single(*send_args, attachments=attachments)
            else:
                result = pt.send_report_email_single(*send_args)
            ok = bool(result.get("success"))
            account = str(result.get("account") or "").strip()
            delivery_account = account or account_username
            if ok and delivery_account:
                successful_accounts.add(delivery_account.lower())
                sent_deliveries.add(delivery_key)
            summary["sent_ok" if ok else "sent_failed"] += 1
            # Track địa chỉ đã gửi để hiển thị trong UI
            summary["sent_to"].append({
                "to": parsed["to"],
                "draft": filename,
                "account": delivery_account,
                "ok": ok,
                "status": "sent" if ok else "failed",
                "stage": result.get("stage") or ("sent" if ok else ""),
                "error_code": "" if ok else _delivery_error_code(
                    result.get("stage") or "", result.get("error") or "",
                ),
                "error": result.get("error") or "",
            })
            row = {
                "timestamp": result.get("sent_at") or _now(),
                "domain": domain,
                "target_url": target_url,
                "draft_file": filename,
                "to": parsed["to"],
                "subject": parsed["subject"],
                "account": delivery_account,
                "success": ok,
                "error": result.get("error") or "",
                "message_id": result.get("message_id") or "",
                "delivery_kind": "report",
                "send_mode": "domain_worker",
                "report_channel": pt.report_channel_from_draft(filename, parsed["to"]),
                **evidence_meta,
            }
            try:
                pt.log_sent(row)
            except Exception as exc:
                _append_event(events_path, {"type": "log_error", "domain": domain, "draft": filename, "error": str(exc)})
            _append_event(events_path, {
                "type": "email_result", "domain": domain, "draft": filename,
                "to": parsed["to"], "account": delivery_account, "success": ok,
                "error": result.get("error") or "",
            })
    return summary, successful_accounts, False


def _precheck_drafts(domain: str, drafts: list, include_vncert: bool, events_path: str) -> list:
    """Parse drafts once and retain only drafts that can actually be emailed."""
    prepared = []
    for path in drafts:
        filename = os.path.basename(path)
        if not include_vncert and filename.endswith("_vncert_report.txt"):
            _append_event(events_path, {"type": "draft_skipped", "domain": domain, "draft": filename, "reason": "vncert_disabled"})
            continue
        try:
            parsed = pt.parse_draft_email(path)
        except Exception as exc:
            _append_event(events_path, {"type": "draft_error", "domain": domain, "draft": filename, "error": str(exc)})
            continue
        if not parsed.get("to"):
            _append_event(events_path, {"type": "draft_skipped", "domain": domain, "draft": filename, "reason": "no_email_recipient"})
            continue
        prepared.append({"path": path, "parsed": parsed})
    return prepared


def _sha256_file(path: str) -> str:
    """Return a stable fingerprint for a staged draft file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manual_evidence_draft_preview(
    domain: str, drafts: list, cfg: dict, include_vncert: bool,
    events_path: str, attachments: list[str], require_browser_evidence: bool,
    target_url: str,
) -> dict:
    """Build the exact normal-report delivery plan without touching SMTP.

    The returned ``delivery_plan`` contains the already-personalized body that
    the review page renders.  The send path verifies the draft file hashes and
    sends this same plan, so a preview cannot silently drift between review and
    confirmation.
    """
    os.makedirs(os.path.dirname(os.path.abspath(events_path)), exist_ok=True)
    prepared_drafts = []
    draft_errors = []
    for path in drafts or []:
        filename = os.path.basename(str(path))
        if not include_vncert and filename.endswith("_vncert_report.txt"):
            continue
        try:
            parsed = pt.parse_draft_email(path)
        except Exception as exc:
            draft_errors.append({"draft": filename, "error": str(exc)})
            continue
        if not parsed.get("to") or not parsed.get("subject"):
            draft_errors.append({"draft": filename, "error": "Draft không có recipient/Subject hợp lệ"})
            continue
        errors = pt.validate_report_delivery(
            parsed,
            target_url=target_url,
            attachments=attachments,
            require_browser_evidence=require_browser_evidence,
        )
        if errors:
            draft_errors.append({"draft": filename, "error": "; ".join(errors)})
            continue
        try:
            fingerprint = _sha256_file(path)
        except OSError as exc:
            draft_errors.append({"draft": filename, "error": f"Không đọc được draft: {exc}"})
            continue
        prepared_drafts.append({
            "path": os.path.abspath(path),
            "parsed": {
                "to": str(parsed.get("to") or "").strip(),
                "subject": str(parsed.get("subject") or "").strip(),
                "body": str(parsed.get("body") or ""),
            },
            "draft": filename,
            "sha256": fingerprint,
        })

    accounts = list(cfg.get("smtp_accounts") or [])
    sent_deliveries = _successfully_sent_deliveries_today()
    delivery_plan = []
    for account_cfg in accounts:
        account_name = str(account_cfg.get("username") or "").strip()
        if not account_name:
            continue
        for item in prepared_drafts:
            parsed = item["parsed"]
            delivery_key = (
                domain.lower().rstrip("."), account_name.lower(),
                str(item["draft"]).lower(), str(parsed["to"]).lower(),
            )
            personalized = pt.personalize_email_body(parsed["body"], cfg, account_cfg)
            delivery_plan.append({
                "account": account_name,
                "to": parsed["to"],
                "subject": parsed["subject"],
                "draft": item["draft"],
                "path": item["path"],
                "body": personalized,
                "draft_sha256": item["sha256"],
                "status": "already_sent" if delivery_key in sent_deliveries else "pending",
            })

    return {
        "version": 1,
        "prepared_at": _now(),
        "domain": domain,
        "target_url": target_url,
        "include_vncert": bool(include_vncert),
        "require_browser_evidence": bool(require_browser_evidence),
        "attachments": [os.path.abspath(str(path)) for path in attachments],
        "drafts_total": len(drafts or []),
        "drafts_sendable": len(prepared_drafts),
        "draft_errors": draft_errors,
        "prepared_drafts": prepared_drafts,
        "delivery_plan": delivery_plan,
    }


def _precheck_report_recipients(domain: str) -> list[dict]:
    """Resolve real email report channels without running the investigation pipeline."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        who_future = pool.submit(pt.get_whois_info, domain)
        rdap_future = pool.submit(pt.get_rdap_abuse_email, domain)
        registry_future = pool.submit(pt.lookup_registry_contact, domain)
        cert_future = pool.submit(pt.get_cert_info, domain)
        origin_future = pool.submit(pt.scan_common_subdomains, domain)
        who = who_future.result()
        rdap = rdap_future.result()
        registry = registry_future.result()
        try:
            cert = cert_future.result()
        except Exception:
            cert = {}
        try:
            origin_scan = origin_future.result()
        except Exception:
            origin_scan = {}

    recipients = []
    registrar = who.get("registrar") if isinstance(who, dict) else None
    emails = who.get("emails") if isinstance(who, dict) else None
    if isinstance(emails, str):
        emails = [emails]
    emails = [
        email for email in (emails or [])
        if email and not any(private in email.lower() for private in pt.WHOIS_PRIVACY_DOMAINS)
    ]
    if not registrar:
        registrar = rdap.get("registrar")
    registrar_uses_webform = bool(
        registrar and any(key in registrar.lower() for key in pt.WEB_FORM_REGISTRARS)
    )
    if not registrar_uses_webform:
        registrar_email = ", ".join(emails) if emails else rdap.get("abuse_email")
        if not registrar_email and registrar:
            registrar_email = pt.lookup_registrar_abuse_email(registrar)
        if registrar_email:
            recipients.append({"channel": "registrar", "email": registrar_email})

    if registry.get("source") == "static_table" and registry.get("abuse_email"):
        recipients.append({"channel": "registry", "email": registry["abuse_email"]})

    primary_ip = cert.get("ip") if isinstance(cert, dict) else None
    candidate_ips = sorted({ip for ip in origin_scan.values() if ip and ip != primary_ip})
    if candidate_ips:
        hosting = pt.get_ip_whois(candidate_ips[0])
        if not pt.is_cloudflare_proxy_contact(hosting) and hosting.get("abuse_email"):
            recipients.append({"channel": "hosting", "email": hosting["abuse_email"]})

    unique = []
    seen = set()
    for recipient in recipients:
        key = str(recipient.get("email") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(recipient)
    return unique


def _cloaking_coverage_gap(cloaking: dict) -> bool:
    return bool((cloaking.get("coverage") or {}).get("multi_vantage_recommended"))


def _cloaking_requires_browser(cloaking: dict) -> bool:
    return (
        str(cloaking.get("verdict") or "INCONCLUSIVE")
        in {"LIKELY", "POSSIBLE", "INCONCLUSIVE"}
        or _cloaking_coverage_gap(cloaking)
    )


def _cloaking_requires_review(cloaking: dict) -> bool:
    return (
        str(cloaking.get("verdict") or "INCONCLUSIVE")
        in {"LIKELY", "POSSIBLE", "INCONCLUSIVE"}
        or _cloaking_coverage_gap(cloaking)
    )


def _cloaking_review_reason(cloaking: dict) -> str:
    if _cloaking_coverage_gap(cloaking):
        return "coverage_gap"
    if str(cloaking.get("verdict") or "") == "LIKELY":
        return "confirmed_signal"
    return "detector_signal"


def _verify_cloaking_with_browser(target: str, cloaking: dict, cfg: dict) -> dict:
    """Run passive browser verification only for detector results that need it."""
    if not _cloaking_requires_browser(cloaking):
        return cloaking
    return pt.run_cloaking_browser_check(target, cloaking, cfg)


def _manual_review_domain_result(
    target: str, domain: str, cloaking: dict, *, duration_seconds: float,
    reputation=None, drafts_total: int = 0,
) -> dict:
    """Build the shared non-send result persisted in the Cloaking Review queue."""
    return {
        "target_url": target, "domain": domain, "success": False,
        "duration_seconds": round(duration_seconds, 1),
        "reputation": reputation,
        "drafts_total": drafts_total, "drafts_sendable": 0,
        "sent_ok": 0, "sent_failed": 0, "already_sent": 0, "sent_to": [],
        "skipped": "manual_review_required", "manual_review_required": True,
        "cloaking_verdict": cloaking.get("verdict", "INCONCLUSIVE"),
        "cloaking_score": cloaking.get("score", 0),
        "cloaking_signals": cloaking.get("signals") or [],
        "cloaking_evidence_path": cloaking.get("evidence_path", ""),
        "cloaking_review_reason": _cloaking_review_reason(cloaking),
        "cloaking_result": cloaking,
    }


def _precheck_cloaking(target: str, cfg: dict) -> dict:
    """Check one exact URL and collect browser evidence before the send phase."""
    cloaking = pt.run_cloaking_check(target, "full", cfg)
    return _verify_cloaking_with_browser(target, cloaking, cfg)


def _capture_worker_browser_evidence(target: str) -> dict:
    """Capture the strongest safe evidence available for a normal report.

    Domain Worker first tries the same no-click DOM-destination capture used by
    Check Domain.  If the page has no static HTTP(S) Register/Login target (or
    the destination cannot be opened), it falls back to one passive source-page
    capture.  A terminal source page is returned as terminal state so the
    caller can continue with a normal draft instead of asking the operator for
    a screenshot of a browser/DNS error page.
    """
    evidence_root = pt._runtime_path(os.path.join("evidence", "browser"))

    def _attempt_summary(value) -> dict:
        value = value if isinstance(value, dict) else {}
        return {
            "success": bool(value.get("success")),
            "terminal": bool(value.get("terminal")),
            "terminal_stage": str(value.get("terminal_stage") or ""),
            "evidence_type": str(value.get("evidence_type") or ""),
            "error": browser_evidence.redact_text(value.get("error", ""))[:500],
        }

    def _has_valid_artifacts(value) -> bool:
        try:
            return bool(browser_evidence.evidence_attachment_paths(value))
        except (OSError, ValueError, TypeError):
            return False

    try:
        dom_result = browser_evidence.capture_dom_destination_evidence(
            target, evidence_root,
            profile_name="domain_worker_dom_destination", headless=True,
        )
    except Exception as exc:
        dom_result = {
            "success": False, "terminal": False, "error": browser_evidence.redact_text(exc),
        }
    if _has_valid_artifacts(dom_result):
        result = dict(dom_result)
        result["capture_strategy"] = "dom_destination"
        result["fallback_reason"] = ""
        result["dom_destination_attempt"] = _attempt_summary(dom_result)
        return result

    try:
        passive_result = browser_evidence.capture_passive_browser_evidence(
            target, evidence_root,
            profile_name="domain_worker_passive", headless=True,
        )
    except Exception as exc:
        passive_result = {
            "success": False, "terminal": False, "error": browser_evidence.redact_text(exc),
        }
    if _has_valid_artifacts(passive_result):
        result = dict(passive_result)
        result["capture_strategy"] = "passive_fallback"
        result["fallback_reason"] = _attempt_summary(dom_result)["error"] or "DOM destination was not available"
        result["dom_destination_attempt"] = _attempt_summary(dom_result)
        return result

    # A terminal page is not content evidence and must not be sent to manual
    # review.  Prefer the passive terminal result because it represents the
    # source page the normal report was requested for.
    if isinstance(passive_result, dict) and passive_result.get("terminal"):
        result = dict(passive_result)
    elif isinstance(dom_result, dict) and dom_result.get("terminal"):
        result = dict(dom_result)
    else:
        result = dict(passive_result if isinstance(passive_result, dict) else dom_result)
    result["capture_strategy"] = "terminal" if result.get("terminal") else "failed"
    result["dom_destination_attempt"] = _attempt_summary(dom_result)
    result["passive_attempt"] = _attempt_summary(passive_result)
    return result


def _canonical_evidence_target(value: str) -> str:
    """Normalize a full URL for daily manual-evidence deduplication."""
    try:
        parsed = urlsplit(str(value or "").strip())
        if not parsed.scheme or not parsed.netloc:
            return str(value or "").strip().lower()
        return "|".join((
            parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query,
        ))
    except (TypeError, ValueError):
        return str(value or "").strip().lower()


def _local_day_from_timestamp(value, local_tz=None) -> str:
    local_tz = local_tz or datetime.now().astimezone().tzinfo
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return observed.astimezone(local_tz).date().isoformat()
    except (TypeError, ValueError):
        return ""


def _evidence_item_has_recipient(item: dict) -> bool:
    prepared = item.get("prepared") if isinstance(item.get("prepared"), dict) else item
    return any(
        str(recipient.get("email") or "").strip()
        for recipient in (prepared.get("recipients") or [])
        if isinstance(recipient, dict)
    )


def list_evidence_review_items(
    review_day: str | None = None, *, root: str | None = None,
) -> list[dict]:
    """List today's normal-report cases that still need browser evidence.

    Records are read from every primary worker job, not only the latest job.
    The latest occurrence of a canonical full URL wins: a newer ready record
    suppresses an older pending manual-evidence record, while duplicate pending
    records collapse to one item.  Returned ``job_dir`` metadata is transient
    UI routing state and is never written back to ``preflight.json``.
    """
    local_tz = datetime.now().astimezone().tzinfo
    day = str(review_day or datetime.now(local_tz).date().isoformat())
    root = os.path.abspath(root or WORKER_DIR)
    if not os.path.isdir(root):
        return []
    latest: dict[str, tuple[tuple[float, float], str, dict | None]] = {}
    for name in os.listdir(root):
        job_dir = os.path.join(root, name)
        if not os.path.isdir(job_dir) or is_cloaking_review_job_dir(job_dir):
            continue
        job = _read_json(os.path.join(job_dir, "job.json"), {})
        if not isinstance(job, dict):
            job = {}
        preflight_path = os.path.join(job_dir, "preflight.json")
        preflight = _read_json(preflight_path, {})
        try:
            preflight_version = int(preflight.get("version", 0) or 0) if isinstance(preflight, dict) else 0
        except (TypeError, ValueError):
            preflight_version = 0
        if not isinstance(preflight, dict) or preflight_version < 4:
            continue
        try:
            preflight_mtime = os.path.getmtime(preflight_path)
        except OSError:
            preflight_mtime = 0.0
        job_created = _local_day_from_timestamp(job.get("created_at"), local_tz)
        try:
            job_time = datetime.fromisoformat(
                str(job.get("created_at", "")).replace("Z", "+00:00"),
            ).timestamp()
        except (TypeError, ValueError, OSError):
            job_time = 0.0
        review_mtime_day = datetime.fromtimestamp(preflight_mtime, local_tz).date().isoformat() if preflight_mtime else ""
        default_item_day = review_mtime_day or job_created
        if default_item_day != day and not any(
            str(item.get("review_day") or "") == day
            for item in (preflight.get("evidence_review") or [])
            if isinstance(item, dict)
        ):
            continue

        def record(target: str, kind: str, item: dict | None = None) -> None:
            target = str(target or "").strip()
            if not target:
                return
            key = _canonical_evidence_target(target)
            candidate_day = str((item or {}).get("review_day") or default_item_day)
            if candidate_day != day:
                return
            sort_key = (preflight_mtime, job_time)
            current = latest.get(key)
            if current and current[0] > sort_key:
                return
            latest[key] = (sort_key, kind, item)

        for item in (preflight.get("ready") or []):
            if isinstance(item, dict):
                record(item.get("target_url"), "ready", item)
        completed = preflight.get("manual_evidence_completed") or {}
        completed_keys = {
            _canonical_evidence_target(key)
            for key in completed
            if str(key or "").strip()
        } if isinstance(completed, dict) else set()
        for item in (preflight.get("evidence_review") or []):
            if not isinstance(item, dict) or not _evidence_item_has_recipient(item):
                continue
            target = str(item.get("target_url") or "").strip()
            if target and (
                target in completed
                or _canonical_evidence_target(target) in completed_keys
            ):
                continue
            enriched = dict(item)
            enriched["job_dir"] = job_dir
            enriched["source_job_id"] = str(job.get("job_id") or name)
            enriched["allowed_accounts"] = list(job.get("allowed_accounts") or [])
            enriched["review_day"] = str(item.get("review_day") or default_item_day)
            record(target, "review", enriched)

    result = [value[2] for value in latest.values() if value[1] == "review" and value[2]]
    return sorted(
        result,
        key=lambda item: (
            str(item.get("domain") or "").lower(),
            str(item.get("target_url") or "").lower(),
        ),
    )


def _mark_manual_evidence_completed_for_duplicates(
    target_url: str, result: dict, *, source_job_dir: str = "",
) -> None:
    """Hide same-day duplicate pending records after one URL is sent.

    The review page intentionally deduplicates by canonical full URL, but two
    browser sessions can still hold stale rows from older jobs.  Marking those
    records completed keeps a successful send from resurfacing when the newer
    row is removed.  Errors here are best-effort housekeeping and never undo a
    successful SMTP delivery.
    """
    target_key = _canonical_evidence_target(target_url)
    if not target_key:
        return
    source_job_dir = os.path.abspath(source_job_dir) if source_job_dir else ""
    root = os.path.abspath(WORKER_DIR)
    if not os.path.isdir(root):
        return
    if source_job_dir:
        try:
            if os.path.normcase(os.path.commonpath([root, source_job_dir])) != os.path.normcase(root):
                # Unit callers may keep an isolated temporary job outside the
                # configured runtime tree; never scan or mutate real runtime
                # jobs in that case.
                return
        except ValueError:
            return
    try:
        job_names = os.listdir(root)
    except OSError:
        return
    completion = {
        "finished_at": _now(),
        "status": result.get("skipped") or "sent",
        "sent_ok": int(result.get("sent_ok", 0) or 0),
        "sent_failed": int(result.get("sent_failed", 0) or 0),
        "canonical_target": target_key,
    }
    for name in job_names:
        job_dir = os.path.join(root, name)
        if (
            not os.path.isdir(job_dir)
            or is_cloaking_review_job_dir(job_dir)
            or os.path.abspath(job_dir) == source_job_dir
        ):
            continue
        preflight_path = os.path.join(job_dir, "preflight.json")
        try:
            with _exclusive_file_lock(preflight_path):
                preflight = _read_json(preflight_path, {})
                if not isinstance(preflight, dict):
                    continue
                try:
                    version = int(preflight.get("version", 0) or 0)
                except (TypeError, ValueError):
                    version = 0
                if version < 4:
                    continue
                pending = preflight.get("evidence_review") or []
                matching = [
                    item for item in pending
                    if isinstance(item, dict)
                    and _canonical_evidence_target(item.get("target_url")) == target_key
                ]
                if not matching:
                    continue
                completed = preflight.get("manual_evidence_completed")
                if not isinstance(completed, dict):
                    completed = {}
                # Preserve the original spelling/path as the lookup key while
                # also recording canonical_target for future migrations.
                for item in matching:
                    original_target = str(item.get("target_url") or target_url)
                    completed[original_target] = dict(completion)
                preflight["manual_evidence_completed"] = completed
                preflight["evidence_review"] = [
                    item for item in pending
                    if not (
                        isinstance(item, dict)
                        and _canonical_evidence_target(item.get("target_url")) == target_key
                    )
                ]
                _atomic_json(preflight_path, preflight)
        except (OSError, ValueError, TypeError, TimeoutError):
            continue


def _write_preflight(
    path: str, *, version: int, ready: list[dict], excluded_no_email: list[dict],
    excluded_already_sent: list[dict], cloaking_review: list[dict], complete: bool,
    evidence_review: list[dict] | None = None,
) -> None:
    payload = {
        "version": version,
        "ready": ready,
        "excluded_no_email": excluded_no_email,
        "excluded_already_sent": excluded_already_sent,
    }
    if version >= 3:
        payload.update({
            "cloaking_review": cloaking_review,
            "complete": bool(complete),
        })
    if version >= 4:
        payload["evidence_review"] = evidence_review or []
        payload["manual_evidence_completed"] = {}
    with _exclusive_file_lock(path):
        existing = _read_json(path, {})
        completed = dict(existing.get("manual_evidence_completed") or {})
        completed_keys = {
            _canonical_evidence_target(key)
            for key in completed
            if str(key or "").strip()
        }
        existing_by_target = {
            str(item.get("target_url")): item
            for item in (existing.get("evidence_review") or [])
            if isinstance(item, dict) and item.get("target_url")
        }
        if version >= 4:
            merged_review = []
            for item in payload["evidence_review"]:
                item_target = str(item.get("target_url") or "")
                if item_target in completed or (
                    item_target and _canonical_evidence_target(item_target) in completed_keys
                ):
                    continue
                previous = existing_by_target.get(str(item.get("target_url")), {})
                merged = dict(item)
                for key in ("manual_send_in_progress", "browser_evidence", "last_send_result"):
                    if previous.get(key) and not merged.get(key):
                        merged[key] = previous[key]
                merged_review.append(merged)
            payload["evidence_review"] = merged_review
            payload["manual_evidence_completed"] = completed
        _atomic_json(path, payload)


def _run_prechecked_domain(
    prepared, cfg, selected_accounts, include_vncert, events_path, stop_path,
    sent_deliveries, approved_cloaking=False, operator_cloaking_evidence=None,
    force_normal_report=False, *, dry_run=False,
):
    """Run the full investigation once, then send after revalidating recipients.

    ``dry_run=True`` stages the same drafts/evidence and returns a delivery plan
    for Domain Evidence Review.  It never calls SMTP; the later send consumes
    the plan after checking its fingerprints.
    """
    target = prepared["target_url"]
    started = time.time()
    send_cfg = dict(cfg)
    send_cfg["smtp_accounts"] = selected_accounts
    result = pt.run_check(target, False, cfg)
    domain = result["domain"]
    cloaking = result.get("cloaking") or {"verdict": "NO_SIGNAL", "score": 0, "signals": []}
    if operator_cloaking_evidence and not force_normal_report:
        cloaking = pt.merge_operator_cloaking_evidence(cloaking, operator_cloaking_evidence)
        result["cloaking"] = cloaking
    cloaking_verdict = cloaking.get("verdict", "INCONCLUSIVE")
    if _cloaking_requires_browser(cloaking) and not operator_cloaking_evidence and not force_normal_report:
        cloaking = _verify_cloaking_with_browser(target, cloaking, cfg)
        result["cloaking"] = cloaking
        cloaking_verdict = cloaking.get("verdict", "INCONCLUSIVE")
        if cloaking_verdict in {"LIKELY", "POSSIBLE"}:
            refreshed = pt.append_cloaking_evidence_to_drafts(result.get("drafts") or [], cloaking)
            if len(refreshed) != len(result.get("drafts") or []):
                result["drafts_error"] = (
                    str(result.get("drafts_error") or "")
                    + "; Cloaking evidence could not be appended to every draft"
                ).strip("; ")
    elif (
        operator_cloaking_evidence
        and cloaking_verdict in {"LIKELY", "POSSIBLE"}
        and not force_normal_report
    ):
        refreshed = pt.append_cloaking_evidence_to_drafts(result.get("drafts") or [], cloaking)
        if len(refreshed) != len(result.get("drafts") or []):
            result["drafts_error"] = (
                str(result.get("drafts_error") or "")
                + "; Cloaking evidence could not be appended to every draft"
            ).strip("; ")
    evidence_failed = (
        cloaking_verdict == "LIKELY"
        and "cloaking evidence" in str(result.get("drafts_error") or "").lower()
    )
    needs_review = (
        not force_normal_report
        and (_cloaking_requires_review(cloaking) or evidence_failed)
    )
    if needs_review and not approved_cloaking:
        _append_event(events_path, {
            "type": "cloaking_manual_review", "domain": domain, "target_url": target,
            "verdict": cloaking_verdict, "score": cloaking.get("score", 0),
            "evidence_path": cloaking.get("evidence_path", ""),
        })
        return (
            _manual_review_domain_result(
                target, domain, cloaking,
                duration_seconds=time.time() - started,
                reputation=result.get("reputation", {}).get("verdict"),
                drafts_total=len(result.get("drafts") or []),
            ),
            set(), False,
        )
    require_general_evidence = bool(prepared.get("require_browser_evidence"))
    browser_capture = prepared.get("browser_evidence") or {}
    browser_attachments = (
        [] if approved_cloaking and not force_normal_report
        else browser_evidence.evidence_attachment_paths(browser_capture)
    )
    if (
        require_general_evidence and not browser_attachments
        and not (approved_cloaking and not force_normal_report)
    ):
        return ({
            "target_url": target, "domain": domain, "success": False,
            "duration_seconds": round(time.time() - started, 1),
            "reputation": result.get("reputation", {}).get("verdict"),
            "drafts_total": len(result.get("drafts") or []),
            "drafts_sendable": 0, "sent_ok": 0, "sent_failed": 0,
            "already_sent": 0, "sent_to": [],
            "skipped": "browser_evidence_required",
            "browser_evidence_error": prepared.get("browser_evidence_error") or "Missing valid Browser Evidence",
        }, set(), False)
    if not force_normal_report and cloaking_verdict not in {"LIKELY", "POSSIBLE"}:
        # ``pt.run_check`` may have staged a provisional cloaking block before
        # the browser/terminal pass downgraded the final verdict.  Normal
        # evidence review must never send that stale internal block.
        refreshed = pt.remove_cloaking_evidence_from_drafts(result.get("drafts") or [])
        if len(refreshed) != len(result.get("drafts") or []):
            result["drafts_error"] = (
                str(result.get("drafts_error") or "")
                + "; Cloaking evidence could not be removed from every draft"
            ).strip("; ")
    if browser_attachments:
        refreshed = pt.append_browser_evidence_to_drafts(
            result.get("drafts") or [], browser_capture,
        )
        if len(refreshed) != len(result.get("drafts") or []):
            raise ValueError("Browser Evidence could not be appended to every draft")
    if force_normal_report:
        refreshed = pt.remove_cloaking_evidence_from_drafts(result.get("drafts") or [])
        if len(refreshed) != len(result.get("drafts") or []):
            result["drafts_error"] = (
                str(result.get("drafts_error") or "")
                + "; Cloaking evidence could not be removed from every draft"
            ).strip("; ")
    evidence_path = str(cloaking.get("evidence_path") or "").strip()
    evidence_attachments = list(browser_attachments) + (
        [evidence_path]
        if not force_normal_report
        and cloaking_verdict in {"LIKELY", "POSSIBLE"}
        and os.path.isfile(evidence_path)
        else []
    )
    if not force_normal_report and cloaking_verdict in {"LIKELY", "POSSIBLE"}:
        evidence_attachments.extend(
            screenshot.get("path") for screenshot in cloaking.get("screenshots") or []
            if screenshot.get("path") and os.path.isfile(screenshot["path"])
        )
        evidence_attachments.extend(
            screenshot.get("path")
            for screenshot in (cloaking.get("operator_evidence") or {}).get("screenshots") or []
            if screenshot.get("path") and os.path.isfile(screenshot["path"])
        )
    require_delivery_evidence = (
        require_general_evidence
        and not (approved_cloaking and not force_normal_report)
    )
    delivery_target = target if require_general_evidence else ""
    manual_preview = None
    if dry_run:
        manual_preview = _manual_evidence_draft_preview(
            domain,
            result.get("drafts") or [],
            send_cfg,
            include_vncert,
            events_path,
            evidence_attachments,
            require_delivery_evidence,
            delivery_target,
        )
        mail = {
            "drafts_total": manual_preview["drafts_total"],
            "drafts_sendable": manual_preview["drafts_sendable"],
            "sent_ok": 0, "sent_failed": 0, "already_sent": sum(
                item.get("status") == "already_sent"
                for item in manual_preview["delivery_plan"]
            ),
            "sent_to": [
                {
                    "to": item["to"], "draft": item["draft"],
                    "account": item["account"], "ok": item["status"] == "already_sent",
                    "status": item["status"], "error": "",
                }
                for item in manual_preview["delivery_plan"]
                if item.get("status") == "already_sent"
            ],
        }
        successful_accounts = set()
        stopped_during_send = False
    else:
        mail, successful_accounts, stopped_during_send = _send_domain_drafts(
            domain, result.get("drafts") or [], send_cfg, include_vncert,
            events_path, stop_path, sent_deliveries=sent_deliveries,
            attachments=evidence_attachments,
            require_browser_evidence=require_delivery_evidence,
            target_url=delivery_target,
        )
    domain_result = {
        "target_url": target, "domain": domain, "success": True,
        "duration_seconds": round(time.time() - started, 1),
        "reputation": result.get("reputation", {}).get("verdict"),
        "cloaking_verdict": cloaking_verdict,
        "cloaking_score": cloaking.get("score", 0),
        "cloaking_signals": cloaking.get("signals") or [],
        "cloaking_evidence_path": cloaking.get("evidence_path", ""),
        "cloaking_approved": bool(approved_cloaking),
        "cloaking_disposition": "not_cloaking" if force_normal_report else (
            "confirmed_cloaking" if approved_cloaking else "automatic"
        ),
        "cloaking_result": cloaking,
        **mail,
    }
    if manual_preview is not None:
        # Kept in memory by the review page only.  It is removed before any
        # event/preflight persistence so draft bodies are not copied to state JSON.
        domain_result["manual_preview"] = {
            **manual_preview,
            "cloaking_result": cloaking,
            "cloaking_verdict": cloaking_verdict,
            "cloaking_score": cloaking.get("score", 0),
            "cloaking_signals": cloaking.get("signals") or [],
            "cloaking_evidence_path": cloaking.get("evidence_path", ""),
            "browser_evidence": browser_capture,
            "evidence_attachments": evidence_attachments,
        }
        domain_result["send_mode"] = "preview"
    if mail.get("drafts_sendable", 0) == 0 and not dry_run:
        domain_result["skipped"] = "no_sendable_email"
        _log_no_email(domain, target)
    elif mail.get("sent_ok", 0) == 0 and mail.get("sent_failed", 0) == 0 and mail.get("already_sent", 0):
        domain_result["skipped"] = "already_sent"
    return domain_result, successful_accounts, stopped_during_send


def _load_manual_evidence_context(job_dir: str, target_url: str) -> tuple[dict, dict]:
    """Load one v4 evidence-review item and its source job."""
    job_dir = os.path.abspath(job_dir)
    with open(os.path.join(job_dir, "job.json"), encoding="utf-8") as handle:
        job = json.load(handle)
    preflight = _read_json(os.path.join(job_dir, "preflight.json"), {})
    item = next(
        (
            dict(value) for value in (preflight.get("evidence_review") or [])
            if isinstance(value, dict) and value.get("target_url") == target_url
        ),
        None,
    )
    if not item:
        raise ValueError("Domain không còn trong danh sách cần ảnh thủ công")
    return job, item


def _filtered_manual_evidence_config(job: dict, allowed_accounts: list[str]) -> tuple[dict, list[dict]]:
    """Return a config limited to the accounts selected for one review case."""
    cfg = pt.load_config()
    allowed = {str(value).strip().lower() for value in allowed_accounts if str(value).strip()}
    job_allowed = {
        str(value).strip().lower()
        for value in (job.get("allowed_accounts") or [])
        if str(value).strip()
    }
    if job_allowed:
        allowed &= job_allowed
    selected = [
        account for account in (cfg.get("smtp_accounts") or [])
        if str(account.get("username") or "").strip().lower() in allowed
    ]
    cfg["smtp_accounts"] = selected
    return cfg, selected


def _manual_evidence_fingerprints(paths: list[str]) -> list[dict]:
    fingerprints = []
    for path in paths:
        absolute = os.path.abspath(str(path))
        try:
            fingerprints.append({
                "path": absolute,
                "size": os.path.getsize(absolute),
                "sha256": _sha256_file(absolute),
            })
        except OSError as exc:
            raise ValueError(f"Không đọc được attachment evidence: {exc}") from exc
    return fingerprints


def _validate_manual_evidence_preview(
    preview: dict, *, job_dir: str, target_url: str, evidence: dict,
    selected_accounts: list[str], cfg: dict,
) -> None:
    """Reject a stale/edited preview before any SMTP call."""
    if not isinstance(preview, dict) or int(preview.get("version", 0) or 0) != 1:
        raise ValueError("Draft preview không hợp lệ; hãy tạo lại preview.")
    if os.path.abspath(str(preview.get("job_dir") or "")) != os.path.abspath(job_dir):
        raise ValueError("Draft preview thuộc job khác; hãy tạo lại preview.")
    if str(preview.get("target_url") or "") != str(target_url):
        raise ValueError("Draft preview không còn khớp full URL hiện tại.")
    selected = list(dict.fromkeys(
        str(value).strip() for value in selected_accounts if str(value).strip()
    ))
    if list(preview.get("accounts") or []) != selected:
        raise ValueError("Danh sách tài khoản đã thay đổi; hãy tạo lại preview.")
    configured = {
        str(account.get("username") or "").strip(): account
        for account in (cfg.get("smtp_accounts") or [])
        if str(account.get("username") or "").strip()
    }
    if set(preview.get("accounts") or []) - set(configured):
        raise ValueError("Có tài khoản trong preview không còn trong cấu hình.")
    current_attachments = browser_evidence.evidence_attachment_paths(evidence or {})
    if current_attachments != list(preview.get("attachments") or []):
        raise ValueError("Evidence đã thay đổi; hãy tạo lại preview trước khi gửi.")
    current_fingerprints = _manual_evidence_fingerprints(current_attachments)
    if current_fingerprints != list(preview.get("attachment_fingerprints") or []):
        raise ValueError("Nội dung evidence đã thay đổi; hãy tạo lại preview trước khi gửi.")
    for item in preview.get("prepared_drafts") or []:
        path = os.path.abspath(str(item.get("path") or ""))
        if not path or not os.path.isfile(path):
            raise ValueError("Draft preview không còn file draft tương ứng.")
        try:
            current_hash = _sha256_file(path)
        except OSError as exc:
            raise ValueError(f"Không đọc được draft preview: {exc}") from exc
        if current_hash != str(item.get("sha256") or ""):
            raise ValueError("Nội dung draft đã thay đổi; hãy tạo lại preview.")
    plan_accounts = list(dict.fromkeys(
        str(item.get("account") or "").strip()
        for item in (preview.get("delivery_plan") or [])
        if str(item.get("account") or "").strip()
    ))
    if plan_accounts != selected:
        raise ValueError("Delivery plan không còn khớp tài khoản đã chọn; hãy tạo lại preview.")


def prepare_manual_evidence_preview(
    job_dir: str, target_url: str, evidence: dict, allowed_accounts: list[str],
) -> dict:
    """Prepare and return the exact normal-report draft plan without sending.

    The page keeps this result in Streamlit session state.  The result contains
    draft/attachment fingerprints; ``send_manual_evidence_item`` revalidates
    them and sends only the displayed body.
    """
    job_dir = os.path.abspath(job_dir)
    job, prepared = _load_manual_evidence_context(job_dir, target_url)
    attachments = browser_evidence.evidence_attachment_paths(evidence or {})
    if not attachments:
        raise ValueError("Browser Evidence thủ công không hợp lệ")
    cfg, selected = _filtered_manual_evidence_config(job, allowed_accounts)
    if not selected:
        raise ValueError("Không có tài khoản SMTP hợp lệ được chọn")
    prepared["browser_evidence"] = evidence
    prepared["require_browser_evidence"] = True
    events_path = os.path.join(job_dir, "events.jsonl")
    result, _accounts, stopped = _run_prechecked_domain(
        prepared, cfg, selected, bool(job.get("include_vncert", False)),
        events_path, None, set(), dry_run=True,
    )
    if stopped:
        raise ValueError("Đã dừng khi chuẩn bị draft preview")
    if result.get("skipped") == "manual_review_required":
        raise ValueError(
            "Lần check lại phát hiện tín hiệu cloaking; domain cần được duyệt tại Cloaking Review."
        )
    preview = dict(result.get("manual_preview") or {})
    if not preview or not preview.get("drafts_sendable"):
        error = str(result.get("drafts_error") or "Không có draft email hợp lệ để gửi")
        raise ValueError(error)
    preview.update({
        "job_dir": job_dir,
        "accounts": [str(account.get("username") or "").strip() for account in selected],
        "attachment_fingerprints": _manual_evidence_fingerprints(attachments),
        "evidence": evidence,
        "source_job_id": str(job.get("job_id") or os.path.basename(job_dir)),
    })
    return preview


def manual_evidence_preview_is_current(
    preview: dict | None, *, job_dir: str, target_url: str,
    evidence: dict, selected_accounts: list[str],
) -> bool:
    """Return whether a review-page preview still matches current files/state."""
    try:
        job, _item = _load_manual_evidence_context(job_dir, target_url)
        cfg, selected = _filtered_manual_evidence_config(job, selected_accounts)
        _validate_manual_evidence_preview(
            preview or {},
            job_dir=os.path.abspath(job_dir),
            target_url=target_url,
            evidence=evidence,
            selected_accounts=[
                str(account.get("username") or "").strip()
                for account in selected
            ],
            cfg=cfg,
        )
        return True
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def _send_manual_preview_deliveries(
    preview: dict, cfg: dict, events_path: str,
    sent_deliveries: set[tuple[str, str, str, str]],
) -> tuple[dict, set[str], bool]:
    """Send exactly the delivery plan generated by ``prepare_*_preview``."""
    domain = str(preview.get("domain") or "")
    attachments = list(preview.get("attachments") or [])
    target_url = str(preview.get("target_url") or "")
    require_browser_evidence = bool(preview.get("require_browser_evidence"))
    summary = {
        "drafts_total": int(preview.get("drafts_total", 0) or 0),
        "drafts_sendable": int(preview.get("drafts_sendable", 0) or 0),
        "sent_ok": 0, "sent_failed": 0, "already_sent": 0, "sent_to": [],
    }
    successful_accounts = set()
    account_map = {
        str(account.get("username") or "").strip(): account
        for account in (cfg.get("smtp_accounts") or [])
        if str(account.get("username") or "").strip()
    }
    proxies = cfg.get("smtp_proxies") or []
    account_order = list(account_map)
    prepared_by_path = {
        os.path.abspath(str(item.get("path") or "")): item
        for item in (preview.get("prepared_drafts") or [])
    }
    for delivery in preview.get("delivery_plan") or []:
        account_name = str(delivery.get("account") or "").strip()
        path = os.path.abspath(str(delivery.get("path") or ""))
        filename = str(delivery.get("draft") or os.path.basename(path))
        recipient = str(delivery.get("to") or "").strip()
        if account_name not in account_map or path not in prepared_by_path:
            summary["sent_failed"] += 1
            summary["sent_to"].append({
                "to": recipient, "draft": filename, "account": account_name,
                "ok": False, "status": "failed", "error": "Delivery preview không hợp lệ",
            })
            continue
        if target_url and not recipient:
            summary["sent_failed"] += 1
            continue
        parsed = prepared_by_path[path].get("parsed") or {}
        errors = pt.validate_report_delivery(
            parsed, target_url=target_url, attachments=attachments,
            require_browser_evidence=require_browser_evidence,
        )
        if errors:
            error = "; ".join(errors)
            summary["sent_failed"] += 1
            summary["sent_to"].append({
                "to": recipient, "draft": filename, "account": account_name,
                "ok": False, "status": "failed", "error": error,
            })
            _append_event(events_path, {
                "type": "draft_error", "domain": domain, "draft": filename,
                "to": recipient, "account": account_name, "error": error,
            })
            continue
        delivery_key = (domain.lower().rstrip("."), account_name.lower(), filename.lower(), recipient.lower())
        if delivery_key in sent_deliveries:
            summary["already_sent"] += 1
            summary["sent_to"].append({
                "to": recipient, "draft": filename, "account": account_name,
                "ok": True, "status": "already_sent", "error": "",
            })
            continue
        account_cfg = account_map[account_name]
        account_index = account_order.index(account_name)
        proxy = proxies[account_index % len(proxies)] if proxies else None
        result = pt.send_report_email_single(
            recipient,
            str(delivery.get("subject") or parsed.get("subject") or ""),
            str(delivery.get("body") or ""),
            account_cfg,
            proxy,
            attachments=attachments or None,
        )
        ok = bool(result.get("success"))
        delivery_account = str(result.get("account") or account_name).strip()
        if ok:
            successful_accounts.add(delivery_account.lower())
            sent_deliveries.add(delivery_key)
        summary["sent_ok" if ok else "sent_failed"] += 1
        row = {
            "to": recipient, "draft": filename, "account": delivery_account,
            "ok": ok, "status": "sent" if ok else "failed",
            "error": str(result.get("error") or ""),
        }
        summary["sent_to"].append(row)
        try:
            pt.log_sent({
                "timestamp": result.get("sent_at") or _now(), "domain": domain,
                "target_url": target_url, "draft_file": filename,
                "to": recipient, "subject": delivery.get("subject") or "",
                "account": delivery_account, "success": ok, "error": row["error"],
                "message_id": result.get("message_id") or "",
                "delivery_kind": "report", "send_mode": "domain_evidence_review",
                "report_channel": pt.report_channel_from_draft(filename, recipient),
                **pt.evidence_log_metadata(attachments, source_hint="manual_upload"),
            })
        except Exception as exc:
            _append_event(events_path, {
                "type": "log_error", "domain": domain, "draft": filename,
                "account": delivery_account, "error": str(exc),
            })
        _append_event(events_path, {
            "type": "email_result", "domain": domain, "draft": filename,
            "to": recipient, "account": delivery_account, "success": ok,
            "error": row["error"],
        })
    return summary, successful_accounts, False


def send_manual_evidence_item(
    job_dir: str, target_url: str, evidence: dict, allowed_accounts: list[str],
    preview: dict | None = None,
) -> dict:
    """Send one preflight-isolated item directly after operator evidence upload."""
    job_dir = os.path.abspath(job_dir)
    preflight_path = os.path.join(job_dir, "preflight.json")
    status_path = os.path.join(job_dir, "status.json")
    events_path = os.path.join(job_dir, "events.jsonl")
    with open(os.path.join(job_dir, "job.json"), encoding="utf-8") as handle:
        job = json.load(handle)
    with _exclusive_file_lock(preflight_path):
        preflight = _read_json(preflight_path, {})
        items = preflight.get("evidence_review") or []
        prepared = next(
            (dict(item) for item in items if item.get("target_url") == target_url), None,
        )
        if not prepared:
            raise ValueError("Domain không còn trong danh sách cần ảnh thủ công")
        if prepared.get("manual_send_in_progress"):
            claimed_at = str(prepared.get("manual_send_claimed_at") or "")
            try:
                claimed_dt = datetime.fromisoformat(claimed_at.replace("Z", "+00:00"))
                if claimed_dt.tzinfo is None:
                    claimed_dt = claimed_dt.replace(tzinfo=timezone.utc)
                claim_age = (datetime.now(timezone.utc) - claimed_dt.astimezone(timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                claim_age = 0
            if claim_age < 30 * 60:
                raise ValueError("Domain này đang được gửi ở một phiên khác")
        prepared["manual_send_in_progress"] = True
        prepared["manual_send_claimed_at"] = _now()
        preflight["evidence_review"] = [
            (prepared if item.get("target_url") == target_url else item)
            for item in items
        ]
        _atomic_json(preflight_path, preflight)
    def release_claim() -> None:
        with _exclusive_file_lock(preflight_path):
            current = _read_json(preflight_path, {})
            current_items = current.get("evidence_review") or []
            current["evidence_review"] = [
                ({**item, "manual_send_in_progress": False, "manual_send_claimed_at": ""}
                 if item.get("target_url") == target_url else item)
                for item in current_items
            ]
            _atomic_json(preflight_path, current)

    attachments = browser_evidence.evidence_attachment_paths(evidence)
    if not attachments:
        release_claim()
        raise ValueError("Browser Evidence thủ công không hợp lệ")
    prepared["browser_evidence"] = evidence
    prepared["require_browser_evidence"] = True

    cfg, selected_accounts = _filtered_manual_evidence_config(job, allowed_accounts)
    if not selected_accounts:
        release_claim()
        raise ValueError("Không có tài khoản SMTP hợp lệ được chọn")
    try:
        if preview is not None:
            _validate_manual_evidence_preview(
                preview,
                job_dir=job_dir,
                target_url=target_url,
                evidence=evidence,
                selected_accounts=[
                    str(account.get("username") or "").strip()
                    for account in selected_accounts
                ],
                cfg=cfg,
            )
            preview = dict(preview)
            preview["evidence"] = evidence
            mail, _accounts, _stopped = _send_manual_preview_deliveries(
                preview, cfg, events_path, _successfully_sent_deliveries_today(),
            )
            result = {
                "target_url": target_url,
                "domain": preview.get("domain") or prepared.get("domain") or pt.normalize_domain(target_url),
                "success": mail.get("sent_failed", 0) == 0 and bool(
                    mail.get("sent_ok", 0) or mail.get("already_sent", 0)
                ),
                "duration_seconds": 0.0,
                "reputation": None,
                "cloaking_verdict": preview.get("cloaking_verdict", "NO_SIGNAL"),
                "cloaking_score": preview.get("cloaking_score", 0),
                "cloaking_signals": preview.get("cloaking_signals") or [],
                "cloaking_evidence_path": preview.get("cloaking_evidence_path", ""),
                "cloaking_approved": False,
                "cloaking_disposition": "automatic",
                "cloaking_result": preview.get("cloaking_result") or {},
                "send_mode": "preview",
                **mail,
            }
            if (
                mail.get("sent_ok", 0) == 0
                and mail.get("sent_failed", 0) == 0
                and mail.get("already_sent", 0)
            ):
                result["skipped"] = "already_sent"
        else:
            result, _accounts, _stopped = _run_prechecked_domain(
                prepared, cfg, selected_accounts, bool(job.get("include_vncert", False)),
                events_path, None, _successfully_sent_deliveries_today(),
            )
    except Exception:
        release_claim()
        raise
    if result.get("skipped") == "manual_review_required":
        review_queue.enqueue_worker_result(
            job=job, job_dir=job_dir, prepared=prepared, domain_result=result,
        )

    terminal = bool(
        result.get("skipped") in {"manual_review_required", "already_sent", "no_sendable_email"}
        or (int(result.get("sent_ok", 0) or 0) > 0 and not int(result.get("sent_failed", 0) or 0))
    )
    with _exclusive_file_lock(preflight_path):
        preflight = _read_json(preflight_path, {})
        current_items = preflight.get("evidence_review") or []
        if terminal:
            preflight["evidence_review"] = [
                item for item in current_items if item.get("target_url") != target_url
            ]
            completed = dict(preflight.get("manual_evidence_completed") or {})
            completed[target_url] = {
                "finished_at": _now(), "status": result.get("skipped") or "sent",
                "sent_ok": int(result.get("sent_ok", 0) or 0),
                "sent_failed": int(result.get("sent_failed", 0) or 0),
            }
            preflight["manual_evidence_completed"] = completed
        else:
            preflight["evidence_review"] = [
                ({
                    **item, "browser_evidence": evidence,
                    "last_send_result": result,
                    "manual_send_in_progress": False,
                    "manual_send_claimed_at": "",
                } if item.get("target_url") == target_url else item)
                for item in current_items
            ]
        _atomic_json(preflight_path, preflight)
    if terminal:
        _mark_manual_evidence_completed_for_duplicates(
            target_url, result, source_job_dir=job_dir,
        )
    _append_event(events_path, {"type": "manual_evidence_send_finished", **result})
    return result


def run_job(job_path: str):
    job_path = os.path.abspath(job_path)
    job_dir = os.path.dirname(job_path)
    status_path = os.path.join(job_dir, "status.json")
    events_path = os.path.join(job_dir, "events.jsonl")
    stop_path = os.path.join(job_dir, "stop.requested")
    preflight_path = os.path.join(job_dir, "preflight.json")

    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)

    targets = job["domains"]
    batch_size = max(1, int(job.get("batch_size", 5)))
    interval_seconds = max(0, int(job.get("interval_seconds", 300)))
    include_vncert = bool(job.get("include_vncert", False))
    precheck_only = bool(job.get("precheck_only", False))
    force_precheck = bool(job.get("force_precheck", False))
    allowed_accounts = job.get("allowed_accounts")  # list of usernames, or None = all
    approved_cloaking_targets = set(job.get("approved_cloaking_targets") or [])
    force_normal_targets = set(job.get("force_normal_targets") or [])
    review_queue_ids = dict(job.get("review_queue_ids") or {})
    retry_targets_config = job.get("retry_targets")
    retry_targets = (
        set(retry_targets_config)
        if isinstance(retry_targets_config, list)
        else None
    )
    operator_cloaking_evidence = job.get("operator_cloaking_evidence") or {}
    cfg = pt.load_config()

    # Filter smtp_accounts to only those selected in the UI (if specified)
    if allowed_accounts is not None:
        allowed_lower = {a.lower() for a in allowed_accounts}
        cfg["smtp_accounts"] = [
            acc for acc in (cfg.get("smtp_accounts") or [])
            if str(acc.get("username", "")).strip().lower() in allowed_lower
        ]
    attempted_account_names = [
        str(account.get("username") or "").strip().lower()
        for account in (cfg.get("smtp_accounts") or [])
        if str(account.get("username") or "").strip()
    ]
    # Dedup CHỈ theo ngày hiện tại (không phải all-time): domain đã gửi ở các ngày
    # trước vẫn được coi là "chưa gửi" cho hôm nay, vì mỗi ngày cho phép report lại.
    sent_deliveries = _successfully_sent_deliveries_today()
    precheck_cache = _load_precheck_cache()
    previous_results = []
    if os.path.exists(status_path):
        try:
            with open(status_path, encoding="utf-8") as f:
                previous_status = json.load(f)
            if previous_status.get("job_id") == job.get("job_id"):
                previous_results = previous_status.get("results") or []
        except (OSError, ValueError, TypeError):
            pass
    # A previous result is complete only when no delivery still needs retrying.
    # In particular, keep fully and partially failed domains in ``ready``: the
    # delivery cache below will skip accounts/recipients that already succeeded.
    completed_targets = {
        item.get("target_url")
        for item in previous_results
        if item.get("target_url")
        and not item.get("error")
        and not int(item.get("sent_failed", 0) or 0)
        and (
            int(item.get("sent_ok", 0) or 0) > 0
            # Backward compatibility for results written before delivery
            # counters were added.
            or item.get("success") is True
            or item.get("skipped") in ("already_sent", "no_sendable_email")
            or (
                item.get("skipped") == "manual_review_required"
                and item.get("target_url") not in (retry_targets or set())
            )
        )
    }
    status = {
        "job_id": job.get("job_id"), "state": "prechecking", "pid": os.getpid(),
        "started_at": _now(), "finished_at": None, "total": len(targets), "processed": 0,
        "precheck_total": len(targets), "precheck_processed": 0, "ready_total": 0,
        "precheck_cached": 0, "cloaking_review_total": 0,
        "evidence_review_total": 0,
        "current_domain": None, "current_stage": "starting", "current_batch": 0, "total_batches": 0,
        "next_batch_in_seconds": 0, "results": previous_results, "excluded_no_email": [],
        "excluded_already_sent": [], "error": None,
    }
    _atomic_json(status_path, status)
    _append_event(events_path, {"type": "job_started", "total": len(targets), "batch_size": batch_size})

    try:
        requested_preflight_version = max(2, int(job.get("preflight_version", 2) or 2))
        preflight = {}
        if os.path.exists(preflight_path):
            try:
                with open(preflight_path, encoding="utf-8") as preflight_file:
                    preflight = json.load(preflight_file)
            except (OSError, ValueError, TypeError):
                preflight = {}
        persisted_version = int(preflight.get("version", 0) or 0)
        reusable_preflight = (
            persisted_version == requested_preflight_version
            and (persisted_version == 2 or bool(preflight.get("complete")))
        )
        if reusable_preflight:
            ready = preflight.get("ready") or []
            cloaking_review_items = preflight.get("cloaking_review") or []
            evidence_review_items = preflight.get("evidence_review") or []
            # Resume cùng job: chỉ giữ các domain chưa có kết quả. Domain đang
            # xử lý lúc bị dừng chưa được append nên vẫn được chạy lại an toàn.
            ready = [item for item in ready if item.get("target_url") not in completed_targets]
            status["excluded_no_email"] = preflight.get("excluded_no_email") or []
            status["excluded_already_sent"] = preflight.get("excluded_already_sent") or []
            status["precheck_processed"] = len(targets)
            status["cloaking_review_total"] = len(cloaking_review_items)
            status["evidence_review_total"] = len(evidence_review_items)
        else:
            ready = []
            cloaking_review_items = []
            evidence_review_items = []
            configured_accounts = cfg.get("smtp_accounts") or []
            detect_cloaking_early = requested_preflight_version >= 3
            _write_preflight(
                preflight_path, version=requested_preflight_version, ready=ready,
                excluded_no_email=status["excluded_no_email"],
                excluded_already_sent=status["excluded_already_sent"],
                cloaking_review=cloaking_review_items, complete=False,
                evidence_review=evidence_review_items,
            )
            for target in targets:
                if _should_stop(stop_path):
                    status["state"] = "stopped"
                    break
                if target in completed_targets:
                    status["precheck_processed"] += 1
                    status["processed"] = status["precheck_processed"]
                    _atomic_json(status_path, status)
                    continue
                status["current_domain"] = target
                status["current_stage"] = "recipient_and_cloaking"
                _atomic_json(status_path, status)
                _append_event(events_path, {"type": "precheck_started", "target_url": target})
                started = time.time()
                target_domain = pt.normalize_domain(target).lower().rstrip(".")
                recipients = []
                recipient_error = None
                cloaking = {
                    "target_url": target, "verdict": "NO_SIGNAL", "score": 0,
                    "signals": [], "coverage": {"multi_vantage_recommended": False},
                }
                cached_precheck = (
                    None if force_precheck else precheck_cache.get(target_domain)
                ) if configured_accounts else None
                if cached_precheck is not None:
                    recipients = cached_precheck.get("recipients") or []
                    status["precheck_cached"] += 1
                    _append_event(events_path, {
                        "type": "precheck_cache_hit", "target_url": target,
                        "domain": target_domain,
                    })

                recipient_future = None
                cloaking_future = None
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    if configured_accounts and cached_precheck is None:
                        recipient_future = pool.submit(
                            _precheck_report_recipients, target_domain,
                        )
                    if detect_cloaking_early:
                        cloaking_future = pool.submit(_precheck_cloaking, target, cfg)
                    if recipient_future is not None:
                        try:
                            recipients = recipient_future.result()
                            precheck_cache[target_domain] = {
                                "checked_at": _now(), "recipients": recipients,
                            }
                            _save_precheck_cache(precheck_cache)
                        except Exception as exc:
                            recipient_error = str(exc)
                    if cloaking_future is not None:
                        try:
                            cloaking = cloaking_future.result()
                        except Exception as exc:
                            cloaking = {
                                "target_url": target, "verdict": "INCONCLUSIVE",
                                "score": 0, "signals": [], "profiles": {},
                                "coverage": {"multi_vantage_recommended": False},
                                "manual_review_required": True, "evidence_path": "",
                                "error": str(exc),
                            }
                            _append_event(events_path, {
                                "type": "cloaking_precheck_error",
                                "target_url": target, "error": str(exc),
                            })

                recipients = [
                    {**recipient, "email": str(recipient.get("email") or "").strip()}
                    for recipient in recipients
                    if isinstance(recipient, dict)
                    and str(recipient.get("email") or "").strip()
                ]
                prepared = {
                    "target_url": target, "domain": target_domain,
                    "recipients": recipients,
                    "precheck_duration_seconds": round(time.time() - started, 1),
                    "cloaking_verdict": cloaking.get("verdict", "INCONCLUSIVE"),
                    "cloaking_score": cloaking.get("score", 0),
                }
                requires_cloaking_review = (
                    detect_cloaking_early and _cloaking_requires_review(cloaking)
                )
                if recipient_error:
                    status["excluded_no_email"].append({
                        "target_url": target, "domain": target_domain,
                        "status": "precheck_error", "error": recipient_error,
                    })
                    _append_event(events_path, {
                        "type": "precheck_error", "target_url": target,
                        "error": recipient_error,
                    })
                elif not recipients and configured_accounts:
                    excluded = {
                        "target_url": target, "domain": target_domain,
                        "status": "no_sendable_email",
                        "cloaking_verdict": cloaking.get("verdict", "INCONCLUSIVE"),
                        "cloaking_score": cloaking.get("score", 0),
                        "cloaking_review_skipped": bool(requires_cloaking_review),
                    }
                    status["excluded_no_email"].append(excluded)
                    _log_no_email(target_domain, target)
                    _append_event(events_path, {
                        "type": (
                            "cloaking_precheck_not_queued_no_email"
                            if requires_cloaking_review
                            else "precheck_no_sendable_email"
                        ),
                        "target_url": target, "domain": target_domain,
                        "verdict": cloaking.get("verdict", "INCONCLUSIVE"),
                        "score": cloaking.get("score", 0),
                    })
                elif requires_cloaking_review:
                    domain_result = _manual_review_domain_result(
                        target, target_domain, cloaking,
                        duration_seconds=time.time() - started,
                    )
                    queue_error = ""
                    queue_item = {}
                    try:
                        queue_item = review_queue.enqueue_worker_result(
                            job=job, job_dir=job_dir, prepared=prepared,
                            domain_result=domain_result,
                        )
                    except Exception as exc:
                        queue_error = str(exc)
                        _append_event(events_path, {
                            "type": "cloaking_review_queue_error",
                            "target_url": target, "error": queue_error,
                        })
                    cloaking_review_items.append({
                        **prepared,
                        "cloaking_evidence_path": cloaking.get("evidence_path", ""),
                        "cloaking_review_reason": _cloaking_review_reason(cloaking),
                        "queue_id": queue_item.get("queue_id", ""),
                        "queue_state": queue_item.get("state", ""),
                        "queue_error": queue_error,
                    })
                    _append_event(events_path, {
                        "type": "cloaking_precheck_isolated", "target_url": target,
                        "domain": target_domain,
                        "verdict": cloaking.get("verdict", "INCONCLUSIVE"),
                        "score": cloaking.get("score", 0),
                        "queue_id": queue_item.get("queue_id", ""),
                    })
                elif recipients:
                    if requested_preflight_version >= 4:
                        prepared["require_browser_evidence"] = True
                        status["current_stage"] = "browser_evidence"
                        _atomic_json(status_path, status)
                        capture = _capture_worker_browser_evidence(target)
                        if browser_evidence.evidence_attachment_paths(capture):
                            prepared["browser_evidence"] = capture
                            ready.append(prepared)
                            _append_event(events_path, {
                                "type": "browser_evidence_captured", "target_url": target,
                                "domain": target_domain,
                                "capture_strategy": capture.get("capture_strategy", ""),
                            })
                        elif capture.get("terminal") and capture.get("terminal_stage") == "source":
                            # Browser/DNS/provider warning pages are not content
                            # evidence. Keep the normal report sendable and do
                            # not ask the operator to upload a screenshot of the
                            # terminal page.
                            prepared["require_browser_evidence"] = False
                            prepared["browser_evidence_terminal"] = True
                            prepared["browser_evidence_error"] = capture.get("error", "")
                            ready.append(prepared)
                            _append_event(events_path, {
                                "type": "browser_evidence_terminal", "target_url": target,
                                "domain": target_domain,
                                "terminal_stage": capture.get("terminal_stage", "source"),
                            })
                        else:
                            evidence_review_items.append({
                                **prepared,
                                "browser_evidence_error": capture.get("error") or "Automatic capture failed",
                                "browser_evidence_terminal": bool(capture.get("terminal")),
                                "capture_strategy": capture.get("capture_strategy", "failed"),
                                "dom_destination_attempt": capture.get("dom_destination_attempt") or {},
                                "passive_attempt": capture.get("passive_attempt") or {},
                                # Persist the operator's local review day so a
                                # stale pending case cannot reappear merely
                                # because a long-running job touched its JSON
                                # after midnight. Legacy records without this
                                # field still fall back to file mtime when read.
                                "review_day": datetime.now().astimezone().date().isoformat(),
                            })
                            _append_event(events_path, {
                                "type": "browser_evidence_manual_required",
                                "target_url": target, "domain": target_domain,
                                "error": capture.get("error") or "Automatic capture failed",
                            })
                    else:
                        ready.append(prepared)
                status["precheck_processed"] += 1
                status["processed"] = status["precheck_processed"]
                status["ready_total"] = len(ready)
                status["cloaking_review_total"] = len(cloaking_review_items)
                status["evidence_review_total"] = len(evidence_review_items)
                status["current_domain"] = None
                status["current_stage"] = "precheck_checkpoint"
                _write_preflight(
                    preflight_path, version=requested_preflight_version, ready=ready,
                    excluded_no_email=status["excluded_no_email"],
                    excluded_already_sent=status["excluded_already_sent"],
                    cloaking_review=cloaking_review_items, complete=False,
                    evidence_review=evidence_review_items,
                )
                _atomic_json(status_path, status)
                _append_event(events_path, {"type": "precheck_finished", "target_url": target})

            if status["state"] == "stopped":
                return
            _write_preflight(
                preflight_path, version=requested_preflight_version, ready=ready,
                excluded_no_email=status["excluded_no_email"],
                excluded_already_sent=status["excluded_already_sent"],
                cloaking_review=cloaking_review_items, complete=True,
                evidence_review=evidence_review_items,
            )

        if retry_targets is not None:
            ready = [
                item for item in ready
                if item.get("target_url") in retry_targets
            ]

        next_state = "ready" if precheck_only else "running"
        status.update({
            "state": next_state, "total": len(previous_results) + len(ready),
            "processed": len(previous_results),
            "ready_total": len(ready), "current_domain": None,
            "current_stage": "ready" if precheck_only else "starting_delivery",
            "total_batches": (len(ready) + batch_size - 1) // batch_size,
        })
        _atomic_json(status_path, status)
        _append_event(events_path, {
            "type": "precheck_completed", "ready": len(ready),
            "excluded_no_email": len(status["excluded_no_email"]),
            "cloaking_review": len(cloaking_review_items),
            "evidence_review": len(evidence_review_items),
        })
        if precheck_only:
            return

        for offset in range(0, len(ready), batch_size):
            if _should_stop(stop_path):
                status["state"] = "stopped"
                break
            batch = ready[offset:offset + batch_size]
            status["current_batch"] = offset // batch_size + 1
            _append_event(events_path, {"type": "batch_started", "batch": status["current_batch"], "domains": [x["target_url"] for x in batch]})
            for prepared in batch:
                if _should_stop(stop_path):
                    status["state"] = "stopped"
                    break
                target = prepared["target_url"]
                domain = prepared["domain"]
                status["current_domain"] = target
                status["current_stage"] = "draft_and_delivery"
                _atomic_json(status_path, status)
                configured_accounts = cfg.get("smtp_accounts") or []
                if configured_accounts:
                    try:
                        domain_result, successful_accounts, stopped_during_send = _run_prechecked_domain(
                            prepared, cfg, configured_accounts, include_vncert, events_path, stop_path,
                            sent_deliveries, approved_cloaking=target in approved_cloaking_targets,
                            operator_cloaking_evidence=operator_cloaking_evidence.get(target),
                            force_normal_report=target in force_normal_targets,
                        )
                        domain = domain_result["domain"]
                    except Exception as exc:
                        successful_accounts = set()
                        stopped_during_send = False
                        domain_result = {
                            "target_url": target, "domain": domain, "success": False,
                            "error": str(exc),
                        }
                        _append_event(events_path, {"type": "domain_error", "target_url": target, "error": str(exc)})
                else:
                    successful_accounts = set()
                    stopped_during_send = False
                    domain_result = {
                        "target_url": target, "domain": domain, "success": False,
                        "error": "No selected SMTP accounts are available in config.ini",
                    }
                try:
                    if domain_result.get("skipped") == "manual_review_required":
                        review_queue.enqueue_worker_result(
                            job=job, job_dir=job_dir, prepared=prepared,
                            domain_result=domain_result,
                        )
                    elif review_queue_ids.get(target):
                        review_queue.complete_send(
                            review_queue_ids[target], domain_result,
                            attempted_accounts=attempted_account_names,
                            send_job_id=str(job.get("job_id") or ""),
                        )
                except Exception as exc:
                    _append_event(events_path, {
                        "type": "cloaking_review_queue_error",
                        "target_url": target, "error": str(exc),
                    })
                if domain_result.get("skipped") == "browser_evidence_required":
                    evidence_review_items = [
                        item for item in evidence_review_items
                        if item.get("target_url") != target
                    ]
                    evidence_review_items.append({
                        **prepared,
                        "browser_evidence_error": domain_result.get("browser_evidence_error")
                        or "Browser Evidence is no longer valid",
                    })
                    ready = [item for item in ready if item.get("target_url") != target]
                    _write_preflight(
                        preflight_path, version=requested_preflight_version,
                        ready=ready, excluded_no_email=status["excluded_no_email"],
                        excluded_already_sent=status["excluded_already_sent"],
                        cloaking_review=cloaking_review_items, complete=True,
                        evidence_review=evidence_review_items,
                    )
                    status["evidence_review_total"] = len(evidence_review_items)
                status["results"].append(domain_result)
                status["processed"] += 1
                status["current_domain"] = None
                status["current_stage"] = "delivery_checkpoint"
                _atomic_json(status_path, status)
                _append_event(events_path, {"type": "domain_finished", **domain_result})
                if stopped_during_send:
                    status["state"] = "stopped"
                    break
            if status["state"] == "stopped":
                break
            if offset + batch_size < len(ready):
                status["state"] = "waiting"
                status["current_stage"] = "batch_wait"
                _atomic_json(status_path, status)
                if _interruptible_wait(interval_seconds, stop_path, status_path, status):
                    status["state"] = "stopped"
                    break
                status["state"] = "running"
                status["current_stage"] = "starting_next_batch"

        if status["state"] not in ("stopped", "failed"):
            status["state"] = "completed"
    except Exception as exc:
        status["state"] = "failed"
        status["error"] = f"{exc}\n{traceback.format_exc()}"
        _append_event(events_path, {"type": "job_error", "error": str(exc)})
    finally:
        status["current_domain"] = None
        status["current_stage"] = (
            "finished" if status.get("state") in {"completed", "failed", "stopped"}
            else str(status.get("state") or "idle")
        )
        status["next_batch_in_seconds"] = 0
        status["finished_at"] = _now()
        _atomic_json(status_path, status)
        _append_event(events_path, {"type": "job_finished", "state": status["state"], "processed": status["processed"]})


def main():
    parser = argparse.ArgumentParser(description="Process and report a queued list of domains")
    parser.add_argument("job_path", help="Path to worker job.json")
    args = parser.parse_args()
    run_job(args.job_path)


if __name__ == "__main__":
    main()

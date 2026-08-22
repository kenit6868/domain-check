#!/usr/bin/env python3
"""Background worker: check domains in batches, generate drafts, then email reports."""

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import time
import traceback
import uuid
from datetime import date, datetime, timezone

import phishing_toolkit as pt
from domain_utils import extract_domains_from_text


WORKER_DIR = os.path.join(pt.BASE_DIR, "worker_jobs")
# Log riêng các domain đã được worker check nhưng KHÔNG tìm được email report nào để gửi
# (registrar chỉ nhận web form, chưa tra được abuse email...). Tách khỏi sent_log.csv vì
# đây không phải 1 lần gửi thành/thất bại — chỉ là "đã thử, không có gì để gửi". Dùng để
# Trang Domain Worker (nút Lọc domain) tự động bỏ qua, tránh dò lại abuse email vô ích
# trong cùng 1 ngày cho domain đã biết chắc không gửi được.
NO_EMAIL_LOG_PATH = os.path.join(pt.BASE_DIR, "no_email_log.csv")


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
    events_path: str, stop_path: str | None = None,
) -> tuple[dict, set[str], bool]:
    summary = {"drafts_total": len(drafts), "drafts_sendable": 0, "sent_ok": 0, "sent_failed": 0, "sent_to": []}
    successful_accounts = set()
    for path in drafts:
        if stop_path and _should_stop(stop_path):
            return summary, successful_accounts, True
        filename = os.path.basename(path)
        if not include_vncert and filename.endswith("_vncert_report.txt"):
            _append_event(events_path, {"type": "draft_skipped", "domain": domain, "draft": filename, "reason": "vncert_disabled"})
            continue
        try:
            parsed = pt.parse_draft_email(path)
        except Exception as exc:
            summary["sent_failed"] += 1
            _append_event(events_path, {"type": "draft_error", "domain": domain, "draft": filename, "error": str(exc)})
            continue
        if not parsed.get("to"):
            _append_event(events_path, {"type": "draft_skipped", "domain": domain, "draft": filename, "reason": "no_email_recipient"})
            continue

        summary["drafts_sendable"] += 1
        accounts = cfg.get("smtp_accounts") or []
        proxies = cfg.get("smtp_proxies") or []
        for index, account_cfg in enumerate(accounts):
            if stop_path and _should_stop(stop_path):
                return summary, successful_accounts, True
            proxy = proxies[index % len(proxies)] if proxies else None
            result = pt.send_report_email_single(
                parsed["to"], parsed["subject"], parsed["body"], account_cfg, proxy
            )
            ok = bool(result.get("success"))
            account = str(result.get("account") or "").strip()
            if ok and account:
                successful_accounts.add(account.lower())
            summary["sent_ok" if ok else "sent_failed"] += 1
            # Track địa chỉ đã gửi để hiển thị trong UI
            summary["sent_to"].append({
                "to": parsed["to"],
                "draft": filename,
                "account": account,
                "ok": ok,
                "error": result.get("error") or "",
            })
            row = {
                "timestamp": _now(),
                "domain": domain,
                "draft_file": filename,
                "to": parsed["to"],
                "subject": parsed["subject"],
                "account": account,
                "success": ok,
                "error": result.get("error") or "",
            }
            try:
                pt.log_sent(row)
            except Exception as exc:
                _append_event(events_path, {"type": "log_error", "domain": domain, "draft": filename, "error": str(exc)})
            _append_event(events_path, {
                "type": "email_result", "domain": domain, "draft": filename,
                "to": parsed["to"], "account": result.get("account"), "success": ok,
                "error": result.get("error") or "",
            })
    return summary, successful_accounts, False


def run_job(job_path: str):
    job_path = os.path.abspath(job_path)
    job_dir = os.path.dirname(job_path)
    status_path = os.path.join(job_dir, "status.json")
    events_path = os.path.join(job_dir, "events.jsonl")
    stop_path = os.path.join(job_dir, "stop.requested")

    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)

    targets = job["domains"]
    batch_size = max(1, int(job.get("batch_size", 5)))
    interval_seconds = max(0, int(job.get("interval_seconds", 300)))
    include_vncert = bool(job.get("include_vncert", False))
    allowed_accounts = job.get("allowed_accounts")  # list of usernames, or None = all
    cfg = pt.load_config()

    # Filter smtp_accounts to only those selected in the UI (if specified)
    if allowed_accounts is not None:
        allowed_lower = {a.lower() for a in allowed_accounts}
        cfg["smtp_accounts"] = [
            acc for acc in (cfg.get("smtp_accounts") or [])
            if str(acc.get("username", "")).strip().lower() in allowed_lower
        ]
    # Dedup CHỈ theo ngày hiện tại (không phải all-time): domain đã gửi ở các ngày
    # trước vẫn được coi là "chưa gửi" cho hôm nay, vì mỗi ngày cho phép report lại.
    reported_domain_accounts = _successfully_reported_domain_accounts_today()
    previous_status = None
    if os.path.exists(status_path):
        try:
            with open(status_path, encoding="utf-8") as f:
                candidate = json.load(f)
            if (
                candidate.get("job_id") == job.get("job_id")
                and candidate.get("state") != "completed"
                and candidate.get("processed", 0) < len(targets)
            ):
                previous_status = candidate
        except (OSError, ValueError):
            pass

    if previous_status:
        status = previous_status
        status.update({
            "state": "running",
            "pid": os.getpid(),
            "finished_at": None,
            "current_domain": None,
            "next_batch_in_seconds": 0,
            "error": None,
        })
        completed_targets = {
            item.get("target_url")
            for item in status.get("results", [])
            if item.get("target_url")
        }
        _append_event(events_path, {
            "type": "job_resumed",
            "processed": status.get("processed", 0),
            "remaining": len(targets) - len(completed_targets),
        })
    else:
        status = {
            "job_id": job.get("job_id"),
            "state": "running",
            "pid": os.getpid(),
            "started_at": _now(),
            "finished_at": None,
            "total": len(targets),
            "processed": 0,
            "current_domain": None,
            "current_batch": 0,
            "total_batches": (len(targets) + batch_size - 1) // batch_size,
            "next_batch_in_seconds": 0,
            "results": [],
            "error": None,
        }
        completed_targets = set()
        _append_event(events_path, {"type": "job_started", "total": len(targets), "batch_size": batch_size})
    _atomic_json(status_path, status)

    try:
        for offset in range(0, len(targets), batch_size):
            if _should_stop(stop_path):
                status["state"] = "stopped"
                break
            batch = [
                target for target in targets[offset:offset + batch_size]
                if target not in completed_targets
            ]
            if not batch:
                continue
            status["current_batch"] = offset // batch_size + 1
            _append_event(events_path, {"type": "batch_started", "batch": status["current_batch"], "domains": batch})

            for target in batch:
                if _should_stop(stop_path):
                    status["state"] = "stopped"
                    break
                status["current_domain"] = target
                _atomic_json(status_path, status)
                _append_event(events_path, {"type": "domain_started", "target_url": target})
                started = time.time()
                target_domain = pt.normalize_domain(target).lower().rstrip(".")
                configured_accounts = cfg.get("smtp_accounts") or []
                unsent_accounts = [
                    account for account in configured_accounts
                    if (target_domain, str(account.get("username") or "").strip().lower())
                    not in reported_domain_accounts
                ]
                if not unsent_accounts:
                    domain_result = {
                        "target_url": target,
                        "domain": target_domain,
                        "success": True,
                        "skipped": "already_sent",
                        "duration_seconds": 0,
                        "drafts_total": 0,
                        "drafts_sendable": 0,
                        "sent_ok": 0,
                        "sent_failed": 0,
                    }
                    status["results"].append(domain_result)
                    status["processed"] += 1
                    status["current_domain"] = None
                    _atomic_json(status_path, status)
                    _append_event(events_path, {"type": "domain_skipped", **domain_result})
                    continue
                try:
                    # Worker chỉ tra cứu dữ liệu VirusTotal đã có, tuyệt đối không
                    # chủ động submit domain mới lên VirusTotal.
                    result = pt.run_check(target, False, cfg)
                    domain = result["domain"]
                    send_cfg = dict(cfg)
                    send_cfg["smtp_accounts"] = unsent_accounts
                    mail, successful_accounts, stopped_during_send = _send_domain_drafts(
                        domain, result.get("drafts") or [], send_cfg, include_vncert,
                        events_path, stop_path,
                    )
                    domain_result = {
                        "target_url": target, "domain": domain, "success": True,
                        "duration_seconds": round(time.time() - started, 1),
                        "reputation": result.get("reputation", {}).get("verdict"), **mail,
                    }
                    if mail.get("drafts_sendable", 0) == 0:
                        # Không có draft nào có địa chỉ email hợp lệ để gửi (vd. registrar chỉ
                        # nhận report qua web form, chưa tra được abuse email...). Đánh dấu rõ
                        # ràng để UI không nhầm với domain đã gửi thành công.
                        domain_result["skipped"] = "no_sendable_email"
                        _log_no_email(domain, target)
                        _append_event(events_path, {
                            "type": "domain_skipped", "target_url": target, "domain": domain,
                            "reason": "no_sendable_email", "drafts_total": mail.get("drafts_total", 0),
                        })
                    normalized_domain = domain.lower().rstrip(".")
                    reported_domain_accounts.update(
                        (normalized_domain, account) for account in successful_accounts
                    )
                    if stopped_during_send:
                        status["state"] = "stopped"
                except Exception as exc:
                    domain_result = {
                        "target_url": target, "domain": pt.normalize_domain(target), "success": False,
                        "duration_seconds": round(time.time() - started, 1),
                        "error": str(exc),
                    }
                    _append_event(events_path, {"type": "domain_error", "target_url": target, "error": str(exc)})
                status["results"].append(domain_result)
                status["processed"] += 1
                status["current_domain"] = None
                _atomic_json(status_path, status)
                _append_event(events_path, {"type": "domain_finished", **domain_result})
                if status["state"] == "stopped":
                    break

            if status["state"] == "stopped":
                break
            has_more = any(target not in completed_targets for target in targets[offset + batch_size:])
            if has_more:
                status["state"] = "waiting"
                _atomic_json(status_path, status)
                _append_event(events_path, {"type": "batch_waiting", "seconds": interval_seconds})
                if _interruptible_wait(interval_seconds, stop_path, status_path, status):
                    status["state"] = "stopped"
                    break
                status["state"] = "running"

        if status["state"] not in ("stopped", "failed"):
            status["state"] = "completed"
    except Exception as exc:
        status["state"] = "failed"
        status["error"] = f"{exc}\n{traceback.format_exc()}"
        _append_event(events_path, {"type": "job_error", "error": str(exc)})
    finally:
        status["current_domain"] = None
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

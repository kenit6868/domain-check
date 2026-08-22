#!/usr/bin/env python3
"""Background worker: check domains in batches, generate drafts, then email reports."""

import argparse
import concurrent.futures
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


WORKER_DIR = pt._runtime_path("worker_jobs")
# Log riêng các domain đã được worker check nhưng KHÔNG tìm được email report nào để gửi
# (registrar chỉ nhận web form, chưa tra được abuse email...). Tách khỏi sent_log.csv vì
# đây không phải 1 lần gửi thành/thất bại — chỉ là "đã thử, không có gì để gửi". Dùng để
# Trang Domain Worker (nút Lọc domain) tự động bỏ qua, tránh dò lại abuse email vô ích
# trong cùng 1 ngày cho domain đã biết chắc không gửi được.
NO_EMAIL_LOG_PATH = pt._runtime_path("no_email_log.csv")


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
    events_path: str, stop_path: str | None = None, prepared_drafts: list | None = None,
) -> tuple[dict, set[str], bool]:
    summary = {"drafts_total": len(drafts), "drafts_sendable": 0, "sent_ok": 0, "sent_failed": 0, "sent_to": []}
    successful_accounts = set()
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
        if hosting.get("abuse_email"):
            recipients.append({"channel": "hosting", "email": hosting["abuse_email"]})

    unique = []
    seen = set()
    for recipient in recipients:
        key = recipient["email"].strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(recipient)
    return unique


def _run_prechecked_domain(prepared, cfg, unsent_accounts, include_vncert, events_path, stop_path):
    """Run the full investigation once, then send only after revalidating draft recipients."""
    target = prepared["target_url"]
    started = time.time()
    send_cfg = dict(cfg)
    send_cfg["smtp_accounts"] = unsent_accounts
    result = pt.run_check(target, False, cfg)
    domain = result["domain"]
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
        domain_result["skipped"] = "no_sendable_email"
        _log_no_email(domain, target)
    return domain_result, successful_accounts, stopped_during_send


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
    previous_results = []
    if os.path.exists(status_path):
        try:
            with open(status_path, encoding="utf-8") as f:
                previous_status = json.load(f)
            if previous_status.get("job_id") == job.get("job_id"):
                previous_results = previous_status.get("results") or []
        except (OSError, ValueError, TypeError):
            pass
    completed_targets = {
        item.get("target_url") for item in previous_results if item.get("target_url")
    }
    status = {
        "job_id": job.get("job_id"), "state": "prechecking", "pid": os.getpid(),
        "started_at": _now(), "finished_at": None, "total": len(targets), "processed": 0,
        "precheck_total": len(targets), "precheck_processed": 0, "ready_total": 0,
        "current_domain": None, "current_batch": 0, "total_batches": 0,
        "next_batch_in_seconds": 0, "results": previous_results, "excluded_no_email": [],
        "excluded_already_sent": [], "error": None,
    }
    _atomic_json(status_path, status)
    _append_event(events_path, {"type": "job_started", "total": len(targets), "batch_size": batch_size})

    try:
        if os.path.exists(preflight_path) and job.get("preflight_version") == 2:
            with open(preflight_path, encoding="utf-8") as f:
                preflight = json.load(f)
            ready = preflight.get("ready") or [] if preflight.get("version") == 2 else []
            # Resume cùng job: chỉ giữ các domain chưa có kết quả. Domain đang
            # xử lý lúc bị dừng chưa được append nên vẫn được chạy lại an toàn.
            ready = [item for item in ready if item.get("target_url") not in completed_targets]
            status["excluded_no_email"] = preflight.get("excluded_no_email") or []
            status["excluded_already_sent"] = preflight.get("excluded_already_sent") or []
            status["precheck_processed"] = len(targets)
        else:
            ready = []
            configured_accounts = cfg.get("smtp_accounts") or []
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
                _atomic_json(status_path, status)
                _append_event(events_path, {"type": "precheck_started", "target_url": target})
                started = time.time()
                target_domain = pt.normalize_domain(target).lower().rstrip(".")
                unsent_accounts = [
                    account for account in configured_accounts
                    if (target_domain, str(account.get("username") or "").strip().lower())
                    not in reported_domain_accounts
                ]
                if not unsent_accounts:
                    status["excluded_already_sent"].append({"target_url": target, "domain": target_domain})
                else:
                    try:
                        recipients = _precheck_report_recipients(target_domain)
                        if recipients:
                            ready.append({
                                "target_url": target, "domain": target_domain,
                                "recipients": recipients,
                                "precheck_duration_seconds": round(time.time() - started, 1),
                            })
                        else:
                            excluded = {
                                "target_url": target, "domain": target_domain,
                                "status": "no_sendable_email",
                            }
                            status["excluded_no_email"].append(excluded)
                            _log_no_email(target_domain, target)
                    except Exception as exc:
                        status["excluded_no_email"].append({
                            "target_url": target, "domain": target_domain,
                            "status": "precheck_error", "error": str(exc),
                        })
                        _append_event(events_path, {"type": "precheck_error", "target_url": target, "error": str(exc)})
                status["precheck_processed"] += 1
                status["processed"] = status["precheck_processed"]
                status["ready_total"] = len(ready)
                status["current_domain"] = None
                _atomic_json(status_path, status)
                _append_event(events_path, {"type": "precheck_finished", "target_url": target})

            if status["state"] == "stopped":
                return
            _atomic_json(preflight_path, {
                "version": 2,
                "ready": ready,
                "excluded_no_email": status["excluded_no_email"],
                "excluded_already_sent": status["excluded_already_sent"],
            })

        next_state = "ready" if precheck_only else "running"
        status.update({
            "state": next_state, "total": len(previous_results) + len(ready),
            "processed": len(previous_results),
            "ready_total": len(ready), "current_domain": None,
            "total_batches": (len(ready) + batch_size - 1) // batch_size,
        })
        _atomic_json(status_path, status)
        _append_event(events_path, {
            "type": "precheck_completed", "ready": len(ready),
            "excluded_no_email": len(status["excluded_no_email"]),
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
                _atomic_json(status_path, status)
                configured_accounts = cfg.get("smtp_accounts") or []
                unsent_accounts = [
                    account for account in configured_accounts
                    if (domain.lower().rstrip("."), str(account.get("username") or "").strip().lower())
                    not in reported_domain_accounts
                ]
                if not unsent_accounts:
                    successful_accounts = set()
                    stopped_during_send = False
                    domain_result = {
                        "target_url": target, "domain": domain, "success": True,
                        "skipped": "already_sent", "drafts_total": 0,
                        "drafts_sendable": 0, "sent_ok": len(configured_accounts),
                        "sent_failed": 0, "sent_to": [],
                    }
                    _append_event(events_path, {
                        "type": "domain_skipped", "target_url": target,
                        "reason": "already_sent",
                    })
                else:
                    try:
                        domain_result, successful_accounts, stopped_during_send = _run_prechecked_domain(
                            prepared, cfg, unsent_accounts, include_vncert, events_path, stop_path,
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
                status["results"].append(domain_result)
                status["processed"] += 1
                status["current_domain"] = None
                reported_domain_accounts.update(
                    (domain.lower().rstrip("."), account) for account in successful_accounts
                )
                _atomic_json(status_path, status)
                _append_event(events_path, {"type": "domain_finished", **domain_result})
                if stopped_during_send:
                    status["state"] = "stopped"
                    break
            if status["state"] == "stopped":
                break
            if offset + batch_size < len(ready):
                status["state"] = "waiting"
                _atomic_json(status_path, status)
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

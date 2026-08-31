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
import cloaking_review_queue as review_queue
from domain_utils import extract_domains_from_text


WORKER_DIR = pt._runtime_path("worker_jobs")
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


def find_active_job_dir() -> str | None:
    """Return an active worker directory, if any persisted job is still running."""
    if not os.path.isdir(WORKER_DIR):
        return None
    for name in os.listdir(WORKER_DIR):
        job_dir = os.path.join(WORKER_DIR, name)
        try:
            with open(os.path.join(job_dir, "status.json"), encoding="utf-8") as status_file:
                state = json.load(status_file).get("state")
        except (OSError, ValueError, TypeError):
            continue
        if state in {"prechecking", "running", "waiting"}:
            return job_dir
    return None


def create_cloaking_review_job(
    queue_ids: list[str], *, decision: str, allowed_accounts: list[str],
    batch_size: int = 5, interval_seconds: int = 0,
) -> str:
    """Create a persisted worker job for exactly the selected review records."""
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

    os.makedirs(WORKER_DIR, exist_ok=True)
    job_id = "review_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    job_dir = os.path.join(WORKER_DIR, job_id)
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
    for domain, entry in entries.items():
        try:
            checked_at = datetime.fromisoformat(str(entry.get("checked_at", "")).replace("Z", "+00:00"))
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            if checked_at.astimezone(local_tz).date() == local_day:
                valid[domain] = entry
        except (AttributeError, TypeError, ValueError):
            continue
    return valid


def _save_precheck_cache(entries: dict[str, dict]):
    """Persist the current in-memory daily cache atomically."""
    os.makedirs(os.path.dirname(PRECHECK_CACHE_PATH), exist_ok=True)
    _atomic_json(PRECHECK_CACHE_PATH, {"version": 1, "entries": entries})


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
) -> tuple[dict, set[str], bool]:
    summary = {"drafts_total": len(drafts), "drafts_sendable": 0, "sent_ok": 0, "sent_failed": 0, "already_sent": 0, "sent_to": []}
    successful_accounts = set()
    sent_deliveries = sent_deliveries if sent_deliveries is not None else set()
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
                "error": result.get("error") or "",
            })
            row = {
                "timestamp": _now(),
                "domain": domain,
                "draft_file": filename,
                "to": parsed["to"],
                "subject": parsed["subject"],
                "account": delivery_account,
                "success": ok,
                "error": result.get("error") or "",
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


def _run_prechecked_domain(
    prepared, cfg, selected_accounts, include_vncert, events_path, stop_path,
    sent_deliveries, approved_cloaking=False, operator_cloaking_evidence=None,
    force_normal_report=False,
):
    """Run the full investigation once, then send only after revalidating draft recipients."""
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
    coverage_gap = bool(
        (cloaking.get("coverage") or {}).get("multi_vantage_recommended")
    )
    if (
        cloaking_verdict in {"LIKELY", "POSSIBLE", "INCONCLUSIVE"} or coverage_gap
    ) and not operator_cloaking_evidence and not force_normal_report:
        cloaking = pt.run_cloaking_browser_check(target, cloaking, cfg)
        result["cloaking"] = cloaking
        cloaking_verdict = cloaking.get("verdict", "INCONCLUSIVE")
        coverage_gap = bool(
            (cloaking.get("coverage") or {}).get("multi_vantage_recommended")
        )
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
        and (
            cloaking_verdict in {"LIKELY", "POSSIBLE", "INCONCLUSIVE"}
            or evidence_failed
            or coverage_gap
        )
    )
    if needs_review and not approved_cloaking:
        _append_event(events_path, {
            "type": "cloaking_manual_review", "domain": domain, "target_url": target,
            "verdict": cloaking_verdict, "score": cloaking.get("score", 0),
            "evidence_path": cloaking.get("evidence_path", ""),
        })
        return ({
            "target_url": target, "domain": domain, "success": False,
            "duration_seconds": round(time.time() - started, 1),
            "reputation": result.get("reputation", {}).get("verdict"),
            "drafts_total": len(result.get("drafts") or []), "drafts_sendable": 0,
            "sent_ok": 0, "sent_failed": 0, "already_sent": 0, "sent_to": [],
            "skipped": "manual_review_required", "manual_review_required": True,
            "cloaking_verdict": cloaking_verdict,
            "cloaking_score": cloaking.get("score", 0),
            "cloaking_signals": cloaking.get("signals") or [],
            "cloaking_evidence_path": cloaking.get("evidence_path", ""),
            "cloaking_review_reason": (
                "coverage_gap" if coverage_gap
                else "confirmed_signal" if cloaking_verdict == "LIKELY"
                else "detector_signal"
            ),
            "cloaking_result": cloaking,
        }, set(), False)
    if force_normal_report:
        refreshed = pt.remove_cloaking_evidence_from_drafts(result.get("drafts") or [])
        if len(refreshed) != len(result.get("drafts") or []):
            result["drafts_error"] = (
                str(result.get("drafts_error") or "")
                + "; Cloaking evidence could not be removed from every draft"
            ).strip("; ")
    evidence_path = str(cloaking.get("evidence_path") or "").strip()
    evidence_attachments = (
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
    mail, successful_accounts, stopped_during_send = _send_domain_drafts(
        domain, result.get("drafts") or [], send_cfg, include_vncert,
        events_path, stop_path, sent_deliveries=sent_deliveries,
        attachments=evidence_attachments,
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
    if mail.get("drafts_sendable", 0) == 0:
        domain_result["skipped"] = "no_sendable_email"
        _log_no_email(domain, target)
    elif mail.get("sent_ok", 0) == 0 and mail.get("sent_failed", 0) == 0 and mail.get("already_sent", 0):
        domain_result["skipped"] = "already_sent"
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
        "precheck_cached": 0,
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
                if configured_accounts:
                    try:
                        cached_precheck = None if force_precheck else precheck_cache.get(target_domain)
                        if cached_precheck is not None:
                            recipients = cached_precheck.get("recipients") or []
                            status["precheck_cached"] += 1
                            _append_event(events_path, {
                                "type": "precheck_cache_hit", "target_url": target,
                                "domain": target_domain,
                            })
                        else:
                            recipients = _precheck_report_recipients(target_domain)
                            precheck_cache[target_domain] = {
                                "checked_at": _now(), "recipients": recipients,
                            }
                            _save_precheck_cache(precheck_cache)
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
                status["results"].append(domain_result)
                status["processed"] += 1
                status["current_domain"] = None
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

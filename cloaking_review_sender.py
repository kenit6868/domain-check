"""Prepare, preview, and directly send one Cloaking Review case.

The persistent review queue remains the source of truth for operator decisions
and per-account delivery progress. This module intentionally does not create a
worker job or launch a background process: the same prepared content shown in
the UI is passed directly to the existing SMTP helper when the operator sends.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import cloaking_review_queue as review_queue
import phishing_toolkit as pt


CONFIRMED_CLOAKING = "confirmed_cloaking"
NOT_CLOAKING = "not_cloaking"
VALID_DECISIONS = {CONFIRMED_CLOAKING, NOT_CLOAKING}
PREPARATION_VERSION = 2
_LOCK_STALE_SECONDS = 10 * 60
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _account_name(value) -> str:
    return str(value or "").strip().lower()


def _account_map(cfg: dict) -> dict[str, tuple[dict, str | None]]:
    proxies = list(cfg.get("smtp_proxies") or [])
    accounts = {}
    for index, account in enumerate(cfg.get("smtp_accounts") or []):
        username = _account_name(account.get("username"))
        if not username or username in accounts:
            continue
        proxy = proxies[index % len(proxies)] if proxies else None
        accounts[username] = (account, proxy)
    return accounts


def _selected_account_names(account_names, cfg: dict) -> list[str]:
    available = _account_map(cfg)
    selected = list(dict.fromkeys(
        name for name in (_account_name(value) for value in (account_names or []))
        if name
    ))
    if not selected:
        raise ValueError("Hãy chọn ít nhất một tài khoản gửi email.")
    missing = [name for name in selected if name not in available]
    if missing:
        raise ValueError(
            "Tài khoản gửi không còn trong cấu hình: " + ", ".join(missing)
        )
    return selected


def normalize_cloaking_result(result: dict | None) -> dict:
    """Apply current operator-evidence semantics to legacy queue results."""
    normalized = dict(result) if isinstance(result, dict) else {}
    operator_evidence = normalized.get("operator_evidence") or {}
    if isinstance(operator_evidence, dict) and operator_evidence:
        normalized = pt.merge_operator_cloaking_evidence(
            normalized, operator_evidence,
        )
    return normalized


def _cloaking_result(item: dict) -> dict:
    return normalize_cloaking_result(
        ((item.get("result") or {}).get("cloaking_result") or {}),
    )


def _evidence_signature(result: dict) -> str:
    payload = json.dumps(
        result or {}, ensure_ascii=False, sort_keys=True, default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _attachment_fingerprints(paths: list[str]) -> list[dict]:
    fingerprints = []
    for path in paths:
        digest = hashlib.sha256()
        with open(path, "rb") as attachment_file:
            for chunk in iter(lambda: attachment_file.read(1024 * 1024), b""):
                digest.update(chunk)
        fingerprints.append({
            "path": os.path.abspath(path),
            "size": os.path.getsize(path),
            "sha256": digest.hexdigest(),
        })
    return fingerprints


def _assert_selectable(item: dict, decision: str) -> None:
    if not item or item.get("state") not in review_queue.ACTIVE_STATES:
        raise ValueError("Case review không còn ở trạng thái có thể gửi.")
    if decision not in VALID_DECISIONS:
        raise ValueError("Chế độ gửi Cloaking Review không hợp lệ.")
    delivered = review_queue.delivery_summary(item)["deliveries"]
    existing_decision = str(item.get("decision") or "")
    if (
        any(row.get("status") in review_queue.DELIVERED_STATUSES for row in delivered)
        and existing_decision in VALID_DECISIONS
        and existing_decision != decision
    ):
        raise ValueError(
            "Case đã gửi một phần theo quyết định trước đó; không thể đổi chế độ cho "
            "các tài khoản còn lại."
        )


def _valid_image_paths(screenshots) -> list[str]:
    paths = []
    seen = set()
    for screenshot in screenshots or []:
        if not isinstance(screenshot, dict):
            continue
        path = os.path.abspath(str((screenshot or {}).get("path") or "").strip())
        if not path or path in seen or not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as image_file:
                header = image_file.read(12)
        except OSError:
            continue
        if size <= 0 or size > _MAX_ATTACHMENT_BYTES:
            continue
        supported = (
            header.startswith(b"\x89PNG\r\n\x1a\n")
            or header.startswith(b"\xff\xd8\xff")
            or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
        )
        if supported:
            seen.add(path)
            paths.append(path)
    return paths


def _evidence_attachments(result: dict) -> list[str]:
    manifest = os.path.abspath(str(result.get("evidence_path") or "").strip())
    if not manifest or not os.path.isfile(manifest):
        raise ValueError(
            "Không tìm thấy manifest evidence đã duyệt. Hãy kiểm tra/chụp lại trước khi gửi."
        )
    try:
        with open(manifest, encoding="utf-8-sig") as manifest_file:
            manifest_payload = json.load(manifest_file)
        if not isinstance(manifest_payload, dict):
            raise ValueError
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            "Manifest evidence không phải JSON hợp lệ. Hãy tạo lại evidence trước khi gửi."
        ) from exc

    images = []
    operator_evidence = result.get("operator_evidence") or {}
    if not isinstance(operator_evidence, dict):
        operator_evidence = {}
    image_sources = [
        result.get("screenshots") or [],
        operator_evidence.get("screenshots") or [],
    ]
    for source in image_sources:
        source_images = _valid_image_paths(source)
        if len(source_images) >= 2:
            images = source_images[:2]
            break
    if len(images) < 2:
        raise ValueError(
            "Cần đủ hai ảnh đối chiếu hợp lệ để gửi báo cáo xác nhận cloaking. "
            "Hãy chụp lại bằng Playwright hoặc bổ sung ảnh thủ công."
        )
    attachments = [manifest, *images]
    for path in attachments:
        size = os.path.getsize(path)
        if size <= 0:
            raise ValueError(
                f"Evidence attachment rỗng: {os.path.basename(path)}. Hãy chụp/lưu lại trước khi gửi."
            )
        if size > _MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"Evidence attachment vượt quá 10 MB: {os.path.basename(path)}. "
                "Hãy tối ưu ảnh rồi tạo lại draft."
            )
    return attachments


def confirmed_evidence_status(result: dict | None) -> dict:
    """Describe whether a case can produce a confirmed-cloaking preview."""
    normalized = normalize_cloaking_result(result)
    automatic_images = _valid_image_paths(normalized.get("screenshots") or [])
    operator_evidence = normalized.get("operator_evidence") or {}
    if not isinstance(operator_evidence, dict):
        operator_evidence = {}
    operator_images = _valid_image_paths(
        operator_evidence.get("screenshots") or [],
    )
    attachments = []
    error = ""
    try:
        attachments = _evidence_attachments(normalized)
    except (OSError, ValueError) as exc:
        error = str(exc)
    verdict_ready = normalized.get("verdict") in {"LIKELY", "POSSIBLE"}
    if not verdict_ready:
        error = (
            "Evidence hiện tại chưa đạt LIKELY/POSSIBLE. Hãy bổ sung hai ảnh "
            "thủ công của cùng URL và xác nhận nội dung khác nhau."
        )
    ready = verdict_ready and bool(attachments)
    pair_source = ""
    if ready:
        selected_images = set(attachments[1:])
        pair_source = (
            "automatic"
            if selected_images.issubset(set(automatic_images))
            else "operator"
        )
    return {
        "ready": ready,
        "reason": error,
        "verdict": normalized.get("verdict") or "INCONCLUSIVE",
        "automatic_images": len(automatic_images),
        "operator_images": len(operator_images),
        "pair_source": pair_source,
        "attachments": attachments,
        "result": normalized,
    }


def _sendable_drafts(paths: list[str], *, include_vncert: bool) -> list[dict]:
    drafts = []
    for path in paths or []:
        filename = os.path.basename(path)
        if not include_vncert and filename.endswith("_vncert_report.txt"):
            continue
        parsed = pt.parse_draft_email(path)
        if not parsed.get("to") or not parsed.get("subject"):
            continue
        drafts.append({
            "path": os.path.abspath(path),
            "draft": filename,
            "to": str(parsed["to"]).strip(),
            "subject": str(parsed["subject"]).strip(),
            "body": str(parsed.get("body") or ""),
        })
    return drafts


def prepare_review_delivery(
    queue_id: str, *, decision: str, account_names: list[str], cfg: dict,
) -> dict:
    """Run the shared check pipeline and return the exact delivery preview.

    This function never sends email. For confirmed cloaking, the approved queue
    evidence replaces any transient detector result from this preparation run.
    """
    item = review_queue.load_item(queue_id)
    _assert_selectable(item, decision)
    selected_accounts = _selected_account_names(account_names, cfg)
    target_url = str(item.get("target_url") or "").strip()
    if not target_url:
        raise ValueError("Case review không có full URL hợp lệ.")

    approved_evidence = _cloaking_result(item)
    if decision == CONFIRMED_CLOAKING:
        evidence_status = confirmed_evidence_status(approved_evidence)
        if not evidence_status["ready"]:
            raise ValueError(evidence_status["reason"])
        approved_evidence = evidence_status["result"]
        attachments = evidence_status["attachments"]
    else:
        attachments = []

    check_result = pt.run_check(target_url, False, cfg)
    draft_paths = list(check_result.get("drafts") or [])
    if not draft_paths:
        raise ValueError(
            "Không tạo được draft gửi email: "
            + str(check_result.get("drafts_error") or "không có kênh email phù hợp")
        )

    if decision == CONFIRMED_CLOAKING:
        updated_paths = pt.append_cloaking_evidence_to_drafts(
            draft_paths, approved_evidence, operator_confirmed=True,
        )
        if len(updated_paths) != len(draft_paths):
            raise OSError("Không thể chèn evidence cloaking vào toàn bộ draft.")
    else:
        updated_paths = pt.remove_cloaking_evidence_from_drafts(draft_paths)
        if len(updated_paths) != len(draft_paths):
            raise OSError("Không thể loại evidence cloaking khỏi toàn bộ draft thường.")

    drafts = _sendable_drafts(
        updated_paths, include_vncert=bool(item.get("include_vncert", False)),
    )
    if not drafts:
        raise ValueError("Không có draft nào chứa địa chỉ email nhận hợp lệ.")

    accounts = _account_map(cfg)
    deliveries = []
    for account_name in selected_accounts:
        account_cfg, _proxy = accounts[account_name]
        for draft in drafts:
            deliveries.append({
                **draft,
                "account": account_name,
                "body": pt.personalize_email_body(draft["body"], cfg, account_cfg),
            })

    return {
        "version": PREPARATION_VERSION,
        "queue_id": queue_id,
        "target_url": target_url,
        "domain": str(check_result.get("domain") or item.get("domain") or pt.normalize_domain(target_url)),
        "decision": decision,
        "accounts": selected_accounts,
        "prepared_at": _now(),
        "evidence_signature": _evidence_signature(approved_evidence),
        "attachments": attachments,
        "attachment_fingerprints": _attachment_fingerprints(attachments),
        "deliveries": deliveries,
        "drafts_sendable": len(drafts),
        "drafts_total": len(draft_paths),
        "drafts_error": str(check_result.get("drafts_error") or ""),
    }


def preparation_is_current(
    preparation: dict | None, item: dict | None, *, decision: str | None,
    account_names: list[str] | None,
) -> bool:
    if not preparation or not item or decision not in VALID_DECISIONS:
        return False
    selected = list(dict.fromkeys(
        name for name in (_account_name(value) for value in (account_names or [])) if name
    ))
    return bool(
        preparation.get("version") == PREPARATION_VERSION
        and preparation.get("queue_id") == item.get("queue_id")
        and preparation.get("target_url") == item.get("target_url")
        and preparation.get("decision") == decision
        and list(preparation.get("accounts") or []) == selected
        and preparation.get("evidence_signature") == _evidence_signature(_cloaking_result(item))
    )


@contextmanager
def _direct_send_lock(queue_id: str):
    os.makedirs(review_queue.REVIEW_DIR, exist_ok=True)
    lock_path = os.path.join(review_queue.REVIEW_DIR, f".{queue_id}.send.lock")
    for attempt in range(2):
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                stale = time.time() - os.path.getmtime(lock_path) > _LOCK_STALE_SECONDS
            except OSError:
                stale = False
            if not stale or attempt:
                raise RuntimeError("Case này đang được gửi ở một phiên khác.")
            try:
                os.remove(lock_path)
            except OSError:
                raise RuntimeError("Case này đang được gửi ở một phiên khác.")
    else:  # pragma: no cover - defensive fallback
        raise RuntimeError("Không thể khóa case để gửi.")
    try:
        os.write(descriptor, f"{os.getpid()} {_now()}".encode("utf-8"))
        yield
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.remove(lock_path)
        except OSError:
            pass


def send_prepared_review(preparation: dict, cfg: dict) -> dict:
    """Send the already-previewed content synchronously and update its ledger."""
    queue_id = str((preparation or {}).get("queue_id") or "")
    if not queue_id:
        raise ValueError("Draft preview không có queue ID hợp lệ.")

    with _direct_send_lock(queue_id):
        item = review_queue.load_item(queue_id)
        decision = str(preparation.get("decision") or "")
        accounts = list(preparation.get("accounts") or [])
        _assert_selectable(item, decision)
        _selected_account_names(accounts, cfg)
        if not preparation_is_current(
            preparation, item, decision=decision, account_names=accounts,
        ):
            raise ValueError("Draft preview đã cũ. Hãy tạo/cập nhật draft lại trước khi gửi.")

        attachments = list(preparation.get("attachments") or [])
        if decision == CONFIRMED_CLOAKING:
            expected_attachments = _evidence_attachments(_cloaking_result(item))
            if attachments != expected_attachments:
                raise ValueError("Danh sách evidence đã thay đổi. Hãy tạo lại draft trước khi gửi.")
            if preparation.get("attachment_fingerprints") != _attachment_fingerprints(
                expected_attachments,
            ):
                raise ValueError("Nội dung evidence đã thay đổi. Hãy kiểm tra và tạo lại draft trước khi gửi.")
        elif attachments:
            raise ValueError("Report thường không được chứa attachment cloaking.")

        attempt_id = (
            "direct_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "_" + uuid.uuid4().hex[:8]
        )
        attempted_at = _now()
        required_accounts = list(dict.fromkeys([
            *review_queue.delivery_summary(item)["required_accounts"],
            *accounts,
        ]))
        review_queue.update_item(
            queue_id,
            decision=decision,
            decision_at=item.get("decision_at") or attempted_at,
            required_accounts=required_accounts,
            attempt_accounts=accounts,
            send_job_id="",
            last_error="",
        )

        current_deliveries = review_queue.delivery_summary(
            review_queue.load_item(queue_id) or item,
        )["deliveries"]
        delivered_keys = {
            (
                _account_name(row.get("account")),
                str(row.get("draft") or "").strip().lower(),
                str(row.get("to") or "").strip().lower(),
            )
            for row in current_deliveries
            if row.get("status") in review_queue.DELIVERED_STATUSES
        }
        configured_accounts = _account_map(cfg)
        sent_rows = []
        sent_ok = sent_failed = already_sent = 0
        log_errors = []
        for delivery in preparation.get("deliveries") or []:
            account_name = _account_name(delivery.get("account"))
            filename = str(delivery.get("draft") or "").strip()
            recipient = str(delivery.get("to") or "").strip()
            delivery_key = (account_name, filename.lower(), recipient.lower())
            if delivery_key in delivered_keys:
                already_sent += 1
                sent_rows.append({
                    "account": account_name, "to": recipient, "draft": filename,
                    "status": "already_sent", "ok": True, "error": "",
                })
                continue

            account_cfg, proxy = configured_accounts[account_name]
            result = pt.send_report_email_single(
                recipient,
                str(delivery.get("subject") or ""),
                str(delivery.get("body") or ""),
                account_cfg,
                proxy,
                attachments=attachments or None,
            )
            ok = bool(result.get("success"))
            sent_ok += int(ok)
            sent_failed += int(not ok)
            row = {
                "account": _account_name(result.get("account") or account_name),
                "to": recipient,
                "draft": filename,
                "status": "sent" if ok else "failed",
                "ok": ok,
                "error": str(result.get("error") or ""),
            }
            sent_rows.append(row)
            # Persist each SMTP outcome before moving to the next delivery. If the
            # page or process stops halfway through a multi-account send, the next
            # attempt can skip the deliveries that already succeeded.
            review_queue.complete_send(
                queue_id,
                {
                    "target_url": preparation.get("target_url"),
                    "domain": preparation.get("domain") or item.get("domain"),
                    "success": ok,
                    "drafts_total": int(preparation.get("drafts_total", 0) or 0),
                    "drafts_sendable": int(preparation.get("drafts_sendable", 0) or 0),
                    "sent_ok": int(ok),
                    "sent_failed": int(not ok),
                    "already_sent": 0,
                    "sent_to": [row],
                    "cloaking_disposition": decision,
                    "cloaking_approved": decision == CONFIRMED_CLOAKING,
                },
                attempted_accounts=[account_name],
                send_job_id=attempt_id,
                attempted_at=attempted_at,
            )
            try:
                pt.log_sent({
                    "timestamp": _now(),
                    "domain": preparation.get("domain") or item.get("domain"),
                    "draft_file": filename,
                    "to": recipient,
                    "subject": delivery.get("subject") or "",
                    "account": row["account"],
                    "success": ok,
                    "error": row["error"],
                })
            except Exception as exc:  # Sending success must survive a log failure.
                log_errors.append(str(exc))

        domain_result = {
            "target_url": preparation.get("target_url"),
            "domain": preparation.get("domain") or item.get("domain"),
            "success": sent_failed == 0 and bool(sent_ok or already_sent),
            "drafts_total": int(preparation.get("drafts_total", 0) or 0),
            "drafts_sendable": int(preparation.get("drafts_sendable", 0) or 0),
            "sent_ok": sent_ok,
            "sent_failed": sent_failed,
            "already_sent": already_sent,
            "sent_to": sent_rows,
            "cloaking_disposition": decision,
            "cloaking_approved": decision == CONFIRMED_CLOAKING,
            "cloaking_result": _cloaking_result(item),
            "cloaking_evidence_path": (
                _cloaking_result(item).get("evidence_path", "")
                if decision == CONFIRMED_CLOAKING else ""
            ),
            "attachments": [os.path.basename(path) for path in attachments],
            "send_mode": "direct",
            "log_errors": log_errors,
        }
        if not preparation.get("deliveries"):
            domain_result["error"] = "Draft preview không có email delivery."
        updated = review_queue.complete_send(
            queue_id,
            domain_result,
            attempted_accounts=accounts,
            send_job_id=attempt_id,
            attempted_at=attempted_at,
        )
        domain_result["queue_state"] = (updated or {}).get("state", "")
        domain_result["queue_item"] = updated or {}
        return domain_result

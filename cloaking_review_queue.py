"""Persistent queue for worker cloaking cases awaiting operator disposition."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import phishing_toolkit as pt


REVIEW_DIR = pt._runtime_path("cloaking_review")
PENDING_REVIEW = "PENDING_REVIEW"
PARTIAL = "PARTIAL"
QUEUED_CLOAKING = "QUEUED_CLOAKING"
QUEUED_NORMAL = "QUEUED_NORMAL"
SENT = "SENT"
FAILED = "FAILED"
SKIPPED = "SKIPPED"
ACTIVE_STATES = {PENDING_REVIEW, PARTIAL, FAILED}
VALID_STATES = {
    PENDING_REVIEW, PARTIAL, QUEUED_CLOAKING, QUEUED_NORMAL, SENT, FAILED, SKIPPED,
}
DELIVERED_STATUSES = {"sent", "already_sent"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_equal(left, right) -> bool:
    """Compare persisted values stably, including legacy JSON ``NaN`` values."""
    try:
        return json.dumps(
            left, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ) == json.dumps(
            right, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return left == right


def _account_name(value) -> str:
    return str(value or "").strip().lower()


def _account_names(values) -> list[str]:
    return list(dict.fromkeys(
        name for name in (_account_name(value) for value in (values or [])) if name
    ))


def has_sendable_recipient(value: dict | None) -> bool:
    """Return whether a prepared/queue record contains a usable email target."""
    if not isinstance(value, dict):
        return False
    prepared = value.get("prepared")
    sources = []
    if isinstance(prepared, dict):
        sources.extend(prepared.get("recipients") or [])
    sources.extend(value.get("recipients") or [])
    sources.extend(value.get("deliveries") or [])
    result = value.get("result")
    if isinstance(result, dict):
        sources.extend(result.get("sent_to") or [])
    for recipient in sources:
        if isinstance(recipient, dict):
            address = recipient.get("email") or recipient.get("to")
        else:
            address = recipient
        if str(address or "").strip():
            return True
    return False


def _delivery_status(row: dict) -> str:
    status = str(row.get("status") or "").strip().lower()
    if status in DELIVERED_STATUSES | {"failed"}:
        return status
    return "sent" if bool(row.get("ok", row.get("success", False))) else "failed"


def _normalize_delivery(
    row: dict, *, send_job_id: str = "", updated_at: str = "",
) -> dict | None:
    if not isinstance(row, dict):
        return None
    account = _account_name(row.get("account"))
    recipient = str(row.get("to") or "").strip()
    draft = str(row.get("draft") or row.get("draft_file") or "").strip()
    if not account and not recipient and not draft:
        return None
    status = _delivery_status(row)
    return {
        "account": account,
        "to": recipient,
        "draft": draft,
        "status": status,
        "ok": status in DELIVERED_STATUSES,
        "error": str(row.get("error") or "").strip(),
        "send_job_id": str(row.get("send_job_id") or send_job_id or "").strip(),
        "updated_at": str(
            row.get("updated_at") or row.get("timestamp") or updated_at or _now()
        ),
    }


def _delivery_key(row: dict) -> tuple[str, str, str]:
    return (
        _account_name(row.get("account")),
        str(row.get("draft") or "").strip().lower(),
        str(row.get("to") or "").strip().lower(),
    )


def _merge_deliveries(existing_rows, incoming_rows) -> list[dict]:
    merged: dict[tuple[str, str, str], dict] = {}
    for raw_row in existing_rows or []:
        row = _normalize_delivery(raw_row)
        if row:
            merged[_delivery_key(row)] = row
    for raw_row in incoming_rows or []:
        row = _normalize_delivery(raw_row)
        if not row:
            continue
        key = _delivery_key(row)
        previous = merged.get(key)
        if not previous:
            merged[key] = row
            continue
        previous_delivered = previous.get("status") in DELIVERED_STATUSES
        incoming_delivered = row.get("status") in DELIVERED_STATUSES
        if previous_delivered and not incoming_delivered:
            continue
        same_attempt = (
            previous.get("send_job_id") == row.get("send_job_id")
            and previous.get("status") == row.get("status")
            and previous.get("error") == row.get("error")
        )
        if same_attempt:
            continue
        incoming_is_newer = str(row.get("updated_at") or "") >= str(
            previous.get("updated_at") or ""
        )
        if incoming_delivered and not previous_delivered:
            merged[key] = row
        elif incoming_delivered == previous_delivered and incoming_is_newer:
            merged[key] = row
    return sorted(
        merged.values(),
        key=lambda row: (
            _account_name(row.get("account")),
            str(row.get("to") or "").lower(),
            str(row.get("draft") or "").lower(),
        ),
    )


def _prepared_recipients(item: dict) -> list[str]:
    recipients = []
    for recipient in (item.get("prepared") or {}).get("recipients") or []:
        if isinstance(recipient, dict):
            value = recipient.get("email") or recipient.get("to")
        else:
            value = recipient
        value = str(value or "").strip()
        if value:
            recipients.append(value)
    return list(dict.fromkeys(recipients))


def delivery_summary(item: dict) -> dict:
    """Return cumulative sender/recipient progress for one review case."""
    deliveries = _merge_deliveries([], item.get("deliveries") or [])
    required_accounts = _account_names(
        item.get("required_accounts")
        or item.get("allowed_accounts")
        or item.get("attempt_accounts")
        or [row.get("account") for row in deliveries]
    )
    attempted_accounts = _account_names([
        *(item.get("attempt_accounts") or []),
        *[
            account
            for attempt in item.get("send_attempts") or []
            for account in (attempt.get("accounts") or [])
        ],
        *[row.get("account") for row in deliveries],
    ])
    expected_map = {
        _account_name(account): max(0, int(count or 0))
        for account, count in (item.get("expected_deliveries_per_account") or {}).items()
        if _account_name(account)
    }
    global_expected = max(
        [max(0, int(item.get("expected_deliveries", 0) or 0)), *expected_map.values()],
        default=0,
    )
    account_rows = []
    completed_accounts = []
    failed_accounts = []
    for account in required_accounts:
        account_deliveries = [
            row for row in deliveries if _account_name(row.get("account")) == account
        ]
        delivered_count = sum(
            row.get("status") in DELIVERED_STATUSES for row in account_deliveries
        )
        failed_count = sum(row.get("status") == "failed" for row in account_deliveries)
        expected = expected_map.get(account, global_expected)
        complete = delivered_count >= expected if expected > 0 else delivered_count > 0
        if complete:
            completed_accounts.append(account)
            status = "sent"
        elif failed_count:
            failed_accounts.append(account)
            status = "failed"
        elif account in attempted_accounts:
            status = "pending"
        else:
            status = "not_attempted"
        account_rows.append({
            "account": account,
            "status": status,
            "delivered": delivered_count,
            "expected": expected,
            "failed": failed_count,
        })
    recipients = list(dict.fromkeys([
        *_prepared_recipients(item),
        *[
            str(row.get("to") or "").strip()
            for row in deliveries if str(row.get("to") or "").strip()
        ],
    ]))
    pending_accounts = [
        account for account in required_accounts if account not in completed_accounts
    ]
    return {
        "required_accounts": required_accounts,
        "attempted_accounts": attempted_accounts,
        "completed_accounts": completed_accounts,
        "pending_accounts": pending_accounts,
        "failed_accounts": failed_accounts,
        "recipients": recipients,
        "deliveries": deliveries,
        "account_rows": account_rows,
        "completed_count": len(completed_accounts),
        "required_count": len(required_accounts),
        "progress": f"{len(completed_accounts)}/{len(required_accounts)}" if required_accounts else "—",
    }


def canonical_target_url(target_url: str) -> str:
    """Normalize one full URL for daily queue deduplication."""
    value = str(target_url or "").strip()
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    scheme = parsed.scheme.lower() or "https"
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        return value.lower()
    port = parsed.port
    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _local_day(timestamp: str | None = None) -> str:
    if timestamp:
        try:
            value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone().date().isoformat()
        except (TypeError, ValueError):
            pass
    return datetime.now().astimezone().date().isoformat()


def _review_day(job: dict, job_id: str) -> str:
    if job.get("created_at"):
        return _local_day(job.get("created_at"))
    prefix = str(job_id)[:8]
    if len(prefix) == 8 and prefix.isdigit():
        try:
            return datetime.strptime(prefix, "%Y%m%d").date().isoformat()
        except ValueError:
            pass
    return _local_day()


def queue_id_for(review_day: str, target_url: str) -> str:
    payload = f"{review_day}\0{canonical_target_url(target_url)}".encode(
        "utf-8", errors="replace",
    )
    return hashlib.sha256(payload).hexdigest()[:24]


def _item_path(queue_id: str) -> str:
    safe_id = "".join(char for char in str(queue_id) if char.isalnum() or char in "-_")
    if not safe_id:
        raise ValueError("A valid cloaking review queue ID is required")
    return os.path.join(REVIEW_DIR, f"{safe_id}.json")


def _atomic_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as output_file:
            json.dump(data, output_file, ensure_ascii=False, indent=2)
            output_file.flush()
            os.fsync(output_file.fileno())
        last_error = None
        for attempt in range(20):
            try:
                os.replace(temp_path, path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(min(0.05 * (attempt + 1), 0.4))
        raise last_error
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def load_item(queue_id: str) -> dict | None:
    try:
        with open(_item_path(queue_id), encoding="utf-8") as input_file:
            item = json.load(input_file)
        return item if isinstance(item, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def list_items(states: set[str] | None = None) -> list[dict]:
    if not os.path.isdir(REVIEW_DIR):
        return []
    items = []
    for filename in os.listdir(REVIEW_DIR):
        if not filename.endswith(".json"):
            continue
        item = load_item(filename[:-5])
        if not item or (states is not None and item.get("state") not in states):
            continue
        items.append(item)
    return sorted(
        items,
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )


def current_review_day() -> str:
    """Return the local calendar day used by queue IDs and the review UI."""
    return _local_day()


def list_items_for_day(
    review_day: str | None = None, states: set[str] | None = None,
) -> list[dict]:
    """List one local day's cases without deleting historical delivery records."""
    selected_day = str(review_day or current_review_day())
    return [
        item for item in list_items(states=states)
        if str(
            item.get("review_day")
            or _local_day(item.get("created_at") or item.get("updated_at"))
        ) == selected_day
    ]


def enqueue_worker_result(
    *, job: dict, job_dir: str, prepared: dict, domain_result: dict,
) -> dict:
    """Create or refresh one pending review record without losing terminal state."""
    target_url = str(domain_result.get("target_url") or prepared.get("target_url") or "").strip()
    job_id = str(job.get("job_id") or os.path.basename(job_dir)).strip()
    if not target_url or not job_id:
        raise ValueError("A worker job ID and target URL are required for cloaking review")
    review_day = _review_day(job, job_id)
    canonical_url = canonical_target_url(target_url)
    queue_id = queue_id_for(review_day, canonical_url)
    existing = load_item(queue_id) or {}
    existing_state = existing.get("state")
    state = existing_state if existing_state in VALID_STATES else PENDING_REVIEW
    now = _now()
    observed_at = str(job.get("created_at") or now)
    source_job_ids = list(dict.fromkeys([
        *(existing.get("source_job_ids") or []),
        *([existing.get("source_job_id")] if existing.get("source_job_id") else []),
        job_id,
    ]))
    source_job_dirs = list(dict.fromkeys([
        *(existing.get("source_job_dirs") or []),
        *([existing.get("source_job_dir")] if existing.get("source_job_dir") else []),
        os.path.abspath(job_dir),
    ]))
    observations = [
        observation for observation in (existing.get("observations") or [])
        if observation.get("source_job_id") != job_id
    ]
    observations.append({
        "source_job_id": job_id,
        "observed_at": observed_at,
        "verdict": domain_result.get("cloaking_verdict"),
        "score": domain_result.get("cloaking_score", 0),
        "evidence_path": domain_result.get("cloaking_evidence_path", ""),
    })
    previous_observed_at = str(existing.get("last_observed_at") or "")
    use_incoming = not previous_observed_at or observed_at >= previous_observed_at
    incoming_accounts = _account_names(job.get("allowed_accounts") or [])
    required_accounts = _account_names([
        *(existing.get("required_accounts") or []),
        *(existing.get("allowed_accounts") or []),
        *incoming_accounts,
    ])
    item = {
        **existing,
        "version": 3,
        "queue_id": queue_id,
        "state": state,
        "created_at": existing.get("created_at") or now,
        "updated_at": existing.get("updated_at") or now,
        "review_day": review_day,
        "canonical_url": canonical_url,
        "source_job_id": job_id if use_incoming else existing.get("source_job_id", job_id),
        "source_job_dir": os.path.abspath(job_dir) if use_incoming else existing.get("source_job_dir", ""),
        "source_job_ids": source_job_ids,
        "source_job_dirs": source_job_dirs,
        "observations": sorted(observations, key=lambda value: str(value.get("observed_at") or "")),
        "last_observed_at": max(previous_observed_at, observed_at),
        "target_url": target_url if use_incoming else existing.get("target_url", target_url),
        "domain": (
            domain_result.get("domain") or prepared.get("domain") or pt.normalize_domain(target_url)
            if use_incoming else existing.get("domain")
        ),
        "prepared": prepared if use_incoming else existing.get("prepared", prepared),
        "result": domain_result if use_incoming else existing.get("result", domain_result),
        "allowed_accounts": (
            incoming_accounts
            if use_incoming else existing.get("allowed_accounts", [])
        ),
        "required_accounts": required_accounts,
        "attempt_accounts": _account_names(existing.get("attempt_accounts") or []),
        "deliveries": _merge_deliveries([], existing.get("deliveries") or []),
        "expected_deliveries": max(
            0, int(existing.get("expected_deliveries", 0) or 0),
        ),
        "expected_deliveries_per_account": {
            _account_name(account): max(0, int(count or 0))
            for account, count in (
                existing.get("expected_deliveries_per_account") or {}
            ).items()
            if _account_name(account)
        },
        "send_attempts": list(existing.get("send_attempts") or []),
        "include_vncert": (
            bool(job.get("include_vncert", False))
            if use_incoming else bool(existing.get("include_vncert", False))
        ),
        "decision": existing.get("decision") or "",
        "decision_at": existing.get("decision_at") or "",
        "send_job_id": existing.get("send_job_id") or "",
        "last_error": existing.get("last_error") or "",
    }
    if existing.get("version", 0) >= 3 and item.get("state") == SENT:
        progress = delivery_summary(item)
        if progress["pending_accounts"] and progress["completed_accounts"]:
            item["state"] = PARTIAL
            item["completed_at"] = ""
            item["last_error"] = (
                f"Delivery is still pending for: {', '.join(progress['pending_accounts'])}"
            )
    if existing and _json_equal(item, existing):
        return existing
    item["updated_at"] = now
    _atomic_json(_item_path(queue_id), item)
    return item


def _state_rank(state: str) -> int:
    return {
        QUEUED_CLOAKING: 6,
        QUEUED_NORMAL: 6,
        SENT: 5,
        SKIPPED: 4,
        PARTIAL: 3,
        FAILED: 2,
        PENDING_REVIEW: 2,
    }.get(state, 0)


def consolidate_daily_duplicates() -> int:
    """Merge legacy job-keyed records into one recoverable daily URL record."""
    items = list_items()
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in items:
        review_day = str(item.get("review_day") or _local_day(item.get("created_at")))
        canonical_url = canonical_target_url(item.get("target_url") or item.get("canonical_url") or "")
        groups.setdefault((review_day, canonical_url), []).append(item)

    archive_dir = os.path.join(REVIEW_DIR, "archive")
    merged_count = 0
    for (review_day, canonical_url), duplicates in groups.items():
        canonical_id = queue_id_for(review_day, canonical_url)
        if len(duplicates) == 1 and duplicates[0].get("queue_id") == canonical_id:
            continue
        if any(
            item.get("state") in {QUEUED_CLOAKING, QUEUED_NORMAL}
            for item in duplicates
        ):
            continue
        ordered = sorted(
            duplicates,
            key=lambda item: str(item.get("last_observed_at") or item.get("updated_at") or ""),
        )
        latest = dict(ordered[-1])
        state_item = max(
            ordered,
            key=lambda item: (
                _state_rank(str(item.get("state") or "")),
                str(item.get("updated_at") or ""),
            ),
        )
        source_job_ids = []
        source_job_dirs = []
        observations = []
        required_accounts = []
        deliveries = []
        expected_by_account: dict[str, int] = {}
        expected_deliveries = 0
        attempts_by_id: dict[str, dict] = {}
        for item in ordered:
            source_job_ids.extend(item.get("source_job_ids") or [])
            if item.get("source_job_id"):
                source_job_ids.append(item["source_job_id"])
            source_job_dirs.extend(item.get("source_job_dirs") or [])
            if item.get("source_job_dir"):
                source_job_dirs.append(item["source_job_dir"])
            observations.extend(item.get("observations") or [{
                "source_job_id": item.get("source_job_id", ""),
                "observed_at": item.get("last_observed_at") or item.get("updated_at", ""),
                "verdict": (item.get("result") or {}).get("cloaking_verdict"),
                "score": (item.get("result") or {}).get("cloaking_score", 0),
                "evidence_path": (item.get("result") or {}).get("cloaking_evidence_path", ""),
            }])
            required_accounts.extend(
                item.get("required_accounts") or item.get("allowed_accounts") or []
            )
            deliveries = _merge_deliveries(deliveries, item.get("deliveries") or [])
            expected_deliveries = max(
                expected_deliveries, int(item.get("expected_deliveries", 0) or 0),
            )
            for account, count in (
                item.get("expected_deliveries_per_account") or {}
            ).items():
                name = _account_name(account)
                if name:
                    expected_by_account[name] = max(
                        expected_by_account.get(name, 0), int(count or 0),
                    )
            for attempt in item.get("send_attempts") or []:
                attempt_id = str(attempt.get("send_job_id") or "").strip()
                if attempt_id:
                    attempts_by_id[attempt_id] = attempt
        latest.update({
            "version": 3,
            "queue_id": canonical_id,
            "review_day": review_day,
            "canonical_url": canonical_url,
            "state": state_item.get("state", PENDING_REVIEW),
            "source_job_ids": list(dict.fromkeys(filter(None, source_job_ids))),
            "source_job_dirs": list(dict.fromkeys(filter(None, source_job_dirs))),
            "observations": sorted(
                {
                    (str(value.get("source_job_id") or ""), str(value.get("observed_at") or "")): value
                    for value in observations
                }.values(),
                key=lambda value: str(value.get("observed_at") or ""),
            ),
            "decision": state_item.get("decision") or "",
            "decision_at": state_item.get("decision_at") or "",
            "send_job_id": state_item.get("send_job_id") or "",
            "send_result": state_item.get("send_result") or latest.get("send_result"),
            "required_accounts": _account_names(required_accounts),
            "attempt_accounts": _account_names(
                state_item.get("attempt_accounts") or latest.get("attempt_accounts") or []
            ),
            "deliveries": deliveries,
            "expected_deliveries": expected_deliveries,
            "expected_deliveries_per_account": expected_by_account,
            "send_attempts": list(attempts_by_id.values()),
            "last_error": state_item.get("last_error") or "",
            "updated_at": _now(),
        })
        progress = delivery_summary(latest)
        if latest.get("state") not in {SKIPPED, PENDING_REVIEW} and progress["required_accounts"]:
            if not progress["pending_accounts"]:
                latest["state"] = SENT
                latest["last_error"] = ""
            elif progress["completed_accounts"]:
                latest["state"] = PARTIAL
        _atomic_json(_item_path(canonical_id), latest)
        os.makedirs(archive_dir, exist_ok=True)
        for item in duplicates:
            old_id = str(item.get("queue_id") or "")
            if not old_id or old_id == canonical_id:
                continue
            source_path = _item_path(old_id)
            if not os.path.isfile(source_path):
                continue
            archive_path = os.path.join(archive_dir, f"{old_id}.json")
            if os.path.exists(archive_path):
                archive_path = os.path.join(archive_dir, f"{old_id}.{uuid.uuid4().hex}.json")
            os.replace(source_path, archive_path)
            merged_count += 1
    return merged_count


def update_item(queue_id: str, **changes) -> dict:
    item = load_item(queue_id)
    if not item:
        raise ValueError(f"Cloaking review item not found: {queue_id}")
    if "state" in changes and changes["state"] not in VALID_STATES:
        raise ValueError(f"Invalid cloaking review state: {changes['state']}")
    item.update(changes)
    item["updated_at"] = _now()
    _atomic_json(_item_path(queue_id), item)
    return item


def mark_selected_for_send(
    queue_ids: list[str], *, state: str, decision: str, send_job_id: str,
    attempt_accounts: list[str] | None = None,
) -> list[dict]:
    if state not in {QUEUED_CLOAKING, QUEUED_NORMAL}:
        raise ValueError("Review send state must be QUEUED_CLOAKING or QUEUED_NORMAL")
    items = []
    for queue_id in dict.fromkeys(queue_ids):
        item = load_item(queue_id)
        if not item or item.get("state") not in ACTIVE_STATES:
            raise ValueError(f"Review item is no longer selectable: {queue_id}")
        items.append(item)
    updated = []
    decision_at = _now()
    normalized_attempts = _account_names(attempt_accounts or [])
    for item in items:
        required_accounts = _account_names(
            item.get("required_accounts") or item.get("allowed_accounts") or normalized_attempts
        )
        updated.append(update_item(
            item["queue_id"], state=state, decision=decision,
            decision_at=decision_at, send_job_id=send_job_id,
            required_accounts=required_accounts,
            attempt_accounts=normalized_attempts,
            last_error="",
        ))
    return updated


def mark_skipped(queue_ids: list[str]) -> list[dict]:
    items = []
    for queue_id in dict.fromkeys(queue_ids):
        item = load_item(queue_id)
        if not item or item.get("state") not in ACTIVE_STATES:
            raise ValueError(f"Review item is no longer selectable: {queue_id}")
        items.append(item)
    updated = []
    decision_at = _now()
    for item in items:
        updated.append(update_item(
            item["queue_id"], state=SKIPPED, decision="skip",
            decision_at=decision_at, last_error="",
        ))
    return updated


def complete_send(
    queue_id: str, domain_result: dict, *,
    attempted_accounts: list[str] | None = None, send_job_id: str = "",
    attempted_at: str = "",
) -> dict | None:
    """Merge one delivery attempt and finish only after every sender completes."""
    item = load_item(queue_id)
    if not item:
        return None
    now = _now()
    incoming_attempt_time = str(attempted_at or now)
    attempt_id = str(send_job_id or item.get("send_job_id") or "").strip()
    result_rows = list(domain_result.get("sent_to") or [])
    result_accounts = [row.get("account") for row in result_rows if isinstance(row, dict)]
    attempts = _account_names(
        attempted_accounts or item.get("attempt_accounts") or result_accounts
    )
    required_accounts = _account_names(
        item.get("required_accounts") or item.get("allowed_accounts") or attempts
    )
    sent_ok = int(domain_result.get("sent_ok", 0) or 0)
    already_sent = int(domain_result.get("already_sent", 0) or 0)
    sent_failed = int(domain_result.get("sent_failed", 0) or 0)

    incoming_rows = []
    for result_row in result_rows:
        row = _normalize_delivery(
            result_row, send_job_id=attempt_id, updated_at=now,
        )
        if row:
            incoming_rows.append(row)

    # Results produced before the delivery ledger did not include ``sent_to``.
    # A single attempted account can still be migrated without guessing which
    # sender completed the delivery.
    sent_in_rows = sum(row.get("status") == "sent" for row in incoming_rows)
    already_in_rows = sum(row.get("status") == "already_sent" for row in incoming_rows)
    missing_sent = max(0, sent_ok - sent_in_rows)
    missing_already = max(0, already_sent - already_in_rows)
    if (missing_sent or missing_already) and len(attempts) == 1:
        recipients = _prepared_recipients(item)
        for index, status in enumerate(
            ["sent"] * missing_sent + ["already_sent"] * missing_already
        ):
            incoming_rows.append(_normalize_delivery({
                "account": attempts[0],
                "to": recipients[index] if index < len(recipients) else (recipients[0] if recipients else ""),
                "draft": f"legacy_delivery_{index + 1}",
                "status": status,
                "ok": True,
            }, send_job_id=attempt_id, updated_at=now))
    incoming_rows = [row for row in incoming_rows if row]
    deliveries = _merge_deliveries(item.get("deliveries") or [], incoming_rows)

    drafts_sendable = max(0, int(domain_result.get("drafts_sendable", 0) or 0))
    attempt_delivery_counts = {
        account: len({
            _delivery_key(row)
            for row in incoming_rows if _account_name(row.get("account")) == account
        })
        for account in attempts
    }
    expected_this_attempt = max(
        [
            drafts_sendable,
            (
                (sent_ok + already_sent + sent_failed + len(attempts) - 1)
                // len(attempts)
                if attempts else 0
            ),
            *attempt_delivery_counts.values(),
        ],
        default=0,
    )
    expected_deliveries = max(
        int(item.get("expected_deliveries", 0) or 0), expected_this_attempt,
    )
    expected_by_account = {
        _account_name(account): max(0, int(count or 0))
        for account, count in (
            item.get("expected_deliveries_per_account") or {}
        ).items()
        if _account_name(account)
    }
    if expected_deliveries:
        for account in required_accounts:
            expected_by_account[account] = max(
                expected_by_account.get(account, 0), expected_deliveries,
            )

    send_attempts = list(item.get("send_attempts") or [])
    attempt_record = {
        "send_job_id": attempt_id,
        "attempted_at": incoming_attempt_time,
        "accounts": attempts,
        "sent_ok": sent_ok,
        "sent_failed": sent_failed,
        "already_sent": already_sent,
        "drafts_sendable": drafts_sendable,
        "recipients": list(dict.fromkeys(
            str(row.get("to") or "").strip()
            for row in incoming_rows if str(row.get("to") or "").strip()
        )),
    }
    existing_attempt_index = next((
        index for index, attempt in enumerate(send_attempts)
        if attempt_id and str(attempt.get("send_job_id") or "") == attempt_id
    ), None)
    if existing_attempt_index is None:
        send_attempts.append(attempt_record)
    else:
        previous_attempt = send_attempts[existing_attempt_index]
        attempt_record["attempted_at"] = (
            previous_attempt.get("attempted_at") or incoming_attempt_time
        )
        send_attempts[existing_attempt_index] = attempt_record
    send_attempts = sorted(
        send_attempts,
        key=lambda attempt: (
            str(attempt.get("attempted_at") or ""),
            str(attempt.get("send_job_id") or ""),
        ),
    )
    latest_attempt = max(
        send_attempts,
        key=lambda attempt: (
            str(attempt.get("attempted_at") or ""),
            str(attempt.get("send_job_id") or ""),
        ),
        default=attempt_record,
    )
    latest_attempt_time = str(latest_attempt.get("attempted_at") or incoming_attempt_time)
    latest_attempt_id = str(latest_attempt.get("send_job_id") or "")
    incoming_is_latest = attempt_id == latest_attempt_id

    candidate = {
        **item,
        "version": 3,
        "required_accounts": required_accounts,
        "attempt_accounts": attempts,
        "deliveries": deliveries,
        "expected_deliveries": expected_deliveries,
        "expected_deliveries_per_account": expected_by_account,
        "send_attempts": send_attempts,
    }
    progress = delivery_summary(candidate)
    if required_accounts:
        if not progress["pending_accounts"]:
            state = SENT
            error = ""
        elif progress["completed_accounts"]:
            state = PARTIAL
            error = (
                f"Delivery is still pending for: {', '.join(progress['pending_accounts'])}"
            )
        else:
            state = FAILED
            error = str(
                next((
                    row.get("error") for row in deliveries
                    if row.get("status") == "failed" and row.get("error")
                ), "")
                or domain_result.get("error")
                or domain_result.get("skipped")
                or "No report email was sent"
            )
    elif sent_ok or (already_sent and not sent_failed):
        # Compatibility for queue records created before sender scope existed.
        state = SENT
        error = ""
    else:
        state = FAILED
        error = str(
            domain_result.get("error")
            or domain_result.get("skipped")
            or "No report email was sent"
        )
    changes = {
        "version": 3,
        "state": state,
        "send_result": domain_result if incoming_is_latest else item.get("send_result"),
        "required_accounts": required_accounts,
        "attempt_accounts": (
            attempts if incoming_is_latest else _account_names(item.get("attempt_accounts") or [])
        ),
        "deliveries": deliveries,
        "expected_deliveries": expected_deliveries,
        "expected_deliveries_per_account": expected_by_account,
        "send_attempts": send_attempts,
        "completed_at": (
            item.get("completed_at") or latest_attempt_time if state == SENT else ""
        ),
        "last_attempt_at": latest_attempt_time,
        "latest_send_job_id": latest_attempt_id,
        "last_error": error,
    }
    if all(_json_equal(item.get(key), value) for key, value in changes.items()):
        return item
    return update_item(queue_id, **changes)


def update_cloaking_result(queue_id: str, cloaking_result: dict) -> dict:
    item = load_item(queue_id)
    if not item:
        raise ValueError(f"Cloaking review item not found: {queue_id}")
    result = dict(item.get("result") or {})
    result.update({
        "cloaking_result": cloaking_result,
        "cloaking_verdict": cloaking_result.get("verdict", "POSSIBLE"),
        "cloaking_score": cloaking_result.get("score", 0),
        "cloaking_signals": cloaking_result.get("signals") or [],
        "cloaking_evidence_path": cloaking_result.get("evidence_path", ""),
        "cloaking_review_reason": "operator_evidence",
    })
    return update_item(queue_id, result=result)


def _recipient_for_draft(prepared: dict, draft: str) -> str:
    recipients = prepared.get("recipients") or []
    draft_name = str(draft or "").lower()
    for recipient in recipients:
        if not isinstance(recipient, dict):
            continue
        channel = str(recipient.get("channel") or "").strip().lower()
        email = str(recipient.get("email") or recipient.get("to") or "").strip()
        if channel and email and f"_{channel}_report" in draft_name:
            return email
    available = _prepared_recipients({"prepared": prepared})
    return available[0] if len(available) == 1 else ""


def _delivery_rows_from_events(
    events_path: str, *, domain: str, prepared: dict, send_job_id: str,
    observed_at: str = "",
) -> list[dict]:
    try:
        with open(events_path, encoding="utf-8") as events_file:
            events = [
                json.loads(line) for line in events_file if line.strip()
            ]
    except (OSError, ValueError, TypeError):
        return []
    normalized_domain = str(domain or "").strip().lower().rstrip(".")
    rows = []
    known_recipient_by_draft = {}
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "email_result":
            continue
        event_domain = str(event.get("domain") or "").strip().lower().rstrip(".")
        if normalized_domain and event_domain != normalized_domain:
            continue
        draft = str(event.get("draft") or event.get("draft_file") or "").strip()
        recipient = str(event.get("to") or "").strip()
        if draft and recipient:
            known_recipient_by_draft[draft.lower()] = recipient
        rows.append({
            "account": event.get("account"),
            "to": recipient,
            "draft": draft,
            "status": "sent" if event.get("success") else "failed",
            "ok": bool(event.get("success")),
            "error": event.get("error") or "",
            "timestamp": event.get("timestamp") or observed_at,
            "send_job_id": send_job_id,
        })
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "draft_skipped":
            continue
        if event.get("reason") != "already_sent_today":
            continue
        event_domain = str(event.get("domain") or "").strip().lower().rstrip(".")
        if normalized_domain and event_domain != normalized_domain:
            continue
        draft = str(event.get("draft") or event.get("draft_file") or "").strip()
        recipient = (
            str(event.get("to") or "").strip()
            or known_recipient_by_draft.get(draft.lower(), "")
            or _recipient_for_draft(prepared, draft)
        )
        rows.append({
            "account": event.get("account"),
            "to": recipient,
            "draft": draft,
            "status": "already_sent",
            "ok": True,
            "error": "",
            "timestamp": event.get("timestamp") or observed_at,
            "send_job_id": send_job_id,
        })
    return rows


def sync_from_worker_jobs(worker_dir: str) -> int:
    """Migrate pending cases and legacy per-account delivery results."""
    if not os.path.isdir(worker_dir):
        return 0
    synced = 0
    for name in os.listdir(worker_dir):
        job_dir = os.path.join(worker_dir, name)
        try:
            with open(os.path.join(job_dir, "job.json"), encoding="utf-8") as job_file:
                job = json.load(job_file)
            with open(os.path.join(job_dir, "status.json"), encoding="utf-8") as status_file:
                status = json.load(status_file)
            try:
                with open(os.path.join(job_dir, "preflight.json"), encoding="utf-8") as preflight_file:
                    preflight = json.load(preflight_file)
            except (OSError, ValueError, TypeError):
                preflight = {}
        except (OSError, ValueError, TypeError):
            continue
        prepared_by_target = {
            item.get("target_url"): item
            for item in [
                *(preflight.get("ready") or []),
                *(preflight.get("cloaking_review") or []),
            ]
            if item.get("target_url")
        }
        latest_by_target = {
            item.get("target_url"): item
            for item in status.get("results") or [] if item.get("target_url")
        }
        for target_url, result in latest_by_target.items():
            if result.get("skipped") != "manual_review_required":
                continue
            prepared = prepared_by_target.get(target_url) or {
                "target_url": target_url,
                "domain": result.get("domain") or pt.normalize_domain(target_url),
                "recipients": [],
            }
            if not has_sendable_recipient(prepared):
                continue
            enqueue_worker_result(
                job=job, job_dir=job_dir,
                prepared=prepared,
                domain_result=result,
            )
            synced += 1
        review_queue_ids = dict(job.get("review_queue_ids") or {})
        attempted_accounts = _account_names(job.get("allowed_accounts") or [])
        send_job_id = str(job.get("job_id") or name)
        for target_url, queue_id in review_queue_ids.items():
            result = latest_by_target.get(target_url)
            current = load_item(queue_id)
            if not result or not current or result.get("skipped") == "manual_review_required":
                continue
            prepared = prepared_by_target.get(target_url) or current.get("prepared") or {}
            observed_at = str(
                status.get("finished_at") or job.get("created_at")
                or current.get("updated_at") or current.get("created_at") or ""
            )
            event_rows = _delivery_rows_from_events(
                os.path.join(job_dir, "events.jsonl"),
                domain=result.get("domain") or current.get("domain") or pt.normalize_domain(target_url),
                prepared=prepared,
                send_job_id=send_job_id,
                observed_at=observed_at,
            )
            result_rows = [
                {
                    **row,
                    "send_job_id": row.get("send_job_id") or send_job_id,
                    "updated_at": row.get("updated_at") or observed_at,
                }
                for row in result.get("sent_to") or [] if isinstance(row, dict)
            ]
            migrated_result = dict(result)
            migrated_result["sent_to"] = _merge_deliveries(
                result_rows, event_rows,
            )
            before = json.dumps(current, ensure_ascii=False, sort_keys=True)
            migrated = complete_send(
                queue_id, migrated_result,
                attempted_accounts=attempted_accounts,
                send_job_id=send_job_id,
                attempted_at=observed_at,
            )
            after = json.dumps(migrated, ensure_ascii=False, sort_keys=True)
            if before != after:
                synced += 1
    consolidate_daily_duplicates()
    return synced

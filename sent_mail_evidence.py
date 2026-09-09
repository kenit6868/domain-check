"""Account-scoped IMAP Sent Mail synchronisation and evidence indexing.

The synchroniser reads the selected account's Sent mailbox, parses message
headers and MIME attachment metadata in memory, and persists only a compact
sanitised index.  Message bodies, credentials and attachment bytes are never
written to the cache.  The index is consumed by ``report_statistics``; it is
kept separate from Provider Replies because inbound replies and outbound
reports are different workflows.
"""

from __future__ import annotations

import email
import hashlib
import imaplib
import json
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header
from email.utils import getaddresses, parsedate_to_datetime
from urllib.parse import urlsplit

import browser_evidence
import phishing_toolkit as pt
import provider_replies


MODULE_VERSION = 2
CACHE_VERSION = 1
CACHE_PATH = pt._runtime_path("sent_mail_evidence_cache.json")
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_KNOWN_SENT_FOLDERS = ("Sent", "Sent Items", "Sent Messages", "INBOX.Sent")
_INTERNALDATE_RE = re.compile(r'INTERNALDATE\s+"([^"]+)"', re.I)
_URL_RE = re.compile(r"https?://[^\s<>\]\[\"']+", re.I)


def account_key(value) -> str:
    return str(value or "").strip().lower()


def _decode(value) -> str:
    parts = []
    for part, charset in decode_header(value or ""):
        if isinstance(part, bytes):
            try:
                parts.append(part.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                parts.append(part.decode("utf-8", errors="replace"))
        else:
            parts.append(str(part))
    return "".join(parts).strip()


def _quote_mailbox(mailbox: str) -> str:
    value = str(mailbox or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def _parse_internal_date(value) -> datetime | None:
    raw = value[0] if isinstance(value, tuple) and value else value
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="replace")
    text = str(raw or "")
    match = _INTERNALDATE_RE.search(text)
    if not match:
        return None
    try:
        parsed = parsedate_to_datetime(match.group(1))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_header_date(value) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(str(value or ""))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _local_datetime(value: datetime | None, local_tz=None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(local_tz or timezone.utc)


def _find_sent_mailbox(conn, account: dict) -> str:
    configured = str(account.get("imap_sent_mailbox") or "").strip()
    if configured:
        return configured
    status, folders = conn.list()
    if status != "OK":
        raise RuntimeError("Không đọc được danh sách thư mục IMAP")
    parsed = []
    for line in folders or []:
        try:
            flags, _, mailbox = provider_replies._parse_imap_list_line(line)
        except (AttributeError, TypeError, ValueError):
            continue
        if mailbox:
            parsed.append((flags, mailbox))
            if "\\sent" in str(flags or "").lower():
                return mailbox
    names = [mailbox for _, mailbox in parsed]
    return next(
        (actual for actual in names for known in _KNOWN_SENT_FOLDERS
         if actual.lower() == known.lower()),
        "Sent",
    )


def _fetch_raw_message(payload) -> tuple[bytes | None, bytes | str | None]:
    """Return ``(raw RFC822 bytes, IMAP metadata)`` for varied IMAP shapes."""
    metadata = None
    raw = None
    for item in payload or []:
        if isinstance(item, tuple):
            if item and metadata is None:
                metadata = item[0]
            if len(item) > 1 and isinstance(item[1], (bytes, bytearray)) and item[1]:
                raw = bytes(item[1])
        elif isinstance(item, (bytes, bytearray)):
            value = bytes(item)
            if _INTERNALDATE_RE.search(value.decode("ascii", errors="replace")):
                metadata = value
            elif b"\r\n" in value or b"\n" in value:
                raw = value
    return raw, metadata


def _safe_urls(raw_body: str) -> list[str]:
    values = []
    for value in _URL_RE.findall(raw_body or ""):
        value = value.rstrip(".,);:")
        redacted = browser_evidence.redact_url(value)
        if redacted and redacted not in values:
            values.append(redacted)
    return values[:20]


def _host(value: str) -> str:
    try:
        candidate = value if "://" in value else "https://" + value
        return (urlsplit(candidate).hostname or "").lower().rstrip(".").removeprefix("www.")
    except ValueError:
        return ""


def _attachment_metadata(msg) -> dict:
    image_count = 0
    attachment_count = 0
    manifest_present = False
    sources = set()
    for part in msg.walk() if msg.is_multipart() else (msg,):
        filename = _decode(part.get_filename() or "")
        disposition = str(part.get("Content-Disposition") or "").lower()
        content_type = str(part.get_content_type() or "").lower()
        is_attachment = "attachment" in disposition or bool(filename)
        if not is_attachment:
            continue
        attachment_count += 1
        extension = os.path.splitext(filename)[1].lower()
        if content_type.startswith("image/") or extension in _IMAGE_EXTENSIONS:
            image_count += 1
        is_manifest = extension == ".json" or content_type == "application/json"
        if not is_manifest:
            continue
        manifest_present = True
        try:
            payload = part.get_payload(decode=True) or b""
            manifest = json.loads(payload.decode("utf-8-sig", errors="replace"))
            evidence_type = str(
                manifest.get("evidence_type") or manifest.get("capture_strategy") or ""
            ).strip().lower().replace("-", "_").replace(" ", "_")
            if evidence_type in {"manual", "manual_upload", "operator", "operator_upload"}:
                sources.add("manual")
            elif evidence_type:
                sources.add("automatic")
        except (AttributeError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            continue
    if image_count == 0:
        source = "none"
    elif len(sources) > 1:
        source = "mixed"
    elif sources:
        source = next(iter(sources))
    else:
        # An image-only attachment is evidence, but its origin cannot be
        # established without a manifest; keep it explicit rather than guess.
        source = "unknown"
    return {
        "evidence_source": source,
        "evidence_images": image_count,
        "evidence_manifest": manifest_present,
        "attachment_count": attachment_count,
    }


def parse_sent_message(
    uid: str,
    account: str,
    raw_message: bytes,
    internal_date: datetime | None = None,
    local_tz=None,
) -> dict:
    """Parse one Sent message without returning or persisting its body."""
    msg = email.message_from_bytes(raw_message)
    subject = _decode(msg.get("Subject"))
    recipients = [address for _, address in getaddresses([
        _decode(msg.get("To")), _decode(msg.get("Cc")),
    ]) if address]
    recipient = ", ".join(dict.fromkeys(recipients))
    header_date = _parse_header_date(msg.get("Date"))
    sent_dt = _local_datetime(internal_date or header_date, local_tz)
    body = provider_replies.extract_body(msg)
    urls = _safe_urls(body)
    target_url = urls[0] if urls else ""
    message_id = str(msg.get("Message-ID") or "").strip()
    in_reply_to = str(msg.get("In-Reply-To") or "").strip()
    references = str(msg.get("References") or "").strip()
    is_followup = bool(in_reply_to or references) and subject.lower().startswith(("re:", "fw:", "fwd:"))
    evidence = _attachment_metadata(msg)
    record = {
        "account": str(account or "").strip(),
        "uid": str(uid or "").strip(),
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "timestamp": sent_dt.isoformat() if sent_dt else str(msg.get("Date") or "").strip(),
        "date": sent_dt.date().isoformat() if sent_dt else "",
        "to": recipient,
        "subject": subject,
        "target_url": target_url,
        "domain": _host(target_url),
        # IMAP only proves that this message exists in Sent.  It does not prove
        # that a non-threaded message is a takedown report: operators may send
        # ordinary mail containing a URL to the same account.  The analytics
        # layer may enrich a known sent_log delivery with this record, but an
        # unmatched observation must never inflate report metrics.
        "delivery_kind": "provider_reply" if is_followup else "observed_sent",
        "send_mode": "imap_sent_sync",
        "report_channel": pt.report_channel_from_draft("", recipient),
        "success": True,
        "source": "imap_sent",
    }
    record.update(evidence)
    identity = message_id or hashlib.sha256(
        f"{account_key(account)}|{uid}|{record['timestamp']}|{subject}|{recipient}".encode("utf-8", "ignore")
    ).hexdigest()
    record["record_id"] = identity
    return record


def _read_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {"version": CACHE_VERSION, "accounts": {}}
    if not isinstance(value, dict):
        return {"version": CACHE_VERSION, "accounts": {}}
    accounts = value.get("accounts")
    return {"version": CACHE_VERSION, "accounts": accounts if isinstance(accounts, dict) else {}}


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
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def save_cached_records(account: str, records: list[dict]) -> None:
    data = _read_cache()
    key = account_key(account)
    if not key:
        return
    previous = data["accounts"].get(key, {})
    previous_records = previous.get("records") if isinstance(previous, dict) else []
    merged = {}
    for item in previous_records or []:
        if isinstance(item, dict):
            identity = str(item.get("record_id") or item.get("message_id") or "").strip()
            if identity:
                merged[identity] = item
    for item in records or []:
        if not isinstance(item, dict):
            continue
        sanitized = {
            key_name: item.get(key_name, "")
            for key_name in (
                "account", "uid", "record_id", "message_id", "in_reply_to", "references",
                "timestamp", "date", "to", "subject", "target_url", "domain",
                "delivery_kind", "send_mode", "report_channel", "success", "source",
                "evidence_source", "evidence_images", "evidence_manifest", "attachment_count",
            )
        }
        identity = str(sanitized.get("record_id") or sanitized.get("message_id") or "").strip()
        if identity:
            merged[identity] = sanitized
    data["accounts"][key] = {
        "account": str(account or "").strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "records": sorted(
            merged.values(), key=lambda row: str(row.get("timestamp") or ""), reverse=True,
        ),
    }
    _write_cache(data)


def load_cached_records(
    account: str,
    date_from: date | None = None,
    date_to: date | None = None,
    local_tz=None,
) -> list[dict]:
    key = account_key(account)
    entry = _read_cache()["accounts"].get(key, {})
    records = entry.get("records") if isinstance(entry, dict) else []
    valid = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        if str(record.get("account") or "").strip().lower() != key:
            continue
        value = record.get("timestamp") or record.get("date")
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            parsed = _parse_header_date(value)
        parsed = _local_datetime(parsed, local_tz)
        if date_from and (parsed is None or parsed.date() < date_from):
            continue
        if date_to and (parsed is None or parsed.date() > date_to):
            continue
        valid.append(dict(record))
    return valid


def clear_cached_records(account: str | None = None) -> bool:
    data = _read_cache()
    if account is None:
        had_values = bool(data["accounts"])
        data["accounts"] = {}
    else:
        key = account_key(account)
        had_values = key in data["accounts"]
        data["accounts"].pop(key, None)
    if had_values:
        _write_cache(data)
    return had_values


def sync_account_sent_mail(
    account: dict,
    date_from: date,
    date_to: date,
    *,
    local_tz=None,
    progress_callback=None,
    imap_factory=None,
    timeout: int = 60,
) -> dict:
    """Synchronise one account's Sent mailbox for an inclusive local date range."""
    if date_from > date_to:
        raise ValueError("Từ ngày không được lớn hơn Đến ngày")
    # Never fall back to the SMTP ``host`` here: a configured SMTP-only
    # account must not be contacted as if it were an IMAP server.
    host = account.get("imap_host")
    username = str(account.get("username") or "").strip()
    password = account.get("password")
    if not host or not username or not password:
        raise ValueError("Tài khoản thiếu cấu hình IMAP")
    factory = imap_factory or imaplib.IMAP4_SSL
    conn = factory(host, int(account.get("imap_port", 993)), timeout=timeout)
    records = []
    errors = []
    try:
        conn.login(username, password)
        sent_mailbox = _find_sent_mailbox(conn, account)
        status, _ = conn.select(_quote_mailbox(sent_mailbox), readonly=True)
        if status != "OK":
            raise RuntimeError(f"Không mở được thư mục {sent_mailbox}")
        since = (date_from - timedelta(days=1)).strftime("%d-%b-%Y")
        before = (date_to + timedelta(days=2)).strftime("%d-%b-%Y")
        status, data = conn.uid("search", None, "SINCE", since, "BEFORE", before)
        if status != "OK":
            raise RuntimeError("Không tìm được email trong thư mục Đã gửi")
        uids = list(reversed(data[0].split() if data and data[0] else []))
        total = len(uids)
        for position, uid in enumerate(uids, 1):
            status, payload = conn.uid(
                "fetch", uid, "(INTERNALDATE BODY.PEEK[])",
            )
            if status != "OK":
                errors.append(f"UID {uid!r}: fetch thất bại")
                continue
            raw_message, metadata = _fetch_raw_message(payload)
            if not raw_message:
                errors.append(f"UID {uid!r}: không có nội dung RFC822")
                continue
            internal_date = _parse_internal_date(metadata)
            try:
                parsed = parse_sent_message(
                    uid.decode("ascii", errors="replace") if isinstance(uid, bytes) else str(uid),
                    username, raw_message, internal_date, local_tz,
                )
            except (TypeError, ValueError, UnicodeError, email.errors.MessageParseError) as exc:
                errors.append(f"UID {uid!r}: {exc}")
                continue
            parsed_dt = _parse_header_date(parsed.get("timestamp"))
            if parsed_dt is None:
                try:
                    parsed_dt = datetime.fromisoformat(str(parsed.get("timestamp")).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    parsed_dt = None
            local_date = _local_datetime(parsed_dt, local_tz)
            if local_date is not None and not (date_from <= local_date.date() <= date_to):
                continue
            records.append(parsed)
            if progress_callback:
                progress_callback(position, total, len(records))
        save_cached_records(username, records)
        return {
            "success": True,
            "account": username,
            "sent_mailbox": sent_mailbox,
            "scanned": total,
            "matched": len(records),
            "with_images": sum(int(row.get("evidence_images") or 0) > 0 for row in records),
            "errors": errors,
        }
    finally:
        try:
            conn.logout()
        except Exception:
            pass

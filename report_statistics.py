"""Account-scoped report effectiveness analytics.

This module only reads local delivery/reply artifacts.  It never opens an IMAP
connection and never sends mail; the UI can therefore use it after the operator
has synchronized the selected mailbox in Provider Replies.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import provider_replies
from phishing_toolkit import SENT_LOG_PATH


MODULE_VERSION = 1
TICKET_UPDATE_ACCOUNT = "[ticket-update]"
REPORT_DELIVERY_KIND = "report"
PROVIDER_REPLY_DELIVERY_KIND = "provider_reply"

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_CHANNEL_LABELS = {
    "registrar": "Registrar",
    "registry": "Registry",
    "hosting": "Hosting/ISP",
    "cdn": "CDN/Cloudflare",
    "vncert": "VNCERT",
    "community": "Community form",
    "provider_reply": "Provider Reply",
    "other": "Khác",
}
_PROVIDER_HINTS = {
    "cloudflare": ("cloudflare", "Cloudflare"),
    "godaddy": ("godaddy", "GoDaddy"),
    "dynadot": ("dynadot", "Dynadot"),
    "tucows": ("tucows", "Tucows / OpenSRS"),
    "opensrs": ("opensrs", "Tucows / OpenSRS"),
    "namesilo": ("namesilo", "NameSilo"),
    "porkbun": ("porkbun", "Porkbun"),
    "sav": ("sav.com", "Sav.com"),
    "key_systems": ("key-systems", "Key-Systems / Instra"),
    "instra": ("instra", "Key-Systems / Instra"),
    "gname": ("gname", "Gname"),
    "cosmotown": ("cosmotown", "Cosmotown"),
    "realtime_register": ("realtime register", "Realtime Register"),
    "pdr": ("publicdomainregistry", "PublicDomainRegistry"),
    "alibaba": ("alibaba", "Alibaba Cloud"),
    "tencent": ("tencent", "DNSPod / Tencent"),
    "namecheap": ("namecheap", "Namecheap"),
    "spaceship": ("spaceship", "Spaceship"),
    "epik": ("epik", "Epik"),
    "west263": ("west263", "West263 / HKDNS"),
    "netcraft": ("netcraft", "Netcraft"),
}


def _account_key(value) -> str:
    return str(value or "").strip().lower()


def _parse_datetime(value, local_tz=None):
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, OverflowError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(local_tz or timezone.utc)


def _in_date_range(value, date_from: date | None, date_to: date | None, local_tz=None) -> bool:
    if date_from is None and date_to is None:
        return True
    parsed = _parse_datetime(value, local_tz)
    if parsed is None:
        return False
    if date_from is not None and parsed.date() < date_from:
        return False
    if date_to is not None and parsed.date() > date_to:
        return False
    return True


def _as_bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "ok", "success"}


def _as_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default


def _domain(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = text if "://" in text else "https://" + text
    try:
        host = urlsplit(candidate).hostname or ""
    except ValueError:
        host = ""
    return host.lower().rstrip(".").removeprefix("www.")


def _text_for_row(row: dict) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("report_channel", "provider", "provider_label", "draft_file", "to", "subject")
    ).lower()


def classify_report_channel(row: dict) -> str:
    """Return a stable channel key for registrar/registry/hosting analysis."""
    explicit = str(row.get("report_channel") or "").strip().lower().replace(" ", "_")
    aliases = {
        "registrar_report": "registrar", "registry_report": "registry",
        "hosting_isp": "hosting", "hosting_report": "hosting",
        "cloudflare": "cdn", "cdn_cloudflare": "cdn", "web_form": "community",
        "provider_reply": "provider_reply",
    }
    if explicit in _CHANNEL_LABELS:
        return explicit
    if explicit in aliases:
        return aliases[explicit]
    text = _text_for_row(row)
    if "vncert" in text or "vnc ert" in text:
        return "vncert"
    if any(term in text for term in ("hosting", "host_report", "isp", "origin_ip")):
        return "hosting"
    if "registry" in text:
        return "registry"
    if "registrar" in text:
        return "registrar"
    if any(term in text for term in ("cloudflare", "cdn")):
        return "cdn"
    if any(term in text for term in ("chongluadao", "coccoc", "community", "web form")):
        return "community"
    return "other"


def channel_label(channel: str) -> str:
    return _CHANNEL_LABELS.get(str(channel or ""), _CHANNEL_LABELS["other"])


def classify_provider(row: dict) -> tuple[str, str]:
    """Infer provider key/label from explicit metadata, recipient, or draft name."""
    explicit_key = str(row.get("provider_key") or row.get("provider") or "").strip().lower()
    explicit_label = str(row.get("provider_label") or "").strip()
    if explicit_key and explicit_key in provider_replies.PROVIDERS:
        return explicit_key, explicit_label or provider_replies.PROVIDERS[explicit_key][0]
    text = _text_for_row(row)
    for key, (hint, label) in _PROVIDER_HINTS.items():
        if hint in text:
            return key, label
    channel = classify_report_channel(row)
    return channel, channel_label(channel)


def evidence_metadata(row: dict) -> dict:
    """Normalize evidence fields, retaining an explicit unknown state for legacy rows."""
    source_raw = str(
        row.get("evidence_source") or row.get("evidence_mode") or row.get("evidence_type") or ""
    ).strip().lower().replace("-", "_").replace(" ", "_")
    count_present = False
    count = None
    for key in ("evidence_images", "attachment_count", "image_count"):
        if key in row and str(row.get(key) or "").strip() != "":
            count = _as_int(row.get(key), None)
            count_present = count is not None
            if count_present:
                break
    manifest_raw = str(row.get("evidence_manifest") or row.get("manifest") or "").strip().lower()
    manifest_present = manifest_raw in {"1", "true", "yes", "y"} or manifest_raw.endswith(".json")
    if count is None:
        attachment_text = str(row.get("attachments") or row.get("attachment_paths") or "")
        if attachment_text.strip():
            count = sum(
                1 for item in re.split(r"[;,|]", attachment_text)
                if os.path.splitext(item.strip())[1].lower() in _IMAGE_EXTENSIONS
            )
            count_present = True
            manifest_present = manifest_present or ".json" in attachment_text.lower()
    if source_raw in {"browser", "automatic", "auto", "browser_automatic", "dom_observed", "dom_destination_opened"}:
        source = "automatic"
    elif source_raw in {"manual", "manual_upload", "operator", "operator_upload", "upload"}:
        source = "manual"
    elif source_raw in {"mixed", "automatic_and_manual"}:
        source = "mixed"
    elif source_raw in {"none", "no_image", "no_images"}:
        source = "none"
    elif count_present and count == 0:
        source = "none"
    else:
        source = "unknown"
    if count is not None and count < 0:
        count = None
        count_present = False
    if source == "none":
        label = "Không có ảnh"
    elif source == "automatic":
        label = "Browser Evidence tự động"
    elif source == "manual":
        label = "Upload thủ công"
    elif source == "mixed":
        label = "Tự động + thủ công"
    elif count is not None and count > 0:
        label = "Có ảnh (chưa phân loại)"
    else:
        label = "Chưa có metadata"
    return {
        "source": source,
        "label": label,
        "images": count if count is not None else 0,
        "known": bool(count_present or source_raw),
        "manifest": manifest_present,
    }


def _delivery_kind(row: dict) -> str:
    value = str(row.get("delivery_kind") or row.get("send_kind") or "").strip().lower()
    if value in {PROVIDER_REPLY_DELIVERY_KIND, "provider_reply", "reply"}:
        return PROVIDER_REPLY_DELIVERY_KIND
    if value in {"ticket_update", "ticket"} or _account_key(row.get("account")) == TICKET_UPDATE_ACCOUNT:
        return "ticket_update"
    return REPORT_DELIVERY_KIND


def _normalize_sent_row(row: dict, local_tz=None) -> dict:
    timestamp = row.get("timestamp") or row.get("sent_at")
    channel = classify_report_channel(row)
    provider_key, provider_label = classify_provider(row)
    evidence = evidence_metadata(row)
    target_url = str(row.get("target_url") or row.get("reported_url") or "").strip()
    return {
        "account": str(row.get("account") or "").strip(),
        "account_key": _account_key(row.get("account")),
        "timestamp": str(timestamp or "").strip(),
        "datetime": _parse_datetime(timestamp, local_tz),
        "domain": _domain(row.get("domain")) or _domain(target_url),
        "target_url": target_url,
        "draft": str(row.get("draft_file") or row.get("draft") or "").strip(),
        "subject": str(row.get("subject") or "").strip(),
        "to": str(row.get("to") or "").strip(),
        "success": _as_bool(row.get("success")),
        "error": str(row.get("error") or "").strip(),
        "message_id": str(row.get("message_id") or "").strip(),
        "thread_message_id": str(row.get("thread_message_id") or row.get("in_reply_to") or "").strip(),
        "ticket_ref": str(row.get("ticket_ref") or "").strip(),
        "delivery_kind": _delivery_kind(row),
        "send_mode": str(row.get("send_mode") or "").strip(),
        "channel": channel,
        "channel_label": channel_label(channel),
        "provider_key": provider_key,
        "provider_label": provider_label,
        "evidence": evidence,
    }


def read_sent_log(path: str | None = None) -> tuple[list[dict], str]:
    """Read the credential-free CSV delivery log; return rows and a user-facing error."""
    path = path or SENT_LOG_PATH
    if not os.path.isfile(path):
        return [], ""
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle)), ""
    except (OSError, csv.Error, UnicodeError) as exc:
        return [], str(exc)


def _mail_value(mail, key, default=""):
    if isinstance(mail, dict):
        return mail.get(key, default)
    return getattr(mail, key, default)


def _reply_datetime(mail, local_tz=None):
    try:
        parsed = provider_replies.received_datetime(mail)
    except (AttributeError, TypeError, ValueError):
        parsed = None
    if parsed is None:
        parsed = _parse_datetime(_mail_value(mail, "server_date") or _mail_value(mail, "date"), local_tz)
    elif parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(local_tz or timezone.utc) if parsed else None


def classify_reply_outcome(mail) -> tuple[str, str]:
    request_type = str(_mail_value(mail, "request_type") or "").strip().lower()
    subject = str(_mail_value(mail, "subject") or "")
    body = str(_mail_value(mail, "body") or "")
    text = f"{subject}\n{body}".lower()
    if request_type == "delivery_failed" or provider_replies.is_delivery_failure(
        str(_mail_value(mail, "sender") or ""), subject,
    ):
        return "delivery_failed", "Gửi thất bại / bị trả lại"
    if request_type == "resolved" or re.search(
        r"\b(action has been taken|domain has been suspended|case is closed|finished analysing|resolved|taken down|taken offline|disabled|terminated|removed)\b",
        text,
        re.I,
    ):
        return "resolved", "Đã xử lý/takedown"
    if request_type == "acknowledgement" or re.search(
        r"\b(received your report|report has been received|ticket has been created|report confirmation|submission received)\b",
        text,
        re.I,
    ):
        return "acknowledged", "Đã tiếp nhận"
    if request_type in provider_replies.ACTION_REQUIRED_TYPES:
        return "action_required", "Yêu cầu bổ sung bằng chứng"
    return "manual_review", "Cần đọc thủ công"


def normalize_reply(mail, local_tz=None) -> dict:
    provider_key = str(_mail_value(mail, "provider") or "").strip().lower()
    provider_label = str(_mail_value(mail, "provider_label") or "").strip()
    if not provider_label:
        provider_label = provider_replies.PROVIDERS.get(provider_key, ("Khác / Chưa nhận diện", ()))[0]
    outcome, outcome_label = classify_reply_outcome(mail)
    domain = str(_mail_value(mail, "domain") or "").strip().lower().rstrip(".")
    return {
        "account": str(_mail_value(mail, "account") or "").strip(),
        "account_key": _account_key(_mail_value(mail, "account")),
        "datetime": _reply_datetime(mail, local_tz),
        "timestamp": str(_mail_value(mail, "server_date") or _mail_value(mail, "date") or "").strip(),
        "domain": domain,
        "provider_key": provider_key,
        "provider_label": provider_label,
        "message_id": str(_mail_value(mail, "message_id") or "").strip(),
        "ticket": str(_mail_value(mail, "ticket") or "").strip(),
        "subject": str(_mail_value(mail, "subject") or "").strip(),
        "request_type": str(_mail_value(mail, "request_type") or "").strip(),
        "request_label": str(_mail_value(mail, "request_label") or "").strip(),
        "source_mailbox": str(_mail_value(mail, "source_mailbox") or "INBOX").strip(),
        "outcome": outcome,
        "outcome_label": outcome_label,
    }


def _provider_matches(sent: dict, reply: dict) -> bool:
    sent_key = str(sent.get("provider_key") or "").lower()
    reply_key = str(reply.get("provider_key") or "").lower()
    if sent_key and reply_key and sent_key == reply_key:
        return True
    sent_channel = sent.get("channel")
    if sent_channel == "cdn" and reply_key == "cloudflare":
        return True
    if sent_channel in {"registrar", "registry", "hosting"}:
        return False
    return False


def _match_reply(sent: dict, replies: list[dict], used: set[int]) -> tuple[int | None, dict | None]:
    best = None
    sent_domain = _domain(sent.get("target_url")) or _domain(sent.get("domain"))
    sent_dt = sent.get("datetime")
    for index, reply in enumerate(replies):
        if index in used:
            continue
        reply_domain = _domain(reply.get("domain"))
        score = 0
        if sent.get("ticket_ref") and reply.get("ticket") and sent["ticket_ref"].lower() == reply["ticket"].lower():
            score += 120
        if sent.get("message_id") and sent["message_id"] in str(reply.get("message_id") or ""):
            score += 120
        if sent_domain and reply_domain:
            if sent_domain != reply_domain:
                continue
            score += 60
        elif not (sent.get("ticket_ref") and reply.get("ticket")):
            continue
        if _provider_matches(sent, reply):
            score += 30
        elif sent.get("provider_key") and reply.get("provider_key"):
            score -= 15
        reply_dt = reply.get("datetime")
        if sent_dt and reply_dt:
            delta = (reply_dt - sent_dt).total_seconds()
            if delta < 0:
                # A provider response received before the outbound delivery is
                # a stale/incorrect date header, not evidence for this report.
                continue
            if 0 <= delta <= 90 * 86400:
                score += 15
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, index, reply)
    return (best[1], best[2]) if best else (None, None)


def _summary_rows(records: list[dict], key: str) -> list[dict]:
    grouped = {}
    for record in records:
        value = str(record.get(key) or "—")
        grouped.setdefault(value, []).append(record)
    rows = []
    for value, items in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0].lower())):
        successful = sum(bool(item.get("success")) for item in items)
        resolved = sum(item.get("reply_outcome") == "resolved" for item in items)
        replied = sum(bool(item.get("reply_outcome")) for item in items)
        rows.append({
            key: value,
            "sent": len(items),
            "success": successful,
            "failed": len(items) - successful,
            "replies": replied,
            "resolved": resolved,
            "response_rate": round(replied / successful * 100, 2) if successful else 0.0,
            "takedown_rate": round(resolved / successful * 100, 2) if successful else 0.0,
        })
    return rows


def build_account_report(
    account: str,
    date_from: date | None = None,
    date_to: date | None = None,
    *,
    sent_rows: list[dict] | None = None,
    provider_mails: list | None = None,
    reply_log: dict | None = None,
    local_tz=None,
    sent_log_path: str | None = None,
) -> dict:
    """Build a report for exactly one sending mailbox and local date range."""
    account_key = _account_key(account)
    raw_rows, sent_error = read_sent_log(sent_log_path) if sent_rows is None else (sent_rows, "")
    normalized_sent = []
    for raw in raw_rows:
        row = _normalize_sent_row(raw, local_tz)
        if row["account_key"] != account_key or row["delivery_kind"] == "ticket_update":
            continue
        if not _in_date_range(row.get("datetime") or row.get("timestamp"), date_from, date_to, local_tz):
            continue
        normalized_sent.append(row)
    reports = [row for row in normalized_sent if row["delivery_kind"] == REPORT_DELIVERY_KIND]
    followups = [row for row in normalized_sent if row["delivery_kind"] == PROVIDER_REPLY_DELIVERY_KIND]

    if provider_mails is None:
        provider_mails = provider_replies.load_mail_cache(account)
    replies = []
    for mail in provider_mails or []:
        reply = normalize_reply(mail, local_tz)
        # A mailbox-scoped report must never borrow a reply whose cache record
        # has no account identity. Provider Replies writes the account on all
        # current records; a missing value is an invalid legacy record here.
        if reply["account_key"] != account_key:
            continue
        if not _in_date_range(reply.get("datetime") or reply.get("timestamp"), date_from, date_to, local_tz):
            continue
        replies.append(reply)

    used_replies = set()
    links = []
    for sent in reports:
        matched_index, matched = _match_reply(sent, replies, used_replies)
        if matched_index is not None:
            used_replies.add(matched_index)
        links.append({
            "domain": sent["domain"] or "—",
            "target_url": sent["target_url"] or "—",
            "to": sent["to"] or "—",
            "account": sent["account"] or account,
            "channel": sent["channel_label"],
            "provider": sent["provider_label"],
            "draft": sent["draft"] or "—",
            "subject": sent["subject"] or "—",
            "sent_at": sent["timestamp"] or "—",
            "send_status": "Đã gửi" if sent["success"] else "Thất bại",
            "evidence": sent["evidence"]["label"],
            "evidence_images": sent["evidence"]["images"],
            "reply_provider": matched["provider_label"] if matched else "—",
            "reply_subject": matched["subject"] if matched else "—",
            "reply_at": matched["timestamp"] if matched else "—",
            "reply_outcome": matched["outcome_label"] if matched else "Chưa có phản hồi",
            "reply_outcome_key": matched["outcome"] if matched else "unmatched",
            "ticket": matched["ticket"] if matched else sent["ticket_ref"] or "—",
        })
        sent["reply_outcome"] = matched["outcome"] if matched else ""

    successful = [row for row in reports if row["success"]]
    resolved = sum(row.get("reply_outcome") == "resolved" for row in successful)
    replied = sum(bool(row.get("reply_outcome")) for row in successful)
    unique_domains = {row["domain"] for row in reports if row.get("domain")}
    resolved_domains = {
        row["domain"] for row in successful
        if row.get("domain") and row.get("reply_outcome") == "resolved"
    }
    evidence_counts = {"automatic": 0, "manual": 0, "mixed": 0, "none": 0, "unknown": 0}
    image_count = 0
    for row in reports:
        evidence = row["evidence"]
        evidence_counts[evidence["source"]] = evidence_counts.get(evidence["source"], 0) + 1
        image_count += int(evidence.get("images") or 0)

    outcome_counts = {}
    for reply in replies:
        outcome_counts[reply["outcome"]] = outcome_counts.get(reply["outcome"], 0) + 1

    provider_rows = _summary_rows(reports, "provider_label")
    channel_rows = _summary_rows(reports, "channel_label")
    subject_rows = _summary_rows(reports, "subject")
    draft_rows = _summary_rows(reports, "draft")
    warnings = []
    if sent_error:
        warnings.append(f"Không đọc được sent_log.csv: {sent_error}")
    if not reports:
        warnings.append("Tài khoản này chưa có delivery report trong khoảng ngày đã chọn.")
    legacy_unknown = sum(not row["evidence"]["known"] for row in reports)
    if legacy_unknown:
        warnings.append(
            f"{legacy_unknown} delivery cũ chưa có evidence metadata; không suy đoán là có hoặc không có ảnh."
        )
    if not replies:
        warnings.append(
            "Chưa có Provider Replies trong cache của tài khoản này; hãy đồng bộ Inbox + Thư rác ở page Phản hồi NCC."
        )
    return {
        "version": MODULE_VERSION,
        "account": account,
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
        "sent_total": len(reports),
        "domain_total": len(unique_domains),
        "sent_success": len(successful),
        "sent_failed": len(reports) - len(successful),
        "followups_sent": len(followups),
        "reply_total": len(replies),
        "linked_reply_total": replied,
        "unmatched_reply_total": len(replies) - len(used_replies),
        "resolved_total": resolved,
        "resolved_domain_total": len(resolved_domains),
        "response_rate": round(replied / len(successful) * 100, 2) if successful else 0.0,
        "takedown_rate": round(resolved / len(successful) * 100, 2) if successful else 0.0,
        "evidence": {
            **evidence_counts,
            "images_total": image_count,
            "with_images": sum(evidence_counts[key] for key in ("automatic", "manual", "mixed")),
            "without_images": evidence_counts["none"],
            "unknown": evidence_counts["unknown"],
        },
        "outcomes": outcome_counts,
        "by_provider": provider_rows,
        "by_channel": channel_rows,
        "by_subject": subject_rows,
        "by_draft": draft_rows,
        "links": links,
        "warnings": warnings,
        "source": {
            "sent_log": sent_log_path or SENT_LOG_PATH,
            "provider_cache": True,
            "reply_log_entries": sum(
                1 for value in (reply_log or {}).values()
                if isinstance(value, dict) and _account_key(value.get("account")) == account_key
            ) if isinstance(reply_log, dict) else 0,
        },
    }

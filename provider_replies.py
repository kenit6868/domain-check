"""Read provider replies and prepare reviewable, provider-aware drafts."""

from __future__ import annotations

import email
import imaplib
import json
import mimetypes
import os
import re
import smtplib
import requests
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime
from html import unescape

# Proxy support — tái dùng từ phishing_toolkit để tránh duplicate code
try:
    from phishing_toolkit import _SMTPWithProxy, _SMTPWithProxySSL, _parse_proxy_url as _pt_parse_proxy
    _HAS_SMTP_PROXY = True
except ImportError:
    _HAS_SMTP_PROXY = False


PROVIDERS = {
    "godaddy": ("GoDaddy", ("godaddy",)), "dynadot": ("Dynadot", ("dynadot",)),
    "tucows": ("Tucows / OpenSRS", ("tucows", "opensrs")), "namesilo": ("NameSilo", ("namesilo",)),
    "porkbun": ("Porkbun", ("porkbun",)), "sav": ("Sav.com", ("sav.com", "abuse.sav.com")),
    "key_systems": ("Key-Systems / Instra", ("key-systems", "key systems", "instra", "cleandns")),
    "gname": ("Gname", ("gname",)), "cosmotown": ("Cosmotown", ("cosmotown",)),
    "realtime_register": ("Realtime Register", ("realtime register", "mydomainprovider")),
    "pdr": ("PublicDomainRegistry", ("publicdomainregistry", "public domain registry")),
    "alibaba": ("Alibaba Cloud", ("alibabacloud", "alibaba cloud")),
    "tencent": ("DNSPod / Tencent", ("dnspod", "tencent")),
    "namecheap": ("Namecheap", ("namecheap",)), "spaceship": ("Spaceship", ("spaceship",)),
    "epik": ("Epik", ("epik",)), "west263": ("West263 / HKDNS", ("west263", "hkdns")),
    "cloudflare": ("Cloudflare", ("cloudflare",)), "netcraft": ("Netcraft", ("netcraft",)),
}
RULES = (
    ("legal_evidence", ("trademark", "authorization letter", "power of attorney", "copyright", "legal declaration", "proof of ownership")),
    ("identity", ("identity document", "passport", "government-issued", "company registration", "proof of identity")),
    ("screenshot", ("screenshot", "screen shot", "image evidence")),
    ("full_url", ("full url", "complete url", "exact url", "url path", "affected url")),
    ("official_url", ("official website", "legitimate website", "official url")),
    ("technical_evidence", ("additional evidence", "supporting evidence", "technical details", "redirect chain")),
    ("clarification", ("additional information", "more information", "clarification", "please provide")),
    ("acknowledgement", ("received your report", "report has been received", "ticket has been created")),
    ("resolved", ("action has been taken", "domain has been suspended", "case is closed", "finished analysing", "resolved")),
)
LABELS = {"legal_evidence": "Chứng cứ/cam kết pháp lý", "identity": "Giấy tờ định danh/doanh nghiệp",
          "screenshot": "Ảnh chụp bằng chứng", "full_url": "URL vi phạm đầy đủ",
          "official_url": "Website chính thức", "technical_evidence": "Bằng chứng kỹ thuật bổ sung",
          "clarification": "Thông tin bổ sung", "acknowledgement": "Đã tiếp nhận báo cáo",
          "resolved": "Đã xử lý/đóng vụ việc", "delivery_failed": "Gửi thất bại / bị từ chối",
          "manual_review": "Cần đọc thủ công"}
URL_RE = re.compile(r"https?://[^\s<>\]\[\"']+", re.I)
DOMAIN_RE = re.compile(r"(?<!@)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\b", re.I)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
ACTION_REQUIRED_TYPES = {
    "legal_evidence", "identity", "screenshot", "full_url", "official_url",
    "technical_evidence", "clarification",
}
# These providers remain visible in the inbox/history table but are never
# offered by the reply workflow.
EXCLUDED_REPLY_PROVIDERS = {"namecheap"}
REQUESTED_TERMS = {
    "legal_evidence": r"(?:trademark|authorization letter|power of attorney|copyright|legal declaration|proof of ownership)",
    "identity": r"(?:identity|passport|government-issued|company registration|document)",
    "screenshot": r"(?:screenshot|screen shot|image evidence|image)",
    "full_url": r"(?:full|complete|exact|affected|reported)?\s*(?:url|link|web address)",
    "official_url": r"(?:official|legitimate)\s*(?:website|url|site)",
    "technical_evidence": r"(?:additional evidence|supporting evidence|technical details|redirect chain|logs?)",
    "clarification": r"(?:information|details|clarification|evidence|proof)",
}
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provider_mail_cache.json")
REPLY_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provider_reply_log.json")
EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence", "provider_replies")


@dataclass
class ProviderMail:
    uid: str; account: str; provider: str; provider_label: str; sender: str; reply_to: str
    subject: str; date: str; message_id: str; body: str; request_type: str; request_label: str
    domain: str; urls: list[str]; ticket: str; channel: str; risk: str
    server_date: str = ""
    def to_dict(self): return asdict(self)


def _decode(value):
    result = []
    for part, charset in decode_header(value or ""):
        if isinstance(part, bytes):
            try: result.append(part.decode(charset or "utf-8", errors="replace"))
            except LookupError: result.append(part.decode("utf-8", errors="replace"))
        else: result.append(part)
    return "".join(result)


def extract_body(msg):
    plain, html = [], []
    for part in (msg.walk() if msg.is_multipart() else (msg,)):
        if "attachment" in (part.get("Content-Disposition") or "").lower(): continue
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"): continue
        raw = part.get_payload(decode=True)
        if raw is None: continue
        try: text = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
        except LookupError: text = raw.decode("utf-8", errors="replace")
        (plain if kind == "text/plain" else html).append(text)
    value = "\n".join(plain)
    if not value:
        value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", "\n".join(html))
        value = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", value)
        value = re.sub(r"(?s)<[^>]+>", " ", value)
    return unescape(value).strip()[:100000]


def detect_provider(sender, subject, body=""):
    sender_addr = parseaddr(sender)[1].lower()
    sender_domain = sender_addr.split("@")[-1] if "@" in sender_addr else ""
    # Ưu tiên match theo sender domain — đáng tin hơn body text
    for key, (label, signals) in PROVIDERS.items():
        if any(signal in sender_domain for signal in signals):
            return key, label
    # Fallback: match trong subject + phần NCC tự viết (đã loại bỏ phần quote/forwarded)
    request_part = provider_request_text(body)
    text = f"{sender} {subject} {request_part[:2000]}".lower()
    for key, (label, signals) in PROVIDERS.items():
        if any(signal in text for signal in signals):
            return key, label
    return "unknown", "Khác / Chưa nhận diện"


def instructed_reply_address(provider, body):
    """Return only a provider-specific reply address explicitly stated in the mail."""
    if provider == "cloudflare":
        return next((address for address in EMAIL_RE.findall(body or "") if address.lower() == "abusereply@cloudflare.com"), "")
    return ""


def provider_request_text(body):
    """Keep the provider-authored part and exclude quoted/original reports."""
    text = body or ""
    markers = (
        r"below is the report we received\s*:", r"below is the report\s*:",
        r"[- ]{3,}original message[- ]{3,}", r"[- ]{3,}forwarded message[- ]{3,}",
        r"the attached returned message", r"original message\s*:",
    )
    positions = []
    for marker in markers:
        match = re.search(marker, text, re.I)
        if match: positions.append(match.start())
    return text[:min(positions)] if positions else text


def _has_explicit_request(text, request_type):
    """Require an actual instruction, not a keyword found in a legal footer."""
    term = REQUESTED_TERMS.get(request_type)
    if not term:
        return False
    normalized = " ".join((text or "").lower().split())
    patterns = (
        rf"(?:please|kindly)\s+(?:provide|send|submit|attach|share|supply|confirm|clarify)[^.!?]{{0,180}}{term}",
        rf"(?<!if )(?<!should )(?:we|our team)\s+(?:need|require|request)[^.!?]{{0,180}}{term}",
        rf"(?:^|[.!?]\s+)(?:provide|send|submit|attach|share|supply)\s+(?:us\s+)?{term}",
    )
    return any(re.search(pattern, normalized, re.I) for pattern in patterns)


def classify_request(subject, body):
    subject_lower = (subject or "").lower()
    status_text = f"{subject}\n{provider_request_text(body)}".lower()
    # Cloudflare's standard forwarding notice is a terminal acknowledgement:
    # the report has already been routed to the parties that can act on it.
    # The generic footer may still mention abusereply@cloudflare.com, but that
    # invitation alone is not a request for more evidence and must not make the
    # message actionable.
    forwarded_notice = (
        "report has been forwarded to the website owner" in status_text
        or "report to the relevant hosting provider" in status_text
        or "report has been forwarded to the relevant hosting provider" in status_text
    )
    # A provider can acknowledge receipt and request evidence in the same
    # message. In that case the explicit instruction wins over the receipt.
    for kind, _ in RULES:
        if kind in ACTION_REQUIRED_TYPES and _has_explicit_request(status_text, kind):
            return kind, LABELS[kind], "approval_required" if kind in ("legal_evidence", "identity") else "review"
    if forwarded_notice or any(value in subject_lower for value in (
        "abuse complaint submitted", "thanks for your report", "report confirmation",
        "report received", "submission received",
    )) or any(value in status_text for value in ("received your report", "report has been received", "ticket has been created")):
        return "acknowledgement", LABELS["acknowledgement"], "review"
    if any(value in subject_lower for value in ("finished analysing", "case closed", "resolved")):
        return "resolved", LABELS["resolved"], "review"
    text = status_text
    for kind, words in RULES:
        if any(word in text for word in words):
            return kind, LABELS[kind], "approval_required" if kind in ("legal_evidence", "identity") else "review"
    return "manual_review", LABELS["manual_review"], "review"


def needs_reply(mail):
    """Return whether a provider message explicitly requires a reviewed response."""
    if is_delivery_failure(mail.sender, mail.subject):
        return False
    if mail.provider in EXCLUDED_REPLY_PROVIDERS:
        return False
    if mail.request_type not in ACTION_REQUIRED_TYPES:
        return False
    if mail.provider == "cloudflare":
        request_text = provider_request_text(mail.body).lower()
        # A forwarding confirmation is complete and does not need a reply even
        # though its footer offers abusereply@cloudflare.com for questions.
        if (
            "report has been forwarded to the website owner" in request_text
            or "report has been forwarded to the relevant hosting provider" in request_text
            or "forwarded this report to the relevant hosting provider" in request_text
        ):
            return False
        # Keep Cloudflare requests that explicitly ask for evidence/details.
        return any(phrase in request_text for phrase in (
            "could not detect any abusive or malicious content",
            "please provide relevant and specific information",
            "additional details, context or evidence",
            "please provide additional evidence",
            "please provide additional information",
        ))
    # Do not promote acknowledgements merely because a footer contains words
    # such as "legal", "additional information" or "reply". Require an
    # explicit request for the particular evidence/information category.
    return _has_explicit_request(provider_request_text(mail.body), mail.request_type)


def is_delivery_failure(sender, subject, content_type=""):
    sender_address = parseaddr(sender)[1].lower()
    subject_lower = (subject or "").lower()
    return (
        "mailer-daemon" in sender_address or sender_address.startswith("postmaster@")
        or any(value in subject_lower for value in (
            "undelivered mail", "delivery status notification", "mail delivery failed",
            "returned to sender", "delivery failure", "message not delivered",
        ))
        or (content_type or "").lower() == "multipart/report"
    )


def _ticket(text):
    patterns = (
        # Require a separator so prose such as "report identification number
        # included in the subject" cannot produce the bogus ticket "included".
        r"report\s*(?:identification number|number|no\.?|id|#)\s*[:#-]\s*([A-Z0-9][A-Z0-9._/-]{3,})",
        r"^\s*\[([A-F0-9]{12,})\]\s*:",
        r"(?:ticket|case|reference|request)\s*(?:number|no\.?|id|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match: return match.group(1).rstrip(".,;:")
    return ""


def _deobfuscate_domains(text):
    value = re.sub(r"(?i)\s*(?:\[\.\]|\(dot\)|\[dot\])\s*", ".", text or "")
    value = re.sub(r"(?i)\bhxxps://", "https://", value)
    return re.sub(r"(?i)\bhxxp://", "http://", value)


def extract_reply_context(mail):
    """Extract only facts explicitly repeated in a provider response."""
    text = _deobfuscate_domains(mail.body)
    reported_url = ""
    patterns = (
        r"reported urls?\s*:\s*(https?://[^\s<>]+)",
        r"(?:report (?:regarding|received for)|reported (?:url|domain)|affected url|domain)\s*[:\-]\s*(https?://[^\s]+|(?:[a-z0-9-]+\.)+[a-z]{2,63}(?:/[^\s]*)?)",
        r"(?:regarding)\s*[:\-]\s*(https?://[^\s]+|(?:[a-z0-9-]+\.)+[a-z]{2,63}(?:/[^\s]*)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = match.group(1).rstrip(".,);:")
            reported_url = value if value.lower().startswith(("http://", "https://")) else "https://" + value
            break
    if not reported_url:
        provider_domains = tuple(signal for _, signals in PROVIDERS.values() for signal in signals if "." in signal)
        reported_url = next((u.rstrip(".,);:") for u in URL_RE.findall(text) if not any(p in u.lower() for p in provider_domains)), "")

    official_url = ""
    match = re.search(r"(?:official|legitimate) (?:website|url)\s*[:\-]\s*(https?://[^\s]+)", text, re.I)
    if match: official_url = match.group(1).rstrip(".,);:")

    evidence = ""
    for label in ("Logs or other evidence of abuse", "Additional details", "Supporting evidence", "Evidence"):
        match = re.search(
            rf"{re.escape(label)}\s*:\s*(.+?)(?=\n\s*(?:Reported URLs?\s*:|Cloudflare is not the hosting provider|Cloudflare Trust & Safety)|\Z)",
            text, re.I | re.S,
        )
        if match:
            evidence = match.group(1).strip()[:6000]
            break
    return {"reported_url": reported_url, "official_url": official_url, "evidence": evidence}


def received_datetime(mail):
    """Return the IMAP server receipt time, falling back to the message Date header."""
    try:
        raw_date = getattr(mail, "server_date", "") or mail.date
        return parsedate_to_datetime(raw_date) if raw_date else None
    except (TypeError, ValueError, OverflowError):
        return None


def _imap_internal_date(fetch_metadata):
    """Extract IMAP INTERNALDATE and convert it to an RFC-style parseable value."""
    text = fetch_metadata.decode("ascii", errors="replace") if isinstance(fetch_metadata, bytes) else str(fetch_metadata or "")
    match = re.search(r'INTERNALDATE\s+"([^"]+)"', text, re.I)
    return match.group(1) if match else ""


def parse_message(uid, account, raw_message):
    msg = email.message_from_bytes(raw_message); subject = _decode(msg.get("Subject")); sender = _decode(msg.get("From")); body = extract_body(msg)
    reply_to = parseaddr(_decode(msg.get("Reply-To")) or sender)[1]
    provider, label = detect_provider(sender, subject, body); kind, request_label, risk = classify_request(subject, body)
    is_bounce = is_delivery_failure(sender, subject, msg.get_content_type())
    if is_bounce:
        provider, label = "mail_server", "Mail server"
        kind, request_label, risk = "delivery_failed", LABELS["delivery_failed"], "review"
    urls = list(dict.fromkeys(u.rstrip(".,);:") for u in URL_RE.findall(body)))[:20]
    sender_domains = {parseaddr(sender)[1].split("@")[-1].lower(), parseaddr(reply_to)[1].split("@")[-1].lower()}
    domain = next((d.lower() for d in DOMAIN_RE.findall(f"{subject}\n{body}") if d.lower() not in sender_domains), "")
    lower = f"{sender} {body[:4000]}".lower()
    instructed = instructed_reply_address(provider, body)
    if instructed: reply_to = instructed
    if is_bounce: channel = "no_reply"; reply_to = ""
    elif any(x in lower for x in ("no-reply", "noreply", "do not reply")): channel = "no_reply"
    elif any(x in lower for x in ("portal", "log in", "login", "web form")) and urls: channel = "portal"
    elif reply_to: channel = "email"
    else: channel = "manual"
    if provider == "cloudflare" and reply_to.lower() == "abusereply@cloudflare.com": channel = "email"
    return ProviderMail(str(uid), account, provider, label, sender, reply_to, subject, msg.get("Date", ""),
                        (msg.get("Message-ID") or "").strip(), body, kind, request_label, domain, urls,
                        "" if is_bounce else _ticket(f"{subject}\n{body}"), channel, risk)


def fetch_provider_mail(account, limit=None, unread_only=False, date_from=None, date_to=None, progress_callback=None):
    host = account.get("imap_host") or account.get("host")
    if not host or not account.get("username") or not account.get("password"): raise ValueError("Tài khoản thiếu cấu hình IMAP")
    conn = imaplib.IMAP4_SSL(host, int(account.get("imap_port", 993)))
    try:
        conn.login(account["username"], account["password"]); status, _ = conn.select(account.get("imap_mailbox", "INBOX"), readonly=True)
        if status != "OK": raise RuntimeError("Không mở được INBOX")
        criteria = []
        if unread_only: criteria.append("UNSEEN")
        # IMAP searches by the server's internal calendar date. Query one extra
        # day on each side so UTC/local-midnight messages are available; the UI
        # performs the final exact filter after timezone conversion.
        if date_from: criteria.extend(("SINCE", (date_from - timedelta(days=1)).strftime("%d-%b-%Y")))
        if date_to:
            criteria.extend(("BEFORE", (date_to + timedelta(days=2)).strftime("%d-%b-%Y")))
        if not criteria: criteria.append("ALL")
        status, data = conn.uid("search", None, *criteria)
        if status != "OK": raise RuntimeError("Không tìm được email")
        result = []
        all_uids = data[0].split()
        if limit is None:
            selected_uids = list(reversed(all_uids))
        else:
            clean_limit = int(limit)
            if clean_limit <= 0:
                raise ValueError("Số lượng email phải là số nguyên dương")
            selected_uids = list(reversed(all_uids[-clean_limit:]))
        total = len(selected_uids)
        for position, uid in enumerate(selected_uids, start=1):
            status, payload = conn.uid("fetch", uid, "(INTERNALDATE BODY.PEEK[])")
            if status == "OK" and payload and isinstance(payload[0], tuple):
                item = parse_message(uid.decode(), account["username"], payload[0][1])
                item.server_date = _imap_internal_date(payload[0][0])
                # Giữ lại: email từ provider đã nhận diện, HOẶC email có request cụ thể (không phải manual_review)
                # Bỏ qua: provider unknown + manual_review = email rác không liên quan
                if item.provider != "unknown" or item.request_type != "manual_review":
                    result.append(item)
            if progress_callback:
                progress_callback(result, position, total)
        return result
    finally:
        try: conn.logout()
        except Exception: pass


def _read_cache_file():
    try:
        with open(CACHE_PATH, encoding="utf-8") as handle:
            value = json.load(handle)
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def load_mail_cache(account_name):
    """Load cached messages for one mailbox. Invalid entries are ignored."""
    result = []
    for value in _read_cache_file().get(account_name, []):
        try:
            mail = ProviderMail(**value)
            if is_delivery_failure(mail.sender, mail.subject):
                mail.provider = "mail_server"; mail.provider_label = "Mail server"
                mail.request_type = "delivery_failed"; mail.request_label = LABELS["delivery_failed"]
                mail.reply_to = ""; mail.ticket = ""; mail.channel = "no_reply"; mail.risk = "review"
            else:
                kind, label, risk = classify_request(mail.subject, mail.body)
                mail.request_type, mail.request_label, mail.risk = kind, label, risk
                instructed = instructed_reply_address(mail.provider, mail.body)
                if instructed:
                    mail.reply_to = instructed; mail.channel = "email"
            result.append(mail)
        except (TypeError, ValueError):
            continue
    return result


def save_mail_cache(account_name, mails):
    """Atomically persist parsed messages; credentials are never written."""
    data = _read_cache_file()
    data[account_name] = [mail.to_dict() for mail in mails]
    temp_path = CACHE_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, CACHE_PATH)


def clear_mail_cache(account_name=None):
    if account_name is None:
        try: os.remove(CACHE_PATH)
        except FileNotFoundError: pass
        return
    data = _read_cache_file()
    data.pop(account_name, None)
    if data:
        temp_path = CACHE_PATH + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, CACHE_PATH)
    else:
        try: os.remove(CACHE_PATH)
        except FileNotFoundError: pass


def reply_log_key(mail):
    """Stable key for one received message across UI reruns and cache reloads."""
    identity = mail.uid or mail.message_id or f"{mail.ticket}|{mail.subject}"
    return f"{mail.account}|{identity}"


def load_reply_log():
    try:
        with open(REPLY_LOG_PATH, encoding="utf-8") as handle:
            value = json.load(handle)
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def record_reply_sent(mail, subject, recipient):
    """Persist a successful provider reply; credentials and body are not stored."""
    data = load_reply_log()
    record = {
        "account": mail.account,
        "uid": mail.uid,
        "message_id": mail.message_id,
        "provider": mail.provider_label,
        "domain": mail.domain,
        "ticket": mail.ticket,
        "subject": subject,
        "recipient": recipient,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    data[reply_log_key(mail)] = record
    temp_path = REPLY_LOG_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, REPLY_LOG_PATH)
    return record


def _sent_message_matches(mail, sent_message):
    """Match a Sent message to its Inbox request using thread headers or ticket."""
    thread_headers = " ".join((
        sent_message.get("In-Reply-To", ""),
        sent_message.get("References", ""),
    ))
    if mail.message_id and mail.message_id in thread_headers:
        return True
    sent_subject = _decode(sent_message.get("Subject", ""))
    if mail.ticket and len(mail.ticket) >= 6 and mail.ticket.lower() in sent_subject.lower():
        return True
    return False


def sync_sent_reply_status(account, mails, date_from=None, date_to=None):
    """Import reply status from the IMAP Sent folder for the supplied Inbox mails."""
    if not mails:
        return {"success": True, "matched": 0, "error": ""}
    host = account.get("imap_host") or account.get("host")
    if not host or not account.get("username") or not account.get("password"):
        return {"success": False, "matched": 0, "error": "Tài khoản thiếu cấu hình IMAP"}
    conn = imaplib.IMAP4_SSL(host, int(account.get("imap_port", 993)))
    try:
        conn.login(account["username"], account["password"])
        status, folders = conn.list()
        if status != "OK":
            return {"success": False, "matched": 0, "error": "Không đọc được danh sách thư mục IMAP"}
        sent_folder = account.get("imap_sent_mailbox", "")
        if not sent_folder:
            for raw_line in folders or []:
                flags, _, mailbox = _parse_imap_list_line(raw_line)
                if "\\sent" in flags.lower() and mailbox:
                    sent_folder = mailbox
                    break
        if not sent_folder:
            known = ("Sent", "Sent Items", "Sent Messages", "INBOX.Sent")
            parsed_names = [_parse_imap_list_line(line)[2] for line in (folders or [])]
            sent_folder = next((actual for actual in parsed_names for name in known if actual.lower() == name.lower()), "Sent")
        status, _ = conn.select(sent_folder, readonly=True)
        if status != "OK":
            return {"success": False, "matched": 0, "error": f"Không mở được thư mục {sent_folder}"}
        criteria = []
        if date_from: criteria.extend(("SINCE", (date_from - timedelta(days=1)).strftime("%d-%b-%Y")))
        if date_to: criteria.extend(("BEFORE", (date_to + timedelta(days=2)).strftime("%d-%b-%Y")))
        if not criteria: criteria.append("ALL")
        status, data = conn.uid("search", None, *criteria)
        if status != "OK":
            return {"success": False, "matched": 0, "error": "Không tìm được email trong thư mục Đã gửi"}
        sent_messages = []
        for uid in reversed(data[0].split()):
            status, payload = conn.uid(
                "fetch", uid,
                "(BODY.PEEK[HEADER.FIELDS (SUBJECT TO DATE MESSAGE-ID IN-REPLY-TO REFERENCES)])",
            )
            if status == "OK" and payload and isinstance(payload[0], tuple):
                sent_messages.append(email.message_from_bytes(payload[0][1]))
        reply_log = load_reply_log()
        matched = 0
        for mail in mails:
            if reply_log_key(mail) in reply_log:
                continue
            sent = next((message for message in sent_messages if _sent_message_matches(mail, message)), None)
            if not sent:
                continue
            reply_log[reply_log_key(mail)] = {
                "account": mail.account, "uid": mail.uid, "message_id": mail.message_id,
                "provider": mail.provider_label, "domain": mail.domain, "ticket": mail.ticket,
                "subject": _decode(sent.get("Subject", "")),
                "recipient": parseaddr(_decode(sent.get("To", "")))[1],
                "sent_at": sent.get("Date", ""), "source": "imap_sent",
            }
            matched += 1
        if matched:
            temp_path = REPLY_LOG_PATH + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(reply_log, handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, REPLY_LOG_PATH)
        return {"success": True, "matched": matched, "error": ""}
    except Exception as exc:
        return {"success": False, "matched": 0, "error": str(exc)}
    finally:
        try: conn.logout()
        except Exception: pass


def mark_mails_seen(account, uids):
    """Mark the supplied IMAP UIDs as read and return a per-call summary."""
    clean_uids = [str(uid).strip() for uid in uids if str(uid).strip()]
    if not clean_uids:
        return {"success": True, "marked": 0, "error": ""}
    host = account.get("imap_host") or account.get("host")
    if not host or not account.get("username") or not account.get("password"):
        return {"success": False, "marked": 0, "error": "Tài khoản thiếu cấu hình IMAP"}
    conn = imaplib.IMAP4_SSL(host, int(account.get("imap_port", 993)))
    try:
        conn.login(account["username"], account["password"])
        status, _ = conn.select(account.get("imap_mailbox", "INBOX"), readonly=False)
        if status != "OK":
            return {"success": False, "marked": 0, "error": "Không mở được INBOX để cập nhật"}
        status, _ = conn.uid("store", ",".join(clean_uids), "+FLAGS.SILENT", "(\\Seen)")
        if status != "OK":
            return {"success": False, "marked": 0, "error": "IMAP không chấp nhận lệnh Seen"}
        return {"success": True, "marked": len(clean_uids), "error": ""}
    except Exception as exc:
        return {"success": False, "marked": 0, "error": str(exc)}
    finally:
        try: conn.logout()
        except Exception: pass


def build_reply(mail, details):
    warnings = []; target = (details.get("reported_url") or "").strip(); official = (details.get("official_url") or "").strip(); evidence = (details.get("evidence") or "").strip(); redirect_url = (details.get("redirect_url") or "").strip(); button_label = (details.get("button_label") or "Register/Login").strip()
    subject = "Re: " + re.sub(r"^(re:\s*)+", "", mail.subject, flags=re.I)
    if mail.provider == "cloudflare" and mail.request_type in ("technical_evidence", "clarification") and mail.ticket:
        subject = f"Re: Phishing Report - Report ID {mail.ticket}"
    intro = f"Dear {mail.provider_label} Abuse Team,\n\nThank you for your response.\n" + (f"Reference/Ticket: {mail.ticket}\n" if mail.ticket else "") + "\n"
    if mail.request_type == "full_url": core = f"The complete reported URL is:\n{target or '[PLEASE ADD THE COMPLETE REPORTED URL]'}"; warnings += [] if target else ["NCC yêu cầu URL đầy đủ nhưng chưa có URL."]
    elif mail.request_type == "official_url": core = f"The official website for comparison is:\n{official or '[PLEASE ADD THE OFFICIAL WEBSITE URL]'}"; warnings += [] if official else ["NCC yêu cầu website chính thức nhưng chưa có URL."]
    elif mail.request_type == "screenshot":
        if details.get("screenshot_attached"):
            core = "Please find the requested supporting screenshot attached."
            if details.get("urlscan_result"): core += f"\nURLScan analysis: {details['urlscan_result']}"
        else:
            core = "[ATTACH VERIFIED SCREENSHOTS BEFORE SENDING]"; warnings.append("Cần tạo hoặc đính kèm ảnh thật trước khi gửi.")
    elif mail.request_type in ("technical_evidence", "clarification"):
        if mail.provider == "cloudflare":
            core = (
                f"Regarding Report ID: {mail.ticket or '[REPORT ID]'},\n\n"
                "We are providing additional technical evidence regarding the reported domain:\n\n"
                f"Reported URL:\n{target}"
            )
            if redirect_url:
                core += (
                    f"\n\nThe website contains a \"{button_label}\" button that directly links users to an external destination:\n\n"
                    f"{redirect_url}\n\n"
                    "This behavior was verified directly in the page DOM. The button contains:\n\n"
                    f'href="{redirect_url}"\n\n'
                    "Steps to reproduce:\n\n"
                    f"1. Visit {target}\n"
                    f"2. Locate the \"{button_label}\" button.\n"
                    "3. Click the button.\n"
                    f"4. The user is directed to {redirect_url}."
                )
            if evidence:
                core += f"\n\nAdditional verified observations:\n{evidence}"
            if details.get("screenshot_attached") and redirect_url:
                core += (
                    "\n\nWe have attached a screenshot showing the reported website, the "
                    f"\"{button_label}\" button, and the corresponding DOM element confirming the external destination URL."
                )
            if target and redirect_url:
                source_domain = re.sub(r"^https?://", "", target, flags=re.I).split("/", 1)[0]
                destination_domain = re.sub(r"^https?://", "", redirect_url, flags=re.I).split("/", 1)[0]
                core += (
                    "\n\nPlease investigate this redirect/linking behavior and the relationship between "
                    f"{source_domain} and {destination_domain}, including whether the destination is being used "
                    "in connection with fraudulent or phishing activity."
                )
            if not target: warnings.append("Chưa có URL website vi phạm đầy đủ.")
            if not redirect_url: warnings.append("Chưa có URL href/redirect đã xác minh.")
        else:
            core = f"Please find the requested information below:\n{evidence or '[PLEASE ADD VERIFIED INFORMATION]'}"
            warnings += [] if evidence else ["Chưa có thông tin bổ sung đã xác minh."]
    elif mail.request_type in ("legal_evidence", "identity"): core = "We are reviewing your request. The requested legal or identity documentation will only be supplied after authorization by the appropriate representative."; warnings.append("Bắt buộc duyệt thủ công; không tự tạo tuyên bố hoặc giấy tờ.")
    elif mail.request_type == "acknowledgement": core = "Thank you for confirming receipt. Please keep us informed of any action taken or additional information required."
    elif mail.request_type == "resolved": core = "Thank you for the update. We acknowledge the status of this case."
    else: core = evidence or "[PLEASE WRITE A RESPONSE BASED ON THE PROVIDER REQUEST]"; warnings.append("Không nhận diện chắc chắn yêu cầu; cần đọc và sửa thủ công.")
    if target and mail.request_type != "full_url" and not (mail.provider == "cloudflare" and mail.request_type in ("technical_evidence", "clarification")): core += f"\n\nReported URL:\n{target}"
    body = intro + core + f"\n\nKind regards,\n{(details.get('contact_name') or 'Reporter').strip()}"
    if details.get("contact_email"): body += f"\n{details['contact_email'].strip()}"
    return subject, body, warnings


def provider_message_vi(mail):
    """Vietnamese reading aid for the provider request; never used as sent content."""
    context = extract_reply_context(mail)
    target = context.get("reported_url") or mail.domain or "(không xác định)"
    ticket = mail.ticket or "(không có)"
    if mail.provider == "cloudflare" and mail.request_type in ("technical_evidence", "clarification"):
        return (
            "Xin chào,\n\n"
            f"Cloudflare đã nhận báo cáo phishing liên quan đến: {target}.\n\n"
            "Cloudflare chưa phát hiện được nội dung lạm dụng hoặc độc hại. Nếu muốn Cloudflare "
            "điều tra thêm, người báo cáo cần cung cấp thông tin liên quan, cụ thể để họ tiếp tục đánh giá vụ việc.\n\n"
            "Khi phản hồi, hãy gửi tới abusereply@cloudflare.com và cung cấp:\n"
            f"- Mã báo cáo trong tiêu đề: {ticket}\n"
            "- Chi tiết, bối cảnh hoặc bằng chứng bổ sung về nội dung đã báo cáo.\n\n"
            "Phần còn lại của email là bản sao báo cáo ban đầu Cloudflare đã nhận."
        )
    if mail.provider == "cloudflare" and mail.request_type == "acknowledgement":
        return (
            f"Cloudflare đã tiếp nhận báo cáo {ticket} liên quan đến {target}. "
            "Báo cáo đã được chuyển cho chủ website và/hoặc nhà cung cấp hosting liên quan. "
            "Đây là thông báo tiếp nhận/chuyển tiếp, không yêu cầu phản hồi."
        )
    messages = {
        "full_url": "NCC yêu cầu cung cấp URL vi phạm đầy đủ và chính xác.",
        "official_url": "NCC yêu cầu cung cấp URL website chính thức để đối chiếu.",
        "screenshot": "NCC yêu cầu cung cấp ảnh chụp màn hình làm bằng chứng.",
        "technical_evidence": "NCC yêu cầu bổ sung bằng chứng hoặc chi tiết kỹ thuật.",
        "clarification": "NCC yêu cầu giải thích và cung cấp thêm thông tin về báo cáo.",
        "legal_evidence": "NCC yêu cầu bằng chứng pháp lý hoặc giấy tờ chứng minh quyền sở hữu thương hiệu.",
        "identity": "NCC yêu cầu giấy tờ xác minh danh tính hoặc doanh nghiệp.",
        "acknowledgement": "NCC xác nhận đã nhận báo cáo; hiện không yêu cầu phản hồi.",
        "resolved": "NCC thông báo vụ việc đã được xử lý hoặc đóng.",
        "delivery_failed": "Đây là thông báo gửi thư thất bại, không phải yêu cầu phản hồi từ NCC.",
    }
    return (
        f"Bản dịch/tóm tắt theo nội dung đã nhận diện:\n\n{messages.get(mail.request_type, 'Email cần được đọc và đánh giá thủ công.')}\n\n"
        f"NCC: {mail.provider_label}\nMã vụ việc: {ticket}\nURL/domain liên quan: {target}"
    )


def build_reply_vi(mail, details):
    """Vietnamese review copy matching the generated English reply."""
    target = (details.get("reported_url") or "").strip()
    redirect_url = (details.get("redirect_url") or "").strip()
    button_label = (details.get("button_label") or "Đăng ký/Đăng nhập").strip()
    evidence = (details.get("evidence") or "").strip()
    ticket = mail.ticket or "[MÃ BÁO CÁO]"
    if mail.provider == "cloudflare" and mail.request_type in ("technical_evidence", "clarification"):
        source_domain = re.sub(r"^https?://", "", target, flags=re.I).split("/", 1)[0]
        destination_domain = re.sub(r"^https?://", "", redirect_url, flags=re.I).split("/", 1)[0]
        value = (
            "Kính gửi Đội ngũ Trust & Safety của Cloudflare,\n\n"
            "Cảm ơn phản hồi của quý vị.\n\n"
            f"Liên quan đến mã báo cáo: {ticket},\n\n"
            "Chúng tôi cung cấp thêm bằng chứng kỹ thuật về domain đã báo cáo:\n\n"
            f"URL đã báo cáo:\n{target}"
        )
        if redirect_url:
            value += (
                f"\n\nWebsite có nút \"{button_label}\" liên kết trực tiếp người dùng tới một địa chỉ bên ngoài:\n\n"
                f"{redirect_url}\n\n"
                "Hành vi này đã được xác minh trực tiếp trong DOM của trang. Nút có thuộc tính:\n\n"
                f'href="{redirect_url}"\n\n'
                "Các bước tái hiện:\n\n"
                f"1. Truy cập {target}\n"
                f"2. Tìm nút \"{button_label}\".\n"
                "3. Bấm vào nút.\n"
                f"4. Người dùng được chuyển tới {redirect_url}."
            )
        if evidence:
            value += f"\n\nQuan sát bổ sung đã xác minh:\n{evidence}"
        if details.get("screenshot_attached") and redirect_url:
            value += (
                f"\n\nẢnh đính kèm hiển thị website vi phạm, nút \"{button_label}\" và element DOM "
                "tương ứng xác nhận URL đích bên ngoài."
            )
        if target and redirect_url:
            value += (
                f"\n\nVui lòng điều tra hành vi redirect/liên kết và mối quan hệ giữa {source_domain} với "
                f"{destination_domain}, bao gồm khả năng URL đích được sử dụng cho hoạt động gian lận hoặc phishing."
            )
        value += "\n\nTrân trọng."
        return value
    translations = {
        "full_url": f"Nội dung trả lời cung cấp URL vi phạm đầy đủ:\n{target}",
        "official_url": f"Nội dung trả lời cung cấp website chính thức:\n{details.get('official_url') or '[CHƯA NHẬP]'}",
        "screenshot": "Nội dung trả lời thông báo ảnh bằng chứng được đính kèm.",
        "technical_evidence": f"Nội dung trả lời cung cấp bằng chứng kỹ thuật:\n{evidence or '[CHƯA NHẬP]'}",
        "clarification": f"Nội dung trả lời cung cấp thông tin giải thích bổ sung:\n{evidence or '[CHƯA NHẬP]'}",
    }
    return translations.get(mail.request_type, "Vui lòng đối chiếu kỹ nội dung tiếng Anh trước khi gửi.")


def download_evidence_image(image_url, domain="evidence"):
    """Download a URLScan screenshot to the local evidence directory."""
    if not image_url.lower().startswith("https://urlscan.io/screenshots/"):
        raise ValueError("Chỉ chấp nhận screenshot từ urlscan.io")
    response = requests.get(image_url, timeout=30)
    response.raise_for_status()
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "image/" not in content_type or len(response.content) > 15 * 1024 * 1024:
        raise ValueError("Dữ liệu tải về không phải ảnh hợp lệ hoặc vượt quá 15 MB")
    safe_domain = re.sub(r"[^a-zA-Z0-9._-]", "_", domain or "evidence")[:100]
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    path = os.path.join(EVIDENCE_DIR, f"{safe_domain}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.png")
    with open(path, "wb") as handle: handle.write(response.content)
    return path


def save_uploaded_evidence(filename, content, domain="evidence"):
    """Validate and persist a manually captured PNG/JPEG evidence image."""
    extension = os.path.splitext(filename or "")[1].lower()
    signatures = {
        ".png": b"\x89PNG\r\n\x1a\n",
        ".jpg": b"\xff\xd8\xff",
        ".jpeg": b"\xff\xd8\xff",
    }
    signature = signatures.get(extension)
    if not signature or not content.startswith(signature):
        raise ValueError("Chỉ chấp nhận ảnh PNG hoặc JPEG hợp lệ")
    if len(content) > 15 * 1024 * 1024:
        raise ValueError("Ảnh vượt quá giới hạn 15 MB")
    safe_domain = re.sub(r"[^a-zA-Z0-9._-]", "_", domain or "evidence")[:100]
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    path = os.path.join(EVIDENCE_DIR, f"{safe_domain}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}{extension}")
    with open(path, "wb") as handle:
        handle.write(content)
    return path


def capture_dom_link_evidence(target_url, domain="evidence"):
    """Capture a browser screenshot with a highlighted Register/Login DOM link.

    The page is inspected without clicking the element or submitting any form.
    A clearly labelled evidence panel is injected into the screenshot so the
    selected element and its resolved href are visible in one image.
    """
    if not (target_url or "").lower().startswith(("http://", "https://")):
        return {"success": False, "error": "URL phải bắt đầu bằng http:// hoặc https://", "path": "", "href": "", "label": ""}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "error": "Chưa cài Playwright", "path": "", "href": "", "label": ""}

    browser = None
    try:
        with sync_playwright() as playwright:
            # Một số website đặt Cloudflare challenge trước nội dung thật và luôn
            # giữ browser headless ở trang "Just a moment...". Dùng Chrome có
            # giao diện để challenge có thể hoàn tất (hoặc người dùng xác minh)
            # trước khi đọc DOM; công cụ vẫn không click hay submit element nào.
            try:
                browser = playwright.chromium.launch(channel="chrome", headless=False)
            except Exception:
                browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                accept_downloads=False,
                service_workers="block",
                ignore_https_errors=True,
            )
            page = context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            try:
                page.wait_for_function(
                    """
                    () => [...document.querySelectorAll('a, button, [role="button"]')]
                        .some(el => /(đăng\\s*k[ýy]|đăng\\s*nhập|register|sign\\s*up|login|log\\s*in)/i
                            .test((el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim()))
                    """,
                    timeout=45000,
                )
            except Exception:
                pass
            result = page.evaluate("""
                () => {
                    const wanted = /(đăng\\s*k[ýy]|đăng\\s*nhập|register|sign\\s*up|login|log\\s*in)/i;
                    const nodes = [...document.querySelectorAll('a, button, [role="button"]')];
                    const candidates = nodes.map((el, index) => {
                        const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim();
                        const href = el.href || el.getAttribute('href') || el.getAttribute('data-href') || '';
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        const visible = rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                        return {el, index, text, href, visible};
                    }).filter(x => x.visible && wanted.test(x.text));
                    candidates.sort((a, b) => Number(Boolean(b.href)) - Number(Boolean(a.href)) || a.index - b.index);
                    const picked = candidates[0];
                    if (!picked) {
                        const challenge = /just a moment|security verification|cloudflare/i.test(document.title + ' ' + document.body.innerText);
                        return {error: challenge
                            ? 'Trang vẫn đang ở bước xác minh Cloudflare. Hãy hoàn tất xác minh trong cửa sổ Chrome rồi thử lại.'
                            : 'Không tìm thấy nút Đăng ký/Đăng nhập hiển thị trên trang'};
                    }
                    const el = picked.el;
                    el.scrollIntoView({block: 'center', inline: 'center'});
                    el.style.setProperty('outline', '5px solid #ff1f1f', 'important');
                    el.style.setProperty('outline-offset', '5px', 'important');
                    el.style.setProperty('box-shadow', '0 0 0 8px rgba(255,255,0,.8)', 'important');
                    const href = el.href || el.getAttribute('href') || el.getAttribute('data-href') || '';
                    const outer = el.outerHTML.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    const panel = document.createElement('section');
                    panel.id = '__codex_dom_evidence';
                    panel.innerHTML = `
                      <div style="font:700 15px Arial;color:#fff;margin-bottom:5px">Automated read-only DOM inspection</div>
                      <div style="font:13px Arial;color:#aab4bf;margin-bottom:18px">No button click or form submission was performed</div>
                      <div style="font:700 14px Arial;color:#ffd54f;margin-bottom:6px">Source URL</div>
                      <div style="font:13px Consolas;word-break:break-all;color:#fff;margin-bottom:12px">${location.href.replace(/</g, '&lt;')}</div>
                      <div style="font:700 14px Arial;color:#ffd54f;margin-bottom:6px">Captured at (UTC)</div>
                      <div style="font:13px Consolas;color:#fff;margin-bottom:18px">${new Date().toISOString()}</div>
                      <div style="font:700 17px Arial;color:#ffd54f;margin-bottom:8px">Selected control</div>
                      <div style="font:16px Arial;margin-bottom:18px;color:#fff">${picked.text.replace(/</g, '&lt;')}</div>
                      <div style="font:700 17px Arial;color:#ffd54f;margin-bottom:8px">Resolved href / destination</div>
                      <div style="font:15px Consolas;word-break:break-all;color:#7ee787;margin-bottom:20px">${String(href).replace(/</g, '&lt;')}</div>
                      <div style="font:700 17px Arial;color:#ffd54f;margin-bottom:8px">DOM element</div>
                      <pre style="white-space:pre-wrap;word-break:break-all;font:13px Consolas;line-height:1.5;color:#fff;background:#252c35;border:1px solid #647382;padding:14px">${outer}</pre>`;
                    panel.style.cssText = 'position:fixed;z-index:2147483647;right:0;top:0;width:42vw;height:100vh;box-sizing:border-box;padding:30px;background:#151a20;border-left:5px solid #ff1f1f;overflow:auto;text-align:left';
                    document.documentElement.appendChild(panel);
                    document.body.style.setProperty('width', '58vw', 'important');
                    document.body.style.setProperty('overflow-x', 'hidden', 'important');
                    return {href, label: picked.text, html: el.outerHTML};
                }
            """)
            if result.get("error"):
                return {"success": False, "error": result["error"], "path": "", "href": "", "label": ""}
            page.wait_for_timeout(500)
            safe_domain = re.sub(r"[^a-zA-Z0-9._-]", "_", domain or "evidence")[:100]
            os.makedirs(EVIDENCE_DIR, exist_ok=True)
            path = os.path.join(EVIDENCE_DIR, f"{safe_domain}_dom_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}.png")
            page.screenshot(path=path, full_page=False)
            context.close()
            return {"success": True, "error": "", "path": path, "href": result.get("href", ""),
                    "label": result.get("label", ""), "html": result.get("html", "")}
    except Exception as exc:
        return {"success": False, "error": str(exc), "path": "", "href": "", "label": ""}
    finally:
        if browser:
            try: browser.close()
            except Exception: pass


def _parse_imap_list_line(raw_line):
    """Return (flags, delimiter, mailbox) from a standard IMAP LIST response."""
    line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
    match = re.match(r'^\((?P<flags>[^)]*)\)\s+(?P<delimiter>NIL|"[^"]*")\s+(?P<mailbox>.+?)\s*$', line)
    if not match: return "", "", ""
    delimiter = match.group("delimiter")
    delimiter = "" if delimiter == "NIL" else delimiter.strip('"')
    mailbox = match.group("mailbox").strip()
    if len(mailbox) >= 2 and mailbox[0] == mailbox[-1] == '"':
        mailbox = mailbox[1:-1].replace(r'\"', '"').replace(r"\\", "\\")
    return match.group("flags"), delimiter, mailbox


def _append_sent_copy(account, raw_message):
    """Best-effort IMAP APPEND so SMTP replies also appear in webmail Sent."""
    host = account.get("imap_host") or account.get("host")
    if not host: return "Thiếu imap_host"
    conn = imaplib.IMAP4_SSL(host, int(account.get("imap_port", 993)))
    try:
        conn.login(account["username"], account["password"])
        status, folders = conn.list()
        if status != "OK": return "Không đọc được danh sách thư mục IMAP"
        sent_folder = account.get("imap_sent_mailbox", "")
        if not sent_folder:
            for raw_line in folders or []:
                flags, _, mailbox = _parse_imap_list_line(raw_line)
                if "\\sent" in flags.lower() and mailbox:
                    sent_folder = mailbox
                    break
        if not sent_folder:
            known = ("Sent", "Sent Items", "Sent Messages", "INBOX.Sent")
            parsed_names = [_parse_imap_list_line(line)[2] for line in (folders or [])]
            sent_folder = next((actual for actual in parsed_names for name in known if actual.lower() == name.lower()), "Sent")
        status, response = conn.append(
            sent_folder, "(\\Seen)", imaplib.Time2Internaldate(datetime.now().timestamp()), raw_message,
        )
        if status != "OK": return f"IMAP APPEND vào {sent_folder} thất bại: {response}"
        return ""
    except Exception as exc:
        return str(exc)
    finally:
        try: conn.logout()
        except Exception: pass


def send_threaded_reply(account, mail, subject, body, attachments=None, proxy_str=None):
    if mail.channel != "email" or not mail.reply_to: return {"success": False, "error": "Không có Reply-To hợp lệ."}
    msg = EmailMessage(); msg["From"] = account["username"]; msg["To"] = mail.reply_to; msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False, usegmt=True)
    msg["Message-ID"] = make_msgid(domain=account["username"].split("@")[-1] if "@" in account["username"] else None)
    if mail.message_id: msg["In-Reply-To"] = mail.message_id; msg["References"] = mail.message_id
    msg.set_content(body)
    for path in attachments or []:
        if not path or not os.path.isfile(path): continue
        mime, _ = mimetypes.guess_type(path); major, minor = (mime or "application/octet-stream").split("/", 1)
        with open(path, "rb") as handle:
            msg.add_attachment(handle.read(), maintype=major, subtype=minor, filename=os.path.basename(path))
    try:
        port = int(account.get("port", 465 if account.get("ssl") else 587))
        use_ssl = bool(account.get("ssl")) or port == 465

        if proxy_str and _HAS_SMTP_PROXY:
            # Route qua proxy để ẩn IP của máy gửi khỏi Received header của mail server
            proxy_info = _pt_parse_proxy(proxy_str)
            if proxy_info is None:
                raise RuntimeError("Proxy được cấu hình nhưng PySocks chưa cài (pip install PySocks)")
            if use_ssl:
                smtp_ctx = _SMTPWithProxySSL(account["host"], port, proxy_info, timeout=30)
            else:
                smtp_ctx = _SMTPWithProxy(account["host"], port, proxy_info, timeout=30)
            with smtp_ctx as server:
                if not use_ssl:
                    server.starttls()
                server.login(account["username"], account["password"])
                server.send_message(msg)
        else:
            smtp = smtplib.SMTP_SSL(account["host"], port, timeout=30) if use_ssl else smtplib.SMTP(account["host"], port, timeout=30)
            with smtp:
                if not use_ssl: smtp.starttls()
                smtp.login(account["username"], account["password"]); smtp.send_message(msg)

        sent_copy_error = _append_sent_copy(account, msg.as_bytes())
        return {"success": True, "error": "", "sent_at": datetime.now(timezone.utc).isoformat(),
                "sent_copy_saved": not bool(sent_copy_error), "sent_copy_error": sent_copy_error}
    except Exception as exc: return {"success": False, "error": str(exc)}

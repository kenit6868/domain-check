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
from datetime import datetime, timezone
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime
from html import unescape


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
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provider_mail_cache.json")
EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence", "provider_replies")


@dataclass
class ProviderMail:
    uid: str; account: str; provider: str; provider_label: str; sender: str; reply_to: str
    subject: str; date: str; message_id: str; body: str; request_type: str; request_label: str
    domain: str; urls: list[str]; ticket: str; channel: str; risk: str
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
    text = f"{sender} {subject} {body[:3000]}".lower()
    for key, (label, signals) in PROVIDERS.items():
        if any(signal in text for signal in signals): return key, label
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


def classify_request(subject, body):
    subject_lower = (subject or "").lower()
    status_text = f"{subject}\n{provider_request_text(body)}".lower()
    if any(value in subject_lower for value in (
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
        r"report\s*(?:identification number|number|no\.?|id|#)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})",
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
    """Parse an RFC email date; return None for missing or malformed headers."""
    try:
        return parsedate_to_datetime(mail.date) if mail.date else None
    except (TypeError, ValueError, OverflowError):
        return None


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


def fetch_provider_mail(account, limit=100, unread_only=False, date_from=None, date_to=None, progress_callback=None):
    host = account.get("imap_host") or account.get("host")
    if not host or not account.get("username") or not account.get("password"): raise ValueError("Tài khoản thiếu cấu hình IMAP")
    conn = imaplib.IMAP4_SSL(host, int(account.get("imap_port", 993)))
    try:
        conn.login(account["username"], account["password"]); status, _ = conn.select(account.get("imap_mailbox", "INBOX"), readonly=True)
        if status != "OK": raise RuntimeError("Không mở được INBOX")
        criteria = []
        if unread_only: criteria.append("UNSEEN")
        if date_from: criteria.extend(("SINCE", date_from.strftime("%d-%b-%Y")))
        if date_to:
            from datetime import timedelta
            criteria.extend(("BEFORE", (date_to + timedelta(days=1)).strftime("%d-%b-%Y")))
        if not criteria: criteria.append("ALL")
        status, data = conn.uid("search", None, *criteria)
        if status != "OK": raise RuntimeError("Không tìm được email")
        result = []
        selected_uids = list(reversed(data[0].split()[-max(1, min(int(limit), 500)):]))
        total = len(selected_uids)
        for position, uid in enumerate(selected_uids, start=1):
            status, payload = conn.uid("fetch", uid, "(BODY.PEEK[])")
            if status == "OK" and payload and isinstance(payload[0], tuple):
                item = parse_message(uid.decode(), account["username"], payload[0][1])
                if item.provider != "unknown" or item.request_type != "manual_review": result.append(item)
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
    warnings = []; target = (details.get("reported_url") or "").strip(); official = (details.get("official_url") or "").strip(); evidence = (details.get("evidence") or "").strip()
    subject = "Re: " + re.sub(r"^(re:\s*)+", "", mail.subject, flags=re.I)
    intro = f"Dear {mail.provider_label} Abuse Team,\n\nThank you for your response.\n" + (f"Reference/Ticket: {mail.ticket}\n" if mail.ticket else "") + "\n"
    if mail.request_type == "full_url": core = f"The complete reported URL is:\n{target or '[PLEASE ADD THE COMPLETE REPORTED URL]'}"; warnings += [] if target else ["NCC yêu cầu URL đầy đủ nhưng chưa có URL."]
    elif mail.request_type == "official_url": core = f"The official website for comparison is:\n{official or '[PLEASE ADD THE OFFICIAL WEBSITE URL]'}"; warnings += [] if official else ["NCC yêu cầu website chính thức nhưng chưa có URL."]
    elif mail.request_type == "screenshot":
        if details.get("screenshot_attached"):
            core = "Please find the requested supporting screenshot attached."
            if details.get("urlscan_result"): core += f"\nURLScan analysis: {details['urlscan_result']}"
        else:
            core = "[ATTACH VERIFIED SCREENSHOTS BEFORE SENDING]"; warnings.append("Cần tạo hoặc đính kèm ảnh thật trước khi gửi.")
    elif mail.request_type in ("technical_evidence", "clarification"): core = f"Please find the requested information below:\n{evidence or '[PLEASE ADD VERIFIED INFORMATION]'}"; warnings += [] if evidence else ["Chưa có thông tin bổ sung đã xác minh."]
    elif mail.request_type in ("legal_evidence", "identity"): core = "We are reviewing your request. The requested legal or identity documentation will only be supplied after authorization by the appropriate representative."; warnings.append("Bắt buộc duyệt thủ công; không tự tạo tuyên bố hoặc giấy tờ.")
    elif mail.request_type == "acknowledgement": core = "Thank you for confirming receipt. Please keep us informed of any action taken or additional information required."
    elif mail.request_type == "resolved": core = "Thank you for the update. We acknowledge the status of this case."
    else: core = evidence or "[PLEASE WRITE A RESPONSE BASED ON THE PROVIDER REQUEST]"; warnings.append("Không nhận diện chắc chắn yêu cầu; cần đọc và sửa thủ công.")
    if target and mail.request_type != "full_url": core += f"\n\nReported URL:\n{target}"
    body = intro + core + f"\n\nKind regards,\n{(details.get('contact_name') or 'Reporter').strip()}"
    if details.get("contact_email"): body += f"\n{details['contact_email'].strip()}"
    return subject, body, warnings


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


def send_threaded_reply(account, mail, subject, body, attachments=None):
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
        smtp = smtplib.SMTP_SSL(account["host"], port, timeout=30) if account.get("ssl") or port == 465 else smtplib.SMTP(account["host"], port, timeout=30)
        with smtp:
            if not (account.get("ssl") or port == 465): smtp.starttls()
            smtp.login(account["username"], account["password"]); smtp.send_message(msg)
        sent_copy_error = _append_sent_copy(account, msg.as_bytes())
        return {"success": True, "error": "", "sent_at": datetime.now(timezone.utc).isoformat(),
                "sent_copy_saved": not bool(sent_copy_error), "sent_copy_error": sent_copy_error}
    except Exception as exc: return {"success": False, "error": str(exc)}

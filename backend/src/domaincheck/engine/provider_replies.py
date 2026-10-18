"""Read provider replies and prepare reviewable, provider-aware drafts."""

from __future__ import annotations

import email
import base64
import re
from dataclasses import asdict, dataclass
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape

MODULE_VERSION = 12

# Camellrp/Roundcube stores provider replies under the nested automation folder.
# Keep this configurable per account because IMAP folder names are server-specific.
CLOUDFLARE_MAILBOX = "6 - Tự động.Cloudflare"
EVIDENCE_MAILBOX = "3 - Cần bằng chứng"


def _imap_mailbox_name(mailbox):
    """Encode a human-readable mailbox name using IMAP modified UTF-7."""
    value = str(mailbox or "")
    if value.isascii():
        encoded = value
        return '"' + encoded.replace('\\', '\\\\').replace('"', '\\"') + '"' if encoded not in ("INBOX", "Junk", "Sent", "Trash", "Drafts") else encoded
    chunks = []
    plain = []
    def flush_plain():
        if plain:
            chunks.append("".join(plain))
            plain.clear()
    for char in value:
        if ord(char) < 0x20 or ord(char) > 0x7e:
            flush_plain()
            chunks.append(char)
        else:
            plain.append(char)
    flush_plain()
    encoded = []
    for chunk in chunks:
        if chunk and all(0x20 <= ord(char) <= 0x7e for char in chunk):
            encoded.append(chunk)
            continue
        raw = chunk.encode("utf-16-be")
        token = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        encoded.append("&" + token + "-")
    encoded_name = "".join(encoded)
    return '"' + encoded_name.replace('\\', '\\\\').replace('"', '\\"') + '"'

# Proxy support — tái dùng từ phishing_toolkit để tránh duplicate code
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
@dataclass
class ProviderMail:
    uid: str; account: str; provider: str; provider_label: str; sender: str; reply_to: str
    subject: str; date: str; message_id: str; body: str; request_type: str; request_label: str
    domain: str; urls: list[str]; ticket: str; channel: str; risk: str
    server_date: str = ""
    source_mailbox: str = "INBOX"
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


def build_reply(mail, details):
    warnings = []; target = (details.get("reported_url") or "").strip(); official = (details.get("official_url") or "").strip(); evidence = (details.get("evidence") or "").strip(); redirect_url = (details.get("redirect_url") or "").strip(); button_label = (details.get("button_label") or "Register/Login").strip(); browser_capture = details.get("browser_evidence") or {}; destination_final_url = str(browser_capture.get("final_url") or "").strip(); destination_opened = browser_capture.get("evidence_type") == "dom_destination_opened"
    subject = "Re: " + re.sub(r"^(re:\s*)+", "", mail.subject, flags=re.I)
    if mail.provider == "cloudflare" and mail.request_type in ("technical_evidence", "clarification") and mail.ticket:
        subject = f"Re: Phishing Report - Report ID {mail.ticket}"
    intro = f"Dear {mail.provider_label} Abuse Team,\n\nThank you for your response.\n" + (f"Reference/Ticket: {mail.ticket}\n" if mail.ticket else "") + "\n"
    if mail.request_type == "full_url": core = f"The complete reported URL is:\n{target or '[PLEASE ADD THE COMPLETE REPORTED URL]'}"; warnings += [] if target else ["NCC yêu cầu URL đầy đủ nhưng chưa có URL."]
    elif mail.request_type == "official_url": core = f"The official website for comparison is:\n{official or '[PLEASE ADD THE OFFICIAL WEBSITE URL]'}"; warnings += [] if official else ["NCC yêu cầu website chính thức nhưng chưa có URL."]
    elif mail.request_type == "screenshot":
        if details.get("screenshot_attached"):
            core = "Please find the requested supporting screenshot attached."
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
                    f'href="{redirect_url}"'
                )
                if destination_opened:
                    core += (
                        "\n\nVerification steps:\n\n"
                        f"1. Open {target}.\n"
                        f"2. Locate the \"{button_label}\" control and inspect its declared HTTP(S) destination.\n"
                        f"3. Open {redirect_url} directly in a separate browser tab using the source page as referrer.\n"
                        "4. No button click, credential entry, or form submission is performed."
                    )
                    if destination_final_url:
                        core += f"\n5. The destination tab loads: {destination_final_url}."
                else:
                    core += (
                        "\n\nSteps to reproduce:\n\n"
                        f"1. Visit {target}\n"
                        f"2. Locate the \"{button_label}\" button.\n"
                        "3. Click the button.\n"
                        f"4. The user is directed to {redirect_url}."
                    )
            if evidence:
                core += f"\n\nAdditional verified observations:\n{evidence}"
            if details.get("screenshot_attached") and redirect_url:
                if destination_opened:
                    core += (
                        "\n\nWe have attached source and destination screenshots plus a JSON "
                        "manifest. They show the reported page, the declared DOM destination, "
                        "and the page displayed after that destination URL was opened directly."
                    )
                else:
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
    role = str(details.get("signature_role") or "").strip()
    company = str(details.get("company_name") or "").strip()
    role_line = " — ".join(value for value in (role, company) if value)
    if role_line: body += f"\n{role_line}"
    registration = str(details.get("business_registration_no") or "").strip()
    if registration: body += f"\nBusiness Registration No. {registration}"
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

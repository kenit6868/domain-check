"""Core workflow helpers for evidence-backed cloaking reports."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

import phishing_toolkit as pt
from cloaking_report import normalize_report_url


CLOUDFLARE_ABUSE_FORM = "https://abuse.cloudflare.com/phishing"


def _signature(item: dict) -> tuple:
    return item.get("status"), item.get("final_url"), item.get("sha256"), item.get("size")


def _different(first: dict, second: dict) -> bool:
    if first.get("error") or second.get("error"):
        return False
    a_status, a_url, a_hash, a_size = _signature(first)
    b_status, b_url, b_hash, b_size = _signature(second)
    if a_status != b_status or a_url != b_url:
        return True
    if a_hash == b_hash:
        return False
    largest = max(a_size or 0, b_size or 0, 1)
    return abs((a_size or 0) - (b_size or 0)) >= 500 and abs((a_size or 0) - (b_size or 0)) / largest >= 0.20


def select_html_pair(probe: dict) -> tuple[str, str] | None:
    """Choose the strongest reproducible pair, preferring the user's PC/mobile+Google flow."""
    results = probe.get("results", {})
    candidates = (
        ("desktop_direct", "mobile_google"),
        ("desktop_direct", "mobile_direct"),
        ("desktop_direct", "desktop_google"),
        ("mobile_direct", "mobile_google"),
    )
    for first_name, second_name in candidates:
        first, second = results.get(first_name, {}), results.get(second_name, {})
        first_path = first.get("headers", {}).get("x-matched-path", "")
        second_path = second.get("headers", {}).get("x-matched-path", "")
        if _different(first, second) or (first_path and second_path and first_path != second_path):
            return first_name, second_name
    return None


def resolve_provider(reported_url: str) -> dict:
    """Resolve registrar and its supported abuse channel without running the full scan pipeline."""
    url = normalize_report_url(reported_url)
    domain = urlsplit(url).hostname or ""
    whois = pt.get_whois_info(domain)
    registrar = whois.get("registrar") if isinstance(whois, dict) else None
    nameservers = whois.get("name_servers") if isinstance(whois, dict) else None
    cloudflare = pt.is_cloudflare(nameservers)

    recipient = None
    source = None
    rdap = {}
    rdap = pt.get_rdap_abuse_email(domain)
    registrar = registrar or rdap.get("registrar")
    if rdap.get("abuse_email"):
        recipient, source = rdap["abuse_email"], "RDAP"
    if not recipient and registrar:
        recipient = pt.lookup_registrar_abuse_email(registrar)
        if recipient:
            source = "static registrar table"
    if not recipient:
        raw_emails = whois.get("emails") if isinstance(whois, dict) else None
        if isinstance(raw_emails, str):
            raw_emails = [raw_emails]
        if isinstance(raw_emails, list):
            valid = [
                email for email in raw_emails if email and "@" in email
                and any(marker in email.lower() for marker in ("abuse", "security", "compliance"))
                and not any(private in email.lower() for private in pt.WHOIS_PRIVACY_DOMAINS)
            ]
            if valid:
                recipient, source = valid[0], "WHOIS abuse contact"

    # Project policy says these registrars require a form even if WHOIS exposes an email.
    webform = None
    if registrar:
        lowered = registrar.lower()
        webform = next((value for key, value in pt.WEB_FORM_REGISTRARS.items() if key in lowered), None)
    if webform:
        recipient = None
        source = "web form required"

    return {
        "domain": domain,
        "registrar": registrar or "Unknown registrar",
        "recipient": recipient,
        "recipient_source": source,
        "webform": webform,
        "cloudflare": cloudflare,
        "cloudflare_form": CLOUDFLARE_ABUSE_FORM if cloudflare else None,
        "lookup_error": whois.get("error") if isinstance(whois, dict) else None,
        "rdap_error": rdap.get("error") if isinstance(rdap, dict) else None,
    }


def build_cloaking_email(
    *, reported_url: str, keyword: str, provider: dict, probe: dict,
    pair: tuple[str, str], desktop_image_name: str, mobile_image_name: str,
    reporter_name: str = "", reporter_email: str = "",
) -> dict:
    """Create a cautious, reproducible provider report from the selected evidence pair."""
    url = normalize_report_url(reported_url)
    domain = urlsplit(url).hostname or provider.get("domain", "")
    first_name, second_name = pair
    first = probe["results"][first_name]
    second = probe["results"][second_name]
    captured = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    label = {
        "desktop_direct": "desktop direct access",
        "mobile_direct": "mobile direct access",
        "desktop_google": "desktop request with Google Referer",
        "mobile_google": "mobile request with Google Referer",
    }
    brand = keyword.strip()
    rendered = probe.get("capture_method") == "rendered_google_click"
    evidence_description = "rendered DOM after JavaScript execution" if rendered else "raw HTML response"
    reproduction = (
        "1. Open the URL directly in a desktop browser.\n"
        "2. Open the same URL in a mobile browser after the Google-search workflow.\n"
        "3. Allow JavaScript to render, then compare the final URL and rendered DOM."
        if rendered else
        "1. Request the URL directly with a desktop browser User-Agent.\n"
        "2. Request the same URL with a mobile browser User-Agent and Google Referer: https://www.google.com/\n"
        "3. Compare the returned content, final URL, response size and x-matched-path header where present."
    )
    subject = f"Abuse report: apparent cloaking on {domain}"
    body = f"""Dear {provider.get('registrar') or 'Abuse'} Abuse Team,

We are reporting {domain} for apparent cloaking / conditional content delivery associated with the Google keyword \"{brand}\".

Reported URL:
{url}

Observed behavior:
The attached screenshots show materially different content at the same reported URL: the desktop capture shows a decoy/benign page, while the mobile capture reached through the Google-search workflow shows {brand}-branded gambling-related content. The attached {evidence_description} files provide a technical comparison.

Technical comparison captured {captured}:
- {label[first_name]}: HTTP {first.get('status')}, {first.get('size')} bytes, SHA-256 {first.get('sha256')}
- {label[second_name]}: HTTP {second.get('status')}, {second.get('size')} bytes, SHA-256 {second.get('sha256')}

Reproduction:
{reproduction}

Evidence attached:
- {desktop_image_name}
- {mobile_image_name}
- html_truc_tiep.html
- html_mobile_tu_google.html
- technical_evidence.txt

This report does not assert credential theft unless independently verified. Please investigate the domain for cloaking, brand impersonation and gambling-related abuse, preserve the submitted evidence, and take the action available under your abuse policy, including referral to the responsible hosting provider where appropriate.

Thank you.

{reporter_name or 'Brand Protection Team'}
{reporter_email}
"""
    return {"subject": subject, "body": body, "captured_at": captured}


def build_attachments(*, probe: dict, pair: tuple[str, str], desktop_image, mobile_image) -> list[dict]:
    first_name, second_name = pair
    return [
        {"filename": desktop_image.name, "data": desktop_image.getvalue(), "mime_type": desktop_image.type or "image/png"},
        {"filename": mobile_image.name, "data": mobile_image.getvalue(), "mime_type": mobile_image.type or "image/png"},
        {"filename": "html_truc_tiep.html", "data": probe["results"][first_name]["html"].encode("utf-8"), "mime_type": "text/html"},
        {"filename": "html_mobile_tu_google.html", "data": probe["results"][second_name]["html"].encode("utf-8"), "mime_type": "text/html"},
        {"filename": "technical_evidence.txt", "data": (probe.get("technical_summary") or probe.get("curl_summary", "")).encode("utf-8"), "mime_type": "text/plain"},
    ]

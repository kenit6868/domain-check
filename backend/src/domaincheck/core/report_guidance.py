"""Read-only report form guidance for a completed Check Domain result."""
from __future__ import annotations

import importlib
import re
from email.utils import getaddresses
from urllib.parse import urlsplit

from domaincheck.core.form_tasks import PROVIDER_FORMS, QuickReportFormTaskAdapter
from domaincheck.core.quick_report_routes import report_forms


def build_report_guidance(*, domain: str, target_url: str, result: dict, profile: dict) -> list[dict]:
    """Reuse legacy pure formatters and routing; never submit or write drafts."""
    toolkit = importlib.import_module("phishing_toolkit")
    config = {key: str(profile.get(key) or "") for key in
              ("brand_name", "contact_name", "contact_email", "company_name",
               "business_registration_no", "signature_role")}
    config["registry_contact"] = result.get("registry_contact") or {}
    adapter = QuickReportFormTaskAdapter()
    guides: list[dict] = []

    def add(channel: str, label: str, url: str | None, text: str, note: str = "") -> None:
        if url:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                return
        guides.append({"channel": channel, "label": label[:120], "form_url": url,
                       "draft": text[:16_000], "note": note[:500]})

    for provider in ("google_gsb", "microsoft_smartscreen", "chongluadao", "coccoc_safe"):
        task = adapter.build(provider, target_url=target_url, domain=domain, config=config)
        add("browser", PROVIDER_FORMS[provider][0], task.form_url, task.draft,
            "Form không có nội dung văn bản cần dán" if provider == "microsoft_smartscreen" else "")
    add("browser", "Netcraft", "https://report.netcraft.com/",
        toolkit.generate_community_report_text(domain, config, target_url))

    if result.get("cloudflare"):
        task = adapter.build("cloudflare", target_url=target_url, domain=domain, config=config)
        add("cdn", "Cloudflare", task.form_url, task.draft)

    whois = result.get("whois") or {}
    registrar = str(whois.get("registrar") or "").strip() if isinstance(whois, dict) else ""
    raw_email = result.get("registrar_abuse_email_used")
    if not raw_email and isinstance(whois, dict):
        raw_email = whois.get("emails")
    if not raw_email and registrar:
        raw_email = getattr(toolkit, "lookup_registrar_abuse_email", lambda _r: None)(registrar)
    raw_values = raw_email if isinstance(raw_email, list) else [str(raw_email or "")]
    recipient_candidates = []
    for _, email in getaddresses([str(value) for value in raw_values]):
        if (re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", email)
                and not any(private in email.casefold() for private in toolkit.WHOIS_PRIVACY_DOMAINS)
                and email.casefold() not in {item.casefold() for item in recipient_candidates}):
            recipient_candidates.append(email)
    has_registrar_webform = any(key in registrar.casefold() for key in toolkit.WEB_FORM_REGISTRARS)
    if recipient_candidates and registrar and not has_registrar_webform:
        recipient = ", ".join(recipient_candidates[:3])
        body = toolkit.render_registrar_email_draft(
            domain, result.get("cert") or {}, result.get("virustotal") or {},
            config, registrar, recipient, target_url,
        )
        add("registrar", f"{registrar or 'Registrar'} · Email", None,
            body,
            f"Email nhận: {recipient}")
    forms = report_forms(domain, {"registrar": registrar})
    for form in forms:
        if form["channel"] == "registrar":
            draft = toolkit.get_webform_draft_text(domain, registrar, form["form_url"],
                config, target_url=target_url)
            add("registrar", registrar, form["form_url"], draft)
    registry = result.get("registry_contact")
    if not isinstance(registry, dict):
        registry = toolkit._static_registry_lookup(domain) or {}
        if registry:
            registry = {"source": "static_table", **registry}
    if registry and registry.get("source") != "not_found":
        registry_name = str(registry.get("registry") or "Registry")
        registry_url = registry.get("report_webform")
        registry_email = str(registry.get("abuse_email") or "").strip()
        note = "Chỉ dùng khi cần leo thang sau khi đã báo registrar."
        if registry_url:
            draft = toolkit.get_registry_webform_draft_text(domain, registry, config,
                target_url=target_url)
            add("registry", f"{registry_name} · Webform", registry_url, draft, note)
        if registry_email and any(
            re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", email)
            for _, email in getaddresses([registry_email])
        ):
            draft = toolkit.render_registry_email_draft(domain, registry, config,
                target_url=target_url)
            add("registry", f"{registry_name} · Email", None, draft,
                f"Email nhận: {registry_email}. {note}")
    return guides

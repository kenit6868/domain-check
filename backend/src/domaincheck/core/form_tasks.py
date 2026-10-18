from __future__ import annotations

import importlib
from dataclasses import dataclass
from urllib.parse import urlencode
from domaincheck.core.quick_report_routes import report_forms


@dataclass(frozen=True, slots=True)
class ProviderFormTask:
    provider: str
    form_url: str
    target_url: str
    draft: str
    mode: str = "fill_only"
    threat_type: str = "Social Engineering"
    threat_category: str = "Other Phishing"
    contact_email: str = ""
    contact_name: str = ""
    brand_name: str = ""
    report_type: str = "phishing"


PROVIDER_FORMS = {
    "google_gsb": ("Google", "https://safebrowsing.google.com/safebrowsing/report_phish/"),
    "microsoft_smartscreen": ("Microsoft", "https://www.microsoft.com/wdsi/support/report-unsafe-site-guest"),
    "cloudflare": ("Cloudflare", "https://abuse.cloudflare.com/phishing"),
    "chongluadao": ("Chống Lừa Đảo", "https://chongluadao.vn/report/reportphishing"),
    "coccoc_safe": ("Cốc Cốc Safe", "https://safe.coccoc.com/"),
    "godaddy_phishing": ("GoDaddy", "https://legalportal.godaddy.com/abuse/phishing"),
    "registry_co_phishing": (".CO Registry", "https://registry.co/report-abuse/form?type=phishing"),
    "xyz_registry_abuse": (".XYZ Registry", "https://gen.xyz/account/submitticket.php?step=2&deptid=6"),
}


def available_providers(domain: str, result: dict) -> tuple[str, ...]:
    providers = ["google_gsb", "microsoft_smartscreen", "chongluadao", "coccoc_safe"]
    if result.get("cloudflare") is True:
        providers.append("cloudflare")
    for form in report_forms(domain, result):
        provider = form['autofill_provider']
        if provider and provider not in providers:
            providers.append(provider)
    return tuple(providers)


class QuickReportFormTaskAdapter:
    """Build fill-only tasks with the characterized Python formatters."""

    _GOOGLE_URL = "https://safebrowsing.google.com/safebrowsing/report_phish/"
    _MICROSOFT_URL = "https://www.microsoft.com/wdsi/support/report-unsafe-site-guest"

    def build(self, provider: str, *, target_url: str, domain: str, config: dict) -> ProviderFormTask:
        if provider == "google_gsb":
            toolkit = importlib.import_module("domaincheck.engine.phishing_toolkit")
            draft = toolkit.generate_safebrowsing_report_text(domain, config, target_url)
            return ProviderFormTask(
                provider=provider,
                form_url=f"{self._GOOGLE_URL}?{urlencode({'url': target_url})}",
                target_url=target_url,
                draft=draft,
            )
        if provider == "microsoft_smartscreen":
            return ProviderFormTask(
                provider=provider,
                form_url=f"{self._MICROSOFT_URL}?{urlencode({'url': target_url})}",
                target_url=target_url,
                draft="",
            )
        if provider not in PROVIDER_FORMS:
            raise ValueError("Unsupported Quick Report form provider")
        toolkit = importlib.import_module("domaincheck.engine.phishing_toolkit")
        if provider == "cloudflare":
            draft = toolkit.generate_cloudflare_report_text(domain, config, target_url)
        elif provider in {"registry_co_phishing", "xyz_registry_abuse"}:
            registry = config.get("registry_contact") or {"registry": ".CO Registry" if provider == "registry_co_phishing" else ".XYZ Registry"}
            draft = toolkit.get_registry_webform_draft_text(domain, registry, config, target_url=target_url)
        elif provider == "godaddy_phishing":
            draft = toolkit.get_webform_draft_text(domain=domain, registrar="GoDaddy",
                webform_url=PROVIDER_FORMS[provider][1], cfg=config, target_url=target_url)
        else:
            draft = toolkit.generate_community_report_text(domain, config, target_url)
        return ProviderFormTask(provider=provider, form_url=PROVIDER_FORMS[provider][1],
            target_url=target_url, draft=draft, contact_email=config.get("contact_email", ""),
            contact_name=config.get("contact_name", ""), brand_name=config.get("brand_name", ""),
            report_type="Trang web lừa đảo" if provider == "coccoc_safe" else "phishing")

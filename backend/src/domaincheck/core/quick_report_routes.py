from __future__ import annotations

import importlib


def report_forms(domain: str, result: dict) -> list[dict]:
    """Use the legacy contact tables verbatim; no lookup or provider request."""
    toolkit = importlib.import_module('phishing_toolkit')
    forms = []
    registrar = str(result.get('registrar') or '').strip()
    for key, url in toolkit.WEB_FORM_REGISTRARS.items():
        if key in registrar.casefold():
            forms.append({'channel': 'registrar', 'label': registrar, 'form_url': url,
                'autofill_provider': 'godaddy_phishing' if key == 'godaddy' else None,
                'note': ''})
            break
    registry = toolkit._static_registry_lookup(domain) or {}
    url = registry.get('report_webform')
    if url:
        provider = None
        if url == 'https://registry.co/report-abuse/form?type=phishing':
            provider = 'registry_co_phishing'
        elif url == 'https://gen.xyz/account/submitticket.php?step=2&deptid=6':
            provider = 'xyz_registry_abuse'
        forms.append({'channel': 'registry', 'label': registry.get('registry') or 'Registry',
            'form_url': url, 'autofill_provider': provider, 'note': registry.get('note') or ''})
    return forms

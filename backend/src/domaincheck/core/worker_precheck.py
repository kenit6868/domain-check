"""Read-only Domain Worker preflight and legacy email rendering boundaries."""
from __future__ import annotations

import importlib
import re
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import Parser
from email.utils import getaddresses

from domaincheck.core.investigation import sanitize_result

ENGINE_VERSION = "domain-worker-preflight-v4-web-1"
REVIEW_VERDICTS = {"LIKELY", "POSSIBLE", "INCONCLUSIVE"}


def valid_recipients(rows: list[dict]) -> list[dict]:
    toolkit = importlib.import_module("phishing_toolkit")
    result, seen = [], set()
    for row in rows:
        if row.get("channel") not in {"registrar", "registry", "hosting"}:
            continue
        for _, email in getaddresses([str(row.get("email") or "")]):
            email = email.strip().lower()
            if (not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", email)
                or toolkit.is_blocked_report_recipient(email)
                or any(private in email for private in toolkit.WHOIS_PRIVACY_DOMAINS)
                or email in seen):
                continue
            seen.add(email)
            result.append({"channel": row["channel"], "email": email})
    return result


def disposition(recipients: list[dict], cloaking: dict) -> str:
    if not recipients:
        return "excluded_no_email"
    if cloaking.get("verdict") == "BLOCKED_OR_UNAVAILABLE":
        return "evidence_review"
    if (cloaking.get("verdict") != "NO_SIGNAL"
            or (cloaking.get("coverage") or {}).get("multi_vantage_recommended")
            or cloaking.get("manual_review_required")):
        return "cloaking_review"
    return "evidence_review"


class LegacyWorkerPrecheckAdapter:
    @staticmethod
    def validate_runtime():
        toolkit = importlib.import_module("phishing_toolkit")
        required = ("resolve_worker_report_recipients", "render_registrar_email_draft",
                    "render_registry_email_draft", "render_hosting_email_draft")
        if not all(callable(getattr(toolkit, name, None)) for name in required):
            raise RuntimeError("Reinstall the backend package and restart its API/worker")

    def run(self, target: str, domain: str) -> dict:
        toolkit = importlib.import_module("phishing_toolkit")
        detector = importlib.import_module("cloaking_detector")

        def contacts():
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = {key: pool.submit(fn, domain) for key, fn in {
                    "whois": toolkit.get_whois_info, "rdap": toolkit.get_rdap_abuse_email,
                    "registry_contact": toolkit.lookup_registry_contact, "cert": toolkit.get_cert_info,
                    "origin_ip_scan": toolkit.scan_common_subdomains}.items()}
                data = {}
                for key, future in futures.items():
                    try:
                        data[key] = future.result() or {}
                    except Exception:
                        if key in {"whois", "rdap", "registry_contact"}:
                            raise
                        data[key] = {}
            primary = data["cert"].get("ip")
            ips = sorted({ip for ip in data["origin_ip_scan"].values() if ip and ip != primary})
            hosting = toolkit.get_ip_whois(ips[0]) if ips else {}
            if not data["whois"].get("registrar") and data["rdap"].get("registrar"):
                data["whois"] = {**data["whois"], "registrar": data["rdap"]["registrar"]}
            data["origin_ip_whois"] = hosting
            data["hosting_ip"] = ips[0] if ips else ""
            rows = toolkit.resolve_worker_report_recipients(data["whois"], data["rdap"],
                                                           data["registry_contact"], hosting)
            return data, valid_recipients(rows)

        def cloaking():
            try:
                return detector.probe_http_cloaking(target, include_path_variant=True,
                                                   evidence_root=None, vantage_points=[])
            except Exception:
                return {"verdict": "INCONCLUSIVE", "manual_review_required": True}

        with ThreadPoolExecutor(max_workers=2) as pool:
            contact_future = pool.submit(contacts)
            cloak_future = pool.submit(cloaking)
            data, recipients = contact_future.result()
            cloak = cloak_future.result()
        # No global legacy cache/log/evidence writes. No body or credential persists.
        safe = sanitize_result({**data, "cloaking": cloak})
        safe.update(recipients=recipients, disposition=disposition(recipients, cloak),
                    hosting_ip=data["hosting_ip"], schema_version=4,
                    browser_verification_pending=disposition(recipients, cloak) == "cloaking_review")
        return safe


def email_examples(domain: str, target: str, result: dict, config: dict) -> list[dict]:
    """Advisory drafts only. Use exact legacy email pools, never webform text."""
    LegacyWorkerPrecheckAdapter.validate_runtime()
    toolkit = importlib.import_module("phishing_toolkit")
    rows = []
    for recipient in valid_recipients(result.get("recipients") or []):
        channel = recipient["channel"]
        if channel == "registrar":
            text = toolkit.render_registrar_email_draft(domain, result.get("cert") or {}, {}, config,
                (result.get("whois") or {}).get("registrar") or "Registrar", recipient["email"], target)
        elif channel == "registry":
            text = toolkit.render_registry_email_draft(domain, result.get("registry_contact") or {}, config, target)
        else:
            text = toolkit.render_hosting_email_draft(domain, result.get("hosting_ip") or "",
                                                    result.get("origin_ip_whois") or {}, config)
        if not text:
            continue
        # Same full-URL normalization used by append_reported_url_to_drafts in legacy.
        marker = f"Reported URL: {target}"
        text = re.sub(rf"(?m)^\s*{re.escape(marker)}\s*$\n?", "", text).rstrip()
        text = toolkit._insert_before_email_signature(text, marker)
        message = Parser(policy=policy.default).parsestr(text)
        rows.append({"channel": channel, "recipient": recipient["email"],
            "subject": str(message["Subject"] or ""), "body": message.get_payload(),
            "draft": text, "advisory": True})
    return rows

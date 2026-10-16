from __future__ import annotations

import os
import asyncio
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from fastapi import HTTPException, Response
from pydantic import SecretStr

from domaincheck.application.domain_checks import DomainCheckNotFound, DomainCheckService, DomainCheckWorker
from domaincheck.core.investigation import LegacyInvestigationAdapter, sanitize_result, validate_target
from domaincheck.infrastructure.db.models import User
from domaincheck.infrastructure.db.session import Database
from domaincheck.main import create_app
from domaincheck.settings import Settings


class InvestigationBoundaryTests(unittest.TestCase):
    def test_vt_submission_requires_separate_permission_and_nonempty_key(self):
        from domaincheck.api.domain_checks import DomainCheckCreate, create_checks
        actor = SimpleNamespace(permissions={"check_domain.check"})
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            settings=SimpleNamespace(vt_api_key=SecretStr("")))))
        body = DomainCheckCreate(target="https://example.com/", submit_vt=True)
        with self.assertRaises(HTTPException) as denied:
            asyncio.run(create_checks(body, request, Response(), None, actor))
        self.assertEqual(denied.exception.status_code, 403)
        actor.permissions.add("check_domain.submit_vt")
        with self.assertRaises(HTTPException) as missing_key:
            asyncio.run(create_checks(body, request, Response(), None, actor))
        self.assertEqual(missing_key.exception.status_code, 409)

    def test_full_legacy_probe_can_run_without_global_logs_drafts_or_evidence(self):
        import phishing_toolkit as toolkit
        probe = {"verdict": "NO_SIGNAL", "profiles": {}, "manual_review_required": False}
        with patch.object(toolkit, "get_cert_info", return_value={"ip": "8.8.8.8"}), \
             patch.object(toolkit, "get_whois_info", return_value={"registrar": "Example Registrar",
                                                                  "emails": ["abuse@example.com"]}), \
             patch.object(toolkit, "check_http", return_value={"status_code": 200}), \
             patch.object(toolkit, "check_mx_records", return_value={"records": []}), \
             patch.object(toolkit, "detect_cdn", return_value=[]), \
             patch.object(toolkit, "scan_common_subdomains", return_value={}), \
             patch.object(toolkit, "check_virustotal", return_value={}), \
             patch.object(toolkit, "check_safebrowsing", return_value={}), \
             patch.object(toolkit, "lookup_registry_contact", return_value={"source": "not_found"}), \
             patch.object(toolkit, "get_rdap_abuse_email", return_value={}), \
             patch.object(toolkit.cloaking_detector, "probe_http_cloaking", return_value=probe) as cloaking, \
             patch.object(toolkit, "log_case", side_effect=AssertionError("legacy log write")), \
             patch.object(toolkit, "generate_email_drafts", side_effect=AssertionError("legacy draft write")), \
             patch.object(toolkit, "generate_registry_draft", side_effect=AssertionError("legacy draft write")):
            result = toolkit.run_check("https://example.com/login", False,
                {"vt_api_key": "", "gsb_api_key": ""}, persist=False,
                create_drafts=False, capture_cloaking_evidence=False)
        self.assertEqual(result["http_check"], {"status_code": 200})
        self.assertEqual(result["drafts"], [])
        self.assertEqual(result["registrar_abuse_email_used"], "abuse@example.com")
        self.assertEqual(result["registrar_abuse_email_source"], "whois")
        self.assertIsNone(result["log_error"])
        self.assertIsNone(cloaking.call_args.kwargs["evidence_root"])

    def test_routes_are_explicit_and_do_not_include_send(self):
        paths = create_app(Settings(_env_file=None)).openapi()["paths"]
        self.assertEqual(set(paths["/api/v1/domain-checks/checks"]), {"post"})
        self.assertEqual(set(paths["/api/v1/domain-checks/checks/{operation_id}"]), {"get"})
        self.assertEqual(set(paths["/api/v1/domain-checks/checks/{check_id}/report-guidance"]), {"get"})
        self.assertFalse(any("send" in path for path in paths if "domain-checks" in path))

    def test_report_guidance_reuses_pure_legacy_formatters_without_evidence(self):
        from domaincheck.core.report_guidance import build_report_guidance
        toolkit = SimpleNamespace(
            generate_community_report_text=lambda _d, _c, url: f"Community {url}",
            get_webform_draft_text=lambda _d, _r, _u, _c, **_k: "Registrar draft",
            render_registrar_email_draft=lambda _d, _cert, _vt, _c, _r, email, _url: f"To: {email}\nSubject: Legacy variant",
            render_registry_email_draft=lambda _d, _r, _c, **_k: "To: abuse@registry.example\nSubject: Registry variant",
            get_registry_webform_draft_text=lambda _d, _r, _c, **_k: "Registry draft",
            _static_registry_lookup=lambda _d: {"registry": "Example Registry", "report_webform": "https://forms.example/registry"},
            WHOIS_PRIVACY_DOMAINS={"privacy.example"},
            WEB_FORM_REGISTRARS={},
        )
        class TaskAdapter:
            def build(self, provider, **kwargs):
                return SimpleNamespace(form_url=f"https://forms.example/{provider}",
                    draft=f"{provider} {kwargs['target_url']}")
        forms = [{"channel": "registrar", "label": "Registrar", "form_url": "https://forms.example/registrar"},
                 {"channel": "registry", "label": "Registry", "form_url": "https://forms.example/registry"}]
        with patch("domaincheck.core.report_guidance.QuickReportFormTaskAdapter", return_value=TaskAdapter()), \
             patch("domaincheck.core.report_guidance.report_forms", return_value=forms), \
             patch("domaincheck.core.report_guidance.importlib.import_module", return_value=toolkit):
            guides = build_report_guidance(domain="example.com", target_url="https://example.com/login",
                result={"whois": {"registrar": "Registrar"}, "cloudflare": True,
                        "registrar_abuse_email_used": "abuse@registrar.example"},
                profile={"brand_name": "Example"})
        self.assertEqual([row["channel"] for row in guides],
            ["browser"] * 5 + ["cdn", "registrar", "registrar", "registry"])
        self.assertEqual(guides[0]["draft"], "google_gsb https://example.com/login")
        self.assertEqual(guides[6]["form_url"], None)
        self.assertIn("To: abuse@registrar.example", guides[6]["draft"])
        self.assertIn("Subject: Legacy variant", guides[6]["draft"])
        self.assertEqual(guides[-1]["draft"], "Registry draft")
        self.assertFalse(any("evidence" in row for row in guides))
        toolkit.WEB_FORM_REGISTRARS = {"registrar": "https://forms.example/registrar"}
        with patch("domaincheck.core.report_guidance.QuickReportFormTaskAdapter", return_value=TaskAdapter()), \
             patch("domaincheck.core.report_guidance.report_forms", return_value=forms), \
             patch("domaincheck.core.report_guidance.importlib.import_module", return_value=toolkit):
            webform_guides = build_report_guidance(domain="example.com", target_url="https://example.com/login",
                result={"whois": {"registrar": "Registrar"}, "registrar_abuse_email_used": "abuse@registrar.example"},
                profile={"brand_name": "Example"})
        self.assertEqual([row["form_url"] for row in webform_guides if row["channel"] == "registrar"],
            ["https://forms.example/registrar"])

        with patch("domaincheck.core.report_guidance.QuickReportFormTaskAdapter", return_value=TaskAdapter()), \
             patch("domaincheck.core.report_guidance.report_forms", return_value=[]), \
             patch("domaincheck.core.report_guidance.importlib.import_module", return_value=toolkit):
            registry_guides = build_report_guidance(domain="example.com", target_url="https://example.com/login",
                result={"whois": {}, "registry_contact": {"source": "static_table", "registry": "Example Registry",
                    "report_webform": "https://forms.example/registry", "abuse_email": "abuse@registry.example"}},
                profile={"brand_name": "Example"})
        self.assertEqual([row["form_url"] for row in registry_guides if row["channel"] == "registry"],
            ["https://forms.example/registry", None])
        self.assertEqual(registry_guides[-2]["draft"], "Registry draft")
        self.assertIn("Subject: Registry variant", registry_guides[-1]["draft"])

    def test_report_guidance_omits_privacy_mail_and_invalid_recipient(self):
        from domaincheck.core.report_guidance import build_report_guidance
        toolkit = SimpleNamespace(
            generate_community_report_text=lambda *_a: "Community draft",
            get_webform_draft_text=lambda *_a, **_k: "Registrar draft",
            _static_registry_lookup=lambda _d: {},
            WHOIS_PRIVACY_DOMAINS={"privacy.example"},
            WEB_FORM_REGISTRARS={},
        )
        adapter = SimpleNamespace(build=lambda provider, **_k: SimpleNamespace(
            form_url=f"https://forms.example/{provider}", draft="Example"))
        with patch("domaincheck.core.report_guidance.QuickReportFormTaskAdapter", return_value=adapter), \
             patch("domaincheck.core.report_guidance.report_forms", return_value=[]), \
             patch("domaincheck.core.report_guidance.importlib.import_module", return_value=toolkit):
            guides = build_report_guidance(domain="example.com", target_url="https://example.com/login",
                result={"whois": {"registrar": "Example", "emails": ["hide@privacy.example", "not-an-email"]}},
                profile={})
        self.assertFalse(any(row["channel"] == "registrar" for row in guides))

    def test_interrupted_item_is_projected_as_unknown(self):
        from domaincheck.api.domain_checks import serialize
        from datetime import date
        check = SimpleNamespace(id=uuid4(), requested_url="https://example.com/",
            domain="example.com", status="running", result={"submit_vt_requested": False},
            cache_provenance={})
        operation = SimpleNamespace(job=SimpleNamespace(id=uuid4(), state="partial",
            stage="completed", business_day=date(2026, 10, 16)), checks=(check,),
            item_states=("unknown",))
        dto = serialize(operation)
        self.assertEqual(dto.check.status, "unknown")
        self.assertEqual(dto.check.result, {})

    def test_create_contract_accepts_exactly_one_target(self):
        from pydantic import ValidationError
        from domaincheck.api.domain_checks import DomainCheckCreate
        self.assertEqual(DomainCheckCreate(target="https://example.com/").target,
                         "https://example.com/")
        for payload in ({"targets": ["https://example.com/"]},
                        {"target": ["https://example.com/a", "https://example.com/b"]},
                        {"target": "https://example.com/", "targets": ["https://example.com/b"]}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                DomainCheckCreate.model_validate(payload)

    def test_target_keeps_full_url_and_rejects_local_targets(self):
        self.assertEqual(validate_target("https://Example.COM/login?a=1"),
                         ("https://example.com/login?a=1", "example.com"))
        for target in ("http://127.0.0.1/", "http://localhost/", "http://host.local/",
                       "http://example.com:8080/", "https://user:pass@example.com/"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_target(target)

    def test_adapter_uses_legacy_pipeline_without_shared_file_artifacts(self):
        toolkit = SimpleNamespace(run_check=lambda *args, **kwargs: (args, kwargs))
        with patch("domaincheck.core.investigation.importlib.import_module", return_value=toolkit):
            args, kwargs = LegacyInvestigationAdapter().run("https://example.com/login",
                submit_vt=False, config={"vt_api_key": ""})
        self.assertEqual(args, ("https://example.com/login", False, {"vt_api_key": ""}))
        self.assertEqual(kwargs, {"persist": False, "create_drafts": False,
                                  "capture_cloaking_evidence": False})

    def test_result_whitelist_keeps_sources_but_removes_secret_paths_and_drafts(self):
        result = sanitize_result({"domain": "example.com", "cert": {"issuer": "CA"},
            "whois": {"registrar": "Registrar", "password": "secret"},
            "cloaking": {"verdict": "POSSIBLE", "evidence_path": "C:/secret.png",
                         "profiles": {"desktop": {"screenshot_path": "C:/secret.png"}}},
            "drafts": [{"body": "secret"}], "log_row": {"ip": "127.0.0.1"}})
        self.assertEqual(result, {"domain": "example.com", "cert": {"issuer": "CA"},
            "whois": {"registrar": "Registrar"}, "cloaking": {"verdict": "POSSIBLE",
                "profiles": {"desktop": {}}}})


@unittest.skipUnless(os.environ.get("DOMAINCHECK_TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class DomainCheckIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_target_worker_and_cross_owner_read(self):
        database = Database(os.environ["DOMAINCHECK_TEST_DATABASE_URL"])
        class Adapter:
            def __init__(self): self.calls = []
            def run(self, target, *, submit_vt, config):
                self.calls.append((target, submit_vt))
                return {"domain": "example.com", "whois": {"registrar": "Example"},
                        "drafts": ["private"], "cloaking": {"verdict": "NO_SIGNAL"}}
        adapter = Adapter()
        now = datetime(2026, 10, 16, 8, tzinfo=UTC)
        try:
            async with database.session_factory() as session, session.begin():
                first = User(display_name="CD first", status="active", timezone="Asia/Bangkok")
                second = User(display_name="CD second", status="active", timezone="Asia/Bangkok")
                session.add_all([first, second]); await session.flush()
                first_id, second_id = first.id, second.id
                op = await DomainCheckService(session, owner_user_id=first_id,
                    actor_user_id=first_id, timezone="Asia/Bangkok", request_id="fixture", now=now).create(
                    "https://example.com/a", submit_vt=False)
                job_id = op.job.id
            worker = DomainCheckWorker(database.session_factory, adapter, now=lambda: now)
            self.assertEqual(await worker.run_next("domain-check-test"), job_id)
            self.assertEqual(adapter.calls, [("https://example.com/a", False)])
            async with database.session_factory() as session, session.begin():
                owned = await DomainCheckService(session, owner_user_id=first_id,
                    actor_user_id=first_id, timezone="Asia/Bangkok", request_id="fixture", now=now).get(job_id)
                self.assertEqual(owned.job.state, "completed")
                self.assertEqual([row.requested_url for row in owned.checks],
                                 ["https://example.com/a"])
                self.assertEqual(owned.checks[0].result["whois"], {"registrar": "Example"})
                self.assertNotIn("drafts", owned.checks[0].result)
                with self.assertRaises(DomainCheckNotFound):
                    await DomainCheckService(session, owner_user_id=second_id,
                        actor_user_id=second_id, timezone="Asia/Bangkok",
                        request_id="fixture", now=now).get(job_id)
        finally:
            await database.dispose()

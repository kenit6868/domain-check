import base64
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import browser_evidence
import lead_triage

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = """Ubuntu 24.04.5 LTS
8cewd.buzz reg=GoDaddy.com, LLC tao=31Z hold=none
uwin789.info reg=? tao=? hold=none
=== backend scam (bare IP?) ===
kzrsgs.cc A: 98.98.114.161 98.98.114.162
mb66ac.xyz A: 166.117.158.82 75.2.99.114
=== host 98.98.114.161 (backend scam chung) ===
OrgName: Zenlayer Inc
OrgAbuseEmail: abuse@zenlayer.com
@neikasian @byc_mb xử lý backend này của tụi au888
"""
SIX_SAMPLE = """Ubuntu 24.04.5 LTS
8cewd.buzz reg=GoDaddy.com, LLC tao=31Z hold=none
uwin789.info reg=? tao=? hold=none
sanmiguelcoffee.co reg=? tao=? hold=none
hdmovie2.travel reg=? tao=? hold=none
caseslux.co reg=? tao=? hold=none
lovecasino.co reg=? tao=? hold=none
=== backend scam (bare IP?) ===
kzrsgs.cc A: 98.98.114.161 98.98.114.162
mb66ac.xyz A: 166.117.158.82 75.2.99.114
"""
PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/cZkAAAAASUVORK5CYII="
)


def case(target="kzrsgs.cc"):
    item = lead_triage.parse_lead(target + " A: 98.98.114.161 98.98.114.162")[0]
    url = target if item["full_url"] else "https://" + target + "/"
    return {
        **item, "check_url": url, "http": {}, "dns": {"ips": ["98.98.114.161"], "error": ""},
        "cloaking": {"verdict": "NO_SIGNAL"}, "worker_cloaking": {"verdict": "NO_SIGNAL"},
        "ip_match": "thay đổi/không khớp", "recipients": [],
        "ip_contacts": [{"ip": "98.98.114.161", "org": "Zenlayer Inc", "abuse_email": "abuse@zenlayer.com"}],
        "worker_draft": {
            "to": "abuse@zenlayer.com", "subject": "Worker report subject",
            "body": f"Dear Zenlayer Inc Abuse Team,\n\nEXACT DOMAIN WORKER REPORT.\n\nReported URL: {url}\n\nRegards,\nReporter\n",
            "filename": "kzrsgs.cc_hosting_report.txt",
        },
    }


def app_for(result, raw=SAMPLE):
    if result["target"] not in [item["target"] for item in lead_triage.parse_lead(raw) if not item["backend_lead"]]:
        raw = f"{result['target']} reg=? hold=none\n" + raw
    app = AppTest.from_file(str(ROOT / "pages/15_Lead_Triage.py"), default_timeout=10)
    app.session_state["lead_triage_input"] = raw
    app.session_state["lead_triage_parsed"] = {"raw": raw, "items": lead_triage.parse_lead(raw)}
    app.session_state["lead_triage_results"] = {
        "source": hashlib.sha256(raw.encode()).hexdigest(), "items": [result],
    }
    return app


def dom_evidence(target: str, directory: str) -> dict:
    """Build a local two-image DOM fixture without visiting a website."""
    result = browser_evidence.create_manual_browser_evidence(
        target, [("source.png", PIXEL_PNG), ("destination.png", PIXEL_PNG)], directory,
    )
    manifest_path = Path(result["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({
        "evidence_type": "dom_destination_opened", "destination_opened": True,
        "final_url": "https://destination.example/login",
        "navigation": {"source_url": target},
        "control": {"found": True, "label": "Register", "resolved_destination": "https://destination.example/login"},
    })
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result["capture_strategy"] = "dom_destination"
    result["evidence_type"] = "dom_destination_opened"
    return result


def cloaking_evidence(target: str, directory: str) -> dict:
    root = Path(directory)
    paths = [root / "desktop.png", root / "mobile.png"]
    for path in paths:
        path.write_bytes(PIXEL_PNG)
    manifest = root / "cloaking.json"
    manifest.write_text(json.dumps({"target_url": target, "verdict": "LIKELY"}), encoding="utf-8")
    return {"target_url": target, "verdict": "LIKELY", "score": 90,
            "profiles": {}, "evidence_path": str(manifest),
            "screenshots": [{"path": str(path), "profile": profile}
                            for path, profile in zip(paths, ("desktop_direct", "mobile_google"))]}


class LeadTriageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        data_patch = patch.object(lead_triage.pt, "DATA_DIR", directory.name)
        data_patch.start()
        self.addCleanup(data_patch.stop)
        cache_patch = patch.object(lead_triage, "REVIEW_CACHE_PATH", str(Path(directory.name) / "lead_triage_cache.json"))
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        log_patch = patch.object(lead_triage.pt, "SENT_LOG_PATH", str(Path(directory.name) / "sent_log.csv"))
        log_patch.start()
        self.addCleanup(log_patch.stop)
        queue_patch = patch.object(lead_triage.review_queue, "REVIEW_DIR", str(Path(directory.name) / "cloaking_review"))
        queue_patch.start()
        self.addCleanup(queue_patch.stop)

    def test_review_cache_round_trip_and_clear(self):
        state = {"raw": SAMPLE, "results": {"source": "abc", "items": []}}
        self.assertTrue(lead_triage.save_review_cache(state))
        self.assertEqual(state, lead_triage.load_review_cache())
        self.assertTrue(lead_triage.clear_review_cache())
        self.assertEqual({}, lead_triage.load_review_cache())

    def test_page_restores_review_in_new_session_until_clear(self):
        result = case()
        with patch("phishing_toolkit.load_config", return_value={}), patch("lead_triage.check_lead") as check:
            app = app_for(result)
            app.session_state["lead_triage_visible_targets"] = [result["target"]]
            app = app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["lead_triage_input"], lead_triage.load_review_cache()["raw"])

            reopened = AppTest.from_file(str(ROOT / "pages/15_Lead_Triage.py"), default_timeout=10).run()
            self.assertFalse(reopened.exception)
            self.assertEqual(app.session_state["lead_triage_input"], reopened.text_area[0].value)
            self.assertTrue(reopened.dataframe)
            self.assertEqual(result["target"], reopened.session_state["lead_triage_results"]["items"][0]["target"])
            self.assertEqual([result["target"]], reopened.session_state["lead_triage_visible_targets"])
            check.assert_not_called()

            reopened = next(button for button in reopened.button if button.label == "Xóa cache").click().run()
            self.assertFalse(reopened.exception)
            self.assertEqual("", reopened.text_area[0].value)
            self.assertFalse(reopened.dataframe)
            self.assertEqual({}, lead_triage.load_review_cache())
            fresh = AppTest.from_file(str(ROOT / "pages/15_Lead_Triage.py"), default_timeout=10).run()
            self.assertEqual("", fresh.text_area[0].value)
            self.assertFalse(fresh.dataframe)

    def test_page_restores_valid_dom_evidence_without_recapture(self):
        result = case()
        with tempfile.TemporaryDirectory() as directory:
            evidence = dom_evidence(result["check_url"], directory)
            app = app_for(result)
            raw = app.session_state["lead_triage_input"]
            target_key = hashlib.sha256(result["target"].encode()).hexdigest()[:16]
            evidence_key = f"lead_triage_evidence_{hashlib.sha256(raw.encode()).hexdigest()[:12]}_{target_key}"
            app.session_state[evidence_key] = evidence
            with (
                patch("phishing_toolkit.load_config", return_value={}),
                patch("provider_replies.capture_dom_link_evidence") as capture,
            ):
                app = app.run()
                reopened = AppTest.from_file(str(ROOT / "pages/15_Lead_Triage.py"), default_timeout=10).run()
            self.assertFalse(reopened.exception)
            self.assertEqual(3, len(lead_triage.validated_dom_attachments(
                reopened.session_state[evidence_key], result["check_url"])))
            capture.assert_not_called()
            self.assertGreaterEqual(len(reopened.image), 2)

    def test_page_restores_review_edits_and_sender_selection(self):
        cfg = {"smtp_accounts": [{"username": "sender@example.org"}]}
        with patch("phishing_toolkit.load_config", return_value=cfg):
            app = app_for(case()).run()
            edit = next(widget for widget in app.text_area if widget.label.startswith("Mô tả bổ sung"))
            app = edit.input("Observed login link in DOM.").run()
            app = app.selectbox[0].set_value("sender@example.org").run()
            reopened = AppTest.from_file(str(ROOT / "pages/15_Lead_Triage.py"), default_timeout=10).run()
        self.assertFalse(reopened.exception)
        self.assertEqual("Observed login link in DOM.", next(
            widget.value for widget in reopened.text_area if widget.label.startswith("Mô tả bổ sung")))
        self.assertEqual("sender@example.org", reopened.selectbox[0].value)

    def test_page_waits_for_explicit_parse_and_hides_stale_list_after_edit(self):
        with patch("lead_triage.check_lead") as check, patch("phishing_toolkit.load_config") as config:
            app = AppTest.from_file(str(ROOT / "pages/15_Lead_Triage.py"), default_timeout=10).run()
            self.assertFalse(app.dataframe)
            app = app.text_area[0].input(SAMPLE).run()
            self.assertFalse(app.dataframe)
            self.assertEqual(["Phân tích ghi chú", "Xóa cache"], [button.label for button in app.button])
            check.assert_not_called()
            config.assert_not_called()

            app = app.button[0].click().run()
            self.assertEqual(2, len(app.dataframe[0].value))
            self.assertEqual(2, len(app.dataframe[1].value))
            app = app.text_area[0].input("https://another.example/login").run()
            self.assertFalse(app.dataframe)
            self.assertEqual(["Phân tích ghi chú", "Xóa cache"], [button.label for button in app.button])
            app = app.text_area[0].input(SAMPLE).run()
            self.assertFalse(app.dataframe)
            check.assert_not_called()
            config.assert_not_called()

    def test_parses_domain_and_backend_rows_without_metadata_targets(self):
        rows = lead_triage.parse_lead(SAMPLE)
        self.assertEqual(4, len(rows))
        registrar_row = lead_triage.parse_lead("sanmiguelcoffee.co reg=GoDaddy.com, LLC tao=31Z hold=none")[0]
        self.assertEqual("GoDaddy.com, LLC", registrar_row["registrar"])
        self.assertEqual(["kzrsgs.cc", "mb66ac.xyz"], [row["domain"] for row in rows if row["backend_lead"]])
        self.assertEqual(["98.98.114.161", "98.98.114.162"], rows[2]["supplied_ips"])

    def test_six_front_domains_are_reports_and_two_backends_are_context(self):
        rows = lead_triage.parse_lead(SIX_SAMPLE)
        self.assertEqual(6, len([item for item in rows if not item["backend_lead"]]))
        self.assertEqual(["kzrsgs.cc", "mb66ac.xyz"], [item["domain"] for item in rows if item["backend_lead"]])
        with patch("phishing_toolkit.load_config", return_value={}):
            app = AppTest.from_file(str(ROOT / "pages/15_Lead_Triage.py"), default_timeout=10).run()
            app = app.text_area[0].input(SIX_SAMPLE).run()
            app = next(button for button in app.button if button.label == "Phân tích ghi chú").click().run()
        self.assertEqual([item["target"] for item in rows[:6]], app.multiselect[0].value)
        self.assertEqual(6, len(app.dataframe[0].value))
        self.assertEqual(2, len(app.dataframe[1].value))

    def test_backend_context_only_checks_dns_and_rdap(self):
        backends = [item for item in lead_triage.parse_lead(SAMPLE) if item["backend_lead"]]
        with (
            patch.object(lead_triage, "_resolve_a", return_value={"ips": ["98.98.114.161"], "error": ""}) as dns,
            patch.object(lead_triage.pt, "get_ip_whois", return_value={"org": "Zenlayer Inc"}) as rdap,
            patch.object(lead_triage.pt, "check_http") as http,
            patch.object(lead_triage.domain_worker, "_precheck_cloaking") as cloaking,
        ):
            checked = lead_triage.check_backend_context(backends)
        self.assertEqual(["kzrsgs.cc", "mb66ac.xyz"], [item["domain"] for item in checked])
        self.assertEqual(2, dns.call_count)
        self.assertEqual(4, rdap.call_count)
        http.assert_not_called()
        cloaking.assert_not_called()

    def test_front_report_includes_backend_leads_without_changing_recipient(self):
        front = case("8cewd.buzz")
        front["supplied_ips"] = []
        front["ip_contacts"] = []
        front["dns"] = {"ips": ["98.98.114.161"], "error": ""}
        front["worker_draft"]["to"] = "abuse@godaddy.com"
        backend = [{
            "domain": "kzrsgs.cc", "supplied_ips": ["98.98.114.161", "98.98.114.162"],
            "dns": {"ips": ["98.98.114.161"], "error": ""},
            "ip_contacts": [{"ip": "98.98.114.161", "org": "Zenlayer Inc"}],
        }]
        draft = lead_triage.build_investigation_draft(front, SAMPLE, backend_results=backend)
        self.assertEqual("abuse@godaddy.com", draft["to"])
        self.assertIn("kzrsgs.cc", draft["body"])
        self.assertIn("IPs supplied: 98.98.114.161, 98.98.114.162", draft["body"])
        self.assertIn("current DNS A: 98.98.114.161", draft["body"])
        self.assertIn("Please verify whether the listed infrastructure leads serve or support", draft["body"])
        self.assertIn("kzrsgs.cc", draft["body_vi"])

    def test_system_dns_fallback(self):
        with (
            patch.object(lead_triage.dns.resolver, "resolve", side_effect=TimeoutError),
            patch.object(lead_triage.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("98.98.114.161", 0))]),
        ):
            self.assertEqual({"ips": ["98.98.114.161"], "error": ""}, lead_triage._resolve_a("kzrsgs.cc"))

    def test_precheck_reuses_worker_and_defers_shared_report(self):
        item = lead_triage.parse_lead("kzrsgs.cc A: 98.98.114.161 98.98.114.162")[0]
        with (
            patch.object(lead_triage, "_resolve_a", return_value={"ips": ["98.98.114.161"], "error": ""}),
            patch.object(lead_triage.domain_worker, "_precheck_report_recipients", return_value=[]) as recipient,
            patch.object(lead_triage.domain_worker, "_precheck_cloaking", return_value={"verdict": "NO_SIGNAL"}) as cloaking,
            patch.object(lead_triage.pt, "check_http", return_value={"status_code": 200}),
            patch.object(lead_triage.pt, "get_ip_whois", return_value={"org": "Zenlayer Inc"}) as whois,
            patch.object(lead_triage, "prepare_worker_report", side_effect=lambda result, cfg: result) as prepare,
        ):
            result = lead_triage.check_lead(item, {})
        self.assertEqual("thay đổi/không khớp", result["ip_match"])
        recipient.assert_called_once_with("kzrsgs.cc")
        cloaking.assert_called_once_with("https://kzrsgs.cc/", {})
        prepare.assert_not_called()
        self.assertEqual(2, whois.call_count)

    def test_selected_prechecks_run_concurrently_and_keep_input_order(self):
        items = lead_triage.parse_lead("\n".join(f"target{index}.example" for index in range(6)))
        barrier = threading.Barrier(6)

        def checked(item, cfg):
            barrier.wait(timeout=3)
            return {**item, "check_url": "https://" + item["domain"] + "/"}

        progress = []
        with patch.object(lead_triage, "check_lead", side_effect=checked) as check:
            results = lead_triage.check_leads(items, {}, lambda done, total: progress.append((done, total)))
        self.assertEqual([item["target"] for item in items], [result["target"] for result in results])
        self.assertEqual(6, check.call_count)
        self.assertEqual([(index, 6) for index in range(1, 7)], progress)

    def test_batch_reuses_backend_snapshot_until_expiry(self):
        with (
            patch("phishing_toolkit.load_config", return_value={}),
            patch("lead_triage.check_lead", side_effect=lambda item, cfg: case(item["target"])) as check,
            patch("lead_triage.check_backend_context", return_value=[]) as backend,
        ):
            app = AppTest.from_file(str(ROOT / "pages/15_Lead_Triage.py"), default_timeout=10).run()
            app = app.text_area[0].input(SAMPLE).run()
            app = next(button for button in app.button if button.label == "Phân tích ghi chú").click().run()
            for _ in range(2):
                app = next(button for button in app.button if "Check đầu mối đã chọn" in button.label).click().run()
                self.assertFalse(app.exception)
            self.assertEqual(1, backend.call_count)
            self.assertEqual(4, check.call_count)
            cached = dict(app.session_state["lead_triage_backend_results"])
            cached["checked_monotonic"] = 0
            app.session_state["lead_triage_backend_results"] = cached
            app = next(button for button in app.button if "Check đầu mối đã chọn" in button.label).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(2, backend.call_count)

    def test_pipeline_preserves_worker_hosting_report_snapshot(self):
        result = case()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kzrsgs.cc_hosting_report.txt"
            path.write_text("To: abuse@zenlayer.com\nSubject: Actual Worker subject\n\nActual Worker body\n\nRegards,\n", encoding="utf-8")
            with (
                patch.object(lead_triage.pt, "run_check", return_value={
                    "drafts": [str(path)], "cloaking": {"verdict": "NO_SIGNAL"},
                }) as run,
                patch.object(lead_triage.pt, "generate_hosting_draft") as generate,
            ):
                lead_triage.prepare_worker_report(result, {})
            path.write_text("Changed by another URL check", encoding="utf-8")
        run.assert_called_once_with(result["check_url"], False, {})
        generate.assert_not_called()
        self.assertEqual("Actual Worker subject", result["worker_draft"]["subject"])
        self.assertIn("Actual Worker body", result["worker_draft"]["body"])

    def test_backend_fallback_uses_exact_shared_hosting_formatter(self):
        result = case()
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(lead_triage.pt, "REPORTS_DIR", directory),
                patch.object(lead_triage.pt, "run_check", return_value={"drafts": [], "cloaking": {"verdict": "NO_SIGNAL"}}),
            ):
                lead_triage.prepare_worker_report(result, {"contact_name": "Reporter"})
                original = lead_triage.pt.parse_draft_email(str(Path(directory) / "kzrsgs.cc_hosting_report.txt"))
                draft = lead_triage.build_investigation_draft(result, SAMPLE)
        self.assertEqual(original["subject"], draft["subject"])
        self.assertEqual(original["body"], draft["base_body"])
        self.assertIn("We are reporting an active phishing operation hosted on your network", draft["body"])
        self.assertIn("The domain kzrsgs.cc (hosted at IP 98.98.114.161)", draft["body"])
        self.assertNotIn("subdomain", draft["body"])
        self.assertNotIn("operator", draft["body"].lower().replace("network operator", ""))
        self.assertNotIn("AU888", draft["body"])
        self.assertLess(draft["body"].index("Additional verification details"), draft["body"].index("Regards,"))
        self.assertIn("Chúng tôi", draft["body_vi"])
        self.assertNotIn("We request", draft["body_vi"])

    def test_dns_timeout_preserves_supplied_information_without_changing_base(self):
        result = case()
        result["dns"] = {"ips": [], "error": "LifetimeTimeout"}
        result["ip_contacts"] = []
        draft = lead_triage.build_investigation_draft(result, SAMPLE)
        self.assertEqual("abuse@zenlayer.com", draft["to"])
        self.assertIn("report Domain Worker", draft["recipient_source"])
        self.assertIn("EXACT DOMAIN WORKER REPORT.", draft["body"])
        self.assertNotIn("DNS lookup", draft["body"])
        self.assertNotIn("AU888", draft["body"])
        self.assertNotIn("internal lead", draft["body"])
        self.assertIn("if abuse is confirmed, disable the responsible service", draft["body"])

    def test_missing_shared_report_does_not_fall_back_to_custom_letter(self):
        result = case()
        result["worker_draft"] = {}
        with self.assertRaises(ValueError):
            lead_triage.build_investigation_draft(result, SAMPLE)

    def test_excluded_brand_is_removed_from_cached_draft_and_observations(self):
        result = case()
        result["worker_draft"]["subject"] = "Abuse Report: AU888"
        result["worker_draft"]["body"] += "\nAssociated with au88.\n"
        draft = lead_triage.build_investigation_draft(
            result, SAMPLE,
            additional_english="Associated with AU888.\nVerified observation.",
        )
        for key in ("subject", "body", "body_vi"):
            self.assertNotIn("au88", draft[key].lower())
        self.assertIn("Verified observation.", draft["body"])
        self.assertIn("EXACT DOMAIN WORKER REPORT.", draft["body"])
        self.assertEqual("Abuse Report: kzrsgs.cc", draft["subject"])
        self.assertEqual("Reported URL: https://au888.example/", lead_triage._without_excluded_brand("Reported URL: https://au888.example/"))

    def test_worker_phishing_narrative_survives_brand_redaction(self):
        result = case()
        result["worker_draft"]["body"] = (
            "Dear Abuse Team,\n\n"
            "We are reporting suspected phishing and impersonation of AU888 at 8cewd.buzz.\n"
            "The site may trick visitors into sharing credentials with AU888 branding.\n\n"
            "Reported URL: https://8cewd.buzz/\n\nRegards,\nReporter\n"
        )
        draft = lead_triage.build_investigation_draft(result, SAMPLE)
        self.assertIn("suspected phishing and impersonation of our brand", draft["body"])
        self.assertIn("may trick visitors into sharing credentials", draft["body"])
        self.assertNotIn("AU888", draft["body"])
        self.assertIn("nghi phishing", draft["body_vi"])
        self.assertNotIn("We are reporting", draft["body_vi"])
        backend = [{"domain": "kzrsgs.cc", "supplied_ips": ["98.98.114.161"],
                    "dns": {"ips": ["98.98.114.161"]}, "ip_contacts": []}]
        with_backend = lead_triage.build_investigation_draft(result, SAMPLE, backend_results=backend)
        self.assertIn("IPs supplied:", with_backend["body"])
        self.assertIn("IP được cung cấp:", with_backend["body_vi"])
        self.assertNotIn("trong ghi chú", with_backend["body_vi"])

    def test_report_addendum_distinguishes_dns_from_unverified_backend_leads(self):
        result = case("8cewd.buzz")
        result["checked_at"] = "2026-10-06T12:00:00+00:00"
        result["worker_draft"]["body"] = (
            "Dear Abuse Team,\n\nThe our brand identity is copied with AU888 branding.\n\n"
            "Reported URL: https://8cewd.buzz/\n\nRegards,\nReporter\n"
        )
        backend = [{
            "domain": "kzrsgs.cc", "supplied_ips": ["98.98.114.161"],
            "dns": {"ips": [], "error": "LifetimeTimeout"},
            "ip_contacts": [{"ip": "98.98.114.161", "org": "Zenlayer Inc"}],
        }]
        draft = lead_triage.build_investigation_draft(result, SAMPLE, backend_results=backend)
        self.assertIn("Domain check time (UTC): 2026-10-06T12:00:00+00:00", draft["body"])
        self.assertIn("RELATED BACKEND FINDINGS", draft["body"])
        self.assertIn("kzrsgs.cc (no DNS/URL overlap observed)", draft["body"])
        self.assertIn("current DNS A: unavailable", draft["body"])
        self.assertIn("RDAP registration for supplied IP 98.98.114.161 (not in current DNS A): Zenlayer Inc", draft["body"])
        backend_section = draft["body"].split("RELATED BACKEND FINDINGS", 1)[1]
        self.assertNotIn("Network operator for 98.98.114.161", backend_section)
        self.assertNotIn("the our brand", draft["body"])
        self.assertNotIn("our brand branding", draft["body"])
        self.assertIn("our branding", draft["body"])
        self.assertIn("Please verify whether the listed infrastructure leads serve or support", draft["body"])
        self.assertIn("Báo cáo nghi phishing", draft["subject_vi"])
        self.assertIn("kzrsgs.cc", draft["body_vi"])

    def test_external_backend_details_only_include_resolved_and_registered_ips(self):
        result = case()
        result["ip_contacts"].append({"ip": "98.98.114.162", "org": "Other Network"})
        draft = lead_triage.build_investigation_draft(result, SAMPLE)
        self.assertIn("Observed DNS A for the reported domain: 98.98.114.161", draft["body"])
        self.assertIn("Network operator for 98.98.114.161: Zenlayer Inc", draft["body"])
        self.assertNotIn("Other Network", draft["body"])
        for text in (draft["body"], draft["body_vi"]):
            self.assertNotIn("internal lead", text)
            self.assertNotIn("ghi chú", text.lower())
            self.assertNotIn("theo mẫu", text.lower())

    def test_shared_evidence_block_and_observations_are_inserted_before_signature(self):
        result = case()
        with tempfile.TemporaryDirectory() as directory:
            evidence = browser_evidence.create_manual_browser_evidence(result["check_url"], [("page.png", PIXEL_PNG)], directory)
            expected_block = browser_evidence.format_email_evidence_block(evidence)
            draft = lead_triage.build_investigation_draft(result, SAMPLE, evidence=evidence, additional_english="Verified observation.")
        self.assertIn(expected_block, draft["body"])
        self.assertIn("EXACT DOMAIN WORKER REPORT.", draft["body"])
        self.assertIn("Verified observation.", draft["body"])
        self.assertLess(draft["body"].index(expected_block), draft["body"].index("Regards,"))
        self.assertIn("1 ảnh", draft["body_vi"])

    def test_send_exact_preview_once_and_blocks_changed_attachment(self):
        result = case()
        account = {"username": "sender@example.org"}
        with tempfile.TemporaryDirectory() as directory:
            evidence = dom_evidence(result["check_url"], directory)
            attachments = browser_evidence.evidence_attachment_paths(evidence)
            draft = lead_triage.build_investigation_draft(result, SAMPLE, evidence=evidence)
            fingerprint = lead_triage.delivery_fingerprint(result["check_url"], draft["to"], draft["subject"], draft["body"], account["username"], attachments)
            args = dict(target=result["check_url"], recipient=draft["to"], subject=draft["subject"], body=draft["body"], account=account,
                        attachments=attachments, evidence=evidence,
                        expected_fingerprint=fingerprint, draft_file=draft["draft_file"])
            with (
                patch.object(lead_triage.pt, "SENT_LOG_PATH", str(Path(directory) / "sent_log.csv")),
                patch.object(lead_triage.pt, "send_report_email_single", return_value={"success": True, "account": account["username"]}) as send,
            ):
                self.assertTrue(lead_triage.send_investigation_draft(**args)["success"])
                self.assertTrue(lead_triage.send_investigation_draft(**args)["already_sent"])
                Path(attachments[0]).write_bytes(PIXEL_PNG + b"changed")
                self.assertFalse(lead_triage.send_investigation_draft(**args)["success"])
            send.assert_called_once_with(draft["to"], draft["subject"], draft["body"], account, proxy_str=None, attachments=attachments)

    def test_page_reload_preserves_results(self):
        app = app_for(case())
        with patch("phishing_toolkit.load_config", return_value={}), patch.object(lead_triage, "MODULE_VERSION", 0):
            app = app.run()
        self.assertFalse(app.exception)
        self.assertEqual(24, lead_triage.MODULE_VERSION)

    def test_recheck_email_button_uses_worker_resolver_and_updates_draft(self):
        result = case("8cewd.buzz")
        refreshed = [{"channel": "hosting", "email": "updated@example.org"}]
        with (
            patch("phishing_toolkit.load_config", return_value={}),
            patch.object(lead_triage.domain_worker, "_precheck_report_recipients", return_value=refreshed) as lookup,
        ):
            app = app_for(result).run()
            app = next(button for button in app.button if "Kiểm tra lại email nhận" in button.label).click().run()
        self.assertFalse(app.exception)
        lookup.assert_called_once_with("8cewd.buzz")
        self.assertEqual("updated@example.org", next(widget.value for widget in app.text_input if widget.label.startswith("Email nhận report")))
        self.assertEqual("Domain Worker precheck — hosting", lead_triage.build_investigation_draft(result, SAMPLE)["recipient_source"])

    def test_failed_email_recheck_clears_stale_result(self):
        result = case()
        result["recipients"] = [{"channel": "hosting", "email": "stale@example.org"}]
        with patch.object(lead_triage.domain_worker, "_precheck_report_recipients", side_effect=TimeoutError):
            lead_triage.refresh_worker_recipients(result)
        self.assertEqual([], result["recipients"])
        self.assertEqual("TimeoutError", result["recipients_error"])

    def test_recipient_follows_domain_worker_draft_channel(self):
        result = case("8cewd.buzz")
        result["worker_draft"]["filename"] = "8cewd.buzz_registry_report.txt"
        result["worker_draft"]["to"] = "old-registry@example.org"
        result["ip_contacts"] = [{"ip": "98.98.114.161", "abuse_email": "backend@example.org"}]
        result["recipients"] = [
            {"channel": "registrar", "email": "registrar@example.org"},
            {"channel": "registry", "email": "registry@example.org"},
            {"channel": "hosting", "email": "hosting@example.org"},
        ]
        draft = lead_triage.build_investigation_draft(result, SAMPLE)
        self.assertEqual("registry@example.org", draft["to"])
        self.assertEqual("Domain Worker precheck — registry", draft["recipient_source"])

    def test_recipient_filters_blocked_and_parses_worker_email_list(self):
        result = case()
        result["recipients"] = [{"channel": "hosting", "email": "abuse@cloudflare.com, valid@example.org"}]
        self.assertEqual("valid@example.org", lead_triage.worker_report_recipient(result)[0])
        result["recipients"] = [{"channel": "registry", "email": "other@example.org"}]
        self.assertEqual("abuse@zenlayer.com", lead_triage.worker_report_recipient(result)[0])
        result["worker_draft"]["to"] = "[TRA ABUSE EMAIL]"
        self.assertEqual("", lead_triage.worker_report_recipient(result)[0])

    def test_dom_report_guides_provider_to_verify_source_destination_and_backend(self):
        result = case("https://example.test/vi-vn/")
        backend = [{"domain": "destination.example", "supplied_ips": ["192.0.2.10"],
                    "dns": {"ips": ["192.0.2.10"]},
                    "ip_contacts": [{"ip": "192.0.2.10", "org": "Amazon.com, Inc."}]}]
        with tempfile.TemporaryDirectory() as directory:
            evidence = dom_evidence(result["check_url"], directory)
            shared_block = browser_evidence.format_email_evidence_block(evidence)
            draft = lead_triage.build_investigation_draft(result, SAMPLE, evidence=evidence, backend_results=backend)
        compact_shared_block = shared_block.replace(
            "Please investigate the reported URL, the disclosed destination, and their "
            "relationship, and take appropriate action under your phishing and abuse policies.\n",
            "",
        ).replace("--- Observed Phishing Behavior and Supporting Evidence ---", "OBSERVED REDIRECT CHAIN AND SUPPORTING EVIDENCE", 1)
        self.assertIn(compact_shared_block, draft["body"])
        self.assertIn("EVIDENCE AND REQUEST:", draft["body"])
        self.assertEqual(1, draft["body"].count("1. Visit https://example.test/vi-vn/"))
        self.assertIn("Assess whether the destination's branding", draft["body"])
        self.assertNotIn("Amazon.com, Inc..", draft["body"])
        self.assertNotIn("Observed DNS A for the reported domain", draft["body"])
        self.assertNotIn("Please investigate the reported URL, the disclosed destination", draft["body"])
        self.assertIn("whether the listed infrastructure serves the reported URL", draft["body"])
        self.assertIn("Điểm cần xác minh:", draft["body_vi"])
        self.assertIn("https://destination.example/login", draft["body_vi"])
        self.assertIn("có phục vụ URL báo cáo hoặc URL đích quan sát được hay không", draft["body_vi"])

    def test_registry_report_includes_unlinked_backend_with_caveat_and_preserves_dom_redirect(self):
        result = case("8cewd.buzz")
        result["dns"] = {"ips": ["104.21.26.223", "172.67.139.119"], "error": ""}
        result["registrar"] = "GoDaddy.com, LLC"
        result["ip_contacts"] = []
        result["worker_draft"]["body"] = (
            "Dear DOTSTRATEGY CO. Abuse Department,\n\n"
            "We request your assistance in reviewing a suspected abuse case involving "
            "8cewd.buzz and unauthorized use of the my brand identity.\n\n"
            "Reported URL: https://8cewd.buzz/\n\nRegards,\nReporter\n"
        )
        backends = [
            {"domain": "kzrsgs.cc", "supplied_ips": ["98.98.114.161"],
             "dns": {"ips": ["98.98.114.161"], "error": ""},
             "ip_contacts": [{"ip": "98.98.114.161", "org": "Zenlayer Inc"}]},
            {"domain": "mb66ac.xyz", "supplied_ips": ["75.2.99.114"],
             "dns": {"ips": ["75.2.99.114"], "error": ""},
             "ip_contacts": [{"ip": "75.2.99.114", "org": "Amazon.com, Inc."}]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            evidence = dom_evidence(result["check_url"], directory)
            path = Path(evidence["manifest_path"])
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["control"] = {"found": True, "label": "ĐĂNG NHẬP / ĐĂNG KÝ",
                                   "resolved_destination": "https://blaut1vn.com/frey"}
            manifest["final_url"] = "https://www.au888vn.yoga/home/reg.html?inviteCode=43311861"
            manifest["navigation"] = {"source_url": result["check_url"],
                                      "requested_destination": "https://blaut1vn.com/frey",
                                      "redirect_chain": [{"url": "https://blaut1vn.com/frey",
                                                          "location": "https://www.au888vn.yoga/?inviteCode=43311861",
                                                          "status": 307}]}
            path.write_text(json.dumps(manifest), encoding="utf-8")
            draft = lead_triage.build_investigation_draft(result, SAMPLE, evidence=evidence,
                                                           backend_results=backends)
        body = draft["body"]
        self.assertLess(body.index("BRAND IMPERSONATION AND FRAUD CONCERNS"), body.index("OBSERVED REDIRECT CHAIN AND SUPPORTING EVIDENCE"))
        self.assertLess(body.index("Registrar: GoDaddy.com, LLC"), body.index("OBSERVED REDIRECT CHAIN AND SUPPORTING EVIDENCE"))
        self.assertLess(body.index("OBSERVED REDIRECT CHAIN AND SUPPORTING EVIDENCE"), body.index("RELATED BACKEND FINDINGS"))
        self.assertLess(body.index("RELATED BACKEND FINDINGS"), body.index("EVIDENCE AND REQUEST:"))
        self.assertIn("one-time passwords (OTPs)", body)
        self.assertIn("NGHI VẤN GIẢ MẠO THƯƠNG HIỆU VÀ LỪA ĐẢO", draft["body_vi"])
        self.assertIn("unauthorized use of our brand identity", body)
        self.assertNotIn("the my brand", body)
        self.assertIn("RELATED BACKEND FINDINGS", body)
        self.assertIn("Registrar: GoDaddy.com, LLC", body)
        self.assertNotIn("Additional verification details:", body)
        self.assertNotIn("Domain check time (UTC):", body)
        self.assertEqual(1, body.count("1. Visit https://8cewd.buzz/"))
        self.assertEqual(1, body.count("EVIDENCE AND REQUEST:"))
        self.assertIn("kzrsgs.cc (no DNS/URL overlap observed)", body)
        self.assertIn("mb66ac.xyz (no DNS/URL overlap observed)", body)
        self.assertIn("DNS A hiện tại:", draft["body_vi"])
        self.assertIn("Zenlayer Inc", body)
        self.assertIn("Amazon.com, Inc", body)
        self.assertIn("https://blaut1vn.com/frey", body)
        self.assertIn("https://www.au888vn.yoga/home/reg.html?inviteCode=43311861", body)
        self.assertNotIn("compare the visible branding", body)
        self.assertNotIn("Please investigate the reported URL, preserve", body)
        self.assertNotIn("Please investigate the reported URL, the disclosed destination", body)
        self.assertIn("kzrsgs.cc", draft["body_vi"])

    def test_shared_registry_formatter_uses_grammatical_brand_identity(self):
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(lead_triage.pt, "REPORTS_DIR", directory),
              patch.object(lead_triage.pt, "_pick", side_effect=lambda rng, choices: choices[-1])):
            path = lead_triage.pt.generate_registry_draft(
                "8cewd.buzz",
                {"source": "static_table", "registry": "DOTSTRATEGY CO.",
                 "abuse_email": "registry@example.org"},
                {"brand_name": "my brand", "contact_name": "Reporter"},
                target_url="https://8cewd.buzz/",
            )
            report = Path(path).read_text(encoding="utf-8")
        self.assertIn("unauthorized use of our brand identity", report)
        self.assertNotIn("the my brand identity", report)

    def test_registry_worker_draft_is_translated_for_vietnamese_review(self):
        result = case("8cewd.buzz")
        result["worker_draft"]["body"] = (
            "Dear DOTSTRATEGY CO. Abuse Department,\n\n"
            "We request your assistance in reviewing a suspected abuse case involving 8cewd.buzz and unauthorized use of the our brand identity.\n\n"
            "We are contacting the registry because the reported URL appears to present an ongoing abuse risk. Please independently review the evidence and coordinate with the sponsoring registrar where appropriate.\n\n"
            "We request an urgent abuse investigation and any action available to the registry after independent verification.\n\n"
            "Domain: 8cewd.buzz\nReported URL: https://8cewd.buzz/\nFirst detected: 2026-10-06\n\nRegards,\nReporter\n"
        )
        draft = lead_triage.build_investigation_draft(result, SAMPLE)
        self.assertIn("Kính gửi bộ phận xử lý lạm dụng", draft["body_vi"])
        self.assertIn("Chúng tôi liên hệ registry", draft["body_vi"])
        self.assertIn("điều tra khẩn cấp", draft["body_vi"])
        self.assertIn("Tên miền: 8cewd.buzz", draft["body_vi"])
        self.assertIn("Ngày phát hiện: 2026-10-06", draft["body_vi"])
        for sentence in ("Dear", "We are contacting", "We request", "Domain:", "First detected:"):
            self.assertNotIn(sentence, draft["body_vi"])
        self.assertIn("We are contacting the registry", draft["body"])

    def test_registry_worker_variants_keep_vietnamese_review_complete(self):
        variants = [
            "We are requesting a registry-level review of suspected phishing and unauthorized impersonation of our brand at 8cewd.buzz.",
            "We are reporting suspected phishing content hosted under 8cewd.buzz that appears to impersonate our brand without authorization.",
            "This report concerns suspected phishing and brand impersonation observed at the reported URL under 8cewd.buzz.",
            "We request your assistance in reviewing a suspected abuse case involving 8cewd.buzz and unauthorized use of the our brand identity.",
            "Please investigate and take proportionate registry-level action under your abuse policy if the reported violation is confirmed.",
            "Please review the reported URL and coordinate prompt mitigation with the sponsoring registrar where appropriate.",
            "We request an urgent abuse investigation and any action available to the registry after independent verification.",
            "Please preserve relevant records, investigate the reported content, and apply the measures provided by your abuse policy if confirmed.",
        ]
        translated = lead_triage._report_review_vi("\n".join(variants), "8cewd.buzz")
        self.assertEqual(8, len(translated.splitlines()))
        self.assertTrue(all(line.startswith(("Chúng tôi", "Báo cáo", "Vui lòng")) for line in translated.splitlines()))
        prior = lead_triage._report_review_vi(
            "This matter was previously reported to the sponsoring registrar on 2026-10-05. The reported URL remains available, so we are requesting registry-level review.",
            "8cewd.buzz",
        )
        self.assertIn("2026-10-05", prior)
        self.assertNotIn("This matter", prior)

    def test_registrar_worker_variants_have_complete_vietnamese_review(self):
        source = lead_triage.pt
        lines = [
            *(variant.format(domain="example.test") for variant in source._OPENING_REGISTRAR),
            *source._DESCRIPTION_REGISTRAR,
            *source._REQUEST_REGISTRAR,
            *source._CLOSING_REGISTRAR,
            "This domain was first identified by our security team on 2026-10-06 during routine brand monitoring.",
            "As of 2026-10-06, 2 security engines on VirusTotal flagged this domain as malicious.",
            "- Registrar: Example Registrar",
            "- SSL Issuer: Example CA",
        ]
        translated = lead_triage._report_review_vi("\n".join(lines), "example.test")
        self.assertEqual(len(lines), len(translated.splitlines()))
        for prefix in ("We ", "The ", "Please ", "This ", "Our ", "Thank you", "As of"):
            self.assertFalse(any(line.startswith(prefix) for line in translated.splitlines()), prefix)
        self.assertIn("2026-10-06", translated)
        self.assertIn("Example Registrar", translated)
        self.assertIn("Nhà đăng ký: Example Registrar", translated)
        self.assertIn("Đơn vị cấp SSL: Example CA", translated)

    def test_send_rejects_manual_and_passive_evidence_before_smtp(self):
        result = case()
        with tempfile.TemporaryDirectory() as directory:
            manual = browser_evidence.create_manual_browser_evidence(
                result["check_url"], [("page.png", PIXEL_PNG)], directory,
            )
            for evidence in (manual, {**manual, "capture_strategy": "passive_fallback"}):
                attachments = browser_evidence.evidence_attachment_paths(evidence)
                self.assertEqual([], lead_triage.validated_dom_attachments(evidence, result["check_url"]))
                with patch.object(lead_triage.pt, "send_report_email_single") as send:
                    outcome = lead_triage.send_investigation_draft(
                        target=result["check_url"], recipient="abuse@example.org",
                        subject="Report", body="Reported URL: " + result["check_url"],
                        account={"username": "sender@example.org"},
                        attachments=attachments, evidence=evidence,
                    )
                self.assertFalse(outcome["success"])
                send.assert_not_called()

    def test_risk_domain_two_draft_modes_preview_then_send_via_shared_ledger(self):
        cfg = {"contact_name": "Reporter", "smtp_accounts": [
            {"username": "sender@example.org", "password": "credential-never-cached"}]}
        with tempfile.TemporaryDirectory() as directory:
            for mode in (lead_triage.review_sender.CONFIRMED_CLOAKING, lead_triage.review_sender.NOT_CLOAKING):
                with self.subTest(mode=mode):
                    target = "confirmed.example" if mode == lead_triage.review_sender.CONFIRMED_CLOAKING else "normal.example"
                    result = case(target)
                    result["cloaking"] = cloaking_evidence(result["check_url"], directory)
                    dom = dom_evidence(result["check_url"], directory)
                    draft_path = Path(directory) / f"{target}_registry_report.txt"
                    draft_path.write_text(
                        "To: abuse@example.org\nSubject: Phishing report\n\nDear Abuse Team,\n\n"
                        "We are reporting suspected phishing and impersonation of our brand.\n\n"
                        "--- Technical Evidence: Multi-profile Cloaking Check ---\n"
                        "Old unapproved evidence\n--- End of Cloaking Evidence ---\n\nRegards,\nReporter\n",
                        encoding="utf-8")
                    with (
                        patch("phishing_toolkit.load_config", return_value=cfg),
                        patch.object(lead_triage.pt, "run_check", return_value={"drafts": [str(draft_path)]}) as check,
                        patch("provider_replies.capture_dom_link_evidence", return_value=dom) as capture,
                        patch.object(lead_triage.pt, "send_report_email_single", return_value={
                            "success": True, "account": "sender@example.org"}) as send,
                        patch.object(lead_triage.pt, "log_sent"),
                    ):
                        app = app_for(result)
                        app.session_state["lead_triage_cache_loaded"] = True
                        app.session_state["lead_triage_visible_targets"] = [target]
                        app = app.run()
                        self.assertFalse(app.exception)
                        app = app.selectbox[0].set_value("sender@example.org").run()
                        label = "Tạo draft cloaking" if mode == lead_triage.review_sender.CONFIRMED_CLOAKING else "Tạo draft không cloaking"
                        app = next(button for button in app.button if button.label == label).click().run()
                        self.assertFalse(app.exception)
                        self.assertTrue(next(button for button in app.button if button.label == "Gửi report đã duyệt").disabled)
                        send.assert_not_called()
                        preview = next(value for key, value in lead_triage.load_review_cache()["cloaking_previews"].items()
                                       if key.startswith("lead_triage_cloaking_preview_") and value["target_url"] == result["check_url"])
                        self.assertEqual(mode, preview["decision"])
                        body = preview["deliveries"][0]["body"]
                        if mode == lead_triage.review_sender.CONFIRMED_CLOAKING:
                            self.assertIn("CONFIRMED CLOAKING", body)
                            capture.assert_not_called()
                        else:
                            self.assertNotIn("Multi-profile Cloaking Check", body)
                            self.assertNotIn("Old unapproved evidence", body)
                            self.assertEqual(browser_evidence.evidence_attachment_paths(dom), preview["attachments"])
                            capture.assert_called_once()
                        self.assertNotIn("credential-never-cached", json.dumps(lead_triage.load_review_cache()))
                        reopened = AppTest.from_file(str(ROOT / "pages/15_Lead_Triage.py"), default_timeout=10).run()
                        self.assertFalse(reopened.exception)
                        self.assertEqual(1, check.call_count)
                        reopened = reopened.checkbox[0].check().run()
                        reopened = next(button for button in reopened.button if button.label == "Gửi report đã duyệt").click().run()
                        self.assertFalse(reopened.exception)
                        send.assert_called_once()
                        self.assertEqual(body, send.call_args.args[2])
                        self.assertEqual(preview["attachments"], send.call_args.kwargs["attachments"])
                        self.assertEqual(lead_triage.review_queue.SENT,
                                         lead_triage.review_queue.load_item(preview["queue_id"])["state"])

    def test_cloaking_prepare_and_send_fail_closed_for_missing_or_changed_evidence(self):
        result = case("risk.example")
        cfg = {"smtp_accounts": [{"username": "sender@example.org"}]}
        result["cloaking"] = {"verdict": "INCONCLUSIVE", "target_url": result["check_url"]}
        with patch.object(lead_triage.pt, "run_check") as check:
            with self.assertRaises(ValueError):
                lead_triage.prepare_lead_review(result, "", decision="confirmed_cloaking",
                                               account_name="sender@example.org", cfg=cfg)
            check.assert_not_called()
        # Replace only the pending case's evidence, leaving its ledger intact.
        with tempfile.TemporaryDirectory() as directory:
            result["cloaking"] = cloaking_evidence(result["check_url"], directory)
            item = lead_triage.ensure_lead_review_case(result, cfg, ["sender@example.org"])
            lead_triage.review_queue.update_cloaking_result(item["queue_id"], result["cloaking"])
            draft_path = Path(directory) / "risk.example_registry_report.txt"
            draft_path.write_text("To: abuse@example.org\nSubject: Report\n\nDear Abuse Team,\n\nPlease review.\nRegards,\nReporter\n", encoding="utf-8")
            with patch.object(lead_triage.pt, "run_check", return_value={"drafts": [str(draft_path)]}):
                preview = lead_triage.prepare_lead_review(result, "", decision="confirmed_cloaking",
                                                         account_name="sender@example.org", cfg=cfg)
            with patch.object(lead_triage.pt, "send_report_email_single") as send:
                with self.assertRaises(ValueError):
                    lead_triage.send_lead_review(preview, result=result, raw_note="", cfg=cfg,
                                                account_name="sender@example.org", backend_results=[],
                                                additional_english="Changed observation.")
                Path(result["cloaking"]["screenshots"][0]["path"]).write_bytes(PIXEL_PNG + b"changed")
                with self.assertRaises(ValueError):
                    lead_triage.send_lead_review(preview, result=result, raw_note="", cfg=cfg,
                                                account_name="sender@example.org", backend_results=[],
                                                additional_english="")
                send.assert_not_called()

    def test_not_cloaking_dom_failure_does_not_prepare_or_send(self):
        result = case("risk-dom.example")
        result["cloaking"] = {"verdict": "POSSIBLE", "target_url": result["check_url"]}
        cfg = {"smtp_accounts": [{"username": "sender@example.org"}]}
        with patch("provider_replies.capture_dom_link_evidence", return_value={"error": "Timeout"}), patch.object(lead_triage.pt, "run_check") as check:
            with self.assertRaisesRegex(ValueError, "DOM"):
                lead_triage.prepare_lead_review(result, "", decision="not_cloaking",
                                               account_name="sender@example.org", cfg=cfg)
            check.assert_not_called()

    def test_dom_validation_rechecks_url_and_manifest(self):
        result = case()
        with tempfile.TemporaryDirectory() as directory:
            evidence = dom_evidence(result["check_url"], directory)
            self.assertEqual(3, len(lead_triage.validated_dom_attachments(evidence, result["check_url"])))
            self.assertEqual([], lead_triage.validated_dom_attachments(evidence, "https://other.example/"))
            Path(evidence["screenshot_paths"][0]).write_bytes(PIXEL_PNG + b"changed")
            self.assertEqual([], lead_triage.validated_dom_attachments(evidence, result["check_url"]))

    def test_page_row_check_shows_only_clicked_target_and_keeps_cached_results(self):
        with patch("phishing_toolkit.load_config", return_value={}), patch("lead_triage.check_lead", side_effect=lambda item, cfg: case(item["target"])) as check, patch("lead_triage.check_backend_context", return_value=[]):
            app = AppTest.from_file(str(ROOT / "pages/15_Lead_Triage.py"), default_timeout=10).run()
            app = app.text_area[0].input(SAMPLE).run()
            app = next(button for button in app.button if button.label == "Phân tích ghi chú").click().run()
            self.assertEqual(":material/search: Check", app.dataframe[0].value.iloc[0]["Check"])
            source = hashlib.sha256(SAMPLE.encode()).hexdigest()
            app.session_state["lead_triage_pending_row"] = (source, "uwin789.info")
            app = app.run()
            self.assertEqual(["uwin789.info"], [row["target"] for row in app.session_state["lead_triage_results"]["items"]])
            self.assertEqual(["uwin789.info"], app.session_state["lead_triage_visible_targets"])
            app.session_state["lead_triage_pending_row"] = (source, "8cewd.buzz")
            app = app.run()
            self.assertEqual(["8cewd.buzz", "uwin789.info"], [row["target"] for row in app.session_state["lead_triage_results"]["items"]])
            self.assertEqual(["8cewd.buzz"], app.session_state["lead_triage_visible_targets"])
            precheck_table = next(
                frame.value for frame in app.dataframe
                if "DNS A hiện tại" in frame.value.columns and "So IP ghi chú" in frame.value.columns
            )
            self.assertEqual(["8cewd.buzz"], precheck_table["Domain/URL"].tolist())
            self.assertEqual(2, check.call_count)

    def test_page_defaults_report_selection_and_two_paths_have_distinct_widgets(self):
        with patch("phishing_toolkit.load_config", return_value={}), patch("lead_triage.check_lead", side_effect=lambda item, cfg: case(item["target"])) as check, patch("lead_triage.check_backend_context", return_value=[]):
            app = AppTest.from_file(str(ROOT / "pages/15_Lead_Triage.py"), default_timeout=10).run()
            app = app.text_area[0].input(SAMPLE).run()
            app = next(button for button in app.button if button.label == "Phân tích ghi chú").click().run()
            self.assertEqual(["8cewd.buzz", "uwin789.info"], app.multiselect[0].value)
            app = next(button for button in app.button if "Check đầu mối đã chọn" in button.label).click().run()
            self.assertEqual(2, check.call_count)
            self.assertFalse(app.exception)
            app = app.text_area[0].input("https://example.test/a\nhttps://example.test/b").run()
            app = next(button for button in app.button if button.label == "Phân tích ghi chú").click().run()
            app = next(button for button in app.button if "Check đầu mối đã chọn" in button.label).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(2, len([widget for widget in app.text_input if widget.label.startswith("Email nhận report")]))

    def test_switching_domain_hides_previous_draft_and_dom_images_for_row_and_batch(self):
        first, second = case("8cewd.buzz"), case("uwin789.info")
        second["worker_draft"]["to"] = "second@example.org"
        source = hashlib.sha256(SAMPLE.encode()).hexdigest()
        target_key = hashlib.sha256(first["target"].encode()).hexdigest()[:16]
        evidence_key = f"lead_triage_evidence_{source[:12]}_{target_key}"
        with tempfile.TemporaryDirectory() as directory:
            evidence = dom_evidence(first["check_url"], directory)
            for mode in ("row", "batch"):
                with (
                    self.subTest(mode=mode),
                    patch("phishing_toolkit.load_config", return_value={}),
                    patch("lead_triage.check_lead", return_value=second),
                    patch("lead_triage.check_backend_context", return_value=[]),
                    patch("provider_replies.capture_dom_link_evidence") as capture,
                ):
                    app = app_for(first)
                    app.session_state["lead_triage_cache_loaded"] = True
                    app.session_state["lead_triage_visible_targets"] = [first["target"]]
                    app.session_state[evidence_key] = evidence
                    app.session_state["lead_triage_additional_english"] = "Observation for first domain."
                    app = app.run()
                    self.assertEqual(2, len(app.image))
                    if mode == "row":
                        app.session_state["lead_triage_pending_row"] = (source, second["target"])
                        app = app.run()
                    else:
                        app = app.multiselect[0].set_value([second["target"]]).run()
                        app = next(button for button in app.button if "Check đầu mối đã chọn" in button.label).click().run()
                    self.assertFalse(app.exception)
                    self.assertEqual([second["target"]], app.session_state["lead_triage_visible_targets"])
                    self.assertEqual(0, len(app.image))
                    preview = next(widget.value for widget in app.text_area if widget.label == "Bản tiếng Anh")
                    self.assertIn(second["check_url"], preview)
                    self.assertNotIn(first["target"], preview)
                    self.assertNotIn("Observation for first domain.", preview)
                    self.assertEqual(["second@example.org"], [widget.value for widget in app.text_input])
                    self.assertEqual(2, len(app.session_state["lead_triage_results"]["items"]))
                    self.assertEqual(evidence, app.session_state[evidence_key])
                    reopened = AppTest.from_file(str(ROOT / "pages/15_Lead_Triage.py"), default_timeout=10).run()
                    self.assertFalse(reopened.exception)
                    self.assertEqual([second["target"]], reopened.session_state["lead_triage_visible_targets"])
                    self.assertEqual(0, len(reopened.image))
                    self.assertEqual(["second@example.org"], [widget.value for widget in reopened.text_input])
                    capture.assert_not_called()

    def test_page_dom_button_calls_provider_replies_and_sends_previewed_artifacts(self):
        result = case()
        app = app_for(result)
        with tempfile.TemporaryDirectory() as directory:
            evidence = dom_evidence(result["check_url"], directory)
            with (
                patch("phishing_toolkit.load_config", return_value={
                    "contact_name": "Reporter", "contact_email": "old@example.org",
                    "signature_logo": "assets/email/logo-win.jpg",
                    "smtp_accounts": [{"username": "sender@example.org", "contact_name": "Sender",
                                       "company_name": "Example Company", "signature_role": "Brand Protection",
                                       "business_registration_no": "123456"}],
                }),
                patch("provider_replies.capture_dom_link_evidence", return_value=evidence) as capture,
                patch("lead_triage.send_investigation_draft", return_value={"success": True, "account": "sender@example.org"}) as send,
            ):
                app = app.run()
                app = next(button for button in app.button if button.label == "Chụp URL nguồn + URL đích từ DOM").click().run()
                capture.assert_called_once_with(result["check_url"], result["domain"])
                self.assertFalse(app.exception)
                self.assertTrue(next(button for button in app.button if button.label == "Gửi report").disabled)

                app = app.selectbox[0].set_value("sender@example.org").run()
                app = app.checkbox[0].check().run()
                preview = next(widget.value for widget in app.text_area if widget.label == "Bản tiếng Anh")
                vietnamese = next(widget.value for widget in app.text_area if widget.label == "Bản tiếng Việt")
                for body in (preview, vietnamese):
                    self.assertIn("Example Company", body)
                    self.assertIn("Business Registration No. 123456", body)
                    self.assertIn("Sender", body)
                app = next(button for button in app.button if button.label == "Gửi report").click().run()
                send.assert_called_once()
                self.assertEqual(preview, send.call_args.kwargs["body"])
                self.assertEqual("assets/email/logo-win.jpg", send.call_args.kwargs["account"]["signature_logo"])
                self.assertEqual(browser_evidence.evidence_attachment_paths(evidence), send.call_args.kwargs["attachments"])
                sent_rows = app.dataframe[0].value
                self.assertEqual("Đã gửi", sent_rows.loc[sent_rows["Domain/URL"] == result["target"], "Trạng thái gửi"].iloc[0])
                self.assertNotIn(result["target"], app.multiselect[0].value)
                self.assertTrue(all(value == "Chưa gửi" for value in sent_rows.loc[sent_rows["Domain/URL"] != result["target"], "Trạng thái gửi"]))
                self.assertTrue(next(button for button in app.button if button.label == "Gửi report").disabled)

    def test_preview_reuses_company_signature_and_logo_before_account_selection(self):
        result = case()
        cfg = {
            "contact_name": "Reporter", "contact_email": "reporter@example.org",
            "company_name": "Example Company", "signature_role": "Brand Protection",
            "business_registration_no": "123456",
            "signature_logo": "assets/email/logo-win.jpg",
            "smtp_accounts": [{"username": "sender@example.org"}],
        }
        with patch("phishing_toolkit.load_config", return_value=cfg):
            app = app_for(result).run()
        self.assertFalse(app.exception)
        english = next(widget.value for widget in app.text_area if widget.label == "Bản tiếng Anh")
        self.assertIn("Brand Protection — Example Company", english)
        self.assertIn("Business Registration No. 123456", english)
        self.assertGreaterEqual(len(app.image), 2)
        self.assertTrue(next(button for button in app.button if button.label == "Gửi report").disabled)

    def test_cloaking_coverage_gap_blocks_send(self):
        result = case()
        result["worker_cloaking"] = {"verdict": "NO_SIGNAL", "coverage": {"multi_vantage_recommended": True}}
        with tempfile.TemporaryDirectory() as directory:
            evidence = browser_evidence.create_manual_browser_evidence(result["check_url"], [("page.png", PIXEL_PNG)], directory)
            app = app_for(result)
            active_raw = app.session_state["lead_triage_input"]
            key = f"lead_triage_evidence_{hashlib.sha256(active_raw.encode()).hexdigest()[:12]}_{hashlib.sha256(result['target'].encode()).hexdigest()[:16]}"
            app.session_state[key] = evidence
            with patch("phishing_toolkit.load_config", return_value={"smtp_accounts": [{"username": "sender@example.org"}]}):
                app = app.run()
                app = app.selectbox[0].set_value("sender@example.org").run()
                app = app.checkbox[0].check().run()
                self.assertTrue(next(button for button in app.button if button.label == "Gửi report").disabled)

    def test_failed_delivery_keeps_target_unsent(self):
        result = case()
        app = app_for(result)
        with tempfile.TemporaryDirectory() as directory:
            evidence = dom_evidence(result["check_url"], directory)
            with (
                patch("phishing_toolkit.load_config", return_value={"smtp_accounts": [{"username": "sender@example.org"}]}),
                patch("provider_replies.capture_dom_link_evidence", return_value=evidence),
                patch("lead_triage.send_investigation_draft", return_value={"success": False, "error": "SMTP unavailable"}),
            ):
                app = app.run()
                app = next(button for button in app.button if button.label == "Chụp URL nguồn + URL đích từ DOM").click().run()
                app = app.selectbox[0].set_value("sender@example.org").run()
                app = app.checkbox[0].check().run()
                app = next(button for button in app.button if button.label == "Gửi report").click().run()
                self.assertEqual("Chưa gửi", app.dataframe[0].value.iloc[0]["Trạng thái gửi"])

    def test_sent_target_cannot_be_checked_again(self):
        result = case()
        app = app_for(result)
        app.session_state["lead_triage_delivery_status"] = {result["target"]: True}
        active_raw = app.session_state["lead_triage_input"]
        with (
            patch("phishing_toolkit.load_config", return_value={}),
            patch("lead_triage.check_lead", side_effect=lambda item, cfg: case(item["target"])) as check,
            patch("lead_triage.check_backend_context", return_value=[]),
        ):
            app = app.run()
            row = app.dataframe[0].value.loc[lambda frame: frame["Domain/URL"] == result["target"]].iloc[0]
            self.assertEqual("Đã gửi", row["Trạng thái gửi"])
            self.assertNotEqual(":material/search: Check", row["Check"])
            self.assertNotIn(result["target"], app.multiselect[0].value)
            app.session_state["lead_triage_pending_row"] = (hashlib.sha256(active_raw.encode()).hexdigest(), result["target"])
            app = app.run()
            check.assert_not_called()
            app = next(button for button in app.button if "Check đầu mối đã chọn" in button.label).click().run()
            self.assertNotIn(result["target"], [call.args[0]["target"] for call in check.call_args_list])

    def test_sent_log_restores_check_lock_in_new_session(self):
        result = case()
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "sent_log.csv"
            log_path.write_text(
                "target_url,status,send_mode\n"
                f"{result['check_url']},sent,lead_triage\n"
                "https://uwin789.info/,failed,lead_triage\n"
                "https://8cewd.buzz/,sent,other_menu\n",
                encoding="utf-8",
            )
            with (
                patch.object(lead_triage.pt, "SENT_LOG_PATH", str(log_path)),
                patch("phishing_toolkit.load_config", return_value={}),
                patch("lead_triage.check_lead", side_effect=lambda item, cfg: case(item["target"])) as check,
                patch("lead_triage.check_backend_context", return_value=[]),
            ):
                app = app_for(result).run()
                rows = app.dataframe[0].value.set_index("Domain/URL")
                self.assertEqual("Đã gửi", rows.loc[result["target"], "Trạng thái gửi"])
                self.assertNotEqual(":material/search: Check", rows.loc[result["target"], "Check"])
                self.assertEqual("Chưa gửi", rows.loc["uwin789.info", "Trạng thái gửi"])
                self.assertEqual("Chưa gửi", rows.loc["8cewd.buzz", "Trạng thái gửi"])
                self.assertNotIn(result["target"], app.multiselect[0].value)
                app = next(button for button in app.button if "Check đầu mối đã chọn" in button.label).click().run()
                self.assertNotIn(result["target"], [call.args[0]["target"] for call in check.call_args_list])


if __name__ == "__main__":
    unittest.main()

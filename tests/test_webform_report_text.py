import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import phishing_toolkit as pt


class WebformReportTextTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "brand_name": "Example Brand",
            "contact_name": "Reporter",
            "contact_email": "reporter@example.test",
        }
        self.url = "https://phish.example.test/vi-vn/?campaign=one"

    def test_registrar_webform_never_contains_urlscan_evidence(self):
        text = pt.get_webform_draft_text(
            "phish.example.test", "Example Registrar", "https://form.example.test",
            self.cfg, target_url=self.url,
            urlscan={
                "result_url": "https://urlscan.io/result/secret/",
                "screenshot_url": "https://urlscan.io/screenshots/secret.png",
            },
        )
        self.assertIn(self.url, text)
        self.assertNotIn("URLScan", text)
        self.assertNotIn("urlscan.io", text.lower())

    def test_browser_form_templates_use_full_url_and_avoid_unverified_claims(self):
        for text in (
            pt.generate_safebrowsing_report_text("phish.example.test", self.cfg, self.url),
            pt.generate_cloudflare_report_text("phish.example.test", self.cfg, self.url),
        ):
            self.assertIn(self.url, text)
            self.assertIn("Example Brand", text)
            self.assertNotIn("URLScan", text)
            self.assertNotIn("harvest OTP", text)
            self.assertNotIn("collect payment", text)

    def test_gsb_and_cloudflare_keep_separate_random_variant_pools(self):
        pools = []

        def capture_pool(_rng, options):
            pools.append(options)
            return options[0]

        with patch.object(pt, "_pick", side_effect=capture_pool):
            pt.generate_safebrowsing_report_text("phish.example.test", self.cfg, self.url)
            pt.generate_cloudflare_report_text("phish.example.test", self.cfg, self.url)

        gsb_pool, cloudflare_pool = pools
        self.assertGreaterEqual(len(gsb_pool), 5)
        self.assertGreaterEqual(len(cloudflare_pool), 5)
        self.assertTrue(all("Safe Browsing" in text for text in gsb_pool))
        self.assertTrue(all("Cloudflare" in text for text in cloudflare_pool))
        self.assertTrue(all("Safe Browsing" not in text for text in cloudflare_pool))
        self.assertTrue(all(self.url in text for text in gsb_pool + cloudflare_pool))
        self.assertTrue(all(
            "credentials" in text or "sensitive" in text or "personal" in text
            for text in gsb_pool + cloudflare_pool
        ))

    def test_variant_selection_is_stable_for_same_domain_and_day(self):
        first = pt.generate_safebrowsing_report_text(
            "phish.example.test", self.cfg, self.url,
        )
        second = pt.generate_safebrowsing_report_text(
            "phish.example.test", self.cfg, self.url,
        )
        self.assertEqual(first, second)

    def test_registrar_generated_webform_uses_shared_factual_text(self):
        who = {"registrar": "NameSilo, LLC", "emails": []}
        vt = {"link": "https://www.virustotal.com/example", "malicious": 0}
        with tempfile.TemporaryDirectory() as tmp, patch.object(pt, "REPORTS_DIR", tmp):
            paths = pt.generate_email_drafts(
                "phish.example.test", {}, who, vt, self.cfg,
                target_url=self.url,
            )
            text = Path(paths[0]).read_text(encoding="utf-8")
        self.assertIn(self.url, text)
        self.assertNotIn("URLScan", text)
        self.assertNotIn("VirusTotal", text)
        self.assertNotIn("OTP", text)
        self.assertNotIn("payment", text.lower())
        self.assertNotIn("serverHold", text)

    def test_registrar_email_is_factual_and_omits_unflagged_vt(self):
        who = {"registrar": "Example Registrar", "emails": ["abuse@example.test"]}
        vt = {"link": "https://www.virustotal.com/example", "malicious": 0, "suspicious": 0}
        with tempfile.TemporaryDirectory() as tmp, patch.object(pt, "REPORTS_DIR", tmp), \
                patch.object(pt, "get_rdap_abuse_email", return_value={}):
            paths = pt.generate_email_drafts(
                "phish.example.test", {}, who, vt, self.cfg,
                target_url=self.url,
            )
            registrar_path = next(path for path in paths if path.endswith("_registrar_report.txt"))
            text = Path(registrar_path).read_text(encoding="utf-8")
        self.assertIn(self.url, text)
        self.assertNotIn("VirusTotal", text)
        self.assertNotIn("OTP", text)
        self.assertNotIn("payment", text.lower())
        self.assertNotIn("serverHold", text)
        self.assertNotIn("clientHold", text)
        self.assertNotIn("[NOTE:", text)

    def test_registry_webform_uses_full_url_and_no_scan_links(self):
        registry = {
            "source": "static_table", "registry": "Example Registry",
            "report_webform": "https://registry.example/report", "abuse_email": "",
        }
        text = pt.get_registry_webform_draft_text(
            "phish.example.test", registry, self.cfg, target_url=self.url,
        )
        self.assertIn(self.url, text)
        self.assertIn("Example Brand", text)
        self.assertNotIn("URLScan", text)
        self.assertNotIn("VirusTotal", text)
        self.assertNotIn("OTP", text)
        self.assertNotIn("payment", text.lower())

    def test_registry_email_does_not_invent_registrar_escalation(self):
        registry = {
            "source": "static_table", "registry": "Example Registry",
            "abuse_email": "abuse@registry.example", "report_webform": "",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(pt, "REPORTS_DIR", tmp):
            path = pt.generate_registry_draft(
                "phish.example.test", registry, self.cfg, target_url=self.url,
            )
            text = Path(path).read_text(encoding="utf-8")
        self.assertIn(self.url, text)
        self.assertNotIn("already submitted", text)
        self.assertNotIn("previously reported", text)
        self.assertNotIn("ClientHold", text)
        self.assertNotIn("ICANN compliance", text)
        self.assertNotIn("WHOIS raw", text)

    def test_registry_email_mentions_prior_report_only_when_confirmed(self):
        registry = {
            "source": "static_table", "registry": "Example Registry",
            "abuse_email": "abuse@registry.example", "report_webform": "",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(pt, "REPORTS_DIR", tmp):
            path = pt.generate_registry_draft(
                "phish.example.test", registry, self.cfg, target_url=self.url,
                registrar_reported=True, registrar_report_date="2026-09-01",
            )
            text = Path(path).read_text(encoding="utf-8")
        self.assertIn("previously reported", text)
        self.assertIn("2026-09-01", text)


if __name__ == "__main__":
    unittest.main()

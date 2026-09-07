import unittest
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


if __name__ == "__main__":
    unittest.main()

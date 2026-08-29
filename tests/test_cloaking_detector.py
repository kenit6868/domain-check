import json
import os
import tempfile
import unittest
from unittest.mock import patch

import cloaking_detector as cd


def profile(
    name,
    *,
    text="Welcome to our company",
    size=1000,
    title="Company",
    final_url="https://example.test/",
    keywords=None,
    fake_404=False,
    password_inputs=0,
    iocs=None,
    mirror=False,
    error="",
):
    return {
        "name": name,
        "label": name.replace("_", " "),
        "requested_url": "https://example.test/",
        "referrer": "",
        "status_code": None if error else 200,
        "final_url": "" if error else final_url,
        "redirect_chain": [],
        "headers": {},
        "body_bytes": 0 if error else size,
        "body_sha256": "a" * 64 if not error else "",
        "truncated": False,
        "error": error,
        "duration_ms": 5,
        "title": title,
        "visible_text": text,
        "text_preview": text[:500],
        "keyword_hits": keywords or [],
        "fake_404": fake_404,
        "forms": 0,
        "password_inputs": password_inputs,
        "meta_refresh": "",
        "scripts": [],
        "iframes": [],
        "mirror_document_header": mirror,
        "known_iocs": iocs or [],
    }


class CloakingDetectorTests(unittest.TestCase):
    def base_profiles(self):
        return {
            name: profile(name)
            for name in ("desktop_direct", "mobile_direct", "desktop_google", "mobile_google")
        }

    def test_identical_profiles_have_no_signal(self):
        result = cd.analyze_profiles(self.base_profiles(), "https://example.test/")
        self.assertEqual(result["verdict"], "NO_SIGNAL")
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["manual_review_required"])

    def test_mobile_sensitive_content_vs_desktop_fake_404_is_likely(self):
        profiles = self.base_profiles()
        profiles["desktop_direct"] = profile(
            "desktop_direct", text="404 not found", size=20_000, title="Not found", fake_404=True,
        )
        profiles["mobile_direct"] = profile(
            "mobile_direct", text="casino tài xỉu nạp tiền khuyến mãi", size=72_000,
            title="Casino", keywords=["casino", "tài xỉu", "nạp tiền"],
        )
        result = cd.analyze_profiles(profiles, "https://example.test/")
        self.assertEqual(result["verdict"], "LIKELY")
        kinds = {signal["kind"] for signal in result["signals"]}
        self.assertIn("keyword_exposure", kinds)
        self.assertIn("fake_404_vs_sensitive", kinds)

    def test_google_referrer_only_content_is_detected(self):
        profiles = self.base_profiles()
        profiles["desktop_google"] = profile(
            "desktop_google", text="casino baccarat nạp tiền", size=60_000,
            title="Casino", keywords=["casino", "baccarat", "nạp tiền"],
        )
        result = cd.analyze_profiles(profiles, "https://example.test/")
        self.assertIn(result["verdict"], {"POSSIBLE", "LIKELY"})
        self.assertIn("keyword_exposure", {item["kind"] for item in result["signals"]})

    def test_vi_vn_path_difference_is_compared(self):
        profiles = self.base_profiles()
        profiles["desktop_direct_vi_vn"] = profile(
            "desktop_direct_vi_vn", text="casino tài xỉu nạp tiền", size=80_000,
            title="Casino", final_url="https://example.test/vi-vn/",
            keywords=["casino", "tài xỉu", "nạp tiền"],
        )
        result = cd.analyze_profiles(profiles, "https://example.test/")
        compared = [item["profiles"] for item in result["comparisons"]]
        self.assertIn(["desktop_direct", "desktop_direct_vi_vn"], compared)
        self.assertIn("keyword_exposure", {item["kind"] for item in result["signals"]})

    def test_known_asset_is_a_strong_likely_signal(self):
        profiles = self.base_profiles()
        profiles["mobile_google"] = profile(
            "mobile_google", iocs=["best-traffic.pages.dev/traffic_dr.js"],
        )
        result = cd.analyze_profiles(profiles, "https://example.test/")
        self.assertEqual(result["verdict"], "LIKELY")
        self.assertGreaterEqual(result["score"], 70)

    def test_mirror_document_header_is_a_strong_likely_signal(self):
        profiles = self.base_profiles()
        profiles["mobile_direct"] = profile("mobile_direct", mirror=True)
        result = cd.analyze_profiles(profiles, "https://example.test/")
        self.assertEqual(result["verdict"], "LIKELY")

    def test_failures_without_evidence_are_inconclusive(self):
        profiles = self.base_profiles()
        profiles["desktop_direct"] = profile("desktop_direct", error="timeout")
        profiles["mobile_direct"] = profile("mobile_direct", error="timeout")
        profiles["desktop_google"] = profile("desktop_google", error="timeout")
        result = cd.analyze_profiles(profiles, "https://example.test/")
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertTrue(result["manual_review_required"])

    def test_small_dynamic_difference_does_not_flag(self):
        profiles = self.base_profiles()
        profiles["mobile_direct"] = profile(
            "mobile_direct", text="Welcome to our company 2026-08-29T10:00:01", size=1010,
        )
        profiles["desktop_direct"] = profile(
            "desktop_direct", text="Welcome to our company 2026-08-29T10:00:00", size=1000,
        )
        result = cd.analyze_profiles(profiles, "https://example.test/")
        self.assertEqual(result["verdict"], "NO_SIGNAL")

    def test_html_parser_extracts_ioc_inputs_and_visible_text(self):
        html = """
        <html><head><title>Casino</title>
        <script src="https://best-traffic.pages.dev/traffic_dr.js"></script></head>
        <body><form><input type="password">Tài xỉu nạp tiền</form></body></html>
        """
        parsed = cd._parse_html(html, "https://example.test/")
        self.assertEqual(parsed["title"], "Casino")
        self.assertEqual(parsed["password_inputs"], 1)
        self.assertIn("tài xỉu", parsed["keyword_hits"])
        self.assertIn("https://best-traffic.pages.dev/traffic_dr.js", parsed["scripts"])

    def test_manifest_removes_full_visible_text(self):
        result = cd.analyze_profiles(self.base_profiles(), "https://example.test/")
        with tempfile.TemporaryDirectory() as directory:
            path = cd.save_evidence_manifest(result, directory)
            with open(path, encoding="utf-8") as file:
                saved = json.load(file)
        self.assertNotIn("visible_text", saved["profiles"]["desktop_direct"])
        self.assertIn("text_preview", saved["profiles"]["desktop_direct"])

    @patch.object(cd, "_fetch_profile")
    def test_probe_preserves_profile_order_and_saves_likely_evidence(self, fetch):
        def fake_fetch(spec, _timeout, _max_bytes):
            if spec["name"] == "mobile_google":
                return profile(
                    spec["name"], text="casino tài xỉu nạp tiền", size=60_000,
                    title="Casino", keywords=["casino", "tài xỉu", "nạp tiền"],
                )
            return profile(spec["name"])

        fetch.side_effect = fake_fetch
        with tempfile.TemporaryDirectory() as directory:
            result = cd.probe_http_cloaking(
                "example.test", include_path_variant=False, evidence_root=directory,
            )
            self.assertTrue(os.path.isfile(result["evidence_path"]))
        self.assertEqual(
            list(result["profiles"]),
            ["desktop_direct", "mobile_direct", "desktop_google", "mobile_google"],
        )

    def test_evidence_block_is_factual_and_omits_no_signal(self):
        no_signal = cd.analyze_profiles(self.base_profiles(), "https://example.test/")
        self.assertEqual(cd.format_evidence_block(no_signal), "")
        likely_profiles = self.base_profiles()
        likely_profiles["mobile_google"] = profile("mobile_google", mirror=True)
        likely = cd.analyze_profiles(likely_profiles, "https://example.test/")
        block = cd.format_evidence_block(likely)
        self.assertIn("Multi-profile Cloaking Check", block)
        self.assertIn("response declared a device-specific mirror-document route", block)
        self.assertNotIn("tự khai báo", block)

    def test_provider_evidence_uses_english_not_localized_ui_details(self):
        profiles = self.base_profiles()
        profiles["desktop_direct"] = profile(
            "desktop_direct", text="404 not found", fake_404=True, title="Not found",
        )
        profiles["mobile_direct"] = profile(
            "mobile_direct", text="casino betting", size=5000, title="Casino",
            keywords=["casino", "betting"], password_inputs=1,
        )
        result = cd.analyze_profiles(profiles, "https://example.test/")
        block = cd.format_evidence_block(result)
        self.assertIn("Desktop, direct visit", block)
        self.assertIn("Mobile, direct visit", block)
        self.assertIn("Sensitive terms were exposed", block)
        for localized_phrase in (
            "trực tiếp", "Từ khóa", "Một profile", "URL cuối", "chỉ xuất hiện",
            "khác nhau", "Kích thước", "Phát hiện asset", "tự khai báo",
        ):
            self.assertNotIn(localized_phrase, block)


    def test_passive_playwright_profiles_capture_screenshots_without_interaction(self):
        class FakeResponse:
            status = 200

        class FakeLocator:
            def __init__(self, text):
                self.text = text

            def inner_text(self, timeout):
                return self.text

        class FakePage:
            def __init__(self, mobile):
                self.mobile = mobile
                self.url = "https://example.test/mobile" if mobile else "https://example.test/"
                self.goto_args = None

            def goto(self, url, **kwargs):
                self.goto_args = (url, kwargs)
                return FakeResponse()

            def wait_for_timeout(self, _milliseconds):
                return None

            def content(self):
                if self.mobile:
                    return "<html><title>Casino</title><body><form><input type='password'>casino betting</form></body></html>"
                return "<html><title>Not found</title><body>404 not found</body></html>"

            def locator(self, _selector):
                return FakeLocator("casino betting" if self.mobile else "404 not found")

            def evaluate(self, _script):
                return []

            def screenshot(self, path, full_page):
                self.assert_full_page = full_page
                with open(path, "wb") as screenshot_file:
                    screenshot_file.write(b"fake-png")

        class FakeContext:
            def __init__(self, mobile):
                self.page = FakePage(mobile)

            def new_page(self):
                return self.page

            def close(self):
                return None

        class FakeBrowser:
            def __init__(self):
                self.contexts = []

            def new_context(self, **kwargs):
                context = FakeContext(kwargs["is_mobile"])
                self.contexts.append(context)
                return context

            def close(self):
                return None

        class FakeChromium:
            def __init__(self, browser):
                self.browser = browser

            def launch(self, headless):
                self.headless = headless
                return self.browser

        class FakePlaywright:
            def __init__(self, browser):
                self.chromium = FakeChromium(browser)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        browser = FakeBrowser()
        with tempfile.TemporaryDirectory() as directory:
            result = cd.probe_playwright_cloaking(
                "https://example.test/", evidence_root=directory,
                _playwright_factory=lambda: FakePlaywright(browser),
            )
            self.assertEqual(result["verdict"], "LIKELY")
            self.assertEqual(len(result["screenshots"]), 2)
            self.assertTrue(all(os.path.isfile(item["path"]) for item in result["screenshots"]))
            self.assertTrue(os.path.isfile(result["evidence_path"]))
        self.assertIsNone(browser.contexts[0].page.goto_args[1]["referer"])
        self.assertEqual(browser.contexts[1].page.goto_args[1]["referer"], cd.GOOGLE_REFERRER)

    def test_playwright_likely_upgrades_http_possible(self):
        http_result = cd.analyze_profiles(self.base_profiles(), "https://example.test/")
        http_result.update({"verdict": "POSSIBLE", "score": 25, "manual_review_required": True})
        browser_profiles = {
            "desktop_direct": profile("desktop_direct", text="404 not found", fake_404=True),
            "mobile_google": profile(
                "mobile_google", text="casino betting", size=5000,
                keywords=["casino", "betting"], password_inputs=1,
            ),
        }
        browser_result = cd.analyze_profiles(browser_profiles, "https://example.test/")
        browser_result.update({"engine": "playwright", "available": True, "screenshots": []})
        merged = cd.merge_playwright_result(http_result, browser_result)
        self.assertEqual(browser_result["verdict"], "LIKELY")
        self.assertEqual(merged["verdict"], "LIKELY")
        self.assertFalse(merged["manual_review_required"])


if __name__ == "__main__":
    unittest.main()

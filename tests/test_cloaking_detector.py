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

    def test_vi_vn_path_difference_is_discovery_not_cloaking(self):
        profiles = self.base_profiles()
        profiles["desktop_direct_vi_vn"] = profile(
            "desktop_direct_vi_vn", text="casino tài xỉu nạp tiền", size=80_000,
            title="Casino", final_url="https://example.test/vi-vn/",
            keywords=["casino", "tài xỉu", "nạp tiền"],
        )
        result = cd.analyze_profiles(profiles, "https://example.test/")
        compared = [item["profiles"] for item in result["comparisons"]]
        self.assertNotIn(["desktop_direct", "desktop_direct_vi_vn"], compared)
        self.assertEqual(result["verdict"], "NO_SIGNAL")
        self.assertEqual(result["path_probes"][0]["status"], "SENSITIVE_CONTENT")
        self.assertFalse(result["path_probes"][0]["contributes_to_cloaking"])

    def test_missing_vi_vn_path_does_not_make_exposed_gambling_cloaking(self):
        profiles = {
            name: profile(
                name, text="casino betting nạp tiền rút tiền", size=81_640,
                title="Casino", keywords=["casino", "betting", "nạp tiền", "rút tiền"],
            )
            for name in ("desktop_direct", "mobile_direct", "desktop_google", "mobile_google")
        }
        profiles["desktop_direct_vi_vn"] = profile(
            "desktop_direct_vi_vn", text="404 not found casino", size=43_875,
            title="Page Not Found", keywords=["casino"], fake_404=True,
            final_url="https://example.test/vi-vn/",
        )
        profiles["desktop_direct_vi_vn"]["status_code"] = 404
        profiles["mobile_google_vi_vn"] = dict(
            profiles["desktop_direct_vi_vn"], name="mobile_google_vi_vn",
            label="mobile google vi vn",
        )
        result = cd.analyze_profiles(profiles, "https://example.test/")
        self.assertEqual(result["verdict"], "NO_SIGNAL")
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["content"]["verdict"], "GAMBLING_EXPOSED")
        self.assertTrue(all(item["status"] == "NOT_FOUND" for item in result["path_probes"]))

    def test_failed_path_variant_does_not_make_base_profiles_inconclusive(self):
        profiles = self.base_profiles()
        profiles["desktop_direct_vi_vn"] = profile("desktop_direct_vi_vn", error="timeout")
        profiles["mobile_google_vi_vn"] = profile("mobile_google_vi_vn", error="timeout")
        result = cd.analyze_profiles(profiles, "https://example.test/")
        self.assertEqual(result["verdict"], "NO_SIGNAL")
        self.assertEqual(result["profiles_failed"], 0)

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

    def test_http_profiles_include_client_hints_iphone_and_googlebot(self):
        specs = {item["name"]: item for item in cd._profile_specs("https://example.test/", False)}
        self.assertEqual(specs["mobile_google"]["client_hints"]["Sec-CH-UA-Mobile"], "?1")
        self.assertIn("iphone_google", specs)
        self.assertIn("Googlebot", specs["googlebot_smartphone"]["user_agent"])

    @patch.object(cd.requests, "Session")
    def test_mirror_route_in_rewrite_header_is_detected(self, session_class):
        class FakeResponse:
            status_code = 200
            url = "https://example.test/"
            encoding = "utf-8"
            history = []
            headers = {"x-middleware-rewrite": "/mirror-document/mobile"}

            def iter_content(self, chunk_size):
                return iter([b"<html><body>ok</body></html>"])

            def close(self):
                return None

        session = session_class.return_value
        session.get.return_value = FakeResponse()
        fetched = cd._fetch_profile(
            cd._profile_specs("https://example.test/", False)[1], 5, 100_000,
        )
        self.assertTrue(fetched["mirror_document_header"])
        self.assertEqual(fetched["mirror_document_routes"], ["/mirror-document/mobile"])

    def test_geo_vary_header_recommends_multi_vantage_without_false_cloaking(self):
        profiles = self.base_profiles()
        for item in profiles.values():
            item["headers"] = {
                "Vary": "Accept-Encoding, CF-IPCountry, User-Agent, Sec-CH-UA-Mobile",
            }
        result = cd.analyze_profiles(profiles, "https://example.test/")
        self.assertEqual(result["verdict"], "NO_SIGNAL")
        self.assertTrue(result["coverage"]["geo_dependent_declared"])
        self.assertTrue(result["coverage"]["multi_vantage_recommended"])

    @patch.object(cd, "_fetch_profile")
    def test_remote_vantage_can_reveal_profile_dependent_content(self, fetch):
        def fake_fetch(spec, _timeout, _max_bytes):
            if spec["name"].endswith("mobile_google") and spec["name"].startswith("vantage_"):
                item = profile(
                    spec["name"], text="casino betting nạp tiền", size=70_000,
                    title="Casino", keywords=["casino", "betting", "nạp tiền"],
                )
            else:
                item = profile(spec["name"], text="Đổi Làn game", size=10_000, title="Đổi Làn")
            item.update({
                "label": spec["label"], "vantage": spec.get("vantage", "local"),
                "vantage_country": spec.get("vantage_country", ""),
            })
            return item

        fetch.side_effect = fake_fetch
        result = cd.probe_http_cloaking(
            "https://example.test/vn/", include_path_variant=False,
            vantage_points=[{
                "name": "vn-mobile", "country": "VN",
                "proxy": "socks5://secret-user:secret-pass@proxy.example:1080",
            }],
        )
        self.assertEqual(result["verdict"], "LIKELY")
        self.assertEqual(result["coverage"]["vantages_attempted"], ["vn-mobile"])
        self.assertNotIn("secret-pass", json.dumps(result))

    def test_playwright_proxy_settings_separate_credentials(self):
        settings = cd._playwright_proxy_settings(
            "socks5://user%40example.com:p%40ss@proxy.example:1080",
        )
        self.assertEqual(settings["server"], "socks5://proxy.example:1080")
        self.assertEqual(settings["username"], "user@example.com")
        self.assertEqual(settings["password"], "p@ss")

    def test_playwright_proxy_error_does_not_expose_credentials(self):
        class BrokenBrowser:
            def new_context(self, **_kwargs):
                raise RuntimeError("proxy password secret-pass rejected")

        with tempfile.TemporaryDirectory() as directory:
            result = cd._capture_browser_profile(
                BrokenBrowser(),
                {
                    "name": "remote", "label": "Remote",
                    "url": "https://example.test/", "user_agent": cd.MOBILE_UA,
                    "referrer": "", "viewport": {"width": 412, "height": 915},
                    "is_mobile": True,
                    "proxy_settings": {
                        "server": "http://proxy.example:8080",
                        "username": "user", "password": "secret-pass",
                    },
                },
                directory,
                1000,
            )
        self.assertIn("browser proxy request failed", result["error"])
        self.assertNotIn("secret-pass", result["error"])

    def test_operator_pair_marks_possible_and_saves_evidence(self):
        result = cd.analyze_profiles(self.base_profiles(), "https://example.test/")
        png = b"\x89PNG\r\n\x1a\noperator-evidence"
        with tempfile.TemporaryDirectory() as directory:
            updated = cd.add_operator_evidence(
                result, images=[("desktop.png", png), ("mobile.png", png)],
                evidence_root=directory,
                acquisition_url="https://example.test/?source=google",
                device="Android phone", network="Vietnam mobile network",
                confirmed_difference=True,
            )
            self.assertEqual(updated["verdict"], "POSSIBLE")
            self.assertTrue(updated["manual_review_required"])
            self.assertTrue(os.path.isfile(updated["evidence_path"]))
            self.assertEqual(len(updated["operator_evidence"]["screenshots"]), 2)
            block = cd.format_evidence_block(updated)
            self.assertIn("Operator-supplied verification", block)
            self.assertIn("paired screenshots showing different content", block)

    def test_operator_evidence_rejects_fake_image(self):
        result = cd.analyze_profiles(self.base_profiles(), "https://example.test/")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                cd.add_operator_evidence(
                    result, images=[("fake.png", b"not-an-image")],
                    evidence_root=directory,
                )

    def test_failures_without_evidence_are_inconclusive(self):
        profiles = self.base_profiles()
        profiles["desktop_direct"] = profile("desktop_direct", error="timeout")
        profiles["mobile_direct"] = profile("mobile_direct", error="timeout")
        profiles["desktop_google"] = profile("desktop_google", error="timeout")
        result = cd.analyze_profiles(profiles, "https://example.test/")
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertTrue(result["manual_review_required"])

    def test_browser_error_pages_are_not_cloaking(self):
        profiles = {
            name: profile(
                name,
                title="Không thể truy cập trang web này",
                text="Không thể truy cập trang web này DNS_PROBE_FINISHED_NXDOMAIN",
                size=4200,
            )
            for name in self.base_profiles()
        }
        result = cd.analyze_profiles(profiles, "https://example.test/")
        self.assertEqual(result["verdict"], "NO_SIGNAL")
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["manual_review_required"])
        self.assertTrue(result["site_state"]["all_profiles_terminal"])
        self.assertEqual(result["site_state"]["verdict"], "BLOCKED_OR_UNAVAILABLE")

    def test_cloudflare_phishing_warning_is_not_cloaking(self):
        profiles = self.base_profiles()
        for item in profiles.values():
            item.update({
                "title": "Suspected phishing site | Cloudflare",
                "visible_text": "This website has been reported for potential phishing.",
                "text_preview": "This website has been reported for potential phishing.",
                "headers": {"Server": "cloudflare", "CF-Ray": "test"},
            })
        result = cd.analyze_profiles(profiles, "https://example.test/")
        self.assertEqual(result["verdict"], "NO_SIGNAL")
        self.assertTrue(result["site_state"]["all_profiles_terminal"])
        self.assertEqual(
            set(result["site_state"]["terminal_profiles"].values()),
            {"CLOUDFLARE_PHISHING_BLOCK"},
        )

    def test_terminal_playwright_result_clears_stale_http_suspicion(self):
        http_result = {
            "verdict": "POSSIBLE", "score": 35,
            "signals": [{"kind": "content_difference", "weight": 25}],
            "manual_review_required": True,
            "coverage": {"multi_vantage_recommended": True},
        }
        browser_result = {
            "verdict": "NO_SIGNAL", "score": 0, "signals": [],
            "site_state": {
                "verdict": "BLOCKED_OR_UNAVAILABLE",
                "all_profiles_terminal": True,
                "terminal_profiles": {"desktop_direct": "UNREACHABLE_ERROR_PAGE"},
            },
            "screenshots": [{"path": "error.png"}],
        }
        merged = cd.merge_playwright_result(http_result, browser_result)
        self.assertEqual(merged["verdict"], "NO_SIGNAL")
        self.assertEqual(merged["score"], 0)
        self.assertEqual(merged["signals"], [])
        self.assertFalse(merged["manual_review_required"])
        self.assertFalse(merged["coverage"]["multi_vantage_recommended"])

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
            [
                "desktop_direct", "mobile_direct", "desktop_google", "mobile_google",
                "iphone_google", "googlebot_smartphone",
            ],
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

            def reload(self, **_kwargs):
                return FakeResponse()

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
            self.assertEqual(len(result["screenshots"]), 9)
            self.assertTrue(all(os.path.isfile(item["path"]) for item in result["screenshots"]))
            self.assertTrue(os.path.isfile(result["evidence_path"]))
        self.assertIsNone(browser.contexts[0].page.goto_args[1]["referer"])
        self.assertEqual(browser.contexts[1].page.goto_args[1]["referer"], cd.GOOGLE_REFERRER)
        self.assertEqual(browser.contexts[2].page.goto_args[1]["referer"], cd.GOOGLE_REFERRER)

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

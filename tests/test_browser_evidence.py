import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import browser_evidence as be


class _FakePage:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.goto_calls = []
        self.evaluate_args = None

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def wait_for_timeout(self, *args, **kwargs):
        return None

    def evaluate(self, script, args):
        self.evaluate_args = args
        return dict(self.snapshot)

    def screenshot(self, path, **kwargs):
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\nphase-one-image")


class _FakeContext:
    def __init__(self, page):
        self.page = page
        self.closed = False

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, context):
        self.context = context
        self.closed = False

    def new_context(self, **kwargs):
        self.context.options = kwargs
        return self.context

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.launch_options = None

    def launch(self, **kwargs):
        self.launch_options = kwargs
        return self.browser


class _FakePlaywrightContext:
    def __init__(self, chromium):
        self.chromium = chromium

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _fake_playwright(snapshot):
    page = _FakePage(snapshot)
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    chromium = _FakeChromium(browser)
    return page, context, browser, chromium, lambda: _FakePlaywrightContext(chromium)


class BrowserEvidenceTests(unittest.TestCase):
    def test_redacts_credentials_and_secret_query_values(self):
        value = be.redact_url(
            "https://user:pass@example.test/path?token=abc&view=mobile#proof"
        )
        self.assertNotIn("user", value)
        self.assertNotIn("pass", value)
        self.assertNotIn("abc", value)
        self.assertIn("token=%5BREDACTED%5D", value)
        self.assertIn("view=mobile", value)

    def test_http_probe_preserves_complete_server_redirect_chain(self):
        first = Mock(status_code=301, url="https://a.test/start", headers={"Location": "/next"})
        second = Mock(status_code=302, url="https://a.test/next", headers={"Location": "https://b.test/end"})
        response = Mock(status_code=200, url="https://b.test/end", history=[first, second])
        with patch.object(be.requests, "get", return_value=response) as request:
            result = be.probe_http_redirect_chain("https://a.test/start")
        self.assertTrue(result["success"])
        self.assertEqual([301, 302], [hop["status"] for hop in result["redirect_chain"]])
        self.assertEqual("https://b.test/end", result["final_url"])
        self.assertTrue(request.call_args.kwargs["stream"])
        self.assertTrue(request.call_args.kwargs["allow_redirects"])
        response.close.assert_called_once_with()

    def test_capture_writes_png_and_manifest_without_claiming_verified_navigation(self):
        snapshot = {
            "title": "Example game", "visibleText": "Welcome",
            "landingUrl": "https://landing.test/vi-vn/",
            "controlFound": True, "controlLabel": "Đăng ký",
            "rawHref": "/register", "resolvedHref": "https://target.test/register",
            "domElement": '<a href="/register">Đăng ký</a>',
        }
        page, context, browser, chromium, factory = _fake_playwright(snapshot)
        http = {
            "success": True, "requested_url": "https://source.test/vi-vn/",
            "final_url": "https://landing.test/vi-vn/", "final_status": 200,
            "redirect_chain": [], "error": "",
        }
        with tempfile.TemporaryDirectory() as folder, patch(
            "playwright.sync_api.sync_playwright", factory,
        ):
            result = be.capture_passive_browser_evidence(
                "https://source.test/vi-vn/", folder,
                profile_name="mobile_google", http_probe=lambda *_a, **_kw: http,
            )
            self.assertTrue(result["success"], result.get("error"))
            self.assertFalse(result["navigation_verified"])
            self.assertEqual("dom_observed", result["evidence_type"])
            self.assertTrue(os.path.isfile(result["screenshot_path"]))
            self.assertTrue(os.path.isfile(result["manifest_path"]))
            validation = be.validate_evidence_artifacts(result)
            self.assertTrue(validation["valid"], validation["errors"])
            manifest = validation["manifest"]
            self.assertEqual("https://source.test/vi-vn/", manifest["requested_url"])
            self.assertEqual("https://landing.test/vi-vn/", manifest["landing_url"])
            self.assertEqual("https://target.test/register", manifest["control"]["resolved_destination"])
            self.assertFalse(manifest["navigation_verified"])
            self.assertIn("requestedUrl", page.evaluate_args)
            self.assertIn("httpRedirectChain", page.evaluate_args)
            self.assertTrue(chromium.launch_options["headless"])
            self.assertFalse(browser.context.options["accept_downloads"])
            self.assertEqual("block", browser.context.options["service_workers"])
            self.assertTrue(context.closed)
            self.assertTrue(browser.closed)

    def test_redirect_chain_text_is_explicitly_server_side(self):
        text = be.format_http_redirect_chain({
            "success": True, "final_url": "https://b.test/end", "final_status": 200,
            "redirect_chain": [{
                "status": 302, "url": "https://a.test/start",
                "location": "https://b.test/end",
            }],
        })
        self.assertIn("a.test/start --HTTP 302-->", text)
        self.assertIn("b.test/end --HTTP 200-->", text)

    def test_terminal_page_is_rejected_without_writing_evidence(self):
        snapshot = {
            "title": "This site can’t be reached", "visibleText": "DNS_PROBE_FINISHED_NXDOMAIN",
            "landingUrl": "chrome-error://chromewebdata/", "controlFound": False,
            "controlLabel": "", "rawHref": "", "resolvedHref": "", "domElement": "",
        }
        _page, _context, _browser, _chromium, factory = _fake_playwright(snapshot)
        with tempfile.TemporaryDirectory() as folder, patch(
            "playwright.sync_api.sync_playwright", factory,
        ):
            result = be.capture_passive_browser_evidence(
                "https://dead.test/", folder,
                http_probe=lambda *_a, **_kw: {"success": False, "redirect_chain": []},
            )
            self.assertFalse(result["success"])
            self.assertTrue(result["terminal"])
            self.assertEqual([], os.listdir(folder))

    def test_no_matching_control_still_records_page_level_evidence(self):
        snapshot = {
            "title": "Public page", "visibleText": "Ordinary content",
            "landingUrl": "https://plain.test/", "controlFound": False,
            "controlLabel": "", "rawHref": "", "resolvedHref": "", "domElement": "",
        }
        _page, _context, _browser, _chromium, factory = _fake_playwright(snapshot)
        with tempfile.TemporaryDirectory() as folder, patch(
            "playwright.sync_api.sync_playwright", factory,
        ):
            result = be.capture_passive_browser_evidence(
                "https://plain.test/", folder,
                http_probe=lambda *_a, **_kw: {"success": True, "redirect_chain": []},
            )
            self.assertTrue(result["success"])
            self.assertFalse(result["control_found"])
            with open(result["manifest_path"], encoding="utf-8") as handle:
                self.assertFalse(json.load(handle)["control"]["found"])

    def test_profile_path_and_credentials_are_sanitized(self):
        self.assertEqual(".._.._secret", be._safe_component("../../secret", "fallback"))
        redacted = be.redact_text(
            "Proxy socks5://alice:hunter2@proxy.test:1080 failed; token=secret-value"
        )
        self.assertNotIn("alice", redacted)
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("secret-value", redacted)

    def test_modified_screenshot_fails_manifest_hash_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            image_path = os.path.join(folder, "proof.png")
            manifest_path = os.path.join(folder, "proof.json")
            with open(image_path, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\noriginal")
            manifest = {
                "version": be.EVIDENCE_VERSION,
                "screenshot": {"sha256": be._sha256(image_path)},
            }
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with open(image_path, "ab") as handle:
                handle.write(b"changed")
            result = be.validate_evidence_artifacts({
                "screenshot_path": image_path, "manifest_path": manifest_path,
            })
            self.assertFalse(result["valid"])
            self.assertIn("Hash screenshot không khớp manifest", result["errors"])

    def test_email_block_is_factual_and_does_not_claim_navigation(self):
        with tempfile.TemporaryDirectory() as folder:
            image_path = os.path.join(folder, "proof.png")
            manifest_path = os.path.join(folder, "proof.json")
            with open(image_path, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\nproof")
            manifest = {
                "version": be.EVIDENCE_VERSION, "evidence_type": "dom_observed",
                "navigation_verified": False, "observed_at": "2026-09-08T00:00:00Z",
                "requested_url": "https://source.test/path",
                "landing_url": "https://source.test/path", "profile": {"name": "desktop"},
                "page": {"title": "Public page"},
                "http": {"success": True, "final_url": "https://source.test/path", "final_status": 200, "redirect_chain": []},
                "control": {"found": True, "label": "Register", "resolved_destination": "https://target.test/register"},
                "screenshot": {"path": image_path, "size": os.path.getsize(image_path), "sha256": be._sha256(image_path)},
            }
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            result = {"success": True, "screenshot_path": image_path, "manifest_path": manifest_path}
            block = be.format_email_evidence_block(result)
            self.assertIn("DOM destination observed", block)
            self.assertIn("https://target.test/register", block)
            self.assertIn("no click or form submission", block)
            self.assertNotIn("navigation verified", block.lower())
            self.assertEqual([image_path, manifest_path], be.evidence_attachment_paths(result))

    def test_manual_upload_accepts_one_to_three_images_and_hashes_each(self):
        with tempfile.TemporaryDirectory() as folder:
            result = be.create_manual_browser_evidence(
                "https://source.test/path",
                [
                    ("one.png", b"\x89PNG\r\n\x1a\none"),
                    ("two.jpg", b"\xff\xd8\xfftwo"),
                    ("three.png", b"\x89PNG\r\n\x1a\nthree"),
                ],
                folder,
            )
            validation = be.validate_evidence_artifacts(result)
            self.assertTrue(validation["valid"], validation["errors"])
            self.assertEqual(3, len(validation["manifest"]["screenshots"]))
            self.assertEqual(4, len(be.evidence_attachment_paths(result)))
            self.assertIn("Operator-supplied", be.format_email_evidence_block(result))

    def test_manual_upload_rejects_invalid_batch_before_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                be.create_manual_browser_evidence(
                    "https://source.test/path", [("fake.png", b"not-image")], folder,
                )
            self.assertEqual([], os.listdir(folder))



if __name__ == "__main__":
    unittest.main()

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


class _FakeResponse:
    def __init__(self, status, url, headers=None):
        self.status = status
        self.url = url
        self.headers = headers or {}


class _FakeLocator:
    def __init__(self, page):
        self.page = page

    def nth(self, index):
        self.page.locator_index = index
        return self

    def click(self, **kwargs):
        self.page.clicked = True
        if self.page.popup is not None:
            self.page.emit("popup", self.page.popup)
        else:
            self.page.current_snapshot = self.page.after_snapshot
            self.page.url = self.page.final_url
            for response in self.page.after_responses:
                self.page.emit("response", response)


class _VerifiedFakePage:
    def __init__(self, before_snapshot, after_snapshot, final_url, *, popup=None, after_responses=None):
        self.current_snapshot = dict(before_snapshot)
        self.before_snapshot = dict(before_snapshot)
        self.after_snapshot = dict(after_snapshot)
        self.final_url = final_url
        self.popup = popup
        self.after_responses = list(after_responses or [])
        self.url = before_snapshot.get("landingUrl", "https://source.test/path")
        self.handlers = {}
        self.clicked = False
        self.locator_index = None
        self.goto_calls = []

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        self.url = self.before_snapshot.get("landingUrl", url)

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def wait_for_timeout(self, *args, **kwargs):
        return None

    def evaluate(self, script, args=None):
        if "candidateIndex" in script:
            return dict(self.current_snapshot)
        if "visibleText" in script and "document.body" in script:
            return {
                "title": self.current_snapshot.get("title", ""),
                "visibleText": self.current_snapshot.get("visibleText", ""),
            }
        return None

    def title(self):
        return self.current_snapshot.get("title", "")

    def locator(self, selector):
        return _FakeLocator(self)

    def on(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    def emit(self, event, value):
        for callback in self.handlers.get(event, []):
            callback(value)

    def screenshot(self, path, **kwargs):
        marker = b"after" if self.clicked else b"before"
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\nverified-" + marker)


class _FakeContext:
    def __init__(self, page, destination_page=None):
        self.page = page
        self.destination_page = destination_page
        self.new_page_count = 0
        self.closed = False
        self.handlers = {}

    def new_page(self):
        self.new_page_count += 1
        if self.new_page_count > 1 and self.destination_page is not None:
            return self.destination_page
        return self.page

    def close(self):
        self.closed = True

    def on(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    def emit(self, event, value):
        for callback in self.handlers.get(event, []):
            callback(value)


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


def _fake_verified_playwright(before_snapshot, after_snapshot, final_url, *, popup=False):
    popup_page = _VerifiedFakePage(
        {**after_snapshot, "landingUrl": final_url}, after_snapshot, final_url,
    )
    page = _VerifiedFakePage(
        before_snapshot, after_snapshot, final_url, popup=popup_page,
        after_responses=[_FakeResponse(
            302, "https://source.test/register", {"location": final_url},
        )],
    )
    context = _FakeContext(page, popup_page)
    browser = _FakeBrowser(context)
    chromium = _FakeChromium(browser)
    return page, popup_page, context, browser, chromium, lambda: _FakePlaywrightContext(chromium)


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
        self.assertNotIn("#proof", value)

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

    def test_verified_navigation_captures_source_and_destination_with_manifest(self):
        before = {
            "title": "Source page", "visibleText": "Register now",
            "landingUrl": "https://source.test/path", "controlFound": True,
            "safeNavigation": True, "candidateIndex": 1,
            "controlLabel": "Register", "rawHref": "/register",
            "resolvedHref": "https://target.test/register",
            "tagName": "A", "type": "", "domElement": '<a href="/register">Register</a>',
        }
        after = {
            "title": "Destination page", "visibleText": "Fraud landing",
            "landingUrl": "https://target.test/register", "controlFound": False,
        }
        page, destination_page, context, browser, chromium, factory = _fake_verified_playwright(
            before, after, "https://target.test/register",
        )
        http = {
            "success": True, "requested_url": "https://source.test/path",
            "final_url": "https://source.test/path", "final_status": 200,
            "redirect_chain": [], "error": "",
        }
        with tempfile.TemporaryDirectory() as folder, patch(
            "playwright.sync_api.sync_playwright", factory,
        ):
            result = be.capture_verified_navigation_evidence(
                "https://source.test/path", folder, profile_name="worker_desktop",
                http_probe=lambda *_a, **_kw: http,
            )
            self.assertTrue(result["success"], result.get("error"))
            self.assertFalse(result["navigation_verified"])
            self.assertTrue(result["destination_opened"])
            self.assertEqual("dom_destination_opened", result["evidence_type"])
            self.assertEqual("https://target.test/register", result["final_url"])
            self.assertEqual(2, len(result["screenshot_paths"]))
            self.assertEqual(3, len(be.evidence_attachment_paths(result)))
            validation = be.validate_evidence_artifacts(result)
            self.assertTrue(validation["valid"], validation["errors"])
            manifest = validation["manifest"]
            self.assertFalse(manifest["navigation_verified"])
            self.assertTrue(manifest["destination_opened"])
            self.assertEqual("new_tab_direct", manifest["navigation"]["mode"])
            self.assertEqual("https://target.test/register", manifest["final_url"])
            self.assertEqual("source_before_open", manifest["screenshots"][0]["role"])
            self.assertEqual("destination_after_open", manifest["screenshots"][1]["role"])
            self.assertFalse(page.clicked)
            self.assertEqual("https://target.test/register", destination_page.goto_calls[0][0])
            self.assertEqual("https://source.test/path", destination_page.goto_calls[0][1]["referer"])
            self.assertTrue(chromium.launch_options["headless"])
            self.assertFalse(browser.context.options["accept_downloads"])
            self.assertTrue(context.closed)
            self.assertTrue(browser.closed)
            block = be.format_email_evidence_block(result)
            self.assertIn("Observed Phishing Behavior and Supporting Evidence", block)
            self.assertNotIn("Technical Evidence", block)
            self.assertIn('href="https://target.test/register"', block)
            self.assertIn("Final destination observed: https://target.test/register", block)
            self.assertIn("source page as the referrer", block)

    def test_verified_navigation_captures_popup_destination(self):
        before = {
            "title": "Source page", "visibleText": "Login",
            "landingUrl": "https://source.test/login", "controlFound": True,
            "safeNavigation": True, "candidateIndex": 0,
            "controlLabel": "Login", "rawHref": "https://target.test/login",
            "resolvedHref": "https://target.test/login",
            "tagName": "A", "type": "", "domElement": '<a href="https://target.test/login">Login</a>',
        }
        after = {
            "title": "Popup destination", "visibleText": "Destination",
            "landingUrl": "https://target.test/login", "controlFound": False,
        }
        page, popup, _context, _browser, _chromium, factory = _fake_verified_playwright(
            before, after, "https://target.test/login", popup=True,
        )
        with tempfile.TemporaryDirectory() as folder, patch(
            "playwright.sync_api.sync_playwright", factory,
        ):
            result = be.capture_verified_navigation_evidence(
                "https://source.test/login", folder,
                http_probe=lambda *_a, **_kw: {"success": True, "redirect_chain": []},
            )
        self.assertTrue(result["success"], result.get("error"))
        self.assertEqual("new_tab_direct", result["navigation"]["mode"])
        self.assertEqual("https://target.test/login", result["final_url"])
        self.assertIsNotNone(popup)

    def test_verified_navigation_falls_back_when_control_is_not_safe(self):
        before = {
            "title": "Source page", "visibleText": "Register",
            "landingUrl": "https://source.test/path", "controlFound": True,
            "safeNavigation": False, "candidateIndex": 0,
            "controlLabel": "Register", "rawHref": "javascript:go()",
            "resolvedHref": "javascript:go()", "tagName": "BUTTON", "type": "submit",
            "domElement": '<button type="submit">Register</button>',
        }
        _page, _popup, _context, _browser, _chromium, factory = _fake_verified_playwright(
            before, before, "https://target.test/register",
        )
        with tempfile.TemporaryDirectory() as folder, patch(
            "playwright.sync_api.sync_playwright", factory,
        ):
            result = be.capture_verified_navigation_evidence(
                "https://source.test/path", folder,
                http_probe=lambda *_a, **_kw: {"success": True, "redirect_chain": []},
            )
            self.assertFalse(result["success"])
            self.assertFalse(result["navigation_verified"])
            self.assertIn("data-href an toàn để mở", result["error"])
            self.assertEqual([], os.listdir(folder))

    def test_verified_navigation_rejects_no_url_change_and_terminal_destination(self):
        before = {
            "title": "Source page", "visibleText": "Register",
            "landingUrl": "https://source.test/path", "controlFound": True,
            "safeNavigation": True, "candidateIndex": 0,
            "controlLabel": "Register", "rawHref": "/register",
            "resolvedHref": "https://source.test/path", "tagName": "A", "type": "",
            "domElement": '<a href="/path">Register</a>',
        }
        _page, _popup, _context, _browser, _chromium, factory = _fake_verified_playwright(
            before, before, "https://source.test/path",
        )
        with tempfile.TemporaryDirectory() as folder, patch(
            "playwright.sync_api.sync_playwright", factory,
        ):
            be_result = be.capture_verified_navigation_evidence(
                "https://source.test/path", folder,
                http_probe=lambda *_a, **_kw: {"success": True, "redirect_chain": []},
            )
            self.assertFalse(be_result["success"])
            self.assertIn("khác trang nguồn", be_result["error"])
            self.assertEqual([], os.listdir(folder))

        terminal = {
            **before,
            "landingUrl": "https://target.test/error",
            "rawHref": "https://target.test/error",
            "resolvedHref": "https://target.test/error",
            "title": "This site can’t be reached",
            "visibleText": "DNS_PROBE_FINISHED_NXDOMAIN",
        }
        terminal_source = {
            **before,
            "rawHref": "https://target.test/error",
            "resolvedHref": "https://target.test/error",
        }
        _page, _popup, _context, _browser, _chromium, factory = _fake_verified_playwright(
            terminal_source, terminal, "https://target.test/error",
        )
        with tempfile.TemporaryDirectory() as folder, patch(
            "playwright.sync_api.sync_playwright", factory,
        ):
            be_result = be.capture_verified_navigation_evidence(
                "https://source.test/path", folder,
                http_probe=lambda *_a, **_kw: {"success": True, "redirect_chain": []},
            )
            self.assertFalse(be_result["success"])
            self.assertTrue(be_result["terminal"])
            self.assertEqual([], os.listdir(folder))

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
            "pageSignals": {"passwordInputs": 1, "identityInputs": 2},
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
            block = be.format_email_evidence_block(result)
            self.assertIn("No visible Register/Login control", block)
            self.assertIn("1 visible password field", block)
            self.assertIn("2 visible identity/contact field", block)
            self.assertNotIn("href=", block)

    def test_visible_control_without_destination_has_dedicated_factual_report(self):
        snapshot = {
            "title": "Impersonated login", "visibleText": "ĐĂNG NHẬP",
            "landingUrl": "https://plain.test/login", "controlFound": True,
            "controlLabel": "ĐĂNG NHẬP", "rawHref": "", "resolvedHref": "",
            "domElement": '<button type="button">ĐĂNG NHẬP</button>',
            "pageSignals": {"passwordInputs": 1, "otpInputs": 1},
        }
        _page, _context, _browser, _chromium, factory = _fake_playwright(snapshot)
        with tempfile.TemporaryDirectory() as folder, patch(
            "playwright.sync_api.sync_playwright", factory,
        ):
            result = be.capture_passive_browser_evidence(
                "https://plain.test/login", folder,
                http_probe=lambda *_a, **_kw: {"success": True, "redirect_chain": []},
            )
            block = be.format_email_evidence_block(result)
            evidence_case = be.classify_evidence_case(
                be.validate_evidence_artifacts(result)["manifest"],
            )
        self.assertTrue(result["success"])
        self.assertEqual("control_without_destination", evidence_case)
        self.assertIn('visible "ĐĂNG NHẬP" control', block)
        self.assertIn("does not expose a usable HTTP(S) destination", block)
        self.assertIn("1 visible password field", block)
        self.assertIn("1 visible OTP/verification-code field", block)
        self.assertNotIn('href=""', block)

    def test_profile_path_and_credentials_are_sanitized(self):
        self.assertEqual(".._.._secret", be._safe_component("../../secret", "fallback"))
        redacted = be.redact_text(
            "Proxy socks5://alice:hunter2@proxy.test:1080 failed; token=secret-value"
        )
        self.assertNotIn("alice", redacted)
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("secret-value", redacted)
        sanitized = be._sanitize_http_result({
            "success": True,
            "requested_url": "https://source.test/?token=secret-value",
            "final_url": "https://target.test/?api_key=secret-value",
            "final_status": "200",
            "redirect_chain": [{
                "status": "302", "url": "https://source.test/?auth=secret-value",
                "location": "https://target.test/?password=secret-value",
            }],
            "error": "proxy socks5://alice:hunter2@proxy.test failed",
            "untrusted_extra": "must not persist",
        })
        serialized = json.dumps(sanitized)
        self.assertNotIn("secret-value", serialized)
        self.assertNotIn("alice", serialized)
        self.assertNotIn("untrusted_extra", serialized)
        self.assertEqual(200, sanitized["final_status"])

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
                "page_signals": {
                    "passwordInputs": 1, "otpInputs": 1,
                    "paymentInputs": 1, "identityInputs": 1,
                },
                "http": {"success": True, "final_url": "https://source.test/path", "final_status": 200, "redirect_chain": []},
                "control": {"found": True, "label": "Register", "resolved_destination": "https://target.test/register"},
                "screenshot": {"path": image_path, "size": os.path.getsize(image_path), "sha256": be._sha256(image_path)},
            }
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            result = {"success": True, "screenshot_path": image_path, "manifest_path": manifest_path}
            block = be.format_email_evidence_block(result)
            self.assertIn("Observed Phishing Behavior and Supporting Evidence", block)
            self.assertNotIn("Technical Evidence", block)
            self.assertIn('href="https://target.test/register"', block)
            self.assertIn("did not claim that a click or form submission", block)
            self.assertIn("visible password field", block)
            self.assertIn("visible OTP/verification-code field", block)
            self.assertIn("visible payment-related field", block)
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
            self.assertIn("Operator-supplied browser screenshot", be.format_email_evidence_block(result))

    def test_manual_upload_rejects_invalid_batch_before_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                be.create_manual_browser_evidence(
                    "https://source.test/path", [("fake.png", b"not-image")], folder,
                )
            self.assertEqual([], os.listdir(folder))

    def test_normal_report_capture_prefers_two_image_dom_destination(self):
        dom = {"success": True, "screenshot_paths": ["source.png", "destination.png"]}
        with (
            patch.object(be, "capture_dom_destination_evidence", return_value=dom) as capture_dom,
            patch.object(be, "capture_passive_browser_evidence") as capture_passive,
            patch.object(be, "evidence_attachment_paths", return_value=[
                "source.png", "destination.png", "manifest.json",
            ]),
        ):
            result = be.capture_normal_report_evidence(
                "https://source.test/path", "C:/evidence",
                profile_name="provider_reply", headless=False,
            )
        self.assertEqual("dom_destination", result["capture_strategy"])
        self.assertEqual(2, len(result["screenshot_paths"]))
        self.assertFalse(capture_dom.call_args.kwargs["headless"])
        capture_passive.assert_not_called()

    def test_normal_report_capture_falls_back_to_passive_source(self):
        dom = {"success": False, "terminal": False, "error": "No safe DOM destination"}
        passive = {"success": True, "screenshot_path": "source.png"}

        def attachments(value):
            return ["source.png", "manifest.json"] if value is passive else []

        with (
            patch.object(be, "capture_dom_destination_evidence", return_value=dom),
            patch.object(be, "capture_passive_browser_evidence", return_value=passive) as capture_passive,
            patch.object(be, "evidence_attachment_paths", side_effect=attachments),
        ):
            result = be.capture_normal_report_evidence(
                "https://source.test/path", "C:/evidence",
                profile_name="cloaking_review", headless=True,
            )
        self.assertEqual("passive_fallback", result["capture_strategy"])
        self.assertEqual("No safe DOM destination", result["fallback_reason"])
        self.assertTrue(capture_passive.call_args.kwargs["headless"])



if __name__ == "__main__":
    unittest.main()

import json
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from cloudflare_profile_bridge import ProfileBridge


class CloudflareProfileBridgeTests(unittest.TestCase):
    def test_one_time_task_is_json_safe_and_result_is_sanitized(self):
        bridge = ProfileBridge()
        received = []
        try:
            token = bridge.register({
                "target_url": "https://example.test/login", "draft": "Report",
                "_contact_email": "reporter@example.test", "mode": "fill_only",
            }, received.append)
            with urlopen(f"http://127.0.0.1:{bridge.port}/task/{token}", timeout=2) as response:
                task = json.load(response)
            self.assertEqual(task["target_url"], "https://example.test/login")
            self.assertNotIn("_callback", task)
            body = json.dumps({"state": "FILLED", "result": " done  now "}).encode()
            request = Request(
                f"http://127.0.0.1:{bridge.port}/result/{token}", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urlopen(request, timeout=2):
                pass
            self.assertEqual(received, [{"state": "FILLED", "result": "done now"}])
        finally:
            bridge.server.shutdown()
            bridge.server.server_close()

    def test_extension_is_restricted_to_supported_forms_and_localhost(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "chrome_extension/cloudflare-profile-worker/manifest.json").read_text())
        self.assertEqual(manifest["version"], "2.6.0")
        expected_icons = {
            "16": "icons/icon16.png",
            "32": "icons/icon32.png",
            "48": "icons/icon48.png",
            "128": "icons/icon128.png",
        }
        self.assertEqual(manifest["icons"], expected_icons)
        self.assertEqual(manifest["action"]["default_icon"]["16"], expected_icons["16"])
        self.assertEqual(manifest["action"]["default_popup"], "popup.html")
        self.assertEqual(manifest["permissions"], ["activeTab"])
        for popup_file in ("popup.html", "popup.css", "popup.js"):
            self.assertTrue((root / "chrome_extension/cloudflare-profile-worker" / popup_file).is_file())
        for relative_path in expected_icons.values():
            self.assertTrue((root / "chrome_extension/cloudflare-profile-worker" / relative_path).is_file())
        self.assertEqual(manifest["content_scripts"][0]["matches"], [
            "https://abuse.cloudflare.com/*",
            "https://safebrowsing.google.com/*",
            "https://www.microsoft.com/*",
            "https://chongluadao.vn/*",
            "https://safe.coccoc.com/*",
            "https://legalportal.godaddy.com/*",
        ])
        self.assertNotIn("<all_urls>", manifest["host_permissions"])
        self.assertEqual(manifest["background"]["service_worker"], "background.js")
        coordinator = (root / "chrome_extension/cloudflare-profile-worker/content.js").read_text(encoding="utf-8")
        adapter = (root / "chrome_extension/cloudflare-profile-worker/adapters/cloudflare.js").read_text(encoding="utf-8")
        self.assertEqual(manifest["content_scripts"][0]["js"], [
            "adapters/cloudflare.js",
            "adapters/google_gsb.js",
            "adapters/microsoft_smartscreen.js",
            "adapters/chongluadao.js",
            "adapters/coccoc_safe.js",
            "adapters/godaddy_phishing.js",
            "content.js",
        ])
        self.assertIn("PhishingToolFormAdapters", coordinator)
        self.assertNotIn("Confirm email address", coordinator)
        self.assertIn("Logs or other evidence of abuse", adapter)
        self.assertIn("Confirm email address", adapter)
        self.assertIn('byCaption(doc, "Confirm email address"', adapter)
        self.assertIn("email fields=${Number(emailReady) + Number(confirmReady)}/2", adapter)
        self.assertIn("Company name", adapter)
        self.assertNotIn("textarea[name=\"comments\"]", adapter)
        self.assertIn("bridge-fetch", coordinator)
        self.assertIn("assistant-status", coordinator)
        self.assertIn("assistant-read-status", coordinator)
        popup = (root / "chrome_extension/cloudflare-profile-worker/popup.js").read_text(encoding="utf-8")
        self.assertIn("assistant-get-status", popup)
        self.assertIn("assistant-read-status", popup)
        self.assertNotIn("ptask", popup)
        self.assertIn('runCommand("refill")', popup)
        self.assertIn('runCommand("recheck")', popup)
        self.assertIn("navigator.clipboard.writeText(diagnostic)", popup)
        self.assertIn("renderChecklist", popup)
        self.assertIn("assistant-command", coordinator)
        self.assertIn("buildChecklist", coordinator)
        self.assertIn("runAssistantCommand", coordinator)
        self.assertNotIn("attachShadow", coordinator)
        self.assertNotIn("pt-form-assistant", coordinator)
        background = (root / "chrome_extension/cloudflare-profile-worker/background.js").read_text(encoding="utf-8")
        self.assertIn("chrome.action.setBadgeText", background)
        self.assertIn("chrome.action.setBadgeBackgroundColor", background)
        self.assertIn('text: "C"', background)
        self.assertIn('text: "✓"', background)
        self.assertIn('text: "×"', background)

    def test_adapter_contract_is_complete(self):
        root = Path(__file__).resolve().parents[1]
        for name in (
            "cloudflare.js", "google_gsb.js", "microsoft_smartscreen.js",
            "chongluadao.js", "coccoc_safe.js", "godaddy_phishing.js",
        ):
            adapter = (root / "chrome_extension/cloudflare-profile-worker/adapters" / name).read_text(encoding="utf-8")
            for member in ("matches:", "waitUntilReady:", "fill:", "validate:", "captchaPending:", "submit:", "detectSuccess:"):
                self.assertIn(member, adapter)

    def test_godaddy_adapter_fills_fields_but_leaves_attestation_and_submit_manual(self):
        root = Path(__file__).resolve().parents[1]
        adapter = (root / "chrome_extension/cloudflare-profile-worker/adapters/godaddy_phishing.js").read_text(encoding="utf-8")
        for selector in ("#email-field", "#company-field", "#source-field", "#info-field"):
            self.assertIn(selector, adapter)
        self.assertIn("submit: () => false", adapter)
        self.assertNotIn('.querySelector("#attested").click', adapter)

    def test_coccoc_adapter_verifies_the_mui_hidden_value(self):
        root = Path(__file__).resolve().parents[1]
        adapter = (root / "chrome_extension/cloudflare-profile-worker/adapters/coccoc_safe.js").read_text(encoding="utf-8")
        self.assertIn('new MouseEvent("mousedown"', adapter)
        self.assertIn('data-value") === "1"', adapter)
        self.assertIn('input[name="type"]', adapter)
        self.assertIn('fields.typeInput.value === "1"', adapter)

    def test_bridge_exposes_only_safe_provider_fields(self):
        bridge = ProfileBridge()
        try:
            token = bridge.register({
                "provider": "google_gsb", "target_url": "https://example.test/",
                "draft": "Report", "threat_type": "Social Engineering",
                "threat_category": "Other Phishing", "language": "Vietnamese",
                "report_type": "Phishing",
                "cookie": "must-not-pass",
            }, lambda _result: None)
            with urlopen(f"http://127.0.0.1:{bridge.port}/task/{token}", timeout=2) as response:
                task = json.load(response)
            self.assertEqual(task["provider"], "google_gsb")
            self.assertEqual(task["threat_category"], "Other Phishing")
            self.assertEqual(task["report_type"], "Phishing")
            self.assertNotIn("cookie", task)
            unknown = bridge.register({"provider": "unknown"}, lambda _result: None)
            with urlopen(f"http://127.0.0.1:{bridge.port}/task/{unknown}", timeout=2) as response:
                fallback = json.load(response)
            self.assertEqual(fallback["provider"], "cloudflare")
        finally:
            bridge.server.shutdown()
            bridge.server.server_close()


if __name__ == "__main__":
    unittest.main()

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

    def test_extension_is_restricted_to_cloudflare_and_localhost(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "chrome_extension/cloudflare-profile-worker/manifest.json").read_text())
        self.assertEqual(manifest["content_scripts"][0]["matches"], ["https://abuse.cloudflare.com/*"])
        self.assertNotIn("<all_urls>", manifest["host_permissions"])
        self.assertEqual(manifest["background"]["service_worker"], "background.js")
        script = (root / "chrome_extension/cloudflare-profile-worker/content.js").read_text()
        self.assertIn("Logs or other evidence of abuse", script)
        self.assertIn("Confirm email address", script)
        self.assertIn("visibleEmailInputs", script)
        self.assertIn("emailCandidates.find", script)
        self.assertIn("Company name", script)
        self.assertNotIn("textarea[name=\"comments\"]", script)
        self.assertIn("bridge-fetch", script)


if __name__ == "__main__":
    unittest.main()

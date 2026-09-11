import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cloudflare_form_worker as cfw


class CloudflareFormWorkerTests(unittest.TestCase):
    def test_normalize_target_preserves_full_path_and_query(self):
        self.assertEqual(
            cfw.normalize_target("HTTPS://Example.COM/login?next=%2Faccount"),
            "https://example.com/login?next=%2Faccount",
        )

    def test_prepare_filters_non_cloudflare_and_deduplicates_today(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            checker = Mock(side_effect=[{"cloudflare": True}, {"cloudflare": False}])
            cfg = {"brand_name": "Example"}
            rows = cfw.prepare_urls(
                ["https://a.example/login", "https://a.example/login", "b.example"],
                cfg, checker=checker, path=path,
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["state"], "READY")
            self.assertIn("https://a.example/login", rows[0]["draft"])
            self.assertEqual(rows[1]["state"], "NOT_CLOUDFLARE")
            self.assertEqual(checker.call_count, 2)
            self.assertEqual(len(cfw.load_ledger(path)["records"]), 2)

    def test_submitted_url_is_not_checked_again_on_same_day(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            checker = Mock(return_value={"cloudflare": True})
            row = cfw.prepare_urls(["example.com"], {"brand_name": "X"}, checker=checker, path=path)[0]
            cfw.update_record(row["id"], path, state="SUBMITTED", result="ok")
            checker.reset_mock()
            again = cfw.prepare_urls(["example.com"], {"brand_name": "X"}, checker=checker, path=path)[0]
            self.assertEqual(again["state"], "SUBMITTED")
            checker.assert_not_called()

    def test_ledger_contains_no_browser_or_captcha_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            row = cfw.prepare_urls(
                ["example.com"], {"brand_name": "X"},
                checker=lambda *_: {"cloudflare": True}, path=path,
            )[0]
            cfw.update_record(
                row["id"], path, state="FAILED",
                last_error="bad ?cf-turnstile-response=secret&token=private",
            )
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("secret", raw)
            self.assertNotIn("private", raw)
            self.assertNotIn("cookie", raw.lower())

    def test_submit_mode_requires_confirmation(self):
        worker = object.__new__(cfw.CloudflareBrowserWorker)
        worker.ledger_path = Path("unused")
        worker._queue = Mock()
        worker._busy = False
        worker._status = "ready"
        import threading
        worker._lock = threading.Lock()
        result = worker.start([{"id": "1"}], mode="submit", delay_seconds=0, confirmed=False)
        self.assertIn("error", result)
        worker._queue.put.assert_not_called()

    def test_quick_report_opens_fill_only_task_with_company_and_contact(self):
        callback_holder = {}
        bridge = Mock()
        bridge.port = 45678
        def register(payload, callback):
            callback_holder.update({"payload": payload, "callback": callback})
            return "one-time-token"
        bridge.register.side_effect = register
        with patch("cloudflare_profile_bridge.profile_bridge", return_value=bridge), patch.object(
            cfw, "open_in_installed_chrome", return_value=True
        ) as opener:
            result = cfw.open_quick_report_form(
                "https://example.test/login", "Evidence", {
                    "contact_name": "Reporter", "contact_email": "r@example.test",
                    "brand_name": "Example Brand",
                },
            )
        self.assertEqual(result["status"], "opened")
        self.assertEqual(callback_holder["payload"]["mode"], "fill_only")
        self.assertEqual(callback_holder["payload"]["_brand_name"], "Example Brand")
        self.assertIn("ptask=one-time-token", opener.call_args.args[0])

    def test_provider_form_passes_taxonomy_in_fill_only_task(self):
        captured = {}
        bridge = Mock(port=45678)
        bridge.register.side_effect = lambda payload, callback: captured.update(payload=payload) or "token"
        with patch("cloudflare_profile_bridge.profile_bridge", return_value=bridge), patch.object(
            cfw, "open_in_installed_chrome", return_value=True
        ) as opener:
            result = cfw.open_profile_form(
                "google_gsb", "https://safebrowsing.google.com/safebrowsing/report_phish/?url=x",
                "https://example.test/login", "Evidence", {},
                threat_type="Social Engineering", threat_category="Other Phishing",
                cookie="not-allowed",
            )
        self.assertEqual(result["status"], "opened")
        self.assertEqual(captured["payload"]["provider"], "google_gsb")
        self.assertEqual(captured["payload"]["mode"], "fill_only")
        self.assertNotIn("cookie", captured["payload"])
        self.assertIn("url=x#ptask=token", opener.call_args.args[0])

    def test_provider_form_rejects_unknown_adapter(self):
        result = cfw.open_profile_form("unknown", "https://example.test", "example.test", "", {})
        self.assertIn("error", result)

    def test_community_form_passes_only_whitelisted_report_type(self):
        captured = {}
        bridge = Mock(port=45678)
        bridge.register.side_effect = lambda payload, callback: captured.update(payload=payload) or "token"
        with patch("cloudflare_profile_bridge.profile_bridge", return_value=bridge), patch.object(
            cfw, "open_in_installed_chrome", return_value=True
        ):
            result = cfw.open_profile_form(
                "coccoc_safe", "https://safe.coccoc.com/", "https://example.test/path",
                "Evidence", {"contact_email": "r@example.test"},
                report_type="Trang web lừa đảo", unexpected="blocked",
            )
        self.assertEqual(result["status"], "opened")
        self.assertEqual(captured["payload"]["report_type"], "Trang web lừa đảo")
        self.assertNotIn("unexpected", captured["payload"])


if __name__ == "__main__":
    unittest.main()

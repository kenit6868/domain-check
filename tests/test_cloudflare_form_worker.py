import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cloudflare_form_worker as cfw


class CloudflareFormWorkerTests(unittest.TestCase):
    def test_atomic_ledger_save_retries_transient_windows_permission_error(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            cfw.os, "replace",
            side_effect=[PermissionError(13, "busy"), PermissionError(13, "busy"), None],
        ) as replace:
            path = Path(folder) / "ledger.json"
            with patch.object(cfw.time, "sleep") as sleep:
                cfw.save_ledger({"records": []}, path)
            self.assertEqual(replace.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_open_installed_chrome_macos_system_and_user_install(self):
        url = "https://www.microsoft.com/wdsi/support/report-unsafe-site-guest#task=test"
        paths = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                 Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
        for installed in paths:
            with self.subTest(installed=installed), patch.object(cfw.os, "name", "posix"), \
                    patch.object(cfw.sys, "platform", "darwin"), \
                    patch.object(Path, "is_file", autospec=True, side_effect=lambda p: p == installed), \
                    patch("subprocess.Popen") as launch:
                self.assertTrue(cfw.open_in_installed_chrome(url))
                launch.assert_called_once_with([str(installed), url], close_fds=True)

    def test_open_installed_chrome_linux_path(self):
        with patch.object(cfw.os, "name", "posix"), patch.object(cfw.sys, "platform", "linux"), \
                patch("shutil.which", side_effect=[None, "/usr/bin/google-chrome-stable"]), \
                patch.object(Path, "is_file", return_value=True), patch("subprocess.Popen") as launch:
            self.assertTrue(cfw.open_in_installed_chrome("https://example.test/#task=test"))
            launch.assert_called_once_with(
                ["/usr/bin/google-chrome-stable", "https://example.test/#task=test"], close_fds=True)

    def test_open_installed_chrome_missing_or_launch_failure(self):
        for exists in (False, True):
            with self.subTest(exists=exists), patch.object(cfw.os, "name", "posix"), \
                    patch.object(cfw.sys, "platform", "darwin"), \
                    patch.object(Path, "is_file", return_value=exists), \
                    patch("subprocess.Popen", side_effect=OSError("Cannot launch")) as launch:
                self.assertFalse(cfw.open_in_installed_chrome("https://example.test"))
                self.assertEqual(launch.call_count, 2 if exists else 0)

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
            self.assertTrue(rows[0]["report_version"])
            self.assertTrue(rows[0]["idempotency_key"])

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

    def test_daily_input_cache_and_safe_clear_preserve_terminal_records(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Path(folder) / "ledger.json"
            input_cache = Path(folder) / "input.json"
            cfw.save_daily_input("example.com", input_cache)
            self.assertEqual(cfw.load_daily_input(input_cache), "example.com")
            rows = cfw.prepare_urls(
                ["sent.example", "ready.example"], {"brand_name": "X"},
                checker=lambda *_: {"cloudflare": True}, path=ledger,
            )
            cfw.update_record(rows[0]["id"], ledger, state="SUBMITTED")
            removed = cfw.clear_daily_cache(ledger_path=ledger, input_path=input_cache)
            self.assertEqual(removed, 1)
            self.assertEqual(cfw.load_daily_input(input_cache), "")
            saved = cfw.today_records(ledger)
            self.assertEqual([item["state"] for item in saved], ["SUBMITTED"])

    def test_api_submit_checkpoints_report_id_and_prevents_same_day_resend(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            row = cfw.prepare_urls(
                ["https://example.com/login"], {"brand_name": "X"},
                checker=lambda *_: {"cloudflare": True}, path=path,
            )[0]
            submitter = Mock(return_value={
                "ok": True, "result": "success", "report_id": "report-123",
                "http_status": 200,
            })
            result = cfw.submit_api_records(
                [row], {"contact_email": "r@example.test"},
                path=path, submitter=submitter,
            )
            self.assertTrue(result[0]["ok"])
            saved = cfw.today_records(path)[0]
            self.assertEqual(saved["state"], "SUBMITTED")
            self.assertEqual(saved["channel"], "api")
            self.assertEqual(saved["report_id"], "report-123")
            self.assertEqual(cfw.submit_api_records(
                [saved], {}, path=path, submitter=submitter
            ), [])
            submitter.assert_called_once()

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

    def test_dashboard_batch_keeps_session_out_of_ledger_and_checkpoints_success(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            row = cfw.prepare_urls(
                ["https://example.com/login"],
                {"brand_name": "X"}, checker=lambda *_: {"cloudflare": True}, path=path,
            )[0]
            submitter = Mock(return_value={
                "ok": True, "result": "success", "report_id": "dashboard-1",
                "http_status": 200,
            })
            worker = cfw.CloudflareSessionBatchWorker(ledger_path=path, submitter=submitter)
            result = worker.start(
                [row], {}, cookie_value="cookie-super-secret",
                delay_seconds=0, confirmed=True,
            )
            self.assertEqual(result["status"], "started")
            worker._thread.join(timeout=2)
            self.assertFalse(worker._thread.is_alive())
            saved = cfw.today_records(path)[0]
            self.assertEqual(saved["state"], "SUBMITTED")
            self.assertEqual(saved["channel"], "dashboard_session")
            self.assertEqual(saved["report_id"], "dashboard-1")
            self.assertNotIn("cookie-super-secret", path.read_text(encoding="utf-8"))
            self.assertNotIn("cookie-super-secret", str(worker.snapshot()))

    def test_dashboard_unknown_is_not_retried_automatically(self):
        from cloudflare_dashboard_session import CloudflareDashboardUnknownError

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            row = cfw.prepare_urls(
                ["https://example.com/login"],
                {"brand_name": "X"}, checker=lambda *_: {"cloudflare": True}, path=path,
            )[0]
            submitter = Mock(side_effect=CloudflareDashboardUnknownError("response lost"))
            worker = cfw.CloudflareSessionBatchWorker(ledger_path=path, submitter=submitter)
            worker.start(
                [row], {}, cookie_value="cookie",
                delay_seconds=0, confirmed=True,
            )
            deadline = time.time() + 2
            while worker.snapshot()["state"] != "PAUSED" and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(cfw.today_records(path)[0]["state"], "UNKNOWN")
            self.assertEqual(submitter.call_count, 1)
            worker.resume()
            worker._thread.join(timeout=2)
            self.assertEqual(submitter.call_count, 1)

    def test_dashboard_definite_item_error_continues_to_next_url(self):
        from cloudflare_dashboard_session import CloudflareDashboardError

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            rows = cfw.prepare_urls(
                ["https://first.example/login", "https://second.example/login"],
                {"brand_name": "X"}, checker=lambda *_: {"cloudflare": True}, path=path,
            )
            submitter = Mock(side_effect=[
                CloudflareDashboardError("invalid report"),
                {"ok": True, "result": "success", "report_id": "report-2", "http_status": 200},
            ])
            worker = cfw.CloudflareSessionBatchWorker(ledger_path=path, submitter=submitter)
            worker.start(rows, {}, cookie_value="secret-cookie", delay_seconds=0, confirmed=True)
            worker._thread.join(timeout=2)
            saved = {item["target_url"]: item for item in cfw.today_records(path)}
            self.assertEqual(saved["https://first.example/login"]["state"], "FAILED")
            self.assertEqual(saved["https://second.example/login"]["state"], "SUBMITTED")
            self.assertEqual(submitter.call_count, 2)
            job_status = cfw.load_job_status(path.with_name("cloudflare_form_job.json"))
            self.assertEqual(job_status["state"], "COMPLETED")
            self.assertEqual(job_status["record_ids"], [item["id"] for item in rows])
            self.assertNotIn("secret-cookie", json.dumps(job_status))
            retry_submitter = Mock(return_value={
                "ok": True, "result": "success", "report_id": "report-1-retry",
                "http_status": 200,
            })
            worker._submitter = retry_submitter
            worker.start(
                [saved["https://first.example/login"]], {}, cookie_value="retry-cookie",
                delay_seconds=0, confirmed=True,
            )
            worker._thread.join(timeout=2)
            retried_status = cfw.load_job_status(path.with_name("cloudflare_form_job.json"))
            self.assertEqual(retried_status["record_ids"], [item["id"] for item in rows])

    def test_dashboard_dedupe_is_terminal_and_keeps_message(self):
        from cloudflare_dashboard_session import CloudflareDashboardDuplicateError

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            row = cfw.prepare_urls(
                ["https://example.com/login"],
                {"brand_name": "X"}, checker=lambda *_: {"cloudflare": True}, path=path,
            )[0]
            message = "You have already submitted this URL recently: https://example.com/login"
            submitter = Mock(side_effect=CloudflareDashboardDuplicateError(message))
            worker = cfw.CloudflareSessionBatchWorker(ledger_path=path, submitter=submitter)
            worker.start(
                [row], {}, cookie_value="cookie", delay_seconds=0, confirmed=True,
            )
            worker._thread.join(timeout=2)
            saved = cfw.today_records(path)[0]
            self.assertEqual(saved["state"], "ALREADY_SUBMITTED")
            self.assertEqual(saved["result"], "dedupe")
            self.assertEqual(saved["last_error"], message)
            self.assertEqual(submitter.call_count, 1)
            self.assertIn("error", worker.start(
                [saved], {}, cookie_value="cookie", delay_seconds=0, confirmed=True,
            ))
            self.assertEqual(submitter.call_count, 1)

    def test_dashboard_auth_failure_waits_for_new_session_and_retries_same_item(self):
        from cloudflare_dashboard_session import CloudflareDashboardSessionError

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            row = cfw.prepare_urls(
                ["https://example.com/login"],
                {"brand_name": "X"}, checker=lambda *_: {"cloudflare": True}, path=path,
            )[0]
            submitter = Mock(side_effect=[
                CloudflareDashboardSessionError("expired"),
                {"ok": True, "result": "success", "report_id": "report-new", "http_status": 200},
            ])
            worker = cfw.CloudflareSessionBatchWorker(ledger_path=path, submitter=submitter)
            worker.start(
                [row], {}, cookie_value="old-cookie",
                delay_seconds=0, confirmed=True,
            )
            deadline = time.time() + 2
            while worker.snapshot()["state"] != "WAITING_FOR_SESSION" and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(cfw.today_records(path)[0]["state"], "WAITING_FOR_SESSION")
            self.assertIn("error", worker.resume())
            self.assertEqual(worker.resume(
                cookie_value="new-cookie"
            )["status"], "running")
            worker._thread.join(timeout=2)
            self.assertFalse(worker._thread.is_alive())
            self.assertEqual(cfw.today_records(path)[0]["state"], "SUBMITTED")
            self.assertEqual(submitter.call_args_list[0].args[-1], "old-cookie")
            self.assertEqual(submitter.call_args_list[1].args[-1], "new-cookie")
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("old-cookie", raw)
            self.assertNotIn("new-cookie", raw)

    def test_dashboard_restart_marks_inflight_request_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            row = cfw.prepare_urls(
                ["https://example.com/login"],
                {"brand_name": "X"}, checker=lambda *_: {"cloudflare": True}, path=path,
            )[0]
            cfw.update_record(
                row["id"], path, state="SUBMITTING", channel="dashboard_session"
            )
            cfw.CloudflareSessionBatchWorker(ledger_path=path, submitter=Mock())
            saved = cfw.today_records(path)[0]
            self.assertEqual(saved["state"], "UNKNOWN")
            self.assertIn("đối chiếu", saved["last_error"])

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

    def test_godaddy_form_uses_fill_only_task_with_contact_and_brand(self):
        captured = {}
        bridge = Mock(port=45678)
        bridge.register.side_effect = lambda payload, callback: captured.update(payload=payload) or "token"
        with patch("cloudflare_profile_bridge.profile_bridge", return_value=bridge), patch.object(
            cfw, "open_in_installed_chrome", return_value=True
        ) as opener:
            result = cfw.open_profile_form(
                "godaddy_phishing", "https://legalportal.godaddy.com/abuse/phishing",
                "https://example.test/login", "Evidence",
                {"contact_email": "r@example.test", "brand_name": "Example Brand"},
            )
        self.assertEqual(result["status"], "opened")
        self.assertEqual(captured["payload"]["provider"], "godaddy_phishing")
        self.assertEqual(captured["payload"]["mode"], "fill_only")
        self.assertEqual(captured["payload"]["_contact_email"], "r@example.test")
        self.assertEqual(captured["payload"]["_brand_name"], "Example Brand")
        self.assertIn("legalportal.godaddy.com/abuse/phishing#ptask=token", opener.call_args.args[0])

    def test_registry_co_form_uses_fill_only_task_and_preserves_query(self):
        captured = {}
        bridge = Mock(port=45678)
        bridge.register.side_effect = lambda payload, callback: captured.update(payload=payload) or "token"
        with patch("cloudflare_profile_bridge.profile_bridge", return_value=bridge), patch.object(
            cfw, "open_in_installed_chrome", return_value=True
        ) as opener:
            result = cfw.open_profile_form(
                "registry_co_phishing",
                "https://registry.co/report-abuse/form?type=phishing",
                "https://phish.example.co/login?campaign=1", "Evidence",
                {"contact_name": "Reporter", "contact_email": "r@example.test",
                 "brand_name": "Example Brand"},
            )
        self.assertEqual(result["status"], "opened")
        self.assertEqual(captured["payload"]["provider"], "registry_co_phishing")
        self.assertEqual(captured["payload"]["mode"], "fill_only")
        self.assertEqual(captured["payload"]["target_url"], "https://phish.example.co/login?campaign=1")
        self.assertIn("registry.co/report-abuse/form?type=phishing#ptask=token", opener.call_args.args[0])

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

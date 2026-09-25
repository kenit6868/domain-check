import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

import cloudflare_form_worker as cfw
import phishing_toolkit as pt


ROOT = Path(__file__).resolve().parents[1]


class CloudflareFormWorkerUiTests(unittest.TestCase):
    def test_input_parser_uses_only_real_urls_from_annotated_redirect_lines(self):
        page_source = (ROOT / "pages" / "14_Cloudflare_Form_Worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("return cfw.parse_target_input(raw)", page_source)

    def test_finished_job_refreshes_page_and_unlocks_new_input(self):
        url = "https://sent.example.test/login"
        row = {
            "id": cfw.record_id(url), "day": cfw.current_day(), "target_url": url,
            "domain": "sent.example.test", "cloudflare": True,
            "draft": "Detailed evidence text", "report_version": "version-1",
            "state": "SUBMITTED", "attempts": 1, "last_error": "",
            "result": "success", "updated_at": "2026-09-25T10:00:00+07:00",
        }
        running = {
            "busy": True, "state": "RUNNING", "status": "Đang gửi",
            "current_id": row["id"], "processed": 0, "total": 1,
            "delay_seconds": 0, "record_ids": [row["id"]],
        }
        completed = {
            **running, "busy": False, "state": "COMPLETED",
            "status": "Batch đã kết thúc.", "current_id": "", "processed": 1,
        }
        session_worker = Mock()
        calls = {"count": 0}

        def snapshot():
            calls["count"] += 1
            return running if calls["count"] == 1 else completed

        session_worker.snapshot.side_effect = snapshot
        with patch.object(cfw, "today_records", return_value=[row]), patch.object(
            cfw, "session_batch_worker", return_value=session_worker
        ), patch.object(cfw, "load_daily_input", return_value=url), patch.object(
            pt, "load_config", return_value={
                "brand_name": "Example", "contact_name": "Reporter",
                "contact_email": "reporter@example.test",
            },
        ):
            app = AppTest.from_file(
                str(ROOT / "pages" / "14_Cloudflare_Form_Worker.py"), default_timeout=10
            ).run()

        self.assertFalse(app.exception)
        self.assertGreaterEqual(calls["count"], 3)
        self.assertFalse(app.text_area[0].disabled)
        prepare = next(item for item in app.button if item.label == "Kiểm tra Cloudflare")
        stop = next(item for item in app.button if item.label == "Dừng")
        self.assertFalse(prepare.disabled)
        self.assertTrue(stop.disabled)
        self.assertTrue(any("Job đã hoàn thành" in item.value for item in app.success))

    def test_empty_page_renders_without_external_access(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            cfw, "LEDGER_PATH", Path(folder) / "ledger.json"
        ), patch.object(cfw, "today_records", return_value=[]), patch.object(
            pt, "load_config", return_value={
                "brand_name": "Example", "contact_name": "Reporter",
                "contact_email": "reporter@example.test",
            },
        ):
            app = AppTest.from_file(
                str(ROOT / "pages" / "14_Cloudflare_Form_Worker.py"), default_timeout=10
            ).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.title[0].value, "Cloudflare Worker")
            self.assertEqual([item.value for item in app.subheader], ["Kiểm tra URL"])
            self.assertFalse(any(item.label == "Cookie Cloudflare" for item in app.text_input))
            self.assertEqual(len(app.dataframe), 0)

    def test_unchecked_input_hides_send_configuration_and_results(self):
        with patch.object(cfw, "today_records", return_value=[]), patch.object(
            cfw, "load_daily_input", return_value="https://unchecked.example.test/login"
        ), patch.object(
            pt, "load_config", return_value={
                "brand_name": "Example", "contact_name": "Reporter",
                "contact_email": "reporter@example.test",
            },
        ):
            app = AppTest.from_file(
                str(ROOT / "pages" / "14_Cloudflare_Form_Worker.py"), default_timeout=10
            ).run()
            self.assertFalse(app.exception)
            self.assertEqual([item.value for item in app.subheader], ["Kiểm tra URL"])
            self.assertFalse(any(item.label == "Cookie Cloudflare" for item in app.text_input))
            self.assertEqual(len(app.dataframe), 0)

    def test_preview_renders_manual_cookie_controls_without_exposing_cookie(self):
        row = {
            "id": cfw.record_id("https://example.test/login"), "day": cfw.current_day(),
            "target_url": "https://example.test/login",
            "domain": "example.test", "cloudflare": True, "draft": "Detailed evidence text",
            "report_version": "version-1", "state": "READY", "attempts": 0,
            "last_error": "", "updated_at": "2026-09-24T10:00:00+07:00",
        }
        old_row = {
            **row, "id": cfw.record_id("https://old.example.test/login"),
            "target_url": "https://old.example.test/login",
            "domain": "old.example.test", "state": "SUBMITTED",
        }
        current_row = {
            **row, "id": cfw.record_id("https://current.example.test/login"),
            "target_url": "https://current.example.test/login",
            "domain": "current.example.test", "state": "SUBMITTING",
        }
        session_worker = Mock()
        session_worker.snapshot.return_value = {
            "busy": True, "state": "RUNNING", "status": "Đang gửi",
            "current_id": current_row["id"], "processed": 0, "total": 2, "delay_seconds": 0,
            "record_ids": [row["id"], current_row["id"]],
        }
        with patch.object(cfw, "today_records", return_value=[row, current_row, old_row]), patch.object(
            cfw, "session_batch_worker", return_value=session_worker
        ), patch.object(
            cfw, "load_daily_input",
            return_value="https://example.test/login\nhttps://current.example.test/login",
        ), patch.object(
            pt, "load_config", return_value={
                "brand_name": "Example", "contact_name": "Reporter",
                "contact_email": "reporter@example.test",
                "cloudflare_account_id": "0123456789abcdef0123456789abcdef",
            },
        ):
            app = AppTest.from_file(
                str(ROOT / "pages" / "14_Cloudflare_Form_Worker.py"), default_timeout=10
            )
            app = app.run()
            self.assertFalse(app.exception)
            self.assertEqual(
                [item.value for item in app.subheader[:3]],
                ["Kiểm tra URL", "Cấu hình gửi", "Kết quả URL"],
            )
            self.assertTrue(any(item.label == "Cookie Cloudflare" for item in app.text_input))
            self.assertFalse(any(item.label == "x-atok Cloudflare" for item in app.text_input))
            self.assertTrue(any(item.label == "Bắt đầu gửi" for item in app.button))
            self.assertTrue(any(item.label == "Thử lại URL lỗi" for item in app.button))
            self.assertEqual(len(app.dataframe), 1)
            table = app.dataframe[0].value
            self.assertEqual(
                list(table["URL"]),
                ["https://example.test/login", "https://current.example.test/login"],
            )
            self.assertNotIn("old.example.test", str(app.dataframe[0].value))
            self.assertTrue(any("Đang xử lý" in item.value for item in app.info))
            self.assertNotIn("Kênh dự phòng", str(app))
            self.assertNotIn("cookie-super-secret", str(app))

    def test_excluded_urls_are_hidden_until_operator_enables_toggle(self):
        urls = [
            "https://ready.example.test/login",
            "https://sent.example.test/login",
            "https://direct.example.test/login",
        ]
        base = {
            "day": cfw.current_day(), "draft": "Detailed evidence text",
            "report_version": "version-1", "attempts": 0, "last_error": "",
            "updated_at": "2026-09-25T10:00:00+07:00",
        }
        rows = [
            {**base, "id": cfw.record_id(urls[0]), "target_url": urls[0],
             "domain": "ready.example.test", "cloudflare": True, "state": "READY"},
            {**base, "id": cfw.record_id(urls[1]), "target_url": urls[1],
             "domain": "sent.example.test", "cloudflare": True, "state": "SUBMITTED"},
            {**base, "id": cfw.record_id(urls[2]), "target_url": urls[2],
             "domain": "direct.example.test", "cloudflare": False,
             "state": "NOT_CLOUDFLARE", "draft": "",
             "last_error": "Không phát hiện nameserver Cloudflare"},
        ]
        session_worker = Mock()
        session_worker.snapshot.return_value = {
            "busy": False, "state": "COMPLETED", "status": "Hoàn tất",
            "current_id": "", "processed": 1, "total": 1,
            "delay_seconds": 0, "record_ids": [],
        }
        with patch.object(cfw, "today_records", return_value=rows), patch.object(
            cfw, "session_batch_worker", return_value=session_worker
        ), patch.object(cfw, "load_daily_input", return_value="\n".join(urls)), patch.object(
            pt, "load_config", return_value={
                "brand_name": "Example", "contact_name": "Reporter",
                "contact_email": "reporter@example.test",
                "cloudflare_account_id": "0123456789abcdef0123456789abcdef",
            },
        ):
            app = AppTest.from_file(
                str(ROOT / "pages" / "14_Cloudflare_Form_Worker.py"), default_timeout=10
            ).run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe), 1)
            action_table = str(app.dataframe[0].value)
            self.assertIn("ready.example.test", action_table)
            self.assertNotIn("sent.example.test", action_table)
            self.assertNotIn("Bảng loại", [item.value for item in app.subheader])
            excluded_toggle = next(
                item for item in app.toggle if item.label.startswith("Hiện URL bị loại")
            )
            app = excluded_toggle.set_value(True).run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe), 2)
            excluded_table = str(app.dataframe[1].value)
            self.assertIn("sent.example.test", excluded_table)
            self.assertIn("direct.example.test", excluded_table)

    def test_completed_job_keeps_successful_url_in_progress_table(self):
        url = "https://sent.example.test/login"
        row = {
            "id": cfw.record_id(url), "day": cfw.current_day(), "target_url": url,
            "domain": "sent.example.test", "cloudflare": True,
            "draft": "Detailed evidence text", "report_version": "version-1",
            "state": "SUBMITTED", "attempts": 1, "last_error": "",
            "result": "success", "report_id": "report-1",
            "updated_at": "2026-09-25T10:00:00+07:00",
        }
        session_worker = Mock()
        session_worker.snapshot.return_value = {
            "busy": False, "state": "COMPLETED", "status": "Batch đã kết thúc.",
            "current_id": "", "processed": 1, "total": 1,
            "delay_seconds": 0, "record_ids": [row["id"]],
        }
        with patch.object(cfw, "today_records", return_value=[row]), patch.object(
            cfw, "session_batch_worker", return_value=session_worker
        ), patch.object(cfw, "load_daily_input", return_value=url), patch.object(
            pt, "load_config", return_value={
                "brand_name": "Example", "contact_name": "Reporter",
                "contact_email": "reporter@example.test",
                "cloudflare_account_id": "0123456789abcdef0123456789abcdef",
            },
        ):
            app = AppTest.from_file(
                str(ROOT / "pages" / "14_Cloudflare_Form_Worker.py"), default_timeout=10
            ).run()
            self.assertFalse(app.exception)
            subheaders = [item.value for item in app.subheader]
            self.assertIn("Kết quả URL", subheaders)
            self.assertNotIn("Bảng loại", subheaders)
            self.assertEqual(len(app.dataframe), 1)
            self.assertIn("✅ Đã gửi", str(app.dataframe[0].value))
            self.assertTrue(any("Job đã hoàn thành" in item.value for item in app.success))

    def test_reloaded_worker_restores_persisted_job_membership(self):
        url = "https://sent.example.test/login"
        new_url = "https://new.example.test/login"
        row = {
            "id": cfw.record_id(url), "day": cfw.current_day(), "target_url": url,
            "domain": "sent.example.test", "cloudflare": True,
            "draft": "Detailed evidence text", "report_version": "version-1",
            "state": "SUBMITTED", "attempts": 1, "last_error": "",
            "result": "success", "updated_at": "2026-09-25T10:00:00+07:00",
        }
        new_row = {
            **row, "id": cfw.record_id(new_url), "target_url": new_url,
            "domain": "new.example.test", "state": "READY", "attempts": 0,
            "result": "", "updated_at": "2026-09-25T10:01:00+07:00",
        }
        session_worker = Mock()
        session_worker.snapshot.return_value = {
            "busy": False, "state": "IDLE", "status": "ready",
            "current_id": "", "processed": 0, "total": 0,
            "delay_seconds": 0, "job_id": "", "record_ids": [],
        }
        persisted = {
            "job_id": "20260925T100000+0700", "state": "COMPLETED",
            "record_ids": [row["id"]], "processed": 1, "total": 1,
        }
        with patch.object(cfw, "today_records", return_value=[row, new_row]), patch.object(
            cfw, "session_batch_worker", return_value=session_worker
        ), patch.object(cfw, "load_job_status", return_value=persisted), patch.object(
            cfw, "load_daily_input", return_value=f"{url}\n{new_url}"
        ), patch.object(
            pt, "load_config", return_value={
                "brand_name": "Example", "contact_name": "Reporter",
                "contact_email": "reporter@example.test",
                "cloudflare_account_id": "0123456789abcdef0123456789abcdef",
            },
        ):
            app = AppTest.from_file(
                str(ROOT / "pages" / "14_Cloudflare_Form_Worker.py"), default_timeout=10
            ).run()
            self.assertFalse(app.exception)
            subheaders = [item.value for item in app.subheader]
            self.assertIn("Kết quả URL", subheaders)
            self.assertNotIn("Bảng loại", subheaders)
            self.assertEqual(len(app.dataframe), 1)
            self.assertIn("sent.example.test", str(app.dataframe[0].value))
            self.assertIn("new.example.test", str(app.dataframe[0].value))


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import Mock, patch

import godaddy_cases as cases
import provider_replies as replies


class GoDaddyCaseTests(unittest.TestCase):
    def make_mail(self, sender, subject, body, account="reporter@example.com"):
        message = EmailMessage()
        message["From"] = sender
        message["To"] = account
        message["Subject"] = subject
        message["Date"] = "Thu, 01 Oct 2026 10:00:00 +0000"
        message["Message-ID"] = "<godaddy-case@example.test>"
        message.set_content(body)
        return replies.parse_message("31", account, message.as_bytes())

    def test_case_persists_and_mail_matches_exact_account_ticket_and_sender(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cases, "CASE_PATH", str(Path(folder) / "cases.json")):
            row = cases.record_case("REPORTER@example.com", "GD-12345", "https://bad.example/login")
            self.assertEqual(row["status"], "submitted")
            self.assertEqual(len(cases.list_cases(account="reporter@example.com")), 1)
            self.assertEqual(len(cases.list_cases(account="reporter@example.com")), 1)
            other = self.make_mail("abuse@other.example", "Case ID: GD-12345", "We received your report.")
            wrong_account = self.make_mail("abuse@godaddy.com", "Case ID: GD-12345", "We received your report.", "other@example.com")
            correct = self.make_mail("abuse@godaddy.com", "Case ID: GD-12345", "We received your report.")
            self.assertEqual(cases.sync_mail("reporter@example.com", [other, wrong_account]), 0)
            self.assertEqual(cases.sync_mail("reporter@example.com", [correct]), 1)
            self.assertEqual(cases.sync_mail("reporter@example.com", [correct]), 0)
            saved = cases.list_cases(account="reporter@example.com")[0]
            self.assertEqual(saved["status"], "acknowledged")
            self.assertNotIn("body", saved)
            self.assertEqual(saved["last_message_id"], correct.message_id)

    def test_manual_portal_result_is_not_overwritten_by_older_email(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cases, "CASE_PATH", str(Path(folder) / "cases.json")):
            cases.record_case("reporter@example.com", "GD-12345", "https://bad.example/login")
            cases.record_portal_status("reporter@example.com", "GD-12345", "in_review")
            old_mail = self.make_mail("abuse@godaddy.com", "Case ID: GD-12345", "We received your report.")
            cases.sync_mail("reporter@example.com", [old_mail])
            self.assertEqual(cases.list_cases()[0]["status"], "in_review")

    def test_followup_is_due_only_without_recent_progress(self):
        row = {"submitted_at": "2026-09-20T00:00:00+00:00", "status": "submitted"}
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.assertTrue(cases.followup_due(row, 7, now))
        row["status"] = "resolved"
        self.assertFalse(cases.followup_due(row, 7, now))
        row["status"] = "acknowledged"
        row["last_mail_at"] = "2026-09-30T00:00:00+00:00"
        self.assertFalse(cases.followup_due(row, 7, now))

    def test_corrupt_case_file_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cases, "CASE_PATH", str(Path(folder) / "cases.json")):
            Path(cases.CASE_PATH).write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "chưa bị ghi đè"):
                cases.record_case("reporter@example.com", "GD-12345", "https://bad.example/login")
            self.assertEqual(Path(cases.CASE_PATH).read_text(encoding="utf-8"), "{broken")

    def test_quick_report_can_save_case_after_form_is_opened(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest

        page = Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py"
        result = Future()
        result.set_result({"domain": "bad.example", "cloudflare": False,
                           "registrar": "GoDaddy.com, LLC", "_original_url": "https://bad.example/login"})
        executor = Mock()
        executor.submit.return_value = result
        st.cache_resource.clear()
        try:
            with tempfile.TemporaryDirectory() as folder, \
                    patch.object(cases, "CASE_PATH", str(Path(folder) / "cases.json")), \
                    patch("phishing_toolkit.load_config", return_value={"contact_email": "reporter@example.com"}), \
                    patch("concurrent.futures.ThreadPoolExecutor", side_effect=lambda *a, **kw:
                          executor if kw.get("thread_name_prefix") == "quick-report"
                          else ThreadPoolExecutor(*a, **kw)):
                app = AppTest.from_file(str(page)).run()
                app.text_area(key="qr_domain_input").set_value("https://bad.example/login")
                next(button for button in app.button if "Kiểm tra tất cả" in button.label).click().run()
                self.assertFalse(app.exception)
                self.assertEqual(cases.list_cases(), [])
                app.text_input(key="quick_godaddy_case_id_0").set_value("GD-12345")
                save_button = next(button for button in app.button if button.label == "Lưu Case ID sau khi đã gửi")
                self.assertFalse(save_button.disabled)
                save_button.click().run()
                self.assertFalse(app.exception)
                self.assertEqual(cases.list_cases(), [])
                self.assertTrue(any("Hãy xác nhận" in item.value for item in app.error))
                app.checkbox(key="quick_godaddy_confirm_0").check()
                next(button for button in app.button if button.label == "Lưu Case ID sau khi đã gửi").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(cases.list_cases()[0]["case_id"], "GD-12345")
        finally:
            st.cache_resource.clear()

    def test_provider_replies_shows_saved_case_without_imap_sync(self):
        from streamlit.testing.v1 import AppTest

        page = Path(__file__).resolve().parents[1] / "pages" / "9_Provider_Replies.py"
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(cases, "CASE_PATH", str(Path(folder) / "cases.json")), \
                patch("phishing_toolkit.load_config", return_value={"smtp_accounts": [
                    {"username": "reporter@example.com", "imap_host": "imap.example.com"},
                ]}), \
                patch("provider_replies.load_mail_cache", return_value=[]):
            cases.record_case("reporter@example.com", "GD-12345", "https://bad.example/login")
            app = AppTest.from_file(str(page)).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("Case GoDaddy" in item.value for item in app.subheader))
            self.assertTrue(any("GD-12345" in str(frame.value) for frame in app.dataframe))
            next(item for item in app.selectbox if item.label == "Trạng thái vừa thấy trên portal GoDaddy").set_value("in_review")
            next(item for item in app.button if item.label == "Lưu trạng thái portal cho case đã chọn").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(cases.list_cases()[0]["status"], "in_review")


if __name__ == "__main__":
    unittest.main()

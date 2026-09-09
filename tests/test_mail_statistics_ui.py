import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import mail_statistics
import phishing_toolkit as pt


ROOT = Path(__file__).resolve().parents[1]


class MailStatisticsUiTests(unittest.TestCase):
    def test_page_reloads_stale_mail_statistics_module(self):
        account = {
            "imap_host": "mail.example.test", "username": "sender@example.test",
            "password": "secret",
        }
        with (
            patch.object(pt, "load_config", return_value={"smtp_accounts": [account]}),
            patch.object(mail_statistics, "MODULE_VERSION", 0),
            patch("importlib.reload", return_value=mail_statistics) as reload_module,
            patch.object(mail_statistics, "load_cached_statistics", return_value=[]),
            patch.object(mail_statistics, "latest_statistics_job", return_value=None),
        ):
            app = AppTest.from_file(
                str(ROOT / "pages" / "11_Mail_Statistics.py"), default_timeout=10,
            ).run()
        self.assertEqual(list(app.exception), [])
        reload_module.assert_called_once_with(mail_statistics)

    def test_stale_pre_schema_result_is_not_rendered_as_success(self):
        account = {
            "imap_host": "mail.example.test", "username": "sender@example.test",
            "password": "secret",
        }
        with (
            patch.object(pt, "load_config", return_value={"smtp_accounts": [account]}),
            patch.object(mail_statistics, "load_cached_statistics", return_value=[]),
            patch.object(mail_statistics, "latest_statistics_job", return_value=None),
        ):
            app = AppTest.from_file(str(ROOT / "pages" / "11_Mail_Statistics.py"), default_timeout=10)
            app.session_state["mail_statistics_result"] = [{
                "account": "old@example.test", "received": 0, "sent": 0, "junk": 0,
                "error": "old error without status",
            }]
            app.session_state["mail_statistics_day"] = date.today().isoformat()
            app.run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.metric), 0)
        self.assertEqual(len(app.dataframe), 0)

    def test_defaults_to_today_and_only_reads_after_button_click(self):
        account = {
            "imap_host": "mail.example.test", "username": "sender@example.test",
            "password": "secret",
        }
        rows = [
            {"account": "sender@example.test", "received": 7, "sent": 4, "junk": 3, "status": "ok", "error": ""},
            {"account": "smtp-only@gmail.com", "received": 0, "sent": 0, "junk": 0, "status": "not_configured", "error": ""},
        ]
        with (
            patch.object(pt, "load_config", return_value={"smtp_accounts": [account]}),
            patch.object(mail_statistics, "latest_statistics_job", side_effect=[None, None, {"state": "complete"}]),
            patch.object(mail_statistics, "create_statistics_job", return_value="job.json") as creator,
            patch.object(mail_statistics, "launch_statistics_job") as launcher,
            patch.object(mail_statistics, "load_cached_statistics", side_effect=[[], rows]),
        ):
            app = AppTest.from_file(str(ROOT / "pages" / "11_Mail_Statistics.py"), default_timeout=10).run()
            self.assertEqual(list(app.exception), [])
            self.assertEqual(app.date_input[0].value, date.today())
            next(button for button in app.button if button.label == "Kiểm tra mail nhận").click().run()
            app.run()

        self.assertEqual(list(app.exception), [])
        creator.assert_called_once_with(date.today(), [account])
        launcher.assert_called_once_with("job.json")
        self.assertEqual(app.metric[0].value, "7")
        self.assertEqual(app.metric[1].value, "3")
        self.assertEqual(app.metric[2].value, "10")
        self.assertEqual(app.metric[3].value, "1/2")
        self.assertEqual(len(app.dataframe[0].value), 2)
        self.assertEqual(app.dataframe[0].value.iloc[0]["Tổng mail nhận"], "10")
        self.assertEqual(app.dataframe[0].value.iloc[1]["Tổng mail nhận"], "—")
        self.assertEqual(app.dataframe[0].value.iloc[1]["Trạng thái"], "Không có trong IMAP")
        self.assertEqual(list(app.error), [])

    def test_check_job_uses_only_selected_account(self):
        accounts = [
            {"imap_host": "mail.example.test", "username": "a@example.test", "password": "secret"},
            {"imap_host": "mail.example.test", "username": "b@example.test", "password": "secret"},
        ]
        with (
            patch.object(pt, "load_config", return_value={"smtp_accounts": accounts}),
            patch.object(mail_statistics, "latest_statistics_job", return_value=None),
            patch.object(mail_statistics, "load_cached_statistics", return_value=[]),
            patch.object(mail_statistics, "create_statistics_job", return_value="job.json") as creator,
            patch.object(mail_statistics, "launch_statistics_job") as launcher,
        ):
            app = AppTest.from_file(str(ROOT / "pages" / "11_Mail_Statistics.py"), default_timeout=10).run()
            self.assertEqual(list(app.exception), [])
            self.assertEqual(len(app.selectbox), 1)
            app.selectbox[0].select("b@example.test").run()
            next(button for button in app.button if button.label == "Kiểm tra mail nhận").click().run()
        creator.assert_called_once_with(date.today(), [accounts[1]])
        launcher.assert_called_once_with("job.json")

    def test_page_does_not_render_report_effectiveness_panel(self):
        account = {
            "imap_host": "mail.example.test", "username": "sender@example.test",
            "password": "secret",
        }
        cached = [{
            "account": "sender@example.test", "received": 1, "sent": 9, "junk": 0,
            "status": "ok", "error": "",
        }]
        with (
            patch.object(pt, "load_config", return_value={"smtp_accounts": [account]}),
            patch.object(mail_statistics, "latest_statistics_job", return_value=None),
            patch.object(mail_statistics, "load_cached_statistics", return_value=cached),
        ):
            app = AppTest.from_file(str(ROOT / "pages" / "11_Mail_Statistics.py"), default_timeout=10).run()
        self.assertEqual(list(app.exception), [])
        self.assertFalse(any("Hiệu quả report" in item.value for item in app.subheader))
        self.assertFalse(any("Mail Sent" in item.label for item in app.metric))

    def test_cached_day_renders_without_imap_and_clear_button_removes_it(self):
        account = {
            "imap_host": "mail.example.test", "username": "sender@example.test",
            "password": "secret",
        }
        rows = [{
            "account": "sender@example.test", "received": 8, "sent": 2,
            "junk": 5, "status": "ok", "error": "",
        }]
        with (
            patch.object(pt, "load_config", return_value={"smtp_accounts": [account]}),
            patch.object(mail_statistics, "daily_mail_statistics") as loader,
            patch.object(mail_statistics, "load_cached_statistics", side_effect=[rows, []]),
            patch.object(mail_statistics, "clear_cached_statistics", return_value=True) as clearer,
            patch.object(mail_statistics, "latest_statistics_job", return_value=None),
        ):
            app = AppTest.from_file(str(ROOT / "pages" / "11_Mail_Statistics.py"), default_timeout=10).run()
            self.assertEqual(app.metric[0].value, "8")
            loader.assert_not_called()
            next(button for button in app.button if button.label == "Xóa cache ngày đã chọn").click().run()
        clearer.assert_called_once_with(date.today())
        self.assertEqual(len(app.metric), 0)


if __name__ == "__main__":
    unittest.main()

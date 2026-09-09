import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import general_statistics as gs
import phishing_toolkit as pt


ROOT = Path(__file__).resolve().parents[1]


def _snapshot(account="a@example.test"):
    return {
        "version": 2,
        "status": "complete",
        "account": account,
        "date_from": date.today().isoformat(),
        "date_to": date.today().isoformat(),
        "updated_at": "2026-09-09T01:00:00+00:00",
        "mail": {"received": 7, "sent": 4, "junk": 3, "status": "ok", "error": ""},
        "sent_sync": {"scanned": 4, "matched": 4, "with_images": 3, "errors": 0, "success": True},
        "reply_sync": {
            "matched": 2,
            "folders": [
                {"folder": "Inbox", "matched": 2, "status": "Thành công"},
                {"folder": "Thư rác", "matched": 0, "status": "Thành công"},
            ],
            "used_cache_fallback": False,
        },
        "report": {
            "sent_total": 3,
            "sent_success": 3,
            "linked_reply_total": 2,
            "resolved_total": 1,
            "evidence": {"automatic": 2, "manual": 1, "mixed": 0, "none": 0, "unknown": 0, "with_images": 3},
            "by_channel": [], "by_provider": [], "by_subject": [], "by_draft": [],
            "outcomes": {}, "links": [], "warnings": [],
        },
        "errors": [],
    }


class GeneralStatisticsUiTests(unittest.TestCase):
    def test_page_loads_without_external_imap_activity(self):
        account = {"imap_host": "imap.example.test", "username": "a@example.test", "password": "secret"}
        with (
            patch.object(pt, "load_config", return_value={"smtp_accounts": [account]}),
            patch.object(gs, "load_snapshot", return_value=None),
            patch.object(gs, "sync_account_general_statistics") as sync,
        ):
            app = AppTest.from_file(
                str(ROOT / "pages" / "13_General_Statistics.py"), default_timeout=10,
            ).run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.button), 2)
        sync.assert_not_called()
        build_spec = (ROOT / "PhishingTool.spec").read_text(encoding="utf-8")
        self.assertIn('(\"general_statistics.py\", \".\")', build_spec)

    def test_sync_is_scoped_to_selected_account_and_range(self):
        accounts = [
            {"imap_host": "imap.example.test", "username": "a@example.test", "password": "secret"},
            {"imap_host": "imap.example.test", "username": "b@example.test", "password": "secret"},
        ]
        snapshot = _snapshot("b@example.test")
        with (
            patch.object(pt, "load_config", return_value={"smtp_accounts": accounts}),
            patch.object(gs, "load_snapshot", return_value=None),
            patch.object(gs, "sync_account_general_statistics", return_value=snapshot) as sync,
        ):
            app = AppTest.from_file(
                str(ROOT / "pages" / "13_General_Statistics.py"), default_timeout=10,
            ).run()
            app.selectbox[0].select("b@example.test").run()
            next(button for button in app.button if button.label == "Đồng bộ & tính thống kê").click().run()

        self.assertEqual(list(app.exception), [])
        sync.assert_called_once()
        self.assertEqual(sync.call_args.args[0], accounts[1])
        self.assertEqual(sync.call_args.args[1:], (date.today(), date.today()))
        self.assertEqual(app.metric[0].value, "7")
        self.assertEqual(app.metric[1].value, "4")
        self.assertEqual(app.metric[2].value, "3")
        self.assertTrue(any("Đã đồng bộ đủ" in item.value for item in app.success))

    def test_stale_unsanitized_session_snapshot_is_not_rendered(self):
        account = {"imap_host": "imap.example.test", "username": "a@example.test", "password": "secret"}
        with (
            patch.object(pt, "load_config", return_value={"smtp_accounts": [account]}),
            patch.object(gs, "load_snapshot", return_value=None),
        ):
            app = AppTest.from_file(
                str(ROOT / "pages" / "13_General_Statistics.py"), default_timeout=10,
            )
            app.session_state["general_statistics_selection"] = (
                f"a@example.test|{date.today().isoformat()}|{date.today().isoformat()}"
            )
            app.session_state["general_statistics_snapshot"] = {
                **_snapshot(), "version": 1, "unsafe_error": "secret",
            }
            app.run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.metric), 0)


if __name__ == "__main__":
    unittest.main()

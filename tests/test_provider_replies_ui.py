import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import phishing_toolkit as pt
import provider_replies as pr


ROOT = Path(__file__).resolve().parents[1]


class ProviderRepliesUiTests(unittest.TestCase):
    def test_page_loads_without_external_imap_or_browser_activity(self):
        account = {
            "imap_host": "mail.example.test", "host": "mail.example.test",
            "username": "sender@example.test", "password": "secret",
        }
        with (
            patch.object(pt, "load_config", return_value={"smtp_accounts": [account]}),
            patch.object(pr, "load_mail_cache", return_value=[]),
            patch.object(pr, "capture_dom_link_evidence") as capture,
        ):
            app = AppTest.from_file(
                str(ROOT / "pages" / "9_Provider_Replies.py"), default_timeout=10,
            ).run()
        self.assertEqual([], list(app.exception))
        capture.assert_not_called()

    def test_provider_page_uses_shared_capture_state_and_exact_attachment_pair(self):
        source = (ROOT / "pages" / "9_Provider_Replies.py").read_text(encoding="utf-8")
        self.assertIn("browser_evidence_attachment_paths", source)
        self.assertIn("st.session_state[browser_evidence_key] = dom_capture", source)
        self.assertIn('st.session_state.pop(f"{key}_urlscan_result", None)', source)
        self.assertIn("attachments = browser_attachments or", source)
        self.assertIn("has_valid_screenshot = bool(browser_attachments or has_legacy_screenshot)", source)
        self.assertIn('width=520', source)
        self.assertNotIn('caption="Ảnh sẽ được đính kèm email", use_container_width=True', source)


if __name__ == "__main__":
    unittest.main()

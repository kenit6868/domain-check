import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import cloudflare_form_worker as cfw
import phishing_toolkit as pt


ROOT = Path(__file__).resolve().parents[1]


class CloudflareFormWorkerUiTests(unittest.TestCase):
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
            self.assertEqual(app.title[0].value, "Cloudflare Form Worker")
            self.assertTrue(any("Chưa" in item.value or "Nhập" in item.value for item in app.info))


if __name__ == "__main__":
    unittest.main()

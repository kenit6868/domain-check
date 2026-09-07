import unittest
from pathlib import Path
from unittest.mock import patch

import phishing_toolkit as pt


class QuickReportRestoreTests(unittest.TestCase):
    def test_cdn_check_returns_all_webform_routing_fields_without_name_error(self):
        whois = {
            "registrar": "NameSilo, LLC",
            "name_servers": ["alice.ns.cloudflare.com"],
        }
        registry = {
            "registry": "Test Registry",
            "report_webform": "https://registry.example.test/report",
        }
        with (
            patch.object(pt, "get_whois_info", return_value=whois),
            patch.object(pt, "is_cloudflare", return_value=True),
            patch.object(pt, "detect_cdn", return_value=["akamai"]),
            patch.object(pt, "_static_registry_lookup", return_value=registry),
            patch.object(pt, "run_cloaking_check", return_value={"verdict": "NO_SIGNAL"}),
        ):
            result = pt.run_cdn_check("https://example.test/path", {})

        self.assertEqual("example.test", result["domain"])
        self.assertTrue(result["cloudflare"])
        self.assertEqual(["akamai"], result["cdn_detected"])
        self.assertEqual("NameSilo, LLC", result["registrar"])
        self.assertEqual(registry, result["registry_contact"])

    def test_page_invalidates_stale_runtime_results_after_core_fix(self):
        source = (
            Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_QUICK_REPORT_RUNTIME_VERSION = 2", source)
        self.assertIn('st.session_state.get("quick_report_runtime_version")', source)
        self.assertIn("cache.clear()", source)


if __name__ == "__main__":
    unittest.main()

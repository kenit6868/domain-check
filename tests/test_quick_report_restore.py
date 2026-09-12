import unittest
import ast
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

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

    def test_page_shows_copy_ready_registry_webform_text(self):
        source = (
            Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py"
        ).read_text(encoding="utf-8")
        self.assertIn("get_registry_webform_draft_text", source)
        self.assertIn("target_url=original_url", source)
        self.assertIn("st.code(registry_text", source)

    def test_page_uses_profile_extension_for_browser_blocking_forms(self):
        source = (
            Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_ENABLE_PLAYWRIGHT_FORM_AUTOMATION = False", source)
        self.assertIn("_GSB_REPORT_URL", source)
        self.assertIn("_MICROSOFT_REPORT_URL", source)
        self.assertIn("def _report_form_url", source)
        self.assertIn("urlencode({'url': target_url})", source)
        self.assertIn('"google_gsb", _report_form_url(_GSB_REPORT_URL, original_url)', source)
        self.assertIn('"microsoft_smartscreen", _report_form_url(_MICROSOFT_REPORT_URL, original_url)', source)
        self.assertNotIn('cfg, language="Vietnamese"', source)
        self.assertIn("Mở & tự điền Google", source)
        self.assertIn("Mở & tự điền Microsoft", source)
        self.assertIn("threat_type=threat, threat_category=category", source)
        self.assertIn("render_community_report_buttons(", source)
        self.assertIn("generate_community_report_text", source)
        self.assertIn("draft=community_text", source)

    def test_manual_form_link_encodes_the_full_reported_url(self):
        page_path = Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py"
        tree = ast.parse(page_path.read_text(encoding="utf-8"))
        helper = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_report_form_url"
        )
        namespace = {"urlencode": urlencode}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), str(page_path), "exec"), namespace)

        result = namespace["_report_form_url"](
            "https://example.test/report",
            "https://phish.example/vi-vn/?campaign=1&source=tool",
        )
        self.assertEqual(
            "https://example.test/report?url=https%3A%2F%2Fphish.example%2Fvi-vn%2F%3Fcampaign%3D1%26source%3Dtool",
            result,
        )

    def test_cloudflare_button_uses_profile_extension_without_legacy_link(self):
        source = (Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py").read_text(encoding="utf-8")
        self.assertIn("open_quick_report_form(original_url, cf_text, cfg)", source)
        self.assertIn("Mở & tự điền Cloudflare Abuse", source)
        self.assertNotIn('st.link_button("↗ Cloudflare Abuse"', source)

    def test_godaddy_registrar_uses_fill_only_extension_adapter(self):
        source = (Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py").read_text(encoding="utf-8")
        self.assertIn('"godaddy_phishing", webform_url_r', source)
        self.assertIn('"Mở & tự điền form GoDaddy"', source)
        self.assertIn('if "godaddy" in r_lower:', source)


if __name__ == "__main__":
    unittest.main()

import unittest
import ast
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import phishing_toolkit as pt


class QuickReportRestoreTests(unittest.TestCase):
    def test_cdn_check_returns_all_webform_routing_fields_without_name_error(self):
        whois = {
            "registrar": "Example Registrar, LLC",
            "name_servers": ["alice.ns.cloudflare.com"],
        }
        registry = {
            "registry": "Test Registry",
            "report_webform": "https://registry.example.test/report",
        }
        with (
            patch.object(pt, "get_whois_info", return_value=whois),
            patch.object(pt, "get_rdap_abuse_email", return_value={"abuse_email": "abuse@namesilo.com"}),
            patch.object(pt, "is_cloudflare", return_value=True),
            patch.object(pt, "detect_cdn", return_value=["akamai"]),
            patch.object(pt, "_static_registry_lookup", return_value=registry),
            patch.object(pt, "run_cloaking_check", return_value={"verdict": "NO_SIGNAL"}),
        ):
            result = pt.run_cdn_check("https://example.test/path", {})

        self.assertEqual("example.test", result["domain"])
        self.assertTrue(result["cloudflare"])
        self.assertEqual(["akamai"], result["cdn_detected"])
        self.assertEqual("Example Registrar, LLC", result["registrar"])
        self.assertEqual(registry, result["registry_contact"])
        self.assertEqual("abuse@namesilo.com", result["report_recipients"][0]["email"])

    def test_page_invalidates_stale_runtime_results_after_core_fix(self):
        source = (
            Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_QUICK_REPORT_RUNTIME_VERSION = 3", source)
        self.assertIn('cache.get("runtime_version")', source)
        self.assertIn("cache.clear()", source)

    def test_refresh_pending_results_and_clear_cache(self):
        import tempfile
        from concurrent.futures import Future, ThreadPoolExecutor
        from unittest.mock import Mock
        import streamlit as st
        from streamlit.testing.v1 import AppTest

        page = Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py"
        pending = Future()
        executor = Mock()
        executor.submit.return_value = pending
        st.cache_resource.clear()
        try:
            with tempfile.TemporaryDirectory() as folder, \
                    patch.object(pt, "_runtime_path", side_effect=lambda name: str(Path(folder) / name)), \
                    patch.object(pt, "load_config", return_value={}), \
                    patch("concurrent.futures.ThreadPoolExecutor", side_effect=lambda *a, **kw:
                          executor if kw.get("thread_name_prefix") == "quick-report"
                          else ThreadPoolExecutor(*a, **kw)):
                app = AppTest.from_file(str(page)).run()
                self.assertFalse(app.exception)
                app.text_area(key="qr_domain_input").set_value("https://example.test/login")
                next(b for b in app.button if "Kiểm tra tất cả" in b.label).click().run()
                self.assertFalse(app.exception)
                executor.submit.assert_called_once()

                # A fresh session models F5; the pending job must survive too.
                refreshed = AppTest.from_file(str(page)).run()
                self.assertFalse(refreshed.exception)
                self.assertEqual(refreshed.text_area(key="qr_domain_input").value,
                                 "https://example.test/login")
                self.assertFalse(pending.cancelled())
                pending.set_result({"domain": "example.test", "cloudflare": False, "_error": "cached test result"})
                refreshed.run()
                self.assertTrue(any("cached test result" in w.value for w in refreshed.warning))
                again = AppTest.from_file(str(page)).run()
                self.assertTrue(any("cached test result" in w.value for w in again.warning))
                next(b for b in again.button if "Xóa cache" in b.label).click().run()
                self.assertFalse(again.exception)
                self.assertEqual(again.text_area(key="qr_domain_input").value, "")
                empty = AppTest.from_file(str(page)).run()
                self.assertFalse(empty.exception)
                self.assertFalse(any("cached test result" in w.value for w in empty.warning))
                self.assertEqual(empty.text_area(key="qr_domain_input").value, "")
                executor.submit.assert_called_once()
        finally:
            st.cache_resource.clear()

    def test_completed_batch_stops_polling_and_form_buttons_can_repeat(self):
        import tempfile
        from concurrent.futures import Future, ThreadPoolExecutor
        from unittest.mock import Mock
        import streamlit as st
        from streamlit.testing.v1 import AppTest

        page = Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py"
        result = Future()
        result.set_result({"domain": "example.test", "cloudflare": True,
                           "registrar": "GoDaddy.com, LLC"})
        executor = Mock()
        executor.submit.return_value = result
        st.cache_resource.clear()
        try:
            with tempfile.TemporaryDirectory() as folder, \
                    patch.object(pt, "_runtime_path", side_effect=lambda name: str(Path(folder) / name)), \
                    patch.object(pt, "load_config", return_value={}), \
                    patch("concurrent.futures.ThreadPoolExecutor", side_effect=lambda *a, **kw:
                          executor if kw.get("thread_name_prefix") == "quick-report"
                          else ThreadPoolExecutor(*a, **kw)), \
                    patch("cloudflare_form_worker.open_profile_form", return_value={"status": "opened"}) as opener:
                app = AppTest.from_file(str(page)).run()
                app.text_area(key="qr_domain_input").set_value("https://example.test/login")
                next(b for b in app.button if "Kiểm tra tất cả" in b.label).click().run()
                self.assertFalse(app.exception)
                for _ in range(2):
                    for key in ("gsb_profile_0", "ms_profile_0"):
                        app.button(key=key).click().run()
                        self.assertFalse(app.exception)
                self.assertEqual(opener.call_count, 4)
                self.assertEqual([c.args[0] for c in opener.call_args_list],
                                 ["google_gsb", "microsoft_smartscreen"] * 2)
                executor.submit.assert_called_once()
        finally:
            st.cache_resource.clear()

        # The timer is conditional and domain actions have their own fragment.
        tree = ast.parse(page.read_text(encoding="utf-8"))
        block = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name == "_render_domain_block")
        self.assertIn("st.fragment", [ast.unparse(d) for d in block.decorator_list])
        render = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                      and n.name == "_render_results")
        self.assertIn("run_every=1 if _results_polling else None",
                      ast.unparse(render.decorator_list[0]))

    def test_page_shows_copy_ready_registry_webform_text(self):
        source = (
            Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py"
        ).read_text(encoding="utf-8")
        self.assertIn("get_registry_webform_draft_text", source)
        self.assertIn("target_url=original_url", source)
        self.assertIn("st.code(registry_text", source)

    def test_page_shows_lightweight_report_emails_before_cloaking(self):
        source = (
            Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py"
        ).read_text(encoding="utf-8")
        self.assertIn('result.get("report_recipients")', source)
        self.assertIn('st.caption(f"Email tố cáo: {contact_text}")', source)
        self.assertLess(source.index('result.get("report_recipients")'), source.index("render_cloaking_details(cloaking)"))

    def test_lightweight_recipients_include_registrar_and_registry_without_hosting(self):
        recipients = pt.resolve_quick_report_recipients(
            "example.test",
            who={"registrar": "Example Registrar", "emails": ["abuse@registrar.test"]},
            registry_contact={"abuse_email": "abuse@registry.test"},
            rdap={},
        )
        self.assertEqual(
            [
                {"channel": "registrar", "label": "Registrar", "email": "abuse@registrar.test"},
                {"channel": "registry", "label": "Registry", "email": "abuse@registry.test"},
            ],
            recipients,
        )

    def test_cdn_check_does_not_run_rdap_for_registrar_webform(self):
        with (
            patch.object(pt, "get_whois_info", return_value={"registrar": "GoDaddy.com, LLC"}),
            patch.object(pt, "get_rdap_abuse_email") as rdap,
            patch.object(pt, "is_cloudflare", return_value=False),
            patch.object(pt, "detect_cdn", return_value=[]),
            patch.object(pt, "_static_registry_lookup", return_value=None),
            patch.object(pt, "run_cloaking_check", return_value={"verdict": "NO_SIGNAL"}),
        ):
            result = pt.run_cdn_check("example.test", {})
        rdap.assert_not_called()
        self.assertEqual([], result["report_recipients"])

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

    def test_registry_co_uses_fill_only_extension_adapter(self):
        source = (Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py").read_text(encoding="utf-8")
        self.assertIn('"registry_co_phishing", registry_form_url', source)
        self.assertIn('"Mở & tự điền form Registry Co"', source)
        self.assertIn('if "registry.co/report-abuse/form" in registry_form_url.lower():', source)

    def test_xyz_registry_uses_fill_only_extension_adapter(self):
        source = (Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py").read_text(encoding="utf-8")
        self.assertIn('"xyz_registry_abuse", registry_form_url', source)
        self.assertIn('"Mở & tự điền form XYZ.COM"', source)
        self.assertIn('elif "gen.xyz/account/submitticket.php" in registry_form_url.lower():', source)

    def test_xyz_registry_uses_current_anti_abuse_ticket_url(self):
        expected = "https://gen.xyz/account/submitticket.php?step=2&deptid=6"
        for suffix in (
            "xyz", "monster", "quest", "baby", "cars", "beauty", "hair",
            "homes", "game", "lol", "mom", "pics", "hosting", "audio",
            "diet", "ceo",
        ):
            self.assertEqual(expected, pt.CCTLD_REGISTRY_CONTACTS[suffix]["report_webform"])

    def test_form_status_messages_are_statements_not_magic_rendered_expressions(self):
        page = Path(__file__).resolve().parents[1] / "pages" / "7_Quick_Report.py"
        tree = ast.parse(page.read_text(encoding="utf-8"))
        conditional_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.IfExp)
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "st"
                for child in ast.walk(node)
            )
        ]
        self.assertEqual([], conditional_calls)


if __name__ == "__main__":
    unittest.main()

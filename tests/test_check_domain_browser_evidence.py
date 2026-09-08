import json
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import phishing_toolkit as pt
import browser_evidence
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class CheckDomainBrowserEvidenceTests(unittest.TestCase):
    def test_quality_gate_blocks_missing_subject_url_placeholder_and_not_flagged(self):
        errors = pt.validate_report_delivery({
            "to": "abuse@example.test", "subject": "",
            "body": (
                "Reported URL: https://wrong.test/\n"
                "--- Evidence: URLScan.io Analysis ---\nVerdict: NOT flagged\n"
                "--- End URLScan Evidence ---\n[PLEASE ADD PROOF]"
            ),
        }, target_url="https://source.test/path")
        self.assertTrue(any("Subject" in error for error in errors))
        self.assertTrue(any("full Reported URL" in error for error in errors))
        self.assertTrue(any("NOT flagged" in error for error in errors))
        self.assertTrue(any("URLScan" in error and "nội bộ" in error for error in errors))
        self.assertTrue(any("placeholder" in error for error in errors))

    def test_quality_gate_accepts_one_to_three_images_with_valid_manifest(self):
        parsed = {
            "to": "abuse@example.test", "subject": "Phishing report",
            "body": "Reported URL: https://source.test/path",
        }
        errors = pt.validate_report_delivery(
            parsed, target_url="https://source.test/path", attachments=[],
            require_browser_evidence=True,
        )
        self.assertTrue(any("1 đến 3 ảnh" in error for error in errors))
        with tempfile.TemporaryDirectory() as folder:
            evidence = pt.browser_evidence.create_manual_browser_evidence(
                "https://source.test/path",
                [("proof.png", b"\x89PNG\r\n\x1a\nproof")], folder,
            )
            self.assertEqual([], pt.validate_report_delivery(
                parsed, target_url="https://source.test/path",
                attachments=pt.browser_evidence.evidence_attachment_paths(evidence),
                require_browser_evidence=True,
            ))

    def test_quality_gate_rejects_evidence_from_another_full_url(self):
        parsed = {
            "to": "abuse@example.test", "subject": "Phishing report",
            "body": "Reported URL: https://source.test/path-b",
        }
        with tempfile.TemporaryDirectory() as folder:
            evidence = pt.browser_evidence.create_manual_browser_evidence(
                "https://source.test/path-a",
                [("proof.png", b"\x89PNG\r\n\x1a\nproof")], folder,
            )
            errors = pt.validate_report_delivery(
                parsed, target_url="https://source.test/path-b",
                attachments=pt.browser_evidence.evidence_attachment_paths(evidence),
                require_browser_evidence=True,
            )
        self.assertTrue(any("full Reported URL hiện tại" in error for error in errors))

    def test_append_browser_evidence_replaces_previous_block(self):
        block = "--- Technical Evidence: Read-only Browser Inspection ---\nNew proof\n--- End of Browser Evidence ---"
        with tempfile.TemporaryDirectory() as folder:
            draft = os.path.join(folder, "report.txt")
            with open(draft, "w", encoding="utf-8") as handle:
                handle.write(
                    "Subject: Report\n\nBody\n\n"
                    "--- Evidence: URLScan.io Analysis ---\nOld URLScan\n"
                    "--- End URLScan Evidence ---\n\n"
                    "--- Technical Evidence: Read-only Browser Inspection ---\n"
                    "Old proof\n--- End of Browser Evidence ---\n"
                )
            with patch.object(pt.browser_evidence, "format_email_evidence_block", return_value=block):
                self.assertEqual([draft], pt.append_browser_evidence_to_drafts([draft], {"success": True}))
            content = Path(draft).read_text(encoding="utf-8")
            self.assertNotIn("Old proof", content)
            self.assertNotIn("Old URLScan", content)
            self.assertEqual(1, content.count("Read-only Browser Inspection"))

    def test_append_browser_evidence_does_not_partially_update_when_a_draft_is_missing(self):
        block = "--- Technical Evidence: Read-only Browser Inspection ---\nProof\n--- End of Browser Evidence ---"
        with tempfile.TemporaryDirectory() as folder:
            draft = os.path.join(folder, "report.txt")
            Path(draft).write_text("Subject: Report\n\nOriginal\n", encoding="utf-8")
            missing = os.path.join(folder, "missing.txt")
            with patch.object(pt.browser_evidence, "format_email_evidence_block", return_value=block):
                self.assertEqual([], pt.append_browser_evidence_to_drafts([draft, missing], {"success": True}))
            self.assertEqual("Subject: Report\n\nOriginal\n", Path(draft).read_text(encoding="utf-8"))

    def test_supporting_evidence_is_inserted_before_signature(self):
        block = (
            "--- Observed Phishing Behavior and Supporting Evidence ---\n"
            "Steps to reproduce:\n1. Visit the reported URL.\n"
            "--- End of Supporting Evidence ---"
        )
        with tempfile.TemporaryDirectory() as folder:
            draft = os.path.join(folder, "report.txt")
            Path(draft).write_text(
                "To: abuse@example.test\nSubject: Report\n\nPlease investigate.\n\n"
                "Regards,\nReporter\n",
                encoding="utf-8",
            )
            with patch.object(pt.browser_evidence, "format_email_evidence_block", return_value=block):
                self.assertEqual([draft], pt.append_browser_evidence_to_drafts([draft], {"success": True}))
            content = Path(draft).read_text(encoding="utf-8")
            self.assertLess(content.index("Observed Phishing Behavior"), content.index("Regards,"))

    def test_append_browser_evidence_replaces_verified_navigation_block(self):
        block = "--- Technical Evidence: Verified Browser Navigation ---\nNew proof\n--- End of Verified Browser Evidence ---"
        with tempfile.TemporaryDirectory() as folder:
            draft = os.path.join(folder, "report.txt")
            with open(draft, "w", encoding="utf-8") as handle:
                handle.write(
                    "Subject: Report\n\nBody\n\n"
                    "--- Technical Evidence: Verified Browser Navigation ---\n"
                    "Old verified proof\n--- End of Verified Browser Evidence ---\n"
                )
            with patch.object(pt.browser_evidence, "format_email_evidence_block", return_value=block):
                self.assertEqual([draft], pt.append_browser_evidence_to_drafts([draft], {"success": True}))
            content = Path(draft).read_text(encoding="utf-8")
            self.assertNotIn("Old verified proof", content)
            self.assertEqual(1, content.count("Verified Browser Navigation"))

    def test_check_domain_page_wires_evidence_into_every_send_action(self):
        source = (ROOT / "pages" / "1_Check_Domain.py").read_text(encoding="utf-8")
        email_ui = (ROOT / "email_send_ui.py").read_text(encoding="utf-8")
        toolkit = (ROOT / "phishing_toolkit.py").read_text(encoding="utf-8")
        self.assertIn("capture_passive_browser_evidence", source)
        self.assertIn("capture_dom_destination_evidence", source)
        self.assertIn("st.segmented_control", source)
        self.assertIn("URL đích cuối", source)
        self.assertIn("create_manual_browser_evidence", source)
        self.assertIn("Ảnh bằng chứng thủ công (1–3 ảnh)", source)
        self.assertGreaterEqual(source.count("require_browser_evidence=True"), 2)
        self.assertIn("attachments=browser_attachments", source)
        self.assertNotIn("append_urlscan_evidence_to_drafts(result.get", source)
        self.assertIn("attachments=attachments", email_ui)
        phase_comment = toolkit.index("# Phase 3: URLScan")
        return_block = toolkit.index('return {\n        "domain": domain', phase_comment)
        self.assertNotIn("append_urlscan_evidence_to_drafts", toolkit[phase_comment:return_block])

    def test_check_domain_verified_mode_captures_and_renders_preview(self):
        target = "https://source.example/path"
        with tempfile.TemporaryDirectory() as folder:
            source_path = os.path.join(folder, "source.png")
            destination_path = os.path.join(folder, "destination.png")
            for path in (source_path, destination_path):
                with open(path, "wb") as handle:
                    handle.write(PNG_1X1)
            manifest_path = os.path.join(folder, "verified.json")
            manifest = {
                "version": pt.browser_evidence.EVIDENCE_VERSION,
                "evidence_type": "dom_destination_opened",
                "navigation_verified": False,
                "destination_opened": True,
                "observed_at": "2026-09-08T00:00:00+00:00",
                "profile": {"name": "check_domain_dom_destination", "user_agent": "test"},
                "requested_url": target,
                "landing_url": target,
                "final_url": target + "/register",
                "http": {"success": True, "final_url": target, "final_status": 200, "redirect_chain": []},
                "navigation": {
                    "mode": "new_tab_direct", "source_url": target,
                    "destination_url": target + "/register", "redirect_chain": [],
                },
                "page": {"title": "Source"},
                "destination_page": {"title": "Destination"},
                "control": {
                    "found": True, "label": "Register",
                    "resolved_destination": target + "/register",
                },
                "screenshots": [
                    {
                        "role": "source_before_open", "path": source_path,
                        "size": os.path.getsize(source_path),
                        "sha256": pt.browser_evidence._sha256(source_path),
                    },
                    {
                        "role": "destination_after_open", "path": destination_path,
                        "size": os.path.getsize(destination_path),
                        "sha256": pt.browser_evidence._sha256(destination_path),
                    },
                ],
            }
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            captured = {
                "success": True, "terminal": False,
                "evidence_type": "dom_destination_opened", "navigation_verified": False,
                "destination_opened": True,
                "requested_url": target, "landing_url": target,
                "final_url": target + "/register", "control_found": True,
                "control_label": "Register", "resolved_destination": target + "/register",
                "screenshot_path": source_path,
                "screenshot_paths": [source_path, destination_path],
                "manifest_path": manifest_path,
                "navigation": manifest["navigation"],
            }
            draft_path = os.path.join(folder, "source.example_registrar_report.txt")
            with open(draft_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "To: abuse@example.test\nSubject: Phishing report\n\n"
                    f"Reported URL: {target}\n"
                )
            result = {
                "domain": "source.example", "target_url": target,
                "cert": {}, "whois": {}, "cloudflare": False,
                "cdn_detected": [], "origin_ip_scan": {}, "origin_ip_whois": {},
                "virustotal": {}, "safebrowsing": {}, "ca_note": None,
                "http_check": {}, "cloaking": {"target_url": target, "verdict": "NO_SIGNAL"},
                "domain_age_days": None, "mx_records": {"records": [], "providers": []},
                "urlscan": {}, "reputation": {"verdict": "unknown", "label": "Unknown", "reasons": []},
                "drafts": [draft_path], "drafts_error": "", "log_error": "",
                "virustotal_submit": None, "registry_contact": {"source": "not_found"},
                "registrar_abuse_email_source": None, "registrar_abuse_email_used": None,
            }
            cfg = {"smtp_accounts": [], "brand_name": "Example"}
            with (
                patch.object(pt, "load_config", return_value=cfg),
                patch.object(
                    browser_evidence, "capture_dom_destination_evidence",
                    return_value=captured,
                ) as capture,
            ):
                app = AppTest.from_file(
                    str(ROOT / "pages" / "1_Check_Domain.py"), default_timeout=10,
                )
                app.session_state["check_domain_result"] = result
                app.session_state["check_domain_cfg"] = cfg
                app = app.run()
                mode = next(control for control in app.segmented_control if control.label == "Phương thức capture")
                app = mode.set_value("Mở URL từ DOM").run()
                action = next(button for button in app.button if button.label == "Mở URL từ DOM và chụp 2 ảnh")
                app = action.click().run()

            self.assertEqual([], list(app.exception))
            capture.assert_called_once()
            self.assertEqual("https://source.example/path/register", app.session_state["check_domain_browser_evidence"]["final_url"])
            self.assertTrue(any("Đã mở URL lấy từ DOM" in item.value for item in app.success))
            self.assertTrue(any("URL đích cuối" in item.value for item in app.markdown))

    def test_check_domain_page_loads_without_starting_capture(self):
        with patch.object(pt.browser_evidence, "capture_passive_browser_evidence") as capture:
            app = AppTest.from_file(
                str(ROOT / "pages" / "1_Check_Domain.py"), default_timeout=10,
            ).run()
        self.assertEqual([], list(app.exception))
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()

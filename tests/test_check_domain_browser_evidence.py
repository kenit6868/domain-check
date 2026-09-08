import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import phishing_toolkit as pt
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


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

    def test_check_domain_page_wires_evidence_into_every_send_action(self):
        source = (ROOT / "pages" / "1_Check_Domain.py").read_text(encoding="utf-8")
        email_ui = (ROOT / "email_send_ui.py").read_text(encoding="utf-8")
        toolkit = (ROOT / "phishing_toolkit.py").read_text(encoding="utf-8")
        self.assertIn("capture_passive_browser_evidence", source)
        self.assertIn("create_manual_browser_evidence", source)
        self.assertIn("Ảnh bằng chứng thủ công (1–3 ảnh)", source)
        self.assertGreaterEqual(source.count("require_browser_evidence=True"), 2)
        self.assertIn("attachments=browser_attachments", source)
        self.assertNotIn("append_urlscan_evidence_to_drafts(result.get", source)
        self.assertIn("attachments=attachments", email_ui)
        phase_comment = toolkit.index("# Phase 3: URLScan")
        return_block = toolkit.index('return {\n        "domain": domain', phase_comment)
        self.assertNotIn("append_urlscan_evidence_to_drafts", toolkit[phase_comment:return_block])

    def test_check_domain_page_loads_without_starting_capture(self):
        with patch.object(pt.browser_evidence, "capture_passive_browser_evidence") as capture:
            app = AppTest.from_file(
                str(ROOT / "pages" / "1_Check_Domain.py"), default_timeout=10,
            ).run()
        self.assertEqual([], list(app.exception))
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()

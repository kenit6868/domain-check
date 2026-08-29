import os
import tempfile
import unittest
from email import message_from_string
from email.policy import default
from unittest.mock import Mock, patch

import phishing_toolkit as pt


class EmailAttachmentTests(unittest.TestCase):
    def test_single_sender_attaches_cloaking_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence_path = os.path.join(temp_dir, "cloaking-evidence.json")
            with open(evidence_path, "w", encoding="utf-8") as evidence_file:
                evidence_file.write('{"verdict":"LIKELY"}')

            smtp = Mock()
            smtp.__enter__ = Mock(return_value=smtp)
            smtp.__exit__ = Mock(return_value=False)
            with (
                patch.object(pt.smtplib, "SMTP", return_value=smtp),
                patch.object(pt, "_imap_save_sent", return_value=None),
            ):
                result = pt.send_report_email_single(
                    "abuse@example.net", "Cloaking evidence", "Evidence is attached.",
                    {
                        "username": "sender@example.org", "password": "secret",
                        "host": "smtp.example.org", "port": 587,
                    },
                    attachments=[evidence_path],
                )

            self.assertTrue(result["success"])
            raw_message = smtp.sendmail.call_args.args[2]
            parsed = message_from_string(raw_message, policy=default)
            attachments = list(parsed.iter_attachments())
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0].get_filename(), "cloaking-evidence.json")
            self.assertEqual(attachments[0].get_content_type(), "application/json")
            self.assertEqual(attachments[0].get_content().strip(), b'{"verdict":"LIKELY"}')

    def test_missing_evidence_attachment_fails_closed_before_smtp(self):
        smtp = Mock()
        with patch.object(pt.smtplib, "SMTP", return_value=smtp):
            result = pt.send_report_email_single(
                "abuse@example.net", "Report", "Body",
                {"username": "sender@example.org", "password": "secret"},
                attachments=[os.path.join(tempfile.gettempdir(), "definitely-missing-cloaking.json")],
            )
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])
        smtp.assert_not_called()


if __name__ == "__main__":
    unittest.main()

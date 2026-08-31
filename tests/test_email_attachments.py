import os
import smtplib
import tempfile
import unittest
from unittest.mock import Mock, patch

import phishing_toolkit as pt


class EmailAttachmentTests(unittest.TestCase):
    def test_single_sender_attaches_cloaking_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence_path = os.path.join(temp_dir, "cloaking-evidence.json")
            with open(evidence_path, "w", encoding="utf-8") as evidence_file:
                evidence_file.write('{"verdict":"LIKELY"}')

            smtp = Mock()
            with (
                patch.object(pt.smtplib, "SMTP", return_value=smtp) as smtp_factory,
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
            self.assertEqual(result["attempts"], 1)
            smtp_factory.assert_called_once_with("smtp.example.org", 587, timeout=60.0)
            smtp.starttls.assert_called_once_with()
            sent_message = smtp.send_message.call_args.args[0]
            attachments = list(sent_message.iter_attachments())
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0].get_filename(), "cloaking-evidence.json")
            self.assertEqual(attachments[0].get_content_type(), "application/json")
            self.assertEqual(attachments[0].get_content().strip(), b'{"verdict":"LIKELY"}')
            self.assertEqual(smtp.send_message.call_args.kwargs["from_addr"], "sender@example.org")
            self.assertEqual(smtp.send_message.call_args.kwargs["to_addrs"], ["abuse@example.net"])

    def test_port_465_uses_implicit_tls_without_starttls(self):
        smtp = Mock()
        with (
            patch.object(pt.smtplib, "SMTP_SSL", return_value=smtp) as ssl_factory,
            patch.object(pt.smtplib, "SMTP") as starttls_factory,
            patch.object(pt, "_imap_save_sent", return_value=None),
        ):
            result = pt.send_report_email_single(
                "abuse@example.net", "Report", "Body",
                {
                    "username": "sender@example.org", "password": "secret",
                    "host": "mail.example.org", "port": 465,
                },
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["transport"], "implicit_tls")
        ssl_factory.assert_called_once_with("mail.example.org", 465, timeout=30.0)
        starttls_factory.assert_not_called()
        smtp.starttls.assert_not_called()
        smtp.send_message.assert_called_once()

    def test_custom_port_can_disable_starttls(self):
        smtp = Mock()
        with (
            patch.object(pt.smtplib, "SMTP", return_value=smtp) as smtp_factory,
            patch.object(pt.smtplib, "SMTP_SSL") as ssl_factory,
            patch.object(pt, "_imap_save_sent", return_value=None),
        ):
            result = pt.send_report_email_single(
                "abuse@example.net", "Report", "Body",
                {
                    "username": "sender@example.org", "password": "secret",
                    "host": "smtp.example.org", "port": 2525, "starttls": False,
                },
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["transport"], "plain")
        smtp_factory.assert_called_once_with("smtp.example.org", 2525, timeout=30.0)
        ssl_factory.assert_not_called()
        smtp.starttls.assert_not_called()
        smtp.send_message.assert_called_once()

    def test_transient_disconnect_reconnects_and_retries_once(self):
        first = Mock()
        first.send_message.side_effect = smtplib.SMTPServerDisconnected("connection closed")
        second = Mock()
        with (
            patch.object(pt.smtplib, "SMTP", side_effect=[first, second]) as smtp_factory,
            patch.object(pt, "_imap_save_sent", return_value=None),
        ):
            result = pt.send_report_email_single(
                "abuse@example.net", "Report", "Body",
                {
                    "username": "sender@example.org", "password": "secret",
                    "host": "smtp.example.org", "port": 587,
                },
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(smtp_factory.call_count, 2)
        first.send_message.assert_called_once()
        second.send_message.assert_called_once()
        first_message = first.send_message.call_args.args[0]
        second_message = second.send_message.call_args.args[0]
        self.assertIs(first_message, second_message)
        self.assertTrue(first_message["Message-ID"])

    def test_large_cloaking_evidence_set_keeps_manifest_and_two_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = os.path.join(temp_dir, "cloaking-evidence.json")
            desktop_path = os.path.join(temp_dir, "desktop.png")
            mobile_path = os.path.join(temp_dir, "mobile.png")
            with open(manifest_path, "wb") as handle:
                handle.write(b'{"verdict":"LIKELY"}')
            with open(desktop_path, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\n" + b"d" * (3 * 1024 * 1024))
            with open(mobile_path, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\n" + b"m" * (1024 * 1024))

            smtp = Mock()
            with (
                patch.object(pt.smtplib, "SMTP", return_value=smtp) as smtp_factory,
                patch.object(pt, "_imap_save_sent", return_value=None),
            ):
                result = pt.send_report_email_single(
                    "abuse@example.net", "Cloaking report", "Evidence is attached.",
                    {
                        "username": "sender@example.org", "password": "secret",
                        "host": "smtp.example.org", "port": 587,
                    },
                    attachments=[manifest_path, desktop_path, mobile_path],
                )

        self.assertTrue(result["success"])
        smtp_factory.assert_called_once_with("smtp.example.org", 587, timeout=60.0)
        sent_message = smtp.send_message.call_args.args[0]
        attachments = list(sent_message.iter_attachments())
        self.assertEqual(len(attachments), 3)
        self.assertEqual(
            [part.get_content_type() for part in attachments],
            ["application/json", "image/png", "image/png"],
        )
        self.assertEqual(
            [part.get_filename() for part in attachments],
            ["cloaking-evidence.json", "desktop.png", "mobile.png"],
        )

    def test_authentication_error_is_not_retried(self):
        smtp = Mock()
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Authentication failed")
        with (
            patch.object(pt.smtplib, "SMTP", return_value=smtp) as smtp_factory,
            patch.object(pt, "_imap_save_sent", return_value=None),
        ):
            result = pt.send_report_email_single(
                "abuse@example.net", "Report", "Body",
                {
                    "username": "sender@example.org", "password": "secret",
                    "host": "smtp.example.org", "port": 587,
                },
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["stage"], "authenticate")
        self.assertEqual(smtp_factory.call_count, 1)
        smtp.send_message.assert_not_called()

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

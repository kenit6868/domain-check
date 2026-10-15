from __future__ import annotations

import smtplib
import unittest
from unittest.mock import MagicMock, patch

from domaincheck.core.mail import ImapConnectionSpec, SmtpConnectionSpec, StandardMailConnectionTester


class MailConnectivityTests(unittest.TestCase):
    def setUp(self):
        self.tester = StandardMailConnectionTester()

    def test_starttls_authenticates_without_sending(self):
        server = MagicMock()
        with patch("domaincheck.core.mail.connectivity.smtplib.SMTP", return_value=server) as factory:
            result = self.tester.test_smtp(SmtpConnectionSpec(
                host="smtp.example.test", port=587, username="sender@example.test",
                password="fixture-secret", transport="starttls",
            ))
        self.assertEqual(result.status, "success")
        factory.assert_called_once()
        self.assertEqual(factory.call_args.kwargs["timeout"], 30.0)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("sender@example.test", "fixture-secret")
        server.send_message.assert_not_called()

    def test_implicit_tls_never_calls_starttls_or_send(self):
        server = MagicMock()
        with patch("domaincheck.core.mail.connectivity.smtplib.SMTP_SSL", return_value=server) as factory:
            result = self.tester.test_smtp(SmtpConnectionSpec(
                host="smtp.example.test", port=465, username="sender@example.test",
                password="fixture-secret", transport="implicit_tls",
            ))
        self.assertEqual(result.transport, "implicit_tls")
        factory.assert_called_once()
        server.starttls.assert_not_called()
        server.send_message.assert_not_called()

    def test_plain_transport_is_only_used_when_explicit(self):
        server = MagicMock()
        with patch("domaincheck.core.mail.connectivity.smtplib.SMTP", return_value=server):
            result = self.tester.test_smtp(SmtpConnectionSpec(
                host="smtp.example.test", port=2525, username="sender@example.test",
                password="fixture-secret", transport="plain",
            ))
        self.assertEqual(result.status, "success")
        server.starttls.assert_not_called()
        server.login.assert_called_once()
        server.send_message.assert_not_called()

    def test_imap_only_authenticates_and_does_not_read_mailbox(self):
        server = MagicMock()
        with patch("domaincheck.core.mail.connectivity.imaplib.IMAP4_SSL", return_value=server) as factory:
            result = self.tester.test_imap(ImapConnectionSpec(
                host="imap.example.test", port=993, username="sender@example.test",
                password="fixture-secret",
            ))
        self.assertEqual(result.status, "success")
        factory.assert_called_once()
        self.assertEqual(factory.call_args.kwargs["timeout"], 30.0)
        server.login.assert_called_once_with("sender@example.test", "fixture-secret")
        server.select.assert_not_called()
        server.list.assert_not_called()
        server.search.assert_not_called()
        server.fetch.assert_not_called()

    def test_failure_is_classified_without_leaking_exception_or_password(self):
        with patch(
            "domaincheck.core.mail.connectivity.smtplib.SMTP",
            side_effect=smtplib.SMTPAuthenticationError(535, b"fixture-secret private detail"),
        ):
            result = self.tester.test_smtp(SmtpConnectionSpec(
                host="smtp.example.test", port=587, username="sender@example.test",
                password="fixture-secret", transport="starttls",
            ))
        self.assertEqual(result.code, "authentication_failed")
        self.assertNotIn("fixture-secret", repr(result))
        self.assertNotIn("private detail", result.message)


if __name__ == "__main__":
    unittest.main()

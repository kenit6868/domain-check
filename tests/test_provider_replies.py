import unittest
import os
import tempfile
from email.message import EmailMessage
from unittest.mock import MagicMock, patch
import provider_replies as pr
from provider_replies import ACTION_REQUIRED_TYPES, build_reply, extract_reply_context, parse_message, received_datetime

class ProviderReplyTests(unittest.TestCase):
    def make_mail(self, sender, subject, body):
        msg = EmailMessage(); msg["From"] = sender; msg["To"] = "reporter@example.com"; msg["Subject"] = subject; msg["Message-ID"] = "<case-1@example.com>"; msg.set_content(body)
        return parse_message("12", "reporter@example.com", msg.as_bytes())

    def test_dynadot_full_url_request(self):
        mail = self.make_mail("Dynadot Abuse <abuse@dynadot.com>", "Case DYN-1234", "Please provide the full URL.")
        self.assertEqual((mail.provider, mail.request_type, mail.channel), ("dynadot", "full_url", "email")); self.assertEqual(mail.ticket, "DYN-1234")

    def test_legal_request_is_not_invented(self):
        mail = self.make_mail("abuse@namecheap.com", "More information", "Please provide proof of trademark ownership.")
        _, body, warnings = build_reply(mail, {"contact_name": "Alice"})
        self.assertEqual(mail.risk, "approval_required"); self.assertIn("only be supplied after authorization", body); self.assertTrue(warnings)

    def test_missing_url_has_placeholder(self):
        mail = self.make_mail("abuse@spaceship.com", "Request", "Please provide the complete URL.")
        _, body, warnings = build_reply(mail, {})
        self.assertIn("[PLEASE ADD", body); self.assertTrue(warnings)

    def test_received_date_can_be_used_for_daily_filter(self):
        msg = EmailMessage(); msg["From"] = "abuse@namecheap.com"; msg["To"] = "reporter@example.com"
        msg["Subject"] = "Case update"; msg["Date"] = "Sun, 16 Aug 2026 09:30:00 +0700"; msg.set_content("We received your report.")
        mail = parse_message("13", "reporter@example.com", msg.as_bytes())
        self.assertEqual(received_datetime(mail).date().isoformat(), "2026-08-16")

    def test_completed_notifications_are_not_action_required(self):
        mail = self.make_mail("report@netcraft.com", "We've finished analysing your submission", "The case is closed.")
        self.assertNotIn(mail.request_type, ACTION_REQUIRED_TYPES)

    def test_mail_cache_survives_reload_and_can_be_cleared(self):
        mail = self.make_mail("abuse@dynadot.com", "Need more information", "Please provide the full URL.")
        with tempfile.TemporaryDirectory() as folder:
            cache_path = os.path.join(folder, "mail-cache.json")
            with patch.object(pr, "CACHE_PATH", cache_path):
                pr.save_mail_cache("reporter@example.com", [mail])
                loaded = pr.load_mail_cache("reporter@example.com")
                self.assertEqual(len(loaded), 1)
                self.assertEqual(loaded[0].subject, mail.subject)
                pr.clear_mail_cache("reporter@example.com")
                self.assertEqual(pr.load_mail_cache("reporter@example.com"), [])

    def test_cloudflare_previous_report_is_prefilled_and_replyable(self):
        body = """Cloudflare received your Phishing report regarding: leswiki[.]nl.
If you have questions, please send an email to abusereply@cloudflare.com.
Report ID: f5e71b6a71721bf4
Logs or other evidence of abuse: The domain leswiki[.]nl is impersonating my brand.
This domain is using Cloudflare services."""
        mail = self.make_mail("Cloudflare <noreply@notify.cloudflare.com>", "Response to your Phishing report", body)
        context = extract_reply_context(mail)
        self.assertEqual(mail.reply_to, "abusereply@cloudflare.com")
        self.assertEqual(mail.channel, "email")
        self.assertEqual(mail.ticket, "f5e71b6a71721bf4")
        self.assertEqual(context["reported_url"], "https://leswiki.nl")
        self.assertIn("impersonating my brand", context["evidence"])

    def test_full_obfuscated_reported_url_wins_over_root_domain(self):
        body = """Cloudflare received your Phishing report regarding: leswiki[.]nl.
Logs or other evidence of abuse: The domain leswiki[.]nl is impersonating my brand.
Reported URLs:
hxxps://leswiki[.]nl/vi-vn/
Cloudflare is not the hosting provider of the reported content.
Cloudflare Trust & Safety"""
        mail = self.make_mail("noreply@notify.cloudflare.com", "Response to your report", body)
        context = extract_reply_context(mail)
        self.assertEqual(context["reported_url"], "https://leswiki.nl/vi-vn/")
        self.assertNotIn("Cloudflare is not", context["evidence"])
        self.assertNotIn("Reported URLs", context["evidence"])

    def test_screenshot_reply_has_attachment(self):
        mail = self.make_mail("abuse@spaceship.com", "Please provide screenshot", "Please provide a screenshot.")
        with tempfile.TemporaryDirectory() as folder:
            image_path = os.path.join(folder, "evidence.png")
            with open(image_path, "wb") as handle: handle.write(b"fake-png-data")
            smtp = MagicMock()
            with patch.object(pr.smtplib, "SMTP_SSL", return_value=smtp), patch.object(pr, "_append_sent_copy", return_value=""):
                result = pr.send_threaded_reply(
                    {"host": "mail.example.com", "port": 465, "ssl": True, "username": "reporter@example.com", "password": "secret"},
                    mail, "Re: screenshot", "Attached.", attachments=[image_path],
                )
            self.assertTrue(result["success"])
            self.assertTrue(result["sent_copy_saved"])
            sent_message = smtp.send_message.call_args.args[0]
            self.assertEqual(len(list(sent_message.iter_attachments())), 1)

    def test_bounce_is_not_treated_as_provider_request(self):
        mail = self.make_mail(
            "MAILER-DAEMON@mail.example.com", "Undelivered Mail Returned to Sender",
            "Your message to abuse@namecheap.com was rejected. Original request: please provide screenshot.",
        )
        self.assertEqual(mail.request_type, "delivery_failed")
        self.assertEqual(mail.provider_label, "Mail server")
        self.assertEqual(mail.channel, "no_reply")
        self.assertNotIn(mail.request_type, ACTION_REQUIRED_TYPES)

    def test_old_cached_bounce_is_migrated_and_blocked(self):
        stale = self.make_mail("MAILER-DAEMON@mail.example.com", "Undelivered Mail Returned to Sender", "Original message to abuse@namecheap.com")
        stale.provider = "namecheap"; stale.provider_label = "Namecheap"; stale.request_type = "screenshot"; stale.request_label = "Ảnh chụp"
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(pr, "CACHE_PATH", os.path.join(folder, "cache.json")):
                pr.save_mail_cache("reporter@example.com", [stale])
                loaded = pr.load_mail_cache("reporter@example.com")[0]
        self.assertEqual(loaded.request_type, "delivery_failed")
        self.assertEqual(loaded.reply_to, "")

    def test_old_cloudflare_cache_gets_reply_route(self):
        body = "For questions, please send an email to abusereply@cloudflare.com with the report ID. Please provide additional evidence."
        mail = self.make_mail("noreply@notify.cloudflare.com", "Response to your report", body)
        mail.reply_to = "noreply@notify.cloudflare.com"; mail.channel = "no_reply"
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(pr, "CACHE_PATH", os.path.join(folder, "cache.json")):
                pr.save_mail_cache("reporter@example.com", [mail])
                loaded = pr.load_mail_cache("reporter@example.com")[0]
        self.assertEqual(loaded.reply_to, "abusereply@cloudflare.com")
        self.assertEqual(loaded.channel, "email")

    def test_imap_list_parser_does_not_treat_dot_separator_as_sent_folder(self):
        flags, delimiter, mailbox = pr._parse_imap_list_line(b'(\\HasNoChildren \\Sent) "." Sent')
        self.assertIn("\\Sent", flags)
        self.assertEqual(delimiter, ".")
        self.assertEqual(mailbox, "Sent")

    def test_acknowledgement_with_quoted_screenshot_text_is_not_actionable(self):
        body = """Thank you. We received your report.
Below is the report we received:
Screenshots: Please attach screenshot evidence."""
        mail = self.make_mail("abuse@dynadot.com", "Abuse Complaint Submitted", body)
        self.assertEqual(mail.request_type, "acknowledgement")
        self.assertNotIn(mail.request_type, ACTION_REQUIRED_TYPES)

    def test_request_before_quoted_report_remains_actionable(self):
        body = """We could not verify the reported content. Please provide additional evidence.
Below is the report we received:
Original report details."""
        mail = self.make_mail("noreply@notify.cloudflare.com", "Response to your report", body)
        self.assertEqual(mail.request_type, "technical_evidence")
        self.assertIn(mail.request_type, ACTION_REQUIRED_TYPES)

if __name__ == "__main__": unittest.main()

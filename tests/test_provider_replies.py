import unittest
import os
import tempfile
from email.message import EmailMessage
from unittest.mock import MagicMock, patch
import provider_replies as pr
from provider_replies import ACTION_REQUIRED_TYPES, build_reply, build_reply_vi, extract_reply_context, load_reply_log, needs_reply, parse_message, provider_message_vi, received_datetime, record_reply_sent, reply_log_key, save_uploaded_evidence

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

    def test_imap_server_date_has_priority_over_sender_date(self):
        mail = self.make_mail("abuse@namecheap.com", "Case update", "We received your report.")
        mail.date = "Mon, 17 Aug 2026 22:00:00 +0000"
        mail.server_date = "18-Aug-2026 05:00:03 +0700"
        value = received_datetime(mail)
        self.assertEqual(value.strftime("%Y-%m-%d %H:%M:%S %z"), "2026-08-18 05:00:03 +0700")

    def test_imap_internal_date_is_extracted_from_fetch_metadata(self):
        metadata = b'1064 (UID 1064 INTERNALDATE "18-Aug-2026 12:17:04 +0700" BODY[] {1234}'
        self.assertEqual(pr._imap_internal_date(metadata), "18-Aug-2026 12:17:04 +0700")

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

    def test_fetch_provider_mail_all_folders_includes_junk_and_statistics(self):
        inbox_mail = self.make_mail("abuse@dynadot.com", "Inbox response", "Please provide the full URL")
        junk_mail = self.make_mail("abuse@cloudflare.com", "Junk response", "Please provide additional evidence")
        account = {"username": "reporter@example.com", "imap_mailbox": "INBOX"}
        with (
            patch.object(pr, "discover_junk_mailbox", return_value="Junk Email"),
            patch.object(pr, "fetch_provider_mail", side_effect=[[inbox_mail], [junk_mail]]) as fetch,
        ):
            mails, statistics = pr.fetch_provider_mail_all_folders(account)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual([mail.source_mailbox for mail in mails], ["INBOX", "Junk Email"])
        self.assertEqual([(row["folder"], row["matched"]) for row in statistics], [("Inbox", 1), ("Thư rác", 1)])

    def test_fetch_provider_mail_all_folders_keeps_inbox_when_junk_fails(self):
        inbox_mail = self.make_mail("abuse@dynadot.com", "Inbox response", "Please provide the full URL")
        account = {"username": "reporter@example.com", "imap_mailbox": "INBOX"}
        with (
            patch.object(pr, "discover_junk_mailbox", return_value="Spam"),
            patch.object(pr, "fetch_provider_mail", side_effect=[[inbox_mail], RuntimeError("denied")]),
        ):
            mails, statistics = pr.fetch_provider_mail_all_folders(account)
        self.assertEqual(mails, [inbox_mail])
        self.assertEqual(statistics[1]["matched"], 0)
        self.assertIn("denied", statistics[1]["status"])

    def test_mark_seen_groups_same_uid_by_source_mailbox(self):
        inbox_mail = self.make_mail("abuse@dynadot.com", "Inbox", "Please provide the full URL")
        junk_mail = self.make_mail("abuse@cloudflare.com", "Junk", "Please provide additional evidence")
        inbox_mail.uid = junk_mail.uid = "7"
        inbox_mail.source_mailbox = "INBOX"
        junk_mail.source_mailbox = "Junk"

        class SeenImap:
            def __init__(self): self.selected = []; self.stored = []
            def login(self, *_): return "OK", []
            def select(self, mailbox, readonly=False): self.selected.append((mailbox, readonly)); return "OK", []
            def uid(self, command, *args): self.stored.append((command, args)); return "OK", []
            def logout(self): return "OK", []

        fake = SeenImap()
        account = {"imap_host": "mail.example.test", "username": "reporter@example.com", "password": "secret"}
        with patch.object(pr.imaplib, "IMAP4_SSL", return_value=fake):
            result = pr.mark_mails_seen(account, [inbox_mail, junk_mail])
        self.assertTrue(result["success"])
        self.assertEqual(result["marked"], 2)
        self.assertEqual(fake.selected, [("INBOX", False), ("Junk", False)])
        self.assertEqual([call[1][0] for call in fake.stored], ["7", "7"])

    def test_mark_seen_skips_non_numeric_cached_uid_before_imap_command(self):
        valid = self.make_mail("abuse@dynadot.com", "Valid", "Please provide the full URL")
        invalid = self.make_mail("abuse@cloudflare.com", "Invalid", "Please provide additional evidence")
        valid.uid = "954"
        invalid.uid = "954©"

        class SeenImap:
            def login(self, *_): return "OK", []
            def select(self, *_args, **_kwargs): return "OK", []
            def uid(self, command, *args):
                self.command = (command, args)
                return "OK", []
            def logout(self): return "OK", []

        fake = SeenImap()
        account = {"imap_host": "mail.example.test", "username": "reporter@example.com", "password": "secret"}
        with patch.object(pr.imaplib, "IMAP4_SSL", return_value=fake):
            result = pr.mark_mails_seen(account, [valid, invalid])
        self.assertTrue(result["success"])
        self.assertEqual((result["marked"], result["skipped"]), (1, 1))
        self.assertEqual(fake.command[1][0], "954")

    def test_acknowledgement_with_quoted_screenshot_text_is_not_actionable(self):
        body = """Thank you. We received your report.
Below is the report we received:
Screenshots: Please attach screenshot evidence."""
        mail = self.make_mail("abuse@dynadot.com", "Abuse Complaint Submitted", body)
        self.assertEqual(mail.request_type, "acknowledgement")
        self.assertNotIn(mail.request_type, ACTION_REQUIRED_TYPES)
        self.assertFalse(needs_reply(mail))

    def test_namecheap_is_excluded_even_when_it_requests_evidence(self):
        mail = self.make_mail(
            "abuse@namecheap.com", "Additional evidence required",
            "Please provide proof of trademark ownership.",
        )
        self.assertFalse(needs_reply(mail))

    def test_spaceship_receipt_with_legal_footer_is_not_actionable(self):
        mail = self.make_mail(
            "legal@spaceship.com", "Abuse report received",
            """We confirm receipt of your report. It will be reviewed following our legal policies.
            When replying, please ensure the engagement ID remains in the subject line.
            Our team will contact you if additional information is required.""",
        )
        self.assertFalse(needs_reply(mail))

    def test_dynadot_receipt_with_additional_information_notice_is_not_actionable(self):
        mail = self.make_mail(
            "abuse@dynadot.com", "Abuse Complaint Received",
            "We will investigate. Please note we cannot disclose additional information without a court order.",
        )
        self.assertFalse(needs_reply(mail))

    def test_non_cloudflare_explicit_evidence_request_is_actionable(self):
        mail = self.make_mail(
            "abuse@nic.top", "More evidence required",
            "The website cannot be opened. Please provide more evidence to prove it is phishing.",
        )
        self.assertTrue(needs_reply(mail))

    def test_receipt_followed_by_real_evidence_request_is_actionable(self):
        mail = self.make_mail(
            "abuse@dynadot.com", "Report received",
            "We received your report. Please provide additional evidence so we can investigate.",
        )
        self.assertEqual(mail.request_type, "technical_evidence")
        self.assertTrue(needs_reply(mail))

    def test_request_before_quoted_report_remains_actionable(self):
        body = """We could not verify the reported content. Please provide additional evidence.
Below is the report we received:
Original report details."""
        mail = self.make_mail("noreply@notify.cloudflare.com", "Response to your report", body)
        self.assertEqual(mail.request_type, "technical_evidence")
        self.assertIn(mail.request_type, ACTION_REQUIRED_TYPES)

    def test_cloudflare_forwarded_notice_does_not_need_reply(self):
        body = """Hello,
Cloudflare received your Phishing report regarding: hct369[.]com[.]tw.
Your abuse report has been forwarded to the website owner.
We have also forwarded this report to the relevant hosting provider.
To respond to this issue, please reply to abusereply@cloudflare.com.
Below is the report we received:
Logs or other evidence of abuse: Please suspend this phishing domain.
"""
        mail = self.make_mail(
            "Cloudflare <noreply@notify.cloudflare.com>",
            "[d5f83a187b4344a1]: Response to your Phishing report",
            body,
        )
        self.assertEqual(mail.request_type, "acknowledgement")
        self.assertNotIn(mail.request_type, ACTION_REQUIRED_TYPES)

    def test_cloudflare_could_not_detect_abuse_requires_reply(self):
        body = """Hello,
Cloudflare received your Phishing report regarding: yypydw[.]pw.
We could not detect any abusive or malicious content. If you wish for Cloudflare
to investigate further, please provide relevant and specific information so that
we can continue assessing this case.
If you have questions about this abuse report, please send an email to
abusereply@cloudflare.com with the following details:
- The report identification number included in the subject line
- Any additional details, context or evidence you can provide regarding the content that was reported.
Below is the report we received:
Logs or other evidence of abuse: Original reporter text.
"""
        mail = self.make_mail(
            "Cloudflare <noreply@notify.cloudflare.com>",
            "[5b8c7c2dedb6dbc3]: Response to your Phishing report",
            body,
        )
        self.assertEqual(mail.request_type, "clarification")
        self.assertIn(mail.request_type, ACTION_REQUIRED_TYPES)
        self.assertTrue(needs_reply(mail))
        self.assertEqual(mail.reply_to, "abusereply@cloudflare.com")
        self.assertEqual(mail.channel, "email")
        self.assertEqual(mail.ticket, "5b8c7c2dedb6dbc3")

        subject, reply, warnings = build_reply(mail, {
            "reported_url": "https://yypydw.pw/vi-vn/",
            "redirect_url": "https://redirect-phishing.example/register",
            "button_label": "Register/Login",
            "evidence": "The registration control was tested manually in a browser.",
            "screenshot_attached": True,
        })
        self.assertEqual(subject, "Re: Phishing Report - Report ID 5b8c7c2dedb6dbc3")
        self.assertIn("redirect-phishing.example/register", reply)
        self.assertIn('href="https://redirect-phishing.example/register"', reply)
        self.assertIn("Steps to reproduce", reply)
        self.assertIn("Register/Login", reply)
        self.assertIn("corresponding DOM element", reply)
        self.assertIn("relationship between yypydw.pw and redirect-phishing.example", reply)
        self.assertEqual(warnings, [])
        self.assertIn("Cloudflare chưa phát hiện", provider_message_vi(mail))
        reply_vi = build_reply_vi(mail, {
            "reported_url": "https://yypydw.pw/vi-vn/",
            "redirect_url": "https://redirect-phishing.example/register",
            "button_label": "Register/Login",
            "screenshot_attached": True,
        })
        self.assertIn("Các bước tái hiện", reply_vi)
        self.assertIn("element DOM", reply_vi)
        self.assertIn("redirect-phishing.example", reply_vi)

        _, incomplete_reply, incomplete_warnings = build_reply(mail, {
            "reported_url": "https://yypydw.pw/vi-vn/",
            "redirect_url": "",
            "evidence": "Verified phishing page.",
            "screenshot_attached": True,
        })
        self.assertNotIn("[PLEASE", incomplete_reply)
        self.assertNotIn("href=", incomplete_reply)
        self.assertNotIn("Steps to reproduce", incomplete_reply)
        self.assertTrue(incomplete_warnings)

    def test_uploaded_evidence_accepts_real_png_signature(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(pr, "EVIDENCE_DIR", folder):
                path = save_uploaded_evidence("proof.png", b"\x89PNG\r\n\x1a\nimage", "example.com")
                self.assertTrue(os.path.isfile(path))

    def test_uploaded_evidence_rejects_fake_image(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(pr, "EVIDENCE_DIR", folder):
                with self.assertRaises(ValueError):
                    save_uploaded_evidence("proof.png", b"not-an-image", "example.com")

    def test_successful_reply_status_is_persisted_without_body(self):
        mail = self.make_mail("abuse@namecheap.com", "Case NC-1234", "Please provide the full URL.")
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(pr, "REPLY_LOG_PATH", os.path.join(folder, "reply-log.json")):
                record = record_reply_sent(mail, "Re: Case NC-1234", "abuse@namecheap.com")
                log = load_reply_log()
        self.assertEqual(log[reply_log_key(mail)]["recipient"], "abuse@namecheap.com")
        self.assertEqual(record["ticket"], "NC-1234")
        self.assertNotIn("body", record)

    def test_sent_message_matches_original_thread_or_ticket(self):
        mail = self.make_mail(
            "Cloudflare <noreply@notify.cloudflare.com>",
            "[1977667f35362d26]: Response to your Phishing report",
            "Please provide additional evidence.",
        )
        threaded = EmailMessage()
        threaded["Subject"] = "Re: Phishing Report - Report ID 1977667f35362d26"
        threaded["In-Reply-To"] = mail.message_id
        self.assertTrue(pr._sent_message_matches(mail, threaded))
        ticket_only = EmailMessage()
        ticket_only["Subject"] = "Re: Phishing Report - Report ID 1977667f35362d26"
        self.assertTrue(pr._sent_message_matches(mail, ticket_only))
        unrelated = EmailMessage(); unrelated["Subject"] = "Unrelated message"
        self.assertFalse(pr._sent_message_matches(mail, unrelated))

if __name__ == "__main__": unittest.main()

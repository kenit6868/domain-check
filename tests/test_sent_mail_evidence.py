import json
import os
import tempfile
import unittest
from datetime import date, timezone
from email.message import EmailMessage
from unittest.mock import patch

import sent_mail_evidence as sme


def _message(subject, message_id, *, to="abuse@example.test", with_evidence=False):
    message = EmailMessage()
    message["From"] = "reporter@example.test"
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = "Tue, 08 Sep 2026 08:30:00 +0700"
    message["Message-ID"] = message_id
    message.set_content("Reported URL: https://phish.example/vi-vn/\n")
    if with_evidence:
        message.add_attachment(
            b"\\x89PNG\\r\\n\\x1a\\nimage",
            maintype="image", subtype="png", filename="source.png",
        )
        manifest = json.dumps({"evidence_type": "dom_destination_opened"}).encode()
        message.add_attachment(
            manifest, maintype="application", subtype="json", filename="evidence.json",
        )
    return message.as_bytes()


class FakeSentImap:
    def __init__(self, messages):
        self.messages = messages
        self.selected = ""
        self.select_arguments = []
        self.logged_out = False

    def login(self, username, password):
        return "OK", []

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"']

    def select(self, mailbox, readonly=True):
        self.select_arguments.append(mailbox)
        self.selected = mailbox.strip('"')
        return "OK", [b"2"]

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b"1 2"]
        uid = args[0]
        internal = {
            b"1": b'1 (INTERNALDATE "08-Sep-2026 08:30:00 +0700" BODY[] {1}',
            b"2": b'2 (INTERNALDATE "07-Sep-2026 23:30:00 +0700" BODY[] {1}',
        }[uid]
        return "OK", [(internal, self.messages[uid])]

    def logout(self):
        self.logged_out = True


class SentMailEvidenceTests(unittest.TestCase):
    def test_sync_reads_sent_attachments_and_local_date_without_persisting_body(self):
        fake = FakeSentImap({
            b"1": _message("Phishing report", "<sent-1@example.test>", with_evidence=True),
            b"2": _message("Older report", "<sent-2@example.test>"),
        })
        account = {
            "imap_host": "imap.example.test", "imap_port": 993,
            "username": "a@example.test", "password": "secret",
        }
        with tempfile.TemporaryDirectory() as folder, patch.object(
            sme, "CACHE_PATH", os.path.join(folder, "sent_mail_evidence.json")
        ):
            result = sme.sync_account_sent_mail(
                account, date(2026, 9, 8), date(2026, 9, 8),
                local_tz=timezone.utc,
                imap_factory=lambda *args, **kwargs: fake,
            )
            records = sme.load_cached_records("a@example.test", date(2026, 9, 8), date(2026, 9, 8), timezone.utc)
            with open(sme.CACHE_PATH, encoding="utf-8") as handle:
                raw = json.load(handle)
        self.assertTrue(result["success"])
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["with_images"], 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["evidence_source"], "automatic")
        self.assertEqual(records[0]["evidence_images"], 1)
        self.assertTrue(records[0]["evidence_manifest"])
        self.assertEqual(records[0]["delivery_kind"], "observed_sent")
        self.assertNotIn("body", str(raw).lower())
        self.assertNotIn("secret", str(raw).lower())
        self.assertTrue(fake.logged_out)

    def test_cache_isolated_by_account_and_merges_records(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            sme, "CACHE_PATH", os.path.join(folder, "sent_mail_evidence.json")
        ):
            sme.save_cached_records("a@example.test", [{
                "account": "a@example.test", "record_id": "a1", "date": "2026-09-08",
                "timestamp": "2026-09-08T00:00:00+00:00", "evidence_source": "automatic",
                "evidence_images": 2,
            }])
            sme.save_cached_records("b@example.test", [{
                "account": "b@example.test", "record_id": "b1", "date": "2026-09-08",
                "timestamp": "2026-09-08T00:00:00+00:00", "evidence_source": "manual",
                "evidence_images": 1,
            }])
            a = sme.load_cached_records("A@EXAMPLE.TEST", date(2026, 9, 8), date(2026, 9, 8), timezone.utc)
            b = sme.load_cached_records("b@example.test", date(2026, 9, 8), date(2026, 9, 8), timezone.utc)
        self.assertEqual([row["record_id"] for row in a], ["a1"])
        self.assertEqual([row["record_id"] for row in b], ["b1"])

    def test_report_statistics_can_enrich_legacy_log_from_sent_cache(self):
        import report_statistics as report_stats
        report = report_stats.build_account_report(
            "a@example.test", date(2026, 9, 8), date(2026, 9, 8),
            sent_rows=[{
                "timestamp": "2026-09-08T01:00:00+00:00", "domain": "phish.example",
                "to": "abuse@example.test", "subject": "Phishing report",
                "account": "a@example.test", "success": "True",
            }],
            sent_mail_records=[{
                "account": "a@example.test", "record_id": "sent-1", "message_id": "<sent-1@example.test>",
                "timestamp": "2026-09-08T01:00:02+00:00", "domain": "phish.example",
                "to": "abuse@example.test", "subject": "Phishing report", "success": True,
                "delivery_kind": "observed_sent", "send_mode": "imap_sent_sync",
                "source": "imap_sent", "evidence_source": "automatic",
                "evidence_images": 2, "evidence_manifest": True,
            }], provider_mails=[], local_tz=timezone.utc,
        )
        self.assertEqual(report["sent_total"], 1)
        self.assertEqual(report["evidence"]["automatic"], 1)
        self.assertEqual(report["evidence"]["images_total"], 2)

    def test_unmatched_observed_sent_message_does_not_become_report(self):
        import report_statistics as report_stats

        report = report_stats.build_account_report(
            "a@example.test", date(2026, 9, 8), date(2026, 9, 8),
            sent_rows=[],
            sent_mail_records=[{
                "account": "a@example.test", "record_id": "ordinary-1",
                "timestamp": "2026-09-08T01:00:00+00:00", "to": "friend@example.test",
                "subject": "Normal note with https://phish.example/", "domain": "phish.example",
                "delivery_kind": "observed_sent", "send_mode": "imap_sent_sync",
                "source": "imap_sent", "success": True,
                "evidence_source": "none", "evidence_images": 0,
            }],
            provider_mails=[], local_tz=timezone.utc,
        )
        self.assertEqual(report["sent_total"], 0)
        self.assertEqual(report["source"]["observed_sent_unmatched"], 1)


if __name__ == "__main__":
    unittest.main()

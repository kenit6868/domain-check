import unittest
import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import mail_statistics as stats


class FakeImap:
    def __init__(self, metadata_as_bytes=False):
        self.selected = ""
        self.select_arguments = []
        self.logged_out = False
        self.metadata_as_bytes = metadata_as_bytes

    def login(self, username, password):
        return "OK", []

    def list(self):
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
            b'(\\HasNoChildren \\Junk) "/" "Junk Email"',
        ]

    def select(self, mailbox, readonly=True):
        self.select_arguments.append(mailbox)
        self.selected = mailbox[1:-1].replace(r'\"', '"').replace(r'\\', '\\')
        return "OK", [b"2"]

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b"1 2"]
        uid = args[0]
        values = {
            ("INBOX", b"1"): b'1 (INTERNALDATE "01-Sep-2026 00:15:00 +0700")',
            ("INBOX", b"2"): b'2 (INTERNALDATE "31-Aug-2026 23:59:00 +0700")',
            ("[Gmail]/Sent Mail", b"1"): b'1 (INTERNALDATE "31-Aug-2026 18:30:00 +0000")',
            ("[Gmail]/Sent Mail", b"2"): b'2 (INTERNALDATE "01-Sep-2026 17:01:00 +0700")',
            ("Junk Email", b"1"): b'1 (INTERNALDATE "01-Sep-2026 08:00:00 +0700")',
            ("Junk Email", b"2"): b'2 (INTERNALDATE "02-Sep-2026 00:01:00 +0700")',
        }
        metadata = values[(self.selected, uid)]
        return "OK", [metadata] if self.metadata_as_bytes else [(metadata, b"")]

    def logout(self):
        self.logged_out = True


class MailStatisticsTests(unittest.TestCase):
    def test_background_job_persists_without_credentials_and_completes_cache(self):
        account = {"username": "sender@example.test", "password": "secret", "imap_host": "mail.example.test"}
        rows = [{"account": "sender@example.test", "received": 2, "sent": 1, "junk": 3, "status": "ok", "error": ""}]
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(stats, "JOB_DIR", temp_dir):
            job_path = stats.create_statistics_job(date(2026, 9, 1), [account])
            with open(job_path, encoding="utf-8") as handle:
                persisted = json.load(handle)
            self.assertNotIn("password", persisted)
            self.assertNotIn("secret", str(persisted))
            with (
                patch("phishing_toolkit.load_config", return_value={"smtp_accounts": [account]}),
                patch.object(stats, "daily_mail_statistics", return_value=rows),
                patch.object(stats, "save_cached_statistics") as saver,
            ):
                stats.run_statistics_job(job_path)
            saver.assert_called_once()
            self.assertEqual(stats.latest_statistics_job(date(2026, 9, 1))["state"], "complete")

    def test_daily_cache_round_trip_and_clear_only_selected_day(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = os.path.join(temp_dir, "mail_statistics_cache.json")
            first_day = date(2026, 9, 1)
            second_day = date(2026, 9, 2)
            rows = [{
                "account": "sender@example.test", "received": 7, "sent": 4,
                "junk": 3, "status": "ok", "error": "",
                "password": "must-not-be-cached",
            }]
            with patch.object(stats, "CACHE_PATH", cache_path):
                stats.save_cached_statistics(first_day, rows)
                stats.save_cached_statistics(second_day, rows)
                self.assertEqual(stats.load_cached_statistics(first_day)[0]["received"], 7)
                self.assertTrue(stats.clear_cached_statistics(first_day))
                self.assertEqual(stats.load_cached_statistics(first_day), [])
                self.assertEqual(stats.load_cached_statistics(second_day)[0]["junk"], 3)
                with open(cache_path, encoding="utf-8") as handle:
                    raw = json.load(handle)
            self.assertNotIn("password", str(raw).lower())

    def test_counts_exact_local_day_in_inbox_and_flagged_sent_folder(self):
        fake = FakeImap()
        account = {
            "imap_host": "mail.example.test", "imap_port": 993,
            "username": "sender@example.test", "password": "secret",
        }
        with patch.object(stats.imaplib, "IMAP4_SSL", return_value=fake) as connect:
            result = stats.count_account_mail(
                account, date(2026, 9, 1), timezone(timedelta(hours=7)),
            )
        self.assertEqual(result["received"], 1)
        self.assertEqual(result["sent"], 2)
        self.assertEqual(result["junk"], 1)
        self.assertIn('"[Gmail]/Sent Mail"', fake.select_arguments)
        self.assertTrue(fake.logged_out)
        connect.assert_called_once_with("mail.example.test", 993, timeout=30)

    def test_incoming_count_matches_statistics_but_never_selects_sent(self):
        fake = FakeImap()
        account = {
            "imap_host": "mail.example.test", "imap_port": 993,
            "username": "sender@example.test", "password": "secret",
        }
        with patch.object(stats.imaplib, "IMAP4_SSL", return_value=fake):
            result = stats.count_account_incoming(
                account, date(2026, 9, 1), date(2026, 9, 1),
                timezone(timedelta(hours=7)),
            )
        self.assertEqual((result["received"], result["junk"]), (1, 1))
        self.assertEqual(result["junk_mailbox"], "Junk Email")
        self.assertNotIn('"[Gmail]/Sent Mail"', fake.select_arguments)

    def test_internaldate_is_converted_before_calendar_day_filter(self):
        local_tz = timezone(timedelta(hours=7))
        metadata = (b'1 (INTERNALDATE "31-Aug-2026 18:30:00 +0000")', b"")
        self.assertEqual(stats._message_local_date(metadata, local_tz), date(2026, 9, 1))

    def test_counts_plain_bytes_metadata_returned_by_real_imap_fetch(self):
        fake = FakeImap(metadata_as_bytes=True)
        account = {
            "imap_host": "mail.example.test", "username": "sender@example.test",
            "password": "secret",
        }
        with patch.object(stats.imaplib, "IMAP4_SSL", return_value=fake):
            result = stats.count_account_mail(
                account, date(2026, 9, 1), timezone(timedelta(hours=7)),
            )
        self.assertEqual(
            (result["received"], result["sent"], result["junk"]), (1, 2, 1),
        )

    def test_account_errors_are_isolated(self):
        good = {"username": "good@example.test"}
        good["imap_host"] = "mail.example.test"
        bad = {"username": "bad@example.test", "imap_host": "mail.example.test"}
        with patch.object(
            stats, "count_account_mail",
            side_effect=[{"account": "good@example.test", "received": 3, "sent": 2, "junk": 1, "status": "ok", "error": ""}, RuntimeError("offline")],
        ):
            result = stats.daily_mail_statistics([good, bad], date.today(), datetime.now().astimezone().tzinfo)
        self.assertEqual(result[0]["received"], 3)
        self.assertEqual(result[1]["error"], "offline")

    def test_account_without_explicit_imap_host_is_reported_without_connection(self):
        account = {"host": "smtp.gmail.com", "username": "smtp-only@gmail.com", "password": "secret"}
        with patch.object(stats, "count_account_mail") as counter:
            result = stats.daily_mail_statistics(
                [account], date.today(), datetime.now().astimezone().tzinfo,
            )
        counter.assert_not_called()
        self.assertEqual(result[0]["status"], "not_configured")
        self.assertEqual(result[0]["error"], "")


if __name__ == "__main__":
    unittest.main()

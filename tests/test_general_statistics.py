import json
import os
import tempfile
import unittest
from datetime import date, timezone
from types import SimpleNamespace
from unittest.mock import patch

import general_statistics as gs


class GeneralStatisticsTests(unittest.TestCase):
    def test_sync_collects_all_sources_and_persists_sanitized_snapshot(self):
        account = {
            "imap_host": "mail.example.test",
            "username": "sender@example.test",
            "password": "secret-password",
        }
        selected_day = date(2026, 9, 9)
        sent_records = [{
            "account": account["username"],
            "timestamp": "2026-09-09T01:00:00+00:00",
            "target_url": "https://phish.example/login",
            "message_id": "<sent@example.test>",
            "evidence_source": "automatic",
            "evidence_images": 2,
            "evidence_manifest": True,
        }]
        provider_mails = [SimpleNamespace(
            account=account["username"], provider="cloudflare",
            request_type="acknowledgement", sender="abuse@cloudflare.com",
            subject="Report received",
        )]
        report = {
            "sent_total": 2,
            "sent_success": 2,
            "linked_reply_total": 1,
            "resolved_total": 1,
            "evidence": {"automatic": 1, "manual": 0, "mixed": 0, "none": 1, "unknown": 0, "with_images": 1},
            "by_channel": [], "by_provider": [], "by_subject": [], "by_draft": [],
            "outcomes": {}, "links": [], "warnings": [],
        }

        def count_mail(received_account, start, end, local_tz, timeout):
            self.assertEqual(received_account, account)
            self.assertEqual((start, end, local_tz), (selected_day, selected_day, timezone.utc))
            self.assertEqual(timeout, 60)
            return {"received": 8, "sent": 4, "junk": 2, "status": "ok", "error": ""}

        def sync_sent(received_account, start, end, *, local_tz, timeout):
            self.assertEqual((received_account, start, end, local_tz, timeout), (account, selected_day, selected_day, timezone.utc, 60))
            return {"success": True, "scanned": 4, "matched": 4, "with_images": 3, "errors": []}

        def fetch_replies(received_account, **kwargs):
            self.assertEqual(received_account, account)
            self.assertEqual(kwargs["date_from"], selected_day)
            self.assertEqual(kwargs["date_to"], selected_day)
            self.assertEqual(kwargs["timeout"], 60)
            return provider_mails, [{"folder": "Inbox", "matched": 1, "status": "Thành công"}]

        def build_report(received_account, start, end, **kwargs):
            self.assertEqual((received_account, start, end), (account["username"], selected_day, selected_day))
            self.assertEqual(kwargs["sent_mail_records"], sent_records)
            self.assertEqual(kwargs["provider_mails"], provider_mails)
            return report

        with tempfile.TemporaryDirectory() as folder, patch.object(gs, "CACHE_PATH", os.path.join(folder, "general.json")):
            snapshot = gs.sync_account_general_statistics(
                account,
                selected_day,
                selected_day,
                local_tz=timezone.utc,
                count_mail_fn=count_mail,
                sent_sync_fn=sync_sent,
                sent_loader_fn=lambda *_args: sent_records,
                provider_fetch_fn=fetch_replies,
                report_builder_fn=build_report,
            )
            loaded = gs.load_snapshot(account["username"], selected_day, selected_day)
            with open(gs.CACHE_PATH, encoding="utf-8") as handle:
                raw_cache = handle.read()

        self.assertEqual(snapshot["status"], "complete")
        self.assertEqual(snapshot["mail"]["received"], 8)
        self.assertEqual(snapshot["sent_sync"]["with_images"], 3)
        self.assertEqual(loaded["report"]["resolved_total"], 1)
        self.assertNotIn("secret-password", raw_cache)
        self.assertNotIn("body", raw_cache.lower())
        metrics = gs.summary_metrics(snapshot)
        self.assertEqual(metrics["total_incoming"], 10)
        self.assertEqual(metrics["spam_rate"], 20.0)
        self.assertEqual(metrics["response_rate"], 50.0)
        self.assertEqual(metrics["takedown_rate"], 50.0)

    def test_source_failure_is_isolated_and_snapshot_remains_usable(self):
        account = {
            "imap_host": "mail.example.test",
            "username": "sender@example.test",
            "password": "secret",
        }
        selected_day = date(2026, 9, 9)
        report = {
            "sent_total": 0, "sent_success": 0, "linked_reply_total": 0, "resolved_total": 0,
            "evidence": {"automatic": 0, "manual": 0, "mixed": 0, "none": 0, "unknown": 0},
            "by_channel": [], "by_provider": [], "by_subject": [], "by_draft": [],
            "outcomes": {}, "links": [], "warnings": [],
        }
        with tempfile.TemporaryDirectory() as folder, patch.object(gs, "CACHE_PATH", os.path.join(folder, "general.json")):
            snapshot = gs.sync_account_general_statistics(
                account,
                selected_day,
                selected_day,
                local_tz=timezone.utc,
                count_mail_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("mail offline")),
                sent_sync_fn=lambda *_args, **_kwargs: {"success": True, "errors": []},
                sent_loader_fn=lambda *_args: [],
                provider_fetch_fn=lambda *_args, **_kwargs: ([], []),
                report_builder_fn=lambda *_args, **_kwargs: report,
            )

        self.assertEqual(snapshot["status"], "partial")
        self.assertEqual(snapshot["mail"]["status"], "error")
        self.assertTrue(any(item["stage"] == "mail_counts" for item in snapshot["errors"]))
        self.assertEqual(gs.summary_metrics(snapshot)["report_total"], 0)

    def test_snapshot_cache_is_account_scoped_and_clearable(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(gs, "CACHE_PATH", os.path.join(folder, "general.json")):
            for account in ("a@example.test", "b@example.test"):
                gs.save_snapshot({
                    "account": account,
                    "date_from": "2026-09-09",
                    "date_to": "2026-09-09",
                    "mail": {}, "report": {},
                })
            self.assertIsNotNone(gs.load_snapshot("a@example.test", date(2026, 9, 9), date(2026, 9, 9)))
            self.assertTrue(gs.clear_snapshots("a@example.test"))
            self.assertIsNone(gs.load_snapshot("a@example.test", date(2026, 9, 9), date(2026, 9, 9)))
            self.assertIsNotNone(gs.load_snapshot("b@example.test", date(2026, 9, 9), date(2026, 9, 9)))

    def test_snapshot_whitelists_provider_status_report_data_and_errors(self):
        account = {
            "imap_host": "imap.example.test",
            "username": "sender@example.test",
            "password": "secret-password",
        }
        selected_day = date(2026, 9, 9)
        raw_report = {
            "sent_total": 1, "sent_success": 1, "sent_failed": 0,
            "linked_reply_total": 0, "reply_total": 0, "resolved_total": 0,
            "response_rate": 0, "takedown_rate": 0,
            "evidence": {"automatic": 1, "manual": 0, "mixed": 0, "none": 0, "unknown": 0},
            "by_channel": [], "by_provider": [], "by_subject": [], "by_draft": [],
            "outcomes": {},
            "links": [{"subject": "safe subject", "body": "secret-password must not persist"}],
            "warnings": ["IMAP imap.example.test rejected secret-password"],
            "untrusted_body": "secret-password must not persist",
        }
        with tempfile.TemporaryDirectory() as folder, patch.object(gs, "CACHE_PATH", os.path.join(folder, "general.json")):
            snapshot = gs.sync_account_general_statistics(
                account,
                selected_day,
                selected_day,
                local_tz=timezone.utc,
                count_mail_fn=lambda *_args, **_kwargs: {
                    "received": 2, "sent": 1, "junk": 0, "status": "ok", "error": "",
                },
                sent_sync_fn=lambda *_args, **_kwargs: {"success": True, "errors": []},
                sent_loader_fn=lambda *_args: [],
                provider_fetch_fn=lambda *_args, **_kwargs: (
                    [], [{"folder": "Inbox", "mailbox": "INBOX", "matched": 3,
                          "status": "Lỗi secret-password tại imap.example.test"}],
                ),
                report_builder_fn=lambda *_args, **_kwargs: raw_report,
            )
            with open(gs.CACHE_PATH, encoding="utf-8") as handle:
                raw_cache = handle.read()

        self.assertEqual(snapshot["reply_sync"]["folders"][0]["matched"], 0)
        self.assertNotIn("secret-password", raw_cache)
        self.assertNotIn("imap.example.test", raw_cache)
        self.assertNotIn("untrusted_body", raw_cache)
        self.assertNotIn("body\":", raw_cache)


if __name__ == "__main__":
    unittest.main()

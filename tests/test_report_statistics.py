import os
import tempfile
import unittest
from datetime import date, timezone
from types import SimpleNamespace
from unittest.mock import patch

import phishing_toolkit as pt
import report_statistics as stats


class ReportStatisticsTests(unittest.TestCase):
    def test_account_report_isolated_and_links_replies_by_domain_provider(self):
        sent_rows = [
            {
                "timestamp": "2026-09-01T09:00:00+00:00",
                "domain": "phish.example",
                "target_url": "https://phish.example/login",
                "draft_file": "phish.example_registrar_report.txt",
                "to": "abuse@godaddy.com",
                "subject": "Phishing report A",
                "account": "a@example.test",
                "success": "True",
                "provider_key": "godaddy",
                "report_channel": "registrar",
                "evidence_source": "browser_automatic",
                "evidence_images": "2",
            },
            {
                "timestamp": "2026-09-01T10:00:00+00:00",
                "domain": "other.example",
                "target_url": "https://other.example/",
                "draft_file": "other.example_hosting_report.txt",
                "to": "abuse@host.example",
                "subject": "Phishing report B",
                "account": "a@example.test",
                "success": "True",
                "report_channel": "hosting",
                "evidence_source": "none",
                "evidence_images": "0",
            },
            {
                "timestamp": "2026-09-01T11:00:00+00:00",
                "domain": "foreign.example",
                "target_url": "https://foreign.example/",
                "draft_file": "foreign.example_registry_report.txt",
                "to": "registry@example.net",
                "subject": "Report from B",
                "account": "b@example.test",
                "success": "True",
                "report_channel": "registry",
                "evidence_source": "manual_upload",
                "evidence_images": "1",
            },
        ]
        replies = [
            SimpleNamespace(
                account="a@example.test", sender="abuse@godaddy.com",
                provider="godaddy", provider_label="GoDaddy", domain="phish.example",
                ticket="GD-1234", subject="Domain has been suspended",
                body="Action has been taken.", request_type="resolved",
                request_label="Đã xử lý", message_id="<reply-a>",
                date="02 Sep 2026 10:00 +0000", server_date="",
                source_mailbox="INBOX",
            ),
            SimpleNamespace(
                account="a@example.test", sender="abuse@host.example",
                provider="unknown", provider_label="Hosting provider", domain="other.example",
                ticket="HOST-1", subject="Report received",
                body="We received your report.", request_type="acknowledgement",
                request_label="Đã tiếp nhận", message_id="<reply-b>",
                date="02 Sep 2026 11:00 +0000", server_date="",
                source_mailbox="Junk",
            ),
            SimpleNamespace(
                account="b@example.test", sender="abuse@registry.example",
                provider="unknown", provider_label="Registry", domain="foreign.example",
                ticket="REG-1", subject="Domain suspended", body="resolved",
                request_type="resolved", request_label="Đã xử lý", message_id="<reply-c>",
                date="02 Sep 2026 12:00 +0000", server_date="",
                source_mailbox="INBOX",
            ),
        ]
        report = stats.build_account_report(
            "a@example.test", date(2026, 9, 1), date(2026, 9, 3),
            sent_rows=sent_rows, provider_mails=replies, reply_log={},
            local_tz=timezone.utc,
        )
        self.assertEqual(report["sent_total"], 2)
        self.assertEqual(report["domain_total"], 2)
        self.assertEqual(report["sent_success"], 2)
        self.assertEqual(report["reply_total"], 2)
        self.assertEqual(report["linked_reply_total"], 2)
        self.assertEqual(report["resolved_total"], 1)
        self.assertEqual(report["resolved_domain_total"], 1)
        self.assertEqual(report["response_rate"], 100.0)
        self.assertEqual(report["takedown_rate"], 50.0)
        self.assertEqual(report["evidence"]["automatic"], 1)
        self.assertEqual(report["evidence"]["none"], 1)
        self.assertEqual({row["domain"] for row in report["links"]}, {"phish.example", "other.example"})
        self.assertEqual(report["unmatched_reply_total"], 0)
        self.assertTrue(any(row["channel"] == "Hosting/ISP" for row in report["links"]))

    def test_missing_reply_account_is_not_attributed_to_selected_mailbox(self):
        mail = SimpleNamespace(
            account="", sender="abuse@example.test", provider="", provider_label="Example",
            domain="phish.example", ticket="T-1", subject="Action has been taken",
            body="Action has been taken.", request_type="resolved", request_label="",
            message_id="<reply>", date="02 Sep 2026 10:00 +0000", server_date="",
            source_mailbox="INBOX",
        )
        report = stats.build_account_report(
            "a@example.test", date(2026, 9, 1), date(2026, 9, 3),
            sent_rows=[{
                "timestamp": "2026-09-01T09:00:00+00:00", "domain": "phish.example",
                "account": "a@example.test", "success": "True", "to": "abuse@example.test",
                "report_channel": "hosting", "evidence_source": "none", "evidence_images": "0",
            }], provider_mails=[mail], reply_log={}, local_tz=timezone.utc,
        )
        self.assertEqual(report["reply_total"], 0)
        self.assertEqual(report["linked_reply_total"], 0)
        self.assertEqual(report["unmatched_reply_total"], 0)

    def test_unrelated_inbound_message_is_excluded_from_effectiveness_metrics(self):
        unrelated = SimpleNamespace(
            account="a@example.test", sender="friend@example.test", provider="unknown",
            provider_label="Khác / Chưa nhận diện", domain="phish.example", ticket="",
            subject="Normal conversation", body="No abuse report here.",
            request_type="manual_review", request_label="Cần đọc thủ công",
            message_id="<ordinary@example.test>", date="02 Sep 2026 10:00 +0000",
            server_date="", source_mailbox="INBOX",
        )
        report = stats.build_account_report(
            "a@example.test", date(2026, 9, 1), date(2026, 9, 3),
            sent_rows=[{
                "timestamp": "2026-09-01T09:00:00+00:00", "domain": "phish.example",
                "account": "a@example.test", "success": "True", "to": "abuse@example.test",
                "report_channel": "hosting", "evidence_source": "none", "evidence_images": "0",
            }],
            provider_mails=[unrelated], reply_log={}, local_tz=timezone.utc,
        )
        self.assertEqual(report["reply_total"], 0)
        self.assertEqual(report["linked_reply_total"], 0)
        self.assertEqual(report["unmatched_reply_total"], 0)

    def test_known_provider_newsletter_without_case_clue_is_excluded(self):
        newsletter = SimpleNamespace(
            account="a@example.test", sender="news@cloudflare.com", provider="cloudflare",
            provider_label="Cloudflare", domain="", ticket="", subject="Product news",
            body="Monthly product update", request_type="manual_review",
            request_label="Cần đọc thủ công", message_id="<newsletter@example.test>",
            date="02 Sep 2026 10:00 +0000", server_date="", source_mailbox="INBOX",
        )
        report = stats.build_account_report(
            "a@example.test", date(2026, 9, 1), date(2026, 9, 3),
            sent_rows=[], provider_mails=[newsletter], reply_log={}, local_tz=timezone.utc,
        )
        self.assertEqual(report["reply_total"], 0)

    def test_same_domain_reply_needs_provider_or_sender_correlation(self):
        unrelated = SimpleNamespace(
            account="a@example.test", sender="other@example.test", provider="unknown",
            provider_label="Khác / Chưa nhận diện", domain="phish.example", ticket="",
            subject="Report received", body="We received your report.",
            request_type="acknowledgement", request_label="Đã tiếp nhận",
            message_id="<unrelated@example.test>", date="02 Sep 2026 10:00 +0000",
            server_date="", source_mailbox="INBOX",
        )
        report = stats.build_account_report(
            "a@example.test", date(2026, 9, 1), date(2026, 9, 3),
            sent_rows=[{
                "timestamp": "2026-09-01T09:00:00+00:00", "domain": "phish.example",
                "account": "a@example.test", "success": "True", "to": "abuse@example.test",
                "report_channel": "hosting", "evidence_source": "none", "evidence_images": "0",
            }],
            provider_mails=[unrelated], reply_log={}, local_tz=timezone.utc,
        )
        self.assertEqual(report["reply_total"], 1)
        self.assertEqual(report["linked_reply_total"], 0)

    def test_reply_received_before_delivery_is_not_linked(self):
        mail = SimpleNamespace(
            account="a@example.test", sender="abuse@example.test", provider="", provider_label="Example",
            domain="phish.example", ticket="T-1", subject="Action has been taken",
            body="Action has been taken.", request_type="resolved", request_label="",
            message_id="<reply>", date="01 Sep 2026 08:00 +0000", server_date="",
            source_mailbox="INBOX",
        )
        report = stats.build_account_report(
            "a@example.test", date(2026, 9, 1), date(2026, 9, 3),
            sent_rows=[{
                "timestamp": "2026-09-01T09:00:00+00:00", "domain": "phish.example",
                "account": "a@example.test", "success": "True", "to": "abuse@example.test",
                "report_channel": "hosting", "evidence_source": "none", "evidence_images": "0",
            }], provider_mails=[mail], reply_log={}, local_tz=timezone.utc,
        )
        self.assertEqual(report["linked_reply_total"], 0)

    def test_reply_log_source_count_is_account_scoped_when_explicitly_supplied(self):
        with patch.object(stats.provider_replies, "load_mail_cache", return_value=[]):
            report = stats.build_account_report(
                "a@example.test", date(2026, 9, 1), date(2026, 9, 1),
                sent_rows=[], provider_mails=None,
                reply_log={"a": {"account": "a@example.test"}, "b": {"account": "b@example.test"}},
                local_tz=timezone.utc,
            )
        self.assertEqual(report["source"]["reply_log_entries"], 1)

    def test_legacy_rows_keep_unknown_evidence_instead_of_guessing(self):
        report = stats.build_account_report(
            "a@example.test", date(2026, 9, 1), date(2026, 9, 1),
            sent_rows=[{
                "timestamp": "2026-09-01T00:00:00+00:00", "domain": "old.example",
                "draft_file": "old.example_registrar_report.txt", "to": "abuse@example.org",
                "subject": "Old report", "account": "a@example.test", "success": "True",
            }], provider_mails=[], reply_log={}, local_tz=timezone.utc,
        )
        self.assertEqual(report["evidence"]["unknown"], 1)
        self.assertEqual(report["evidence"]["with_images"], 0)
        self.assertTrue(any("chưa có evidence metadata" in warning for warning in report["warnings"]))

    def test_read_sent_log_handles_missing_file(self):
        rows, error = stats.read_sent_log(os.path.join("not", "present", "sent_log.csv"))
        self.assertEqual(rows, [])
        self.assertEqual(error, "")

    def test_evidence_log_metadata_reads_manifest_type_without_content(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = os.path.join(folder, "evidence.json")
            image = os.path.join(folder, "source.png")
            with open(manifest, "w", encoding="utf-8") as handle:
                handle.write('{"evidence_type":"dom_destination_opened"}')
            with open(image, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\nimage")
            metadata = pt.evidence_log_metadata([image, manifest])
        self.assertEqual(metadata["evidence_source"], "automatic")
        self.assertEqual(metadata["evidence_images"], 1)
        self.assertTrue(metadata["evidence_manifest"])


if __name__ == "__main__":
    unittest.main()

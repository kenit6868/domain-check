import json
import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

import domain_worker


class DomainWorkerTests(unittest.TestCase):
    def test_reads_only_successful_domain_accounts_from_current_day(self):
        with tempfile.TemporaryDirectory() as job_dir:
            sent_log = os.path.join(job_dir, "sent_log.csv")
            now = datetime.now(timezone.utc)
            yesterday = now - timedelta(days=1)
            with open(sent_log, "w", newline="", encoding="utf-8") as f:
                f.write("timestamp,domain,account,success\n")
                f.write(f"{now.isoformat()},sent.example,sender1@example.org,True\n")
                f.write(f"{now.isoformat()},failed.example,sender1@example.org,False\n")
                f.write(f"{yesterday.isoformat()},old.example,sender1@example.org,True\n")
            with patch.object(domain_worker.pt, "SENT_LOG_PATH", sent_log):
                self.assertEqual(
                    domain_worker._successfully_reported_domain_accounts_today(
                        local_day=now.date(), local_tz=timezone.utc
                    ),
                    {("sent.example", "sender1@example.org")},
                )

    def test_atomic_status_write_retries_windows_file_lock(self):
        with tempfile.TemporaryDirectory() as job_dir:
            path = os.path.join(job_dir, "status.json")
            real_replace = os.replace
            attempts = {"count": 0}

            def flaky_replace(src, dst):
                attempts["count"] += 1
                if attempts["count"] < 3:
                    raise PermissionError("temporarily locked")
                return real_replace(src, dst)

            with patch.object(domain_worker.os, "replace", side_effect=flaky_replace):
                domain_worker._atomic_json(path, {"state": "running"})

            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"state": "running"})
            self.assertEqual(attempts["count"], 3)

    def test_job_processes_domains_and_skips_vncert_by_default(self):
        with tempfile.TemporaryDirectory() as job_dir:
            job_path = os.path.join(job_dir, "job.json")
            normal_draft = os.path.join(job_dir, "example_registrar_report.txt")
            vncert_draft = os.path.join(job_dir, "example_vncert_report.txt")
            for path in (normal_draft, vncert_draft):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("draft")
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump({
                    "job_id": "test",
                    "domains": ["one.example", "two.example"],
                    "batch_size": 1,
                    "interval_seconds": 0,
                    "include_vncert": False,
                }, f)

            fake_result = {
                "domain": "checked.example",
                "drafts": [normal_draft, vncert_draft],
                "reputation": {"verdict": "unknown"},
            }
            parsed = {"to": "abuse@example.net", "subject": "Report", "body": "Body"}
            send_result = [{"account": "sender@example.org", "success": True, "error": None}]

            with (
                patch.object(domain_worker.pt, "load_config", return_value={"smtp_accounts": [{}]}),
                patch.object(domain_worker, "_successfully_reported_domain_accounts_today", return_value=set()),
                patch.object(domain_worker.pt, "run_check", return_value=fake_result) as run_check,
                patch.object(domain_worker.pt, "parse_draft_email", return_value=parsed) as parse_draft,
                patch.object(domain_worker.pt, "send_report_email_bulk", return_value=send_result) as send,
                patch.object(domain_worker.pt, "log_sent"),
            ):
                domain_worker.run_job(job_path)

            with open(os.path.join(job_dir, "status.json"), encoding="utf-8") as f:
                status = json.load(f)
            self.assertEqual(status["state"], "completed")
            self.assertEqual(status["processed"], 2)
            self.assertEqual(run_check.call_count, 2)
            self.assertEqual(parse_draft.call_count, 2)
            self.assertEqual(send.call_count, 2)

    def test_job_sends_only_from_accounts_that_have_not_reported_domain_today(self):
        with tempfile.TemporaryDirectory() as job_dir:
            job_path = os.path.join(job_dir, "job.json")
            draft_path = os.path.join(job_dir, "target.example_registrar_report.txt")
            with open(draft_path, "w", encoding="utf-8") as f:
                f.write("draft")
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump({
                    "job_id": "per-account-dedupe",
                    "domains": ["target.example"],
                    "batch_size": 1,
                    "interval_seconds": 0,
                    "include_vncert": False,
                }, f)

            accounts = [
                {"username": "sender1@example.org"},
                {"username": "sender2@example.org"},
            ]
            fake_result = {
                "domain": "target.example",
                "drafts": [draft_path],
                "reputation": {"verdict": "unknown"},
            }

            def fake_send(_to, _subject, _body, send_cfg):
                self.assertEqual(
                    [account["username"] for account in send_cfg["smtp_accounts"]],
                    ["sender2@example.org"],
                )
                return [{"account": "sender2@example.org", "success": True, "error": None}]

            with (
                patch.object(domain_worker.pt, "load_config", return_value={"smtp_accounts": accounts}),
                patch.object(
                    domain_worker,
                    "_successfully_reported_domain_accounts_today",
                    return_value={("target.example", "sender1@example.org")},
                ),
                patch.object(domain_worker.pt, "run_check", return_value=fake_result),
                patch.object(
                    domain_worker.pt,
                    "parse_draft_email",
                    return_value={"to": "abuse@example.net", "subject": "Report", "body": "Body"},
                ),
                patch.object(domain_worker.pt, "send_report_email_bulk", side_effect=fake_send) as send,
                patch.object(domain_worker.pt, "log_sent"),
            ):
                domain_worker.run_job(job_path)

            self.assertEqual(send.call_count, 1)


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone

import domain_worker


class DomainWorkerTests(unittest.TestCase):
    def setUp(self):
        self._cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._cache_dir.cleanup)
        cache_path = os.path.join(self._cache_dir.name, "domain_precheck_cache.json")
        cache_patch = patch.object(domain_worker, "PRECHECK_CACHE_PATH", cache_path)
        cache_patch.start()
        self.addCleanup(cache_patch.stop)

    def test_precheck_cache_reuses_only_current_day_entries(self):
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        with open(domain_worker.PRECHECK_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "entries": {
                "fresh.example": {"checked_at": now.isoformat(), "recipients": [{"email": "fresh@example.org"}]},
                "old.example": {"checked_at": yesterday.isoformat(), "recipients": [{"email": "old@example.org"}]},
            }}, f)
        cached = domain_worker._load_precheck_cache(local_day=now.date(), local_tz=timezone.utc)
        self.assertEqual(set(cached), {"fresh.example"})

    def test_email_body_uses_each_sender_account_address(self):
        cfg = {"contact_name": "Byc", "contact_email": "byc@camellrp.com"}
        body = "Regards,\nByc\nbyc@camellrp.com"
        gmail = {"username": "byc.okwin@gmail.com"}
        custom = {
            "username": "sender@example.org",
            "contact_name": "Security Team",
            "contact_email": "abuse@example.org",
        }
        self.assertIn("byc.okwin@gmail.com", domain_worker.pt.personalize_email_body(body, cfg, gmail))
        custom_body = domain_worker.pt.personalize_email_body(body, cfg, custom)
        self.assertIn("Security Team", custom_body)
        self.assertIn("abuse@example.org", custom_body)

    def test_old_draft_placeholder_is_removed_when_urlscan_evidence_exists(self):
        with tempfile.TemporaryDirectory() as draft_dir:
            path = os.path.join(draft_dir, "target.example_registrar_report.txt")
            screenshot = "https://urlscan.io/screenshots/scan-id.png"
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "To: abuse@example.org\nSubject: Report\n\n"
                    "Screenshots: [ĐÍNH KÈM ẢNH CHỤP MÀN HÌNH — bắt buộc có thanh địa chỉ trình duyệt]\n"
                    "(Ảnh chụp phải hiển thị rõ URL \"https://target.example/\" trên thanh địa chỉ của trình duyệt)\n"
                    f"\n--- Evidence: URLScan.io Analysis ---\nScreenshot: {screenshot}\n"
                )
            domain_worker.pt.append_urlscan_evidence_to_drafts(
                [path], {"status": "done", "screenshot_url": screenshot, "result_url": "https://urlscan.io/result/id/"}
            )
            parsed = domain_worker.pt.parse_draft_email(path)
            self.assertNotIn("ĐÍNH KÈM", parsed["body"])
            self.assertNotIn("Ảnh chụp phải hiển thị", parsed["body"])
            self.assertIn(f"Screenshot: {screenshot}", parsed["body"])

    def test_cloaking_evidence_is_refreshed_without_duplicate_blocks(self):
        with tempfile.TemporaryDirectory() as draft_dir:
            path = os.path.join(draft_dir, "target.example_registrar_report.txt")
            with open(path, "w", encoding="utf-8") as draft_file:
                draft_file.write("To: abuse@example.org\nSubject: Report\n\nBody\n")
            result = {
                "verdict": "LIKELY", "score": 80,
                "observed_at": "2026-08-29T00:00:00+00:00",
                "target_url": "https://target.example/", "profiles": {},
                "signals": [{"detail": "mobile and desktop differ"}],
            }
            domain_worker.pt.append_cloaking_evidence_to_drafts([path], result)
            result["score"] = 90
            domain_worker.pt.append_cloaking_evidence_to_drafts([path], result)
            with open(path, encoding="utf-8") as draft_file:
                content = draft_file.read()
            self.assertEqual(
                content.count("Technical Evidence: Multi-profile Cloaking Check"), 1,
            )
            self.assertIn("score: 90/100", content)

    def test_external_body_never_sends_internal_vietnamese_instructions(self):
        body = (
            "[NOTE: Verify this address before sending.]\n"
            "Evidence:\n"
            "Screenshots: [ĐÍNH KÈM ẢNH CHỤP MÀN HÌNH — bắt buộc có thanh địa chỉ trình duyệt]\n"
            "(Ảnh chụp phải hiển thị rõ URL \"https://target.example/\" trên thanh địa chỉ của trình duyệt)\n"
            "Regards,\nByc\nbyc@camellrp.com"
        )
        rendered = domain_worker.pt.prepare_external_email_body(body)
        self.assertNotIn("NOTE", rendered)
        self.assertNotIn("ĐÍNH KÈM", rendered)
        self.assertNotIn("Ảnh chụp", rendered)
        self.assertNotIn("Screenshot", rendered)

    def test_urlscan_404_placeholder_is_not_treated_as_screenshot(self):
        placeholder = Mock(status_code=404, headers={"content-type": "image/png"})
        placeholder.close = Mock()
        screenshot = Mock(status_code=200, headers={"content-type": "image/png"})
        screenshot.close = Mock()
        with patch.object(domain_worker.pt.requests, "get", return_value=placeholder):
            self.assertFalse(domain_worker.pt._urlscan_screenshot_available("https://urlscan.io/screenshots/x.png"))
        with patch.object(domain_worker.pt.requests, "get", return_value=screenshot):
            self.assertTrue(domain_worker.pt._urlscan_screenshot_available("https://urlscan.io/screenshots/x.png"))

    def test_precheck_only_resolves_recipients_without_full_pipeline(self):
        with tempfile.TemporaryDirectory() as job_dir:
            job_path = os.path.join(job_dir, "job.json")
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump({
                    "job_id": "precheck-only", "domains": ["target.example"],
                    "batch_size": 5, "interval_seconds": 0,
                    "include_vncert": False, "precheck_only": True,
                    "preflight_version": 2,
                }, f)
            recipients = [{"channel": "registry", "email": "abuse@example.net"}]
            with (
                patch.object(domain_worker.pt, "load_config", return_value={"smtp_accounts": [{}]}),
                patch.object(domain_worker, "_successfully_sent_deliveries_today", return_value=set()),
                patch.object(domain_worker, "_precheck_report_recipients", return_value=recipients),
                patch.object(domain_worker.pt, "run_check") as run_check,
            ):
                domain_worker.run_job(job_path)
            with open(os.path.join(job_dir, "status.json"), encoding="utf-8") as f:
                status = json.load(f)
            with open(os.path.join(job_dir, "preflight.json"), encoding="utf-8") as f:
                preflight = json.load(f)
            self.assertEqual(status["state"], "ready")
            self.assertEqual(status["ready_total"], 1)
            self.assertEqual(preflight["ready"][0]["recipients"], recipients)
            run_check.assert_not_called()

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

    def test_worker_cache_reads_successful_sends_from_all_days(self):
        with tempfile.TemporaryDirectory() as job_dir:
            sent_log = os.path.join(job_dir, "sent_log.csv")
            with open(sent_log, "w", newline="", encoding="utf-8") as f:
                f.write("timestamp,domain,account,success\n")
                f.write("2020-01-01T00:00:00+00:00,old.example,sender@example.org,True\n")
                f.write("2020-01-01T00:00:00+00:00,failed.example,sender@example.org,False\n")
            with patch.object(domain_worker.pt, "SENT_LOG_PATH", sent_log):
                self.assertEqual(
                    domain_worker._successfully_reported_domain_accounts(),
                    {("old.example", "sender@example.org")},
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

    def test_force_stop_marks_job_stopped_and_terminates_process_group(self):
        with tempfile.TemporaryDirectory() as job_dir:
            status_path = os.path.join(job_dir, "status.json")
            with open(status_path, "w", encoding="utf-8") as f:
                json.dump({
                    "job_id": "stop-test", "state": "running", "pid": 4321,
                    "processed": 3, "current_domain": "active.example",
                }, f)
            with patch.object(domain_worker.os, "name", "posix"), patch.object(
                domain_worker.os, "killpg", create=True
            ) as killpg:
                stopped, _message = domain_worker.stop_job_process(job_dir)
            self.assertTrue(stopped)
            killpg.assert_called_once_with(4321, domain_worker.signal.SIGTERM)
            self.assertTrue(os.path.exists(os.path.join(job_dir, "stop.requested")))
            with open(status_path, encoding="utf-8") as f:
                status = json.load(f)
            self.assertEqual(status["state"], "stopped")
            self.assertEqual(status["processed"], 3)
            self.assertTrue(status["stop_forced"])

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

            def fake_run_check(target, _submit, _cfg):
                return {
                    "domain": target,
                    "drafts": [normal_draft, vncert_draft],
                    "reputation": {"verdict": "unknown"},
                }
            parsed = {"to": "abuse@example.net", "subject": "Report", "body": "Body"}
            send_result = [{"account": "sender@example.org", "success": True, "error": None}]

            with (
                patch.object(domain_worker.pt, "load_config", return_value={"smtp_accounts": [{}]}),
                patch.object(domain_worker, "_successfully_sent_deliveries_today", return_value=set()),
                patch.object(domain_worker, "_successfully_reported_domain_accounts", return_value=set()),
                patch.object(domain_worker, "_precheck_report_recipients", return_value=[{"channel": "registry", "email": "abuse@example.net"}]),
                patch.object(domain_worker.pt, "run_check", side_effect=fake_run_check) as run_check,
                patch.object(domain_worker.pt, "parse_draft_email", return_value=parsed) as parse_draft,
                patch.object(domain_worker.pt, "send_report_email_single", return_value=send_result[0]) as send,
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

            def fake_send(_to, _subject, _body, account, _proxy):
                self.assertEqual(account["username"], "sender2@example.org")
                return {"account": "sender2@example.org", "success": True, "error": None}

            with (
                patch.object(domain_worker.pt, "load_config", return_value={"smtp_accounts": accounts}),
                patch.object(
                    domain_worker,
                    "_successfully_sent_deliveries_today",
                    return_value={(
                        "target.example", "sender1@example.org",
                        "target.example_registrar_report.txt", "abuse@example.net",
                    )},
                ),
                patch.object(domain_worker, "_precheck_report_recipients", return_value=[{"channel": "registry", "email": "abuse@example.net"}]),
                patch.object(domain_worker.pt, "run_check", return_value=fake_result),
                patch.object(
                    domain_worker.pt,
                    "parse_draft_email",
                    return_value={"to": "abuse@example.net", "subject": "Report", "body": "Body"},
                ),
                patch.object(domain_worker.pt, "send_report_email_single", side_effect=fake_send) as send,
                patch.object(domain_worker.pt, "log_sent"),
            ):
                domain_worker.run_job(job_path)

            self.assertEqual(send.call_count, 1)

    def test_job_resumes_without_reprocessing_completed_targets(self):
        with tempfile.TemporaryDirectory() as job_dir:
            job_path = os.path.join(job_dir, "job.json")
            status_path = os.path.join(job_dir, "status.json")
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump({
                    "job_id": "resume-test",
                    "domains": ["done.example", "remaining.example"],
                    "batch_size": 1,
                    "interval_seconds": 0,
                    "include_vncert": False,
                }, f)
            with open(status_path, "w", encoding="utf-8") as f:
                json.dump({
                    "job_id": "resume-test",
                    "state": "waiting",
                    "processed": 1,
                    "total": 2,
                    "current_batch": 1,
                    "total_batches": 2,
                    "results": [{
                        "target_url": "done.example",
                        "domain": "done.example",
                        "success": True,
                    }],
                }, f)

            with (
                patch.object(domain_worker.pt, "load_config", return_value={"smtp_accounts": [{}]}),
                patch.object(domain_worker, "_successfully_sent_deliveries_today", return_value=set()),
                patch.object(domain_worker, "_successfully_reported_domain_accounts", return_value=set()),
                patch.object(domain_worker, "_precheck_report_recipients", return_value=[{"channel": "registry", "email": "abuse@example.net"}]),
                patch.object(
                    domain_worker.pt,
                    "run_check",
                    return_value={
                        "domain": "remaining.example",
                        "drafts": [],
                        "reputation": {"verdict": "unknown"},
                    },
                ) as run_check,
            ):
                domain_worker.run_job(job_path)

            with open(status_path, encoding="utf-8") as f:
                status = json.load(f)
            self.assertEqual(status["state"], "completed")
            # The completed target remains in results; only the remaining target
            # runs through the full pipeline after its lightweight precheck.
            self.assertEqual(status["processed"], 2)
            self.assertEqual(run_check.call_count, 1)
            self.assertEqual(run_check.call_args.args[0], "remaining.example")

    def test_retry_reprocesses_failed_result_from_cached_preflight(self):
        with tempfile.TemporaryDirectory() as job_dir:
            job_path = os.path.join(job_dir, "job.json")
            status_path = os.path.join(job_dir, "status.json")
            preflight_path = os.path.join(job_dir, "preflight.json")
            target = "https://failed.example/path"
            prepared = {
                "target_url": target,
                "domain": "failed.example",
                "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
            }
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump({
                    "job_id": "retry-failed", "domains": [target],
                    "batch_size": 1, "interval_seconds": 0,
                    "include_vncert": False, "precheck_only": False,
                    "preflight_version": 2,
                }, f)
            with open(preflight_path, "w", encoding="utf-8") as f:
                json.dump({"version": 2, "ready": [prepared]}, f)
            with open(status_path, "w", encoding="utf-8") as f:
                json.dump({
                    "job_id": "retry-failed", "state": "completed",
                    "results": [{
                        "target_url": target, "domain": "failed.example",
                        "sent_ok": 0, "sent_failed": 1,
                    }],
                }, f)

            retried = {
                "target_url": target, "domain": "failed.example",
                "sent_ok": 1, "sent_failed": 0,
            }
            with (
                patch.object(domain_worker.pt, "load_config", return_value={"smtp_accounts": [{}]}),
                patch.object(domain_worker, "_successfully_sent_deliveries_today", return_value=set()),
                patch.object(domain_worker, "_run_prechecked_domain", return_value=(retried, set(), False)) as run_domain,
            ):
                domain_worker.run_job(job_path)

            with open(status_path, encoding="utf-8") as f:
                status = json.load(f)
            self.assertEqual(run_domain.call_count, 1)
            self.assertEqual(status["results"][-1]["sent_ok"], 1)
            self.assertEqual(status["results"][-1]["sent_failed"], 0)

    def test_possible_cloaking_requires_manual_review_and_does_not_send(self):
        with tempfile.TemporaryDirectory() as job_dir:
            events_path = os.path.join(job_dir, "events.jsonl")
            result = {
                "domain": "target.example", "drafts": ["unused.txt"],
                "reputation": {"verdict": "suspicious"},
                "cloaking": {
                    "verdict": "POSSIBLE", "score": 45,
                    "signals": [{"code": "content_mismatch"}],
                    "evidence_path": os.path.join(job_dir, "evidence.json"),
                },
            }
            with (
                patch.object(domain_worker.pt, "run_check", return_value=result),
                patch.object(domain_worker.pt, "run_cloaking_browser_check", return_value=result["cloaking"]),
                patch.object(domain_worker, "_send_domain_drafts") as send,
            ):
                domain_result, _accounts, stopped = domain_worker._run_prechecked_domain(
                    {"target_url": "https://target.example/"},
                    {}, [], False, events_path, None, set(),
                )
            self.assertFalse(stopped)
            self.assertEqual(domain_result["skipped"], "manual_review_required")
            self.assertEqual(domain_result["cloaking_verdict"], "POSSIBLE")
            send.assert_not_called()
            with open(events_path, encoding="utf-8") as event_file:
                self.assertIn("cloaking_manual_review", event_file.read())

    def test_terminal_browser_page_skips_cloaking_review_and_sends_normally(self):
        initial = {
            "domain": "target.example", "drafts": ["report.txt"],
            "reputation": {"verdict": "suspicious"},
            "cloaking": {
                "verdict": "POSSIBLE", "score": 30,
                "signals": [{"kind": "content_difference"}],
            },
        }
        terminal = {
            "verdict": "NO_SIGNAL", "score": 0, "signals": [],
            "manual_review_required": False,
            "coverage": {"multi_vantage_recommended": False},
            "site_state": {
                "verdict": "BLOCKED_OR_UNAVAILABLE",
                "all_profiles_terminal": True,
            },
            "screenshots": [{"path": "browser-error.png"}],
        }
        mail = {
            "drafts_total": 1, "drafts_sendable": 1, "sent_ok": 1,
            "sent_failed": 0, "already_sent": 0, "sent_to": [],
        }
        with tempfile.TemporaryDirectory() as job_dir:
            with (
                patch.object(domain_worker.pt, "run_check", return_value=initial),
                patch.object(
                    domain_worker.pt, "run_cloaking_browser_check", return_value=terminal,
                ) as browser_check,
                patch.object(
                    domain_worker, "_send_domain_drafts",
                    return_value=(mail, set(), False),
                ) as send,
            ):
                domain_result, _accounts, _stopped = domain_worker._run_prechecked_domain(
                    {"target_url": "https://target.example/"}, {}, [], False,
                    os.path.join(job_dir, "events.jsonl"), None, set(),
                )
        browser_check.assert_called_once()
        self.assertTrue(domain_result["success"])
        self.assertEqual(domain_result["cloaking_verdict"], "NO_SIGNAL")
        self.assertEqual(send.call_args.kwargs["attachments"], [])

    def test_geo_device_coverage_gap_requires_manual_review(self):
        result = {
            "domain": "target.example", "drafts": ["unused.txt"],
            "reputation": {"verdict": "suspicious"},
            "cloaking": {
                "verdict": "NO_SIGNAL", "score": 0, "signals": [],
                "coverage": {"multi_vantage_recommended": True},
            },
        }
        browser_result = dict(result["cloaking"])
        browser_result["playwright"] = {"available": True, "verdict": "NO_SIGNAL"}
        with tempfile.TemporaryDirectory() as job_dir:
            with (
                patch.object(domain_worker.pt, "run_check", return_value=result),
                patch.object(
                    domain_worker.pt, "run_cloaking_browser_check",
                    return_value=browser_result,
                ) as browser_check,
                patch.object(domain_worker, "_send_domain_drafts") as send,
            ):
                domain_result, _accounts, stopped = domain_worker._run_prechecked_domain(
                    {"target_url": "https://target.example/"}, {}, [], False,
                    os.path.join(job_dir, "events.jsonl"), None, set(),
                )
        self.assertFalse(stopped)
        browser_check.assert_called_once()
        send.assert_not_called()
        self.assertEqual(domain_result["skipped"], "manual_review_required")
        self.assertEqual(domain_result["cloaking_review_reason"], "coverage_gap")

    def test_likely_cloaking_sends_with_evidence_attachment(self):
        with tempfile.TemporaryDirectory() as job_dir:
            evidence_path = os.path.join(job_dir, "cloaking-evidence.json")
            with open(evidence_path, "w", encoding="utf-8") as evidence_file:
                json.dump({"verdict": "LIKELY"}, evidence_file)
            result = {
                "domain": "target.example", "drafts": ["report.txt"],
                "reputation": {"verdict": "suspicious"},
                "cloaking": {
                    "verdict": "LIKELY", "score": 90,
                    "signals": [{"code": "known_cloaking_asset"}],
                    "evidence_path": evidence_path,
                },
            }
            mail = {
                "drafts_total": 1, "drafts_sendable": 1, "sent_ok": 1,
                "sent_failed": 0, "already_sent": 0, "sent_to": [],
            }
            with (
                patch.object(domain_worker.pt, "run_check", return_value=result),
                patch.object(domain_worker, "_send_domain_drafts", return_value=(mail, {"sender@example.org"}, False)) as send,
            ):
                domain_result, accounts, stopped = domain_worker._run_prechecked_domain(
                    {"target_url": "https://target.example/"},
                    {}, [{"username": "sender@example.org"}], False,
                    os.path.join(job_dir, "events.jsonl"), None, set(),
                )
            self.assertFalse(stopped)
            self.assertTrue(domain_result["success"])
            self.assertEqual(accounts, {"sender@example.org"})
            self.assertEqual(send.call_args.kwargs["attachments"], [evidence_path])

    def test_playwright_can_upgrade_possible_and_worker_then_sends(self):
        with tempfile.TemporaryDirectory() as job_dir:
            draft_path = os.path.join(job_dir, "report.txt")
            evidence_path = os.path.join(job_dir, "combined-evidence.json")
            screenshot_path = os.path.join(job_dir, "mobile.png")
            for path in (draft_path, evidence_path, screenshot_path):
                with open(path, "wb") as output_file:
                    output_file.write(b"evidence")
            initial = {
                "domain": "target.example", "drafts": [draft_path],
                "reputation": {"verdict": "suspicious"},
                "cloaking": {"verdict": "POSSIBLE", "score": 30, "signals": []},
            }
            upgraded = {
                "verdict": "LIKELY", "score": 80, "signals": [{"code": "browser_difference"}],
                "evidence_path": evidence_path,
                "screenshots": [{"path": screenshot_path, "label": "mobile"}],
                "playwright": {"available": True, "verdict": "LIKELY"},
            }
            mail = {
                "drafts_total": 1, "drafts_sendable": 1, "sent_ok": 1,
                "sent_failed": 0, "already_sent": 0, "sent_to": [],
            }
            with (
                patch.object(domain_worker.pt, "run_check", return_value=initial),
                patch.object(domain_worker.pt, "run_cloaking_browser_check", return_value=upgraded) as browser_check,
                patch.object(domain_worker.pt, "append_cloaking_evidence_to_drafts", return_value=[draft_path]),
                patch.object(domain_worker, "_send_domain_drafts", return_value=(mail, set(), False)) as send,
            ):
                domain_result, _accounts, _stopped = domain_worker._run_prechecked_domain(
                    {"target_url": "https://target.example/"}, {}, [], False,
                    os.path.join(job_dir, "events.jsonl"), None, set(),
                )
            browser_check.assert_called_once()
            self.assertTrue(domain_result["success"])
            self.assertEqual(domain_result["cloaking_verdict"], "LIKELY")
            self.assertEqual(
                send.call_args.kwargs["attachments"],
                [evidence_path, screenshot_path],
            )

    def test_approved_possible_cloaking_can_retry_and_send(self):
        result = {
            "domain": "target.example", "drafts": [],
            "reputation": {"verdict": "suspicious"},
            "cloaking": {"verdict": "POSSIBLE", "score": 40, "signals": []},
        }
        mail = {
            "drafts_total": 0, "drafts_sendable": 0, "sent_ok": 0,
            "sent_failed": 0, "already_sent": 0, "sent_to": [],
        }
        with tempfile.TemporaryDirectory() as job_dir:
            with (
                patch.object(domain_worker.pt, "run_check", return_value=result),
                patch.object(domain_worker, "_send_domain_drafts", return_value=(mail, set(), False)) as send,
                patch.object(domain_worker, "_log_no_email"),
            ):
                domain_result, _accounts, _stopped = domain_worker._run_prechecked_domain(
                    {"target_url": "https://target.example/"},
                    {}, [], False, os.path.join(job_dir, "events.jsonl"), None,
                    set(), approved_cloaking=True,
                )
        self.assertTrue(domain_result["success"])
        self.assertTrue(domain_result["cloaking_approved"])
        send.assert_called_once()

    def test_approved_operator_evidence_is_attached_on_retry(self):
        result = {
            "domain": "target.example", "drafts": ["report.txt"],
            "reputation": {"verdict": "suspicious"},
            "cloaking": {
                "target_url": "https://target.example/",
                "verdict": "NO_SIGNAL", "score": 0, "signals": [],
            },
        }
        mail = {
            "drafts_total": 1, "drafts_sendable": 1, "sent_ok": 1,
            "sent_failed": 0, "already_sent": 0, "sent_to": [],
        }
        with tempfile.TemporaryDirectory() as job_dir:
            manifest = os.path.join(job_dir, "operator.json")
            desktop = os.path.join(job_dir, "desktop.png")
            mobile = os.path.join(job_dir, "mobile.png")
            for path in (manifest, desktop, mobile):
                with open(path, "wb") as evidence_file:
                    evidence_file.write(b"evidence")
            operator = {
                "confirmed_difference": True,
                "manifest_path": manifest,
                "screenshots": [{"path": desktop}, {"path": mobile}],
            }
            with (
                patch.object(domain_worker.pt, "run_check", return_value=result),
                patch.object(
                    domain_worker.pt, "append_cloaking_evidence_to_drafts",
                    return_value=["report.txt"],
                ),
                patch.object(
                    domain_worker, "_send_domain_drafts",
                    return_value=(mail, set(), False),
                ) as send,
            ):
                domain_result, _accounts, _stopped = domain_worker._run_prechecked_domain(
                    {"target_url": "https://target.example/"}, {}, [], False,
                    os.path.join(job_dir, "events.jsonl"), None, set(),
                    approved_cloaking=True, operator_cloaking_evidence=operator,
                )
        self.assertEqual(domain_result["cloaking_verdict"], "POSSIBLE")
        self.assertEqual(
            send.call_args.kwargs["attachments"],
            [manifest, desktop, mobile],
        )


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import cloaking_review_queue as queue


class CloakingReviewQueueTests(unittest.TestCase):
    def test_enqueue_refreshes_pending_item_without_duplicating_it(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, "REVIEW_DIR", directory):
            job = {"job_id": "job-1", "allowed_accounts": ["sender@example.org"]}
            prepared = {"target_url": "https://target.example/path", "domain": "target.example"}
            first = queue.enqueue_worker_result(
                job=job, job_dir=directory, prepared=prepared,
                domain_result={
                    "target_url": prepared["target_url"], "domain": "target.example",
                    "skipped": "manual_review_required", "cloaking_score": 30,
                },
            )
            second = queue.enqueue_worker_result(
                job=job, job_dir=directory, prepared=prepared,
                domain_result={
                    "target_url": prepared["target_url"], "domain": "target.example",
                    "skipped": "manual_review_required", "cloaking_score": 80,
                },
            )

            self.assertEqual(first["queue_id"], second["queue_id"])
            self.assertEqual(second["state"], queue.PENDING_REVIEW)
            self.assertEqual(second["result"]["cloaking_score"], 80)
            self.assertEqual(len(queue.list_items()), 1)

    def test_enqueue_is_idempotent_with_legacy_nan_evidence_value(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, "REVIEW_DIR", directory):
            job = {
                "job_id": "job-nan", "created_at": "2026-08-30T02:00:00+00:00",
                "allowed_accounts": ["sender@example.org"],
            }
            prepared = {"target_url": "https://nan.example/", "domain": "nan.example"}
            result = {
                "target_url": prepared["target_url"], "domain": "nan.example",
                "skipped": "manual_review_required", "cloaking_score": float("nan"),
            }
            first = queue.enqueue_worker_result(
                job=job, job_dir=directory, prepared=prepared, domain_result=result,
            )
            second = queue.enqueue_worker_result(
                job=job, job_dir=directory, prepared=prepared, domain_result=result,
            )

            self.assertEqual(second["updated_at"], first["updated_at"])

    def test_same_url_from_different_jobs_merges_within_local_day(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, "REVIEW_DIR", directory):
            prepared = {"target_url": "HTTPS://Target.Example/path/", "domain": "target.example"}
            first = queue.enqueue_worker_result(
                job={"job_id": "job-a", "created_at": "2026-08-30T02:00:00+00:00"},
                job_dir=directory, prepared=prepared,
                domain_result={
                    "target_url": prepared["target_url"], "domain": "target.example",
                    "skipped": "manual_review_required", "cloaking_score": 30,
                },
            )
            second = queue.enqueue_worker_result(
                job={"job_id": "job-b", "created_at": "2026-08-30T05:00:00+00:00"},
                job_dir=directory,
                prepared={**prepared, "target_url": "https://target.example/path"},
                domain_result={
                    "target_url": "https://target.example/path", "domain": "target.example",
                    "skipped": "manual_review_required", "cloaking_score": 80,
                },
            )

            self.assertEqual(first["queue_id"], second["queue_id"])
            self.assertEqual(second["source_job_ids"], ["job-a", "job-b"])
            self.assertEqual(second["result"]["cloaking_score"], 80)
            self.assertEqual(len(queue.list_items()), 1)
            unchanged = queue.enqueue_worker_result(
                job={"job_id": "job-b", "created_at": "2026-08-30T05:00:00+00:00"},
                job_dir=directory,
                prepared={**prepared, "target_url": "https://target.example/path"},
                domain_result={
                    "target_url": "https://target.example/path", "domain": "target.example",
                    "skipped": "manual_review_required", "cloaking_score": 80,
                },
            )
            self.assertEqual(unchanged["updated_at"], second["updated_at"])

    def test_same_url_on_different_days_creates_separate_daily_cases(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, "REVIEW_DIR", directory):
            prepared = {"target_url": "https://target.example/", "domain": "target.example"}
            first = queue.enqueue_worker_result(
                job={"job_id": "day-one", "created_at": "2026-08-29T05:00:00+00:00"},
                job_dir=directory, prepared=prepared,
                domain_result={**prepared, "skipped": "manual_review_required"},
            )
            second = queue.enqueue_worker_result(
                job={"job_id": "day-two", "created_at": "2026-08-30T05:00:00+00:00"},
                job_dir=directory, prepared=prepared,
                domain_result={**prepared, "skipped": "manual_review_required"},
            )

            self.assertNotEqual(first["queue_id"], second["queue_id"])
            self.assertEqual(len(queue.list_items()), 2)

    def test_consolidation_archives_legacy_job_keyed_duplicate(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, "REVIEW_DIR", directory):
            target = "https://target.example/"
            legacy_ids = ["legacy-one", "legacy-two"]
            for index, legacy_id in enumerate(legacy_ids, start=1):
                queue._atomic_json(queue._item_path(legacy_id), {
                    "version": 1,
                    "queue_id": legacy_id,
                    "state": queue.PENDING_REVIEW,
                    "created_at": f"2026-08-30T0{index}:00:00+00:00",
                    "updated_at": f"2026-08-30T0{index}:00:00+00:00",
                    "source_job_id": f"job-{index}",
                    "source_job_dir": directory,
                    "target_url": target,
                    "domain": "target.example",
                    "prepared": {"target_url": target, "domain": "target.example"},
                    "result": {"target_url": target, "cloaking_score": index * 10},
                })

            self.assertEqual(queue.consolidate_daily_duplicates(), 2)
            items = queue.list_items()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["source_job_ids"], ["job-1", "job-2"])
            self.assertEqual(
                sorted(os.listdir(os.path.join(directory, "archive"))),
                ["legacy-one.json", "legacy-two.json"],
            )

    def test_queue_state_requires_explicit_selection_before_send(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, "REVIEW_DIR", directory):
            item = queue.enqueue_worker_result(
                job={"job_id": "job-2"}, job_dir=directory,
                prepared={"target_url": "https://target.example/", "domain": "target.example"},
                domain_result={
                    "target_url": "https://target.example/", "domain": "target.example",
                    "skipped": "manual_review_required",
                },
            )
            queued = queue.mark_selected_for_send(
                [item["queue_id"]], state=queue.QUEUED_CLOAKING,
                decision="confirmed_cloaking", send_job_id="send-job",
            )[0]
            self.assertEqual(queued["state"], queue.QUEUED_CLOAKING)
            with self.assertRaises(ValueError):
                queue.mark_selected_for_send(
                    [item["queue_id"]], state=queue.QUEUED_CLOAKING,
                    decision="confirmed_cloaking", send_job_id="other-job",
                )
            completed = queue.complete_send(item["queue_id"], {
                "target_url": item["target_url"], "sent_ok": 1, "sent_failed": 0,
            })
            self.assertEqual(completed["state"], queue.SENT)
            refreshed = queue.enqueue_worker_result(
                job={"job_id": "job-2"}, job_dir=directory,
                prepared=item["prepared"], domain_result=item["result"],
            )
            self.assertEqual(refreshed["state"], queue.SENT)
            self.assertEqual(refreshed["send_result"]["sent_ok"], 1)

    def test_partial_send_stays_active_until_every_required_account_completes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, "REVIEW_DIR", directory):
            accounts = ["sender1@example.org", "sender2@example.org"]
            item = queue.enqueue_worker_result(
                job={"job_id": "source-partial", "allowed_accounts": accounts},
                job_dir=directory,
                prepared={
                    "target_url": "https://partial.example/",
                    "domain": "partial.example",
                    "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                },
                domain_result={
                    "target_url": "https://partial.example/",
                    "domain": "partial.example",
                    "skipped": "manual_review_required",
                },
            )
            queue.mark_selected_for_send(
                [item["queue_id"]], state=queue.QUEUED_CLOAKING,
                decision="confirmed_cloaking", send_job_id="send-first",
                attempt_accounts=accounts,
            )
            partial = queue.complete_send(
                item["queue_id"],
                {
                    "target_url": item["target_url"], "drafts_sendable": 1,
                    "sent_ok": 1, "sent_failed": 1, "already_sent": 0,
                    "sent_to": [
                        {
                            "account": accounts[0], "to": "abuse@example.net",
                            "draft": "partial.example_registry_report.txt",
                            "ok": True, "status": "sent", "error": "",
                        },
                        {
                            "account": accounts[1], "to": "abuse@example.net",
                            "draft": "partial.example_registry_report.txt",
                            "ok": False, "status": "failed", "error": "SMTP timeout",
                        },
                    ],
                },
                attempted_accounts=accounts, send_job_id="send-first",
            )

            self.assertEqual(partial["state"], queue.PARTIAL)
            self.assertIn(partial["state"], queue.ACTIVE_STATES)
            summary = queue.delivery_summary(partial)
            self.assertEqual(summary["completed_accounts"], [accounts[0]])
            self.assertEqual(summary["pending_accounts"], [accounts[1]])
            self.assertEqual(summary["recipients"], ["abuse@example.net"])

            queue.mark_selected_for_send(
                [item["queue_id"]], state=queue.QUEUED_CLOAKING,
                decision="confirmed_cloaking", send_job_id="send-second",
                attempt_accounts=[accounts[1]],
            )
            completed = queue.complete_send(
                item["queue_id"],
                {
                    "target_url": item["target_url"], "drafts_sendable": 1,
                    "sent_ok": 1, "sent_failed": 0, "already_sent": 0,
                    "sent_to": [{
                        "account": accounts[1], "to": "abuse@example.net",
                        "draft": "partial.example_registry_report.txt",
                        "ok": True, "status": "sent", "error": "",
                    }],
                },
                attempted_accounts=[accounts[1]], send_job_id="send-second",
            )

            self.assertEqual(completed["state"], queue.SENT)
            summary = queue.delivery_summary(completed)
            self.assertEqual(summary["completed_accounts"], accounts)
            self.assertEqual(summary["pending_accounts"], [])
            self.assertEqual(len(completed["send_attempts"]), 2)

    def test_older_attempt_merge_does_not_replace_latest_attempt_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, "REVIEW_DIR", directory):
            accounts = ["sender1@example.org", "sender2@example.org"]
            item = queue.enqueue_worker_result(
                job={"job_id": "source-order", "allowed_accounts": accounts},
                job_dir=directory,
                prepared={
                    "target_url": "https://ordered.example/", "domain": "ordered.example",
                    "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                },
                domain_result={
                    "target_url": "https://ordered.example/", "domain": "ordered.example",
                    "skipped": "manual_review_required",
                },
            )
            newer_result = {
                "marker": "newer", "drafts_sendable": 1,
                "sent_ok": 1, "sent_failed": 0, "already_sent": 0,
                "sent_to": [{
                    "account": accounts[1], "to": "abuse@example.net",
                    "draft": "ordered.example_registry_report.txt",
                    "status": "sent", "ok": True,
                    "updated_at": "2026-08-30T02:00:00+00:00",
                }],
            }
            queue.complete_send(
                item["queue_id"], newer_result,
                attempted_accounts=[accounts[1]], send_job_id="newer-job",
                attempted_at="2026-08-30T02:00:00+00:00",
            )
            older_result = {
                "marker": "older", "drafts_sendable": 1,
                "sent_ok": 1, "sent_failed": 0, "already_sent": 0,
                "sent_to": [{
                    "account": accounts[0], "to": "abuse@example.net",
                    "draft": "ordered.example_registry_report.txt",
                    "status": "sent", "ok": True,
                    "updated_at": "2026-08-30T01:00:00+00:00",
                }],
            }
            completed = queue.complete_send(
                item["queue_id"], older_result,
                attempted_accounts=[accounts[0]], send_job_id="older-job",
                attempted_at="2026-08-30T01:00:00+00:00",
            )
            first_updated_at = completed["updated_at"]
            unchanged = queue.complete_send(
                item["queue_id"], older_result,
                attempted_accounts=[accounts[0]], send_job_id="older-job",
                attempted_at="2026-08-30T01:00:00+00:00",
            )

            self.assertEqual(completed["state"], queue.SENT)
            self.assertEqual(completed["send_result"]["marker"], "newer")
            self.assertEqual(completed["attempt_accounts"], [accounts[1]])
            self.assertEqual(completed["last_attempt_at"], "2026-08-30T02:00:00+00:00")
            self.assertEqual(unchanged["updated_at"], first_updated_at)

    def test_one_attempted_account_does_not_complete_two_account_case(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, "REVIEW_DIR", directory):
            accounts = ["sender1@example.org", "sender2@example.org"]
            item = queue.enqueue_worker_result(
                job={"job_id": "source-two", "allowed_accounts": accounts},
                job_dir=directory,
                prepared={
                    "target_url": "https://remaining.example/",
                    "domain": "remaining.example",
                    "recipients": [{"channel": "registrar", "email": "abuse@example.net"}],
                },
                domain_result={
                    "target_url": "https://remaining.example/",
                    "domain": "remaining.example",
                    "skipped": "manual_review_required",
                },
            )
            queue.mark_selected_for_send(
                [item["queue_id"]], state=queue.QUEUED_CLOAKING,
                decision="confirmed_cloaking", send_job_id="send-one",
                attempt_accounts=[accounts[0]],
            )
            partial = queue.complete_send(
                item["queue_id"],
                {
                    "target_url": item["target_url"], "drafts_sendable": 1,
                    "sent_ok": 0, "sent_failed": 0, "already_sent": 1,
                    "sent_to": [{
                        "account": accounts[0], "to": "abuse@example.net",
                        "draft": "remaining.example_registrar_report.txt",
                        "ok": True, "status": "already_sent", "error": "",
                    }],
                },
                attempted_accounts=[accounts[0]], send_job_id="send-one",
            )

            self.assertEqual(partial["state"], queue.PARTIAL)
            self.assertEqual(queue.delivery_summary(partial)["pending_accounts"], [accounts[1]])

    def test_new_sender_scope_reopens_same_day_sent_case_as_partial(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, "REVIEW_DIR", directory):
            target = "https://expanded-scope.example/"
            first_account = "sender1@example.org"
            second_account = "sender2@example.org"
            item = queue.enqueue_worker_result(
                job={"job_id": "scope-one", "allowed_accounts": [first_account]},
                job_dir=directory,
                prepared={
                    "target_url": target, "domain": "expanded-scope.example",
                    "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                },
                domain_result={
                    "target_url": target, "domain": "expanded-scope.example",
                    "skipped": "manual_review_required",
                },
            )
            queue.mark_selected_for_send(
                [item["queue_id"]], state=queue.QUEUED_CLOAKING,
                decision="confirmed_cloaking", send_job_id="scope-send-one",
                attempt_accounts=[first_account],
            )
            sent = queue.complete_send(
                item["queue_id"], {
                    "drafts_sendable": 1, "sent_ok": 1, "sent_failed": 0,
                    "sent_to": [{
                        "account": first_account, "to": "abuse@example.net",
                        "draft": "expanded-scope.example_registry_report.txt",
                        "status": "sent", "ok": True,
                    }],
                }, attempted_accounts=[first_account], send_job_id="scope-send-one",
            )
            self.assertEqual(sent["state"], queue.SENT)

            reopened = queue.enqueue_worker_result(
                job={
                    "job_id": "scope-two",
                    "allowed_accounts": [first_account, second_account],
                },
                job_dir=directory,
                prepared=item["prepared"],
                domain_result={
                    "target_url": target, "domain": "expanded-scope.example",
                    "skipped": "manual_review_required",
                },
            )

            self.assertEqual(reopened["state"], queue.PARTIAL)
            self.assertEqual(
                queue.delivery_summary(reopened)["pending_accounts"],
                [second_account],
            )

    def test_failed_item_stays_failed_when_source_job_is_synced_again(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, "REVIEW_DIR", directory):
            item = queue.enqueue_worker_result(
                job={"job_id": "job-failed"}, job_dir=directory,
                prepared={"target_url": "https://failed.example/", "domain": "failed.example"},
                domain_result={
                    "target_url": "https://failed.example/", "domain": "failed.example",
                    "skipped": "manual_review_required",
                },
            )
            queue.update_item(item["queue_id"], state=queue.FAILED, last_error="SMTP failed")
            refreshed = queue.enqueue_worker_result(
                job={"job_id": "job-failed"}, job_dir=directory,
                prepared=item["prepared"], domain_result=item["result"],
            )

            self.assertEqual(refreshed["state"], queue.FAILED)
            self.assertEqual(refreshed["last_error"], "SMTP failed")

    def test_sync_migrates_latest_manual_review_result(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as review_dir:
            job_dir = os.path.join(root, "job-3")
            os.makedirs(job_dir)
            target = "https://target.example/"
            with open(os.path.join(job_dir, "job.json"), "w", encoding="utf-8") as output:
                json.dump({"job_id": "job-3", "allowed_accounts": []}, output)
            with open(os.path.join(job_dir, "preflight.json"), "w", encoding="utf-8") as output:
                json.dump({
                    "version": 2,
                    "ready": [{"target_url": target, "domain": "target.example", "recipients": []}],
                }, output)
            with open(os.path.join(job_dir, "status.json"), "w", encoding="utf-8") as output:
                json.dump({
                    "results": [
                        {"target_url": target, "skipped": "manual_review_required", "cloaking_score": 20},
                        {"target_url": target, "skipped": "manual_review_required", "cloaking_score": 70},
                    ],
                }, output)

            with patch.object(queue, "REVIEW_DIR", review_dir):
                self.assertEqual(queue.sync_from_worker_jobs(root), 1)
                items = queue.list_items({queue.PENDING_REVIEW})
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["result"]["cloaking_score"], 70)

    def test_sync_migrates_legacy_review_job_delivery_events_per_account(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as review_dir:
            target = "https://legacy-partial.example/"
            accounts = ["sender1@example.org", "sender2@example.org"]
            with patch.object(queue, "REVIEW_DIR", review_dir):
                item = queue.enqueue_worker_result(
                    job={"job_id": "source-legacy", "allowed_accounts": accounts},
                    job_dir=root,
                    prepared={
                        "target_url": target, "domain": "legacy-partial.example",
                        "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                    },
                    domain_result={
                        "target_url": target, "domain": "legacy-partial.example",
                        "skipped": "manual_review_required",
                    },
                )
                queue.mark_selected_for_send(
                    [item["queue_id"]], state=queue.QUEUED_CLOAKING,
                    decision="confirmed_cloaking", send_job_id="review-legacy",
                    attempt_accounts=[accounts[0]],
                )
                # Simulate a version-2 queue record written before per-account tracking.
                queue.update_item(item["queue_id"], version=2)

                job_dir = os.path.join(root, "review-legacy")
                os.makedirs(job_dir)
                with open(os.path.join(job_dir, "job.json"), "w", encoding="utf-8") as output:
                    json.dump({
                        "job_id": "review-legacy", "created_at": "2026-08-30T00:00:00+00:00",
                        "domains": [target], "allowed_accounts": [accounts[0]],
                        "review_queue_ids": {target: item["queue_id"]},
                    }, output)
                with open(os.path.join(job_dir, "preflight.json"), "w", encoding="utf-8") as output:
                    json.dump({"version": 2, "ready": [item["prepared"]]}, output)
                with open(os.path.join(job_dir, "status.json"), "w", encoding="utf-8") as output:
                    json.dump({"state": "completed", "results": [{
                        "target_url": target, "domain": "legacy-partial.example",
                        "drafts_sendable": 1, "sent_ok": 0,
                        "sent_failed": 0, "already_sent": 1, "sent_to": [],
                    }]}, output)
                with open(os.path.join(job_dir, "events.jsonl"), "w", encoding="utf-8") as output:
                    output.write(json.dumps({
                        "type": "draft_skipped", "domain": "legacy-partial.example",
                        "draft": "legacy-partial.example_registry_report.txt",
                        "account": accounts[0], "reason": "already_sent_today",
                    }) + "\n")

                queue.sync_from_worker_jobs(root)
                migrated = queue.load_item(item["queue_id"])
                first_updated_at = migrated["updated_at"]
                queue.sync_from_worker_jobs(root)
                migrated_again = queue.load_item(item["queue_id"])

            self.assertEqual(migrated["version"], 3)
            self.assertEqual(migrated["state"], queue.PARTIAL)
            summary = queue.delivery_summary(migrated)
            self.assertEqual(summary["completed_accounts"], [accounts[0]])
            self.assertEqual(summary["pending_accounts"], [accounts[1]])
            self.assertEqual(summary["deliveries"][0]["status"], "already_sent")
            self.assertEqual(summary["deliveries"][0]["to"], "abuse@example.net")
            self.assertEqual(migrated_again["updated_at"], first_updated_at)


if __name__ == "__main__":
    unittest.main()

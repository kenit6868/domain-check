import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cloaking_review_queue as review_queue
import cloaking_review_sender as sender
import phishing_toolkit as pt


class CloakingReviewSenderTests(unittest.TestCase):
    def _case(self, review_dir, job_dir, *, accounts=None, complete_evidence=True):
        accounts = accounts or ["sender@example.org"]
        root = Path(review_dir)
        manifest = root / "evidence.json"
        desktop = root / "desktop.png"
        mobile = root / "mobile.png"
        manifest.write_text('{"verdict":"LIKELY"}', encoding="utf-8")
        desktop.write_bytes(b"\x89PNG\r\n\x1a\n desktop evidence")
        if complete_evidence:
            mobile.write_bytes(b"\x89PNG\r\n\x1a\n mobile evidence")
        screenshots = [
            {"path": str(desktop), "profile": "desktop_direct", "label": "Desktop"},
            {"path": str(mobile), "profile": "mobile_google", "label": "Mobile Google"},
        ]
        cloaking = {
            "target_url": "https://review.example/path",
            "observed_at": "2026-08-31T01:00:00+00:00",
            "verdict": "LIKELY",
            "score": 90,
            "signals": [{
                "kind": "content_difference",
                "profiles": ["desktop_direct", "mobile_google"],
                "comparison": {"text_similarity": 0.1, "size_ratio": 3.0},
            }],
            "profiles": {},
            "screenshots": screenshots,
            "playwright": {
                "available": True,
                "verdict": "LIKELY",
                "score": 90,
                "screenshots": screenshots,
            },
            "evidence_path": str(manifest),
        }
        return review_queue.enqueue_worker_result(
            job={"job_id": "source", "allowed_accounts": accounts},
            job_dir=job_dir,
            prepared={
                "target_url": "https://review.example/path",
                "domain": "review.example",
                "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
            },
            domain_result={
                "target_url": "https://review.example/path",
                "domain": "review.example",
                "skipped": "manual_review_required",
                "cloaking_verdict": "LIKELY",
                "cloaking_score": 90,
                "cloaking_result": cloaking,
            },
        )

    @staticmethod
    def _draft(
        directory, *, with_old_evidence=False,
        filename="review.example_registry_report.txt",
        recipient="abuse@example.net",
    ):
        path = Path(directory) / filename
        body = "Dear Abuse Team,\n\nPlease investigate this phishing URL."
        if with_old_evidence:
            body += (
                "\n\n--- Technical Evidence: Multi-profile Cloaking Check ---\n"
                "Assessment: POSSIBLE\n"
                "--- End of Cloaking Evidence ---\n"
            )
        path.write_text(
            f"To: {recipient}\n"
            "Subject: Abuse Report: review.example\n\n"
            + body,
            encoding="utf-8",
        )
        return str(path)

    @staticmethod
    def _cfg(accounts=None):
        accounts = accounts or ["sender@example.org"]
        return {
            "contact_name": "Reporter",
            "contact_email": "contact@example.org",
            "smtp_accounts": [
                {
                    "username": account,
                    "password": "secret",
                    "host": "smtp.example.org",
                    "port": 587,
                }
                for account in accounts
            ],
            "smtp_proxies": [],
        }

    def test_confirmed_preview_uses_approved_evidence_when_recheck_is_no_signal(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as job_dir,
            tempfile.TemporaryDirectory() as report_dir,
            patch.object(review_queue, "REVIEW_DIR", review_dir),
        ):
            item = self._case(review_dir, job_dir)
            draft = self._draft(report_dir)
            with patch.object(pt, "run_check", return_value={
                "domain": "review.example",
                "drafts": [draft],
                "drafts_error": "",
                "cloaking": {"verdict": "NO_SIGNAL", "score": 0},
            }):
                prepared = sender.prepare_review_delivery(
                    item["queue_id"],
                    decision=sender.CONFIRMED_CLOAKING,
                    account_names=["sender@example.org"],
                    cfg=self._cfg(),
                )

        self.assertEqual(len(prepared["attachments"]), 3)
        body = prepared["deliveries"][0]["body"]
        self.assertIn("Operator disposition: CONFIRMED CLOAKING", body)
        self.assertIn("Cloaking behavior was observed at the reported URL", body)
        self.assertIn("Assessment: LIKELY (score: 90/100)", body)

    def test_legacy_inconclusive_operator_pair_can_prepare_confirmed_preview(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as job_dir,
            tempfile.TemporaryDirectory() as report_dir,
            patch.object(review_queue, "REVIEW_DIR", review_dir),
        ):
            item = self._case(review_dir, job_dir)
            queued = review_queue.load_item(item["queue_id"])
            evidence = dict(queued["result"]["cloaking_result"])
            manual_screenshots = list(evidence["screenshots"])
            evidence.update({
                "verdict": "INCONCLUSIVE",
                "score": 10,
                "screenshots": [],
                "operator_evidence": {
                    "confirmed_difference": True,
                    "screenshots": manual_screenshots,
                    "manifest_path": evidence["evidence_path"],
                },
            })
            review_queue.update_cloaking_result(item["queue_id"], evidence)
            status = sender.confirmed_evidence_status(evidence)
            self.assertTrue(status["ready"])
            self.assertEqual(status["verdict"], "POSSIBLE")
            self.assertEqual(status["pair_source"], "operator")
            draft = self._draft(report_dir)
            with patch.object(pt, "run_check", return_value={
                "domain": "review.example", "drafts": [draft], "drafts_error": "",
            }):
                prepared = sender.prepare_review_delivery(
                    item["queue_id"],
                    decision=sender.CONFIRMED_CLOAKING,
                    account_names=["sender@example.org"],
                    cfg=self._cfg(),
                )

        self.assertEqual(len(prepared["attachments"]), 3)
        self.assertIn("Assessment: POSSIBLE", prepared["deliveries"][0]["body"])

    def test_not_cloaking_preview_removes_evidence_and_attachments(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as job_dir,
            tempfile.TemporaryDirectory() as report_dir,
            patch.object(review_queue, "REVIEW_DIR", review_dir),
        ):
            item = self._case(review_dir, job_dir)
            draft = self._draft(report_dir, with_old_evidence=True)
            with patch.object(pt, "run_check", return_value={
                "domain": "review.example", "drafts": [draft], "drafts_error": "",
            }):
                prepared = sender.prepare_review_delivery(
                    item["queue_id"],
                    decision=sender.NOT_CLOAKING,
                    account_names=["sender@example.org"],
                    cfg=self._cfg(),
                )

        self.assertEqual(prepared["attachments"], [])
        self.assertNotIn("Multi-profile Cloaking Check", prepared["deliveries"][0]["body"])

    def test_changed_evidence_after_preview_is_blocked_before_smtp(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as job_dir,
            tempfile.TemporaryDirectory() as report_dir,
            patch.object(review_queue, "REVIEW_DIR", review_dir),
        ):
            item = self._case(review_dir, job_dir)
            draft = self._draft(report_dir)
            cfg = self._cfg()
            with patch.object(pt, "run_check", return_value={
                "domain": "review.example", "drafts": [draft], "drafts_error": "",
            }):
                prepared = sender.prepare_review_delivery(
                    item["queue_id"],
                    decision=sender.CONFIRMED_CLOAKING,
                    account_names=["sender@example.org"],
                    cfg=cfg,
                )
            Path(prepared["attachments"][1]).write_bytes(
                b"\x89PNG\r\n\x1a\n changed after preview",
            )
            with patch.object(pt, "send_report_email_single") as send:
                with self.assertRaisesRegex(ValueError, "Nội dung evidence đã thay đổi"):
                    sender.send_prepared_review(prepared, cfg)
            send.assert_not_called()

    def test_direct_send_uses_previewed_body_and_completes_queue_without_job(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as job_dir,
            tempfile.TemporaryDirectory() as report_dir,
            patch.object(review_queue, "REVIEW_DIR", review_dir),
        ):
            item = self._case(review_dir, job_dir)
            draft = self._draft(report_dir)
            cfg = self._cfg()
            with patch.object(pt, "run_check", return_value={
                "domain": "review.example", "drafts": [draft], "drafts_error": "",
            }):
                prepared = sender.prepare_review_delivery(
                    item["queue_id"],
                    decision=sender.CONFIRMED_CLOAKING,
                    account_names=["sender@example.org"],
                    cfg=cfg,
                )
            with (
                patch.object(pt, "send_report_email_single", return_value={
                    "success": True, "account": "sender@example.org", "error": "",
                }) as send,
                patch.object(pt, "log_sent") as log_sent,
            ):
                result = sender.send_prepared_review(prepared, cfg)

            sent_args = send.call_args.args
            self.assertEqual(sent_args[2], prepared["deliveries"][0]["body"])
            self.assertEqual(send.call_args.kwargs["attachments"], prepared["attachments"])
            self.assertEqual(result["queue_state"], review_queue.SENT)
            updated = review_queue.load_item(item["queue_id"])
            self.assertEqual(updated["decision"], sender.CONFIRMED_CLOAKING)
            self.assertEqual(updated["state"], review_queue.SENT)
            self.assertFalse(any(name.startswith("review_") for name in os.listdir(review_dir)))
            log_sent.assert_called_once()

    def test_direct_send_keeps_case_partial_for_unattempted_required_account(self):
        accounts = ["sender1@example.org", "sender2@example.org"]
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as job_dir,
            tempfile.TemporaryDirectory() as report_dir,
            patch.object(review_queue, "REVIEW_DIR", review_dir),
        ):
            item = self._case(review_dir, job_dir, accounts=accounts)
            draft = self._draft(report_dir)
            cfg = self._cfg(accounts)
            with patch.object(pt, "run_check", return_value={
                "domain": "review.example", "drafts": [draft], "drafts_error": "",
            }):
                prepared = sender.prepare_review_delivery(
                    item["queue_id"],
                    decision=sender.CONFIRMED_CLOAKING,
                    account_names=[accounts[0]],
                    cfg=cfg,
                )
            with (
                patch.object(pt, "send_report_email_single", return_value={
                    "success": True, "account": accounts[0], "error": "",
                }),
                patch.object(pt, "log_sent"),
            ):
                result = sender.send_prepared_review(prepared, cfg)

            self.assertEqual(result["queue_state"], review_queue.PARTIAL)
            summary = review_queue.delivery_summary(review_queue.load_item(item["queue_id"]))
            self.assertEqual(summary["completed_accounts"], [accounts[0]])
            self.assertEqual(summary["pending_accounts"], [accounts[1]])

    def test_interrupted_direct_send_keeps_completed_delivery_for_retry(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as job_dir,
            tempfile.TemporaryDirectory() as report_dir,
            patch.object(review_queue, "REVIEW_DIR", review_dir),
        ):
            item = self._case(review_dir, job_dir)
            drafts = [
                self._draft(report_dir),
                self._draft(
                    report_dir,
                    filename="review.example_host_report.txt",
                    recipient="security@example.net",
                ),
            ]
            cfg = self._cfg()
            with patch.object(pt, "run_check", return_value={
                "domain": "review.example", "drafts": drafts, "drafts_error": "",
            }):
                prepared = sender.prepare_review_delivery(
                    item["queue_id"],
                    decision=sender.CONFIRMED_CLOAKING,
                    account_names=["sender@example.org"],
                    cfg=cfg,
                )
                with (
                    patch.object(
                        pt, "send_report_email_single",
                        side_effect=[
                            {"success": True, "account": "sender@example.org", "error": ""},
                            KeyboardInterrupt(),
                        ],
                    ),
                    patch.object(pt, "log_sent"),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        sender.send_prepared_review(prepared, cfg)

                interrupted = review_queue.load_item(item["queue_id"])
                delivered = review_queue.delivery_summary(interrupted)["deliveries"]
                self.assertEqual(
                    [row["to"] for row in delivered if row["status"] == "sent"],
                    ["abuse@example.net"],
                )

                prepared_retry = sender.prepare_review_delivery(
                    item["queue_id"],
                    decision=sender.CONFIRMED_CLOAKING,
                    account_names=["sender@example.org"],
                    cfg=cfg,
                )
                with (
                    patch.object(pt, "send_report_email_single", return_value={
                        "success": True, "account": "sender@example.org", "error": "",
                    }) as send_retry,
                    patch.object(pt, "log_sent"),
                ):
                    retried = sender.send_prepared_review(prepared_retry, cfg)

            self.assertEqual(send_retry.call_count, 1)
            self.assertEqual(send_retry.call_args.args[0], "security@example.net")
            self.assertEqual(retried["queue_state"], review_queue.SENT)

    def test_confirmed_preview_fails_closed_without_two_images(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as job_dir,
            tempfile.TemporaryDirectory() as report_dir,
            patch.object(review_queue, "REVIEW_DIR", review_dir),
        ):
            item = self._case(review_dir, job_dir, complete_evidence=False)
            draft = self._draft(report_dir)
            with patch.object(pt, "run_check", return_value={
                "domain": "review.example", "drafts": [draft], "drafts_error": "",
            }) as run_check:
                with self.assertRaisesRegex(ValueError, "hai ảnh đối chiếu"):
                    sender.prepare_review_delivery(
                        item["queue_id"],
                        decision=sender.CONFIRMED_CLOAKING,
                        account_names=["sender@example.org"],
                        cfg=self._cfg(),
                    )
            run_check.assert_not_called()


if __name__ == "__main__":
    unittest.main()

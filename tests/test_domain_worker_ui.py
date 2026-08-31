import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import cloaking_review_queue as review_queue
import domain_worker
import phishing_toolkit as pt


ROOT = Path(__file__).resolve().parents[1]


class DomainWorkerUiTests(unittest.TestCase):
    def test_v3_precheck_renders_normal_and_early_cloaking_counts(self):
        with (
            tempfile.TemporaryDirectory() as runtime_dir,
            tempfile.TemporaryDirectory() as review_dir,
        ):
            runtime = Path(runtime_dir)
            worker_dir = runtime / "worker_jobs"
            cloaking_worker_dir = runtime / "cloaking_send_jobs"
            job_dir = worker_dir / "v3-ui"
            job_dir.mkdir(parents=True)
            cloaking_worker_dir.mkdir()
            normal = "https://normal-ui.example/path"
            cloaked = "https://cloaked-ui.example/path"
            job = {
                "job_id": "v3-ui", "domains": [normal, cloaked],
                "allowed_accounts": ["sender@example.org"],
                "precheck_only": True, "preflight_version": 3,
            }
            (job_dir / "job.json").write_text(
                json.dumps(job), encoding="utf-8",
            )
            (job_dir / "status.json").write_text(json.dumps({
                "job_id": "v3-ui", "state": "ready",
                "precheck_total": 2, "precheck_processed": 2,
                "ready_total": 1, "precheck_cached": 0,
                "cloaking_review_total": 1,
                "excluded_no_email": [], "results": [],
            }), encoding="utf-8")
            prepared_normal = {
                "target_url": normal, "domain": "normal-ui.example",
                "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                "cloaking_verdict": "NO_SIGNAL", "cloaking_score": 0,
            }
            prepared_cloaked = {
                "target_url": cloaked, "domain": "cloaked-ui.example",
                "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                "cloaking_verdict": "LIKELY", "cloaking_score": 80,
                "queue_state": review_queue.PENDING_REVIEW,
            }
            (job_dir / "preflight.json").write_text(json.dumps({
                "version": 3, "complete": True,
                "ready": [prepared_normal],
                "cloaking_review": [prepared_cloaked],
                "excluded_no_email": [], "excluded_already_sent": [],
            }), encoding="utf-8")
            legacy_review_dir = worker_dir / "review_legacy_newer"
            legacy_review_dir.mkdir()
            (legacy_review_dir / "job.json").write_text(json.dumps({
                "job_id": "review_legacy_newer",
                "review_queue_ids": {cloaked: "legacy-id"},
                "preflight_version": 2,
            }), encoding="utf-8")
            (legacy_review_dir / "status.json").write_text(json.dumps({
                "job_id": "review_legacy_newer", "state": "completed",
                "ready_total": 0, "results": [],
            }), encoding="utf-8")

            with patch.object(review_queue, "REVIEW_DIR", review_dir):
                review_queue.enqueue_worker_result(
                    job=job, job_dir=str(job_dir), prepared=prepared_cloaked,
                    domain_result={
                        "target_url": cloaked, "domain": "cloaked-ui.example",
                        "skipped": "manual_review_required",
                        "cloaking_verdict": "LIKELY", "cloaking_score": 80,
                    },
                )
                with (
                    patch.object(domain_worker, "WORKER_DIR", str(worker_dir)),
                    patch.object(domain_worker, "CLOAKING_WORKER_DIR", str(cloaking_worker_dir)),
                    patch.object(domain_worker, "NO_EMAIL_LOG_PATH", str(runtime / "no_email.csv")),
                    patch.object(pt, "SENT_LOG_PATH", str(runtime / "sent.csv")),
                    patch.object(pt, "load_config", return_value={
                        "smtp_accounts": [{"username": "sender@example.org"}],
                    }),
                ):
                    app = AppTest.from_file(
                        str(ROOT / "streamlit_app.py"), default_timeout=10,
                    ).run()
                    app = app.switch_page("pages/6_Domain_Worker.py").run()

            self.assertEqual(list(app.exception), [])
            metrics = {metric.label: str(metric.value) for metric in app.metric}
            self.assertEqual(metrics["Domain sẵn sàng"], "1")
            self.assertEqual(metrics["Cloaking tách riêng"], "1")
            self.assertTrue(any(
                "Bạn có thể mở Cloaking Review" in warning.value
                for warning in app.warning
            ))
            self.assertIn(
                "🔎 Check toàn bộ, lọc email & cloaking",
                [button.label for button in app.button],
            )

    def test_no_email_cloaking_is_not_counted_or_linked_for_review(self):
        with (
            tempfile.TemporaryDirectory() as runtime_dir,
            tempfile.TemporaryDirectory() as review_dir,
        ):
            runtime = Path(runtime_dir)
            worker_dir = runtime / "worker_jobs"
            cloaking_worker_dir = runtime / "cloaking_send_jobs"
            job_dir = worker_dir / "no-email-ui"
            job_dir.mkdir(parents=True)
            cloaking_worker_dir.mkdir()
            target = "https://no-email-ui.example/path"
            job = {
                "job_id": "no-email-ui", "domains": [target],
                "allowed_accounts": ["sender@example.org"],
                "precheck_only": True, "preflight_version": 3,
            }
            excluded = {
                "target_url": target, "domain": "no-email-ui.example",
                "status": "no_sendable_email", "cloaking_verdict": "LIKELY",
                "cloaking_score": 80, "cloaking_review_skipped": True,
            }
            prepared = {
                "target_url": target, "domain": "no-email-ui.example",
                "recipients": [], "cloaking_verdict": "LIKELY",
                "cloaking_score": 80,
            }
            (job_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
            (job_dir / "status.json").write_text(json.dumps({
                "job_id": "no-email-ui", "state": "ready",
                "precheck_total": 1, "precheck_processed": 1,
                "ready_total": 0, "precheck_cached": 1,
                "cloaking_review_total": 1,
                "excluded_no_email": [excluded], "results": [],
            }), encoding="utf-8")
            (job_dir / "preflight.json").write_text(json.dumps({
                "version": 3, "complete": True, "ready": [],
                "cloaking_review": [prepared],
                "excluded_no_email": [excluded], "excluded_already_sent": [],
            }), encoding="utf-8")

            with patch.object(review_queue, "REVIEW_DIR", review_dir):
                review_queue.enqueue_worker_result(
                    job=job, job_dir=str(job_dir), prepared=prepared,
                    domain_result={
                        "target_url": target, "domain": "no-email-ui.example",
                        "skipped": "manual_review_required",
                        "cloaking_verdict": "LIKELY", "cloaking_score": 80,
                    },
                )
                with (
                    patch.object(domain_worker, "WORKER_DIR", str(worker_dir)),
                    patch.object(
                        domain_worker, "CLOAKING_WORKER_DIR", str(cloaking_worker_dir),
                    ),
                    patch.object(
                        domain_worker, "NO_EMAIL_LOG_PATH", str(runtime / "no_email.csv"),
                    ),
                    patch.object(pt, "SENT_LOG_PATH", str(runtime / "sent.csv")),
                    patch.object(pt, "load_config", return_value={
                        "smtp_accounts": [{"username": "sender@example.org"}],
                    }),
                ):
                    app = AppTest.from_file(
                        str(ROOT / "streamlit_app.py"), default_timeout=10,
                    ).run()
                    app = app.switch_page("pages/6_Domain_Worker.py").run()

            self.assertEqual(list(app.exception), [])
            metrics = {metric.label: str(metric.value) for metric in app.metric}
            self.assertEqual(metrics["Cloaking tách riêng"], "0")
            self.assertFalse(any(
                "Bạn có thể mở Cloaking Review" in warning.value
                for warning in app.warning
            ))
            self.assertTrue(any(
                "không có email nhận" in info.value for info in app.info
            ))


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import cloaking_review_queue as review_queue
import domain_worker
import phishing_toolkit as pt


ROOT = Path(__file__).resolve().parents[1]


class CloakingReviewUiTests(unittest.TestCase):
    def test_pending_queue_uses_native_multi_row_selection(self):
        source = (ROOT / "pages" / "10_Cloaking_Review.py").read_text(encoding="utf-8")
        self.assertIn('selection_mode="multi-row"', source)
        self.assertIn('on_select="rerun"', source)
        self.assertNotIn("st.data_editor(", source)
        self.assertIn('"Email nhận"', source)
        self.assertIn('"Đã gửi từ"', source)
        self.assertIn('"Còn chờ"', source)
        self.assertIn('"Tiến độ gửi email"', source)
        self.assertIn("review_queue.delivery_summary", source)
        self.assertIn("find_active_cloaking_job_dir", source)
        self.assertNotIn("active_job_dir = domain_worker.find_active_job_dir()", source)
        self.assertIn("CLOAKING_WORKER_DIR", source)

    def test_partial_sender_progress_renders_in_streamlit(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as worker_dir,
            tempfile.TemporaryDirectory() as cloaking_worker_dir,
        ):
            accounts = ["sender1@example.org", "sender2@example.org"]
            with patch.object(review_queue, "REVIEW_DIR", review_dir):
                item = review_queue.enqueue_worker_result(
                    job={"job_id": "ui-source", "allowed_accounts": accounts},
                    job_dir=worker_dir,
                    prepared={
                        "target_url": "https://ui-progress.example/",
                        "domain": "ui-progress.example",
                        "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                    },
                    domain_result={
                        "target_url": "https://ui-progress.example/",
                        "domain": "ui-progress.example",
                        "skipped": "manual_review_required",
                    },
                )
                review_queue.mark_selected_for_send(
                    [item["queue_id"]], state=review_queue.QUEUED_CLOAKING,
                    decision="confirmed_cloaking", send_job_id="ui-send",
                    attempt_accounts=[accounts[0]],
                )
                review_queue.complete_send(
                    item["queue_id"], {
                        "drafts_sendable": 1, "sent_ok": 1,
                        "sent_failed": 0, "already_sent": 0,
                        "sent_to": [{
                            "account": accounts[0], "to": "abuse@example.net",
                            "draft": "ui-progress.example_registry_report.txt",
                            "status": "sent", "ok": True,
                        }],
                    }, attempted_accounts=[accounts[0]], send_job_id="ui-send",
                )
                with (
                    patch.object(domain_worker, "WORKER_DIR", worker_dir),
                    patch.object(domain_worker, "CLOAKING_WORKER_DIR", cloaking_worker_dir),
                    patch.object(pt, "load_config", return_value={
                        "smtp_accounts": [{"username": account} for account in accounts],
                    }),
                ):
                    app = AppTest.from_file(
                        str(ROOT / "pages" / "10_Cloaking_Review.py"),
                        default_timeout=10,
                    ).run()

            self.assertEqual(list(app.exception), [])
            overview = app.dataframe[0].value
            self.assertEqual(overview.iloc[0]["Tiến độ gửi email"], "1/2")
            self.assertEqual(overview.iloc[0]["Email nhận"], "abuse@example.net")
            self.assertEqual(overview.iloc[0]["Đã gửi từ"], accounts[0])
            self.assertEqual(overview.iloc[0]["Còn chờ"], accounts[1])

    def test_active_primary_worker_does_not_disable_cloaking_send_actions(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as worker_dir,
            tempfile.TemporaryDirectory() as cloaking_worker_dir,
        ):
            primary_dir = Path(worker_dir) / "primary-active"
            primary_dir.mkdir()
            (primary_dir / "job.json").write_text(
                '{"job_id":"primary-active","domains":["normal.example"]}',
                encoding="utf-8",
            )
            (primary_dir / "status.json").write_text(
                '{"state":"running"}', encoding="utf-8",
            )
            with patch.object(review_queue, "REVIEW_DIR", review_dir):
                review_queue.enqueue_worker_result(
                    job={
                        "job_id": "ui-independent",
                        "allowed_accounts": ["sender@example.org"],
                    },
                    job_dir=worker_dir,
                    prepared={
                        "target_url": "https://ui-independent.example/",
                        "domain": "ui-independent.example",
                        "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                    },
                    domain_result={
                        "target_url": "https://ui-independent.example/",
                        "domain": "ui-independent.example",
                        "skipped": "manual_review_required",
                    },
                )
                with (
                    patch.object(domain_worker, "WORKER_DIR", worker_dir),
                    patch.object(domain_worker, "CLOAKING_WORKER_DIR", cloaking_worker_dir),
                    patch.object(pt, "load_config", return_value={
                        "smtp_accounts": [{"username": "sender@example.org"}],
                    }),
                ):
                    app = AppTest.from_file(
                        str(ROOT / "pages" / "10_Cloaking_Review.py"),
                        default_timeout=10,
                    ).run()

            self.assertEqual(list(app.exception), [])
            buttons = {button.label: button for button in app.button}
            self.assertFalse(
                buttons["Xác nhận cloaking và gửi kèm bằng chứng"].disabled,
            )
            self.assertFalse(
                buttons["Không phải cloaking — gửi report thường"].disabled,
            )


if __name__ == "__main__":
    unittest.main()

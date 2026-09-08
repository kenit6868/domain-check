import json
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import cloaking_review_queue as review_queue
import domain_worker
import browser_evidence
import phishing_toolkit as pt


ROOT = Path(__file__).resolve().parents[1]
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class DomainWorkerUiTests(unittest.TestCase):
    def test_v4_ui_exposes_manual_evidence_send_flow(self):
        worker_source = (ROOT / "pages" / "6_Domain_Worker.py").read_text(encoding="utf-8")
        review_source = (ROOT / "pages" / "12_Domain_Evidence_Review.py").read_text(encoding="utf-8")
        self.assertIn('"preflight_version": 4', worker_source)
        self.assertIn('cached_preflight.get("evidence_review")', worker_source)
        self.assertIn("Mở Domain Evidence Review", worker_source)
        self.assertNotIn("file_uploader", worker_source)
        self.assertNotIn("create_manual_browser_evidence", worker_source)
        self.assertNotIn("send_manual_evidence_item", worker_source)
        self.assertIn("Ảnh bằng chứng thủ công (1–3 ảnh PNG/JPEG)", review_source)
        self.assertIn("prepare_manual_evidence_preview", review_source)
        self.assertIn("đúng draft đã preview", review_source)

    def test_v4_manual_evidence_case_renders_without_sending(self):
        with tempfile.TemporaryDirectory() as runtime_dir, tempfile.TemporaryDirectory() as review_dir:
            runtime = Path(runtime_dir)
            worker_dir = runtime / "worker_jobs"
            job_dir = worker_dir / "v4-evidence-ui"
            job_dir.mkdir(parents=True)
            (runtime / "cloaking_send_jobs").mkdir()
            target = "https://manual-ui.example/path"
            job = {
                "job_id": "v4-evidence-ui", "domains": [target],
                "allowed_accounts": ["sender@example.org"],
                "precheck_only": True, "preflight_version": 4,
            }
            (job_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
            (job_dir / "status.json").write_text(json.dumps({
                "job_id": "v4-evidence-ui", "state": "ready", "ready_total": 0,
                "precheck_total": 1, "precheck_processed": 1, "precheck_cached": 0,
                "cloaking_review_total": 0, "evidence_review_total": 1,
                "excluded_no_email": [], "results": [],
            }), encoding="utf-8")
            (job_dir / "preflight.json").write_text(json.dumps({
                "version": 4, "complete": True, "ready": [], "cloaking_review": [],
                "evidence_review": [{
                    "target_url": target, "domain": "manual-ui.example",
                    "recipients": [{"channel": "registrar", "email": "abuse@example.org"}],
                    "browser_evidence_error": "capture unavailable",
                }],
                "excluded_no_email": [], "excluded_already_sent": [],
            }), encoding="utf-8")
            with (
                patch.object(review_queue, "REVIEW_DIR", review_dir),
                patch.object(domain_worker, "WORKER_DIR", str(worker_dir)),
                patch.object(domain_worker, "CLOAKING_WORKER_DIR", str(runtime / "cloaking_send_jobs")),
                patch.object(domain_worker, "NO_EMAIL_LOG_PATH", str(runtime / "no_email.csv")),
                patch.object(pt, "SENT_LOG_PATH", str(runtime / "sent.csv")),
                patch.object(pt, "load_config", return_value={
                    "smtp_accounts": [{"username": "sender@example.org"}],
                }),
                patch.object(domain_worker, "send_manual_evidence_item") as send,
            ):
                app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=10).run()
                app = app.switch_page("pages/6_Domain_Worker.py").run()
            self.assertEqual([], list(app.exception))
            self.assertEqual([], list(app.file_uploader))
            send.assert_not_called()

    def test_dedicated_evidence_review_page_lists_daily_case_without_sending(self):
        with tempfile.TemporaryDirectory() as runtime_dir:
            runtime = Path(runtime_dir)
            worker_dir = runtime / "worker_jobs"
            job_dir = worker_dir / "evidence-review-page"
            job_dir.mkdir(parents=True)
            target = "https://manual-page.example/path"
            (job_dir / "job.json").write_text(json.dumps({
                "job_id": "evidence-review-page", "domains": [target],
                "allowed_accounts": ["sender@example.org"],
            }), encoding="utf-8")
            (job_dir / "preflight.json").write_text(json.dumps({
                "version": 4, "complete": False, "ready": [],
                "evidence_review": [{
                    "target_url": target, "domain": "manual-page.example",
                    "recipients": [{"channel": "registrar", "email": "abuse@example.org"}],
                    "browser_evidence_error": "automatic capture unavailable",
                }],
            }), encoding="utf-8")
            with (
                patch.object(domain_worker, "WORKER_DIR", str(worker_dir)),
                patch.object(pt, "load_config", return_value={
                    "smtp_accounts": [{"username": "sender@example.org"}],
                }),
            ):
                app = AppTest.from_file(
                    str(ROOT / "streamlit_app.py"), default_timeout=10,
                ).run()
                app = app.switch_page("pages/12_Domain_Evidence_Review.py").run()
            self.assertEqual([], list(app.exception))
            self.assertGreaterEqual(len(app.dataframe), 1)
            self.assertTrue(any("Danh sách hôm nay" in caption.value for caption in app.caption))

    def test_dedicated_evidence_review_upload_sends_directly_without_new_job(self):
        with tempfile.TemporaryDirectory() as runtime_dir, tempfile.TemporaryDirectory() as evidence_dir:
            runtime = Path(runtime_dir)
            worker_dir = runtime / "worker_jobs"
            job_dir = worker_dir / "evidence-review-send"
            job_dir.mkdir(parents=True)
            target = "https://manual-send-page.example/path"
            (job_dir / "job.json").write_text(json.dumps({
                "job_id": "evidence-review-send", "domains": [target],
                "allowed_accounts": ["sender@example.org"],
            }), encoding="utf-8")
            (job_dir / "preflight.json").write_text(json.dumps({
                "version": 4, "complete": True, "ready": [],
                "evidence_review": [{
                    "target_url": target, "domain": "manual-send-page.example",
                    "recipients": [{"channel": "registrar", "email": "abuse@example.org"}],
                    "browser_evidence_error": "automatic capture unavailable",
                }],
            }), encoding="utf-8")
            uploaded_evidence = {
                "success": True,
                "evidence_type": "manual_upload",
                "requested_url": target,
                "screenshot_paths": [],
                "manifest_path": "",
            }
            send_result = {
                "target_url": target, "domain": "manual-send-page.example",
                "success": True, "sent_ok": 1, "sent_failed": 0,
                "already_sent": 0, "sent_to": [],
            }
            preview = {
                "version": 1,
                "job_dir": str(job_dir),
                "target_url": target,
                "accounts": ["sender@example.org"],
                "evidence": uploaded_evidence,
                "upload_signature": [{"name": "source.png", "size": len(PNG_1X1), "sha256": __import__("hashlib").sha256(PNG_1X1).hexdigest()}],
                "delivery_plan": [{
                    "account": "sender@example.org", "to": "abuse@example.org",
                    "draft": "manual-send-page.example_registrar_report.txt",
                    "subject": "Report", "body": "Draft body",
                }],
            }
            with (
                patch.object(domain_worker, "WORKER_DIR", str(worker_dir)),
                patch.object(pt, "load_config", return_value={
                    "smtp_accounts": [{"username": "sender@example.org"}],
                }),
                patch.object(
                    browser_evidence, "create_manual_browser_evidence",
                    return_value=uploaded_evidence,
                ) as create_evidence,
                patch.object(
                    domain_worker, "prepare_manual_evidence_preview",
                    return_value=preview,
                ) as prepare_preview,
                patch.object(
                    domain_worker, "manual_evidence_preview_is_current",
                    return_value=True,
                ),
                patch.object(
                    domain_worker, "send_manual_evidence_item",
                    return_value=send_result,
                ) as send_manual,
            ):
                app = AppTest.from_file(
                    str(ROOT / "streamlit_app.py"), default_timeout=10,
                )
                app.session_state["domain_evidence_review_active_target"] = target
                app = app.run().switch_page(
                    "pages/12_Domain_Evidence_Review.py",
                ).run()
                uploader = next(
                    item for item in app.file_uploader
                    if item.label == "Ảnh bằng chứng thủ công (1–3 ảnh PNG/JPEG)"
                )
                app = uploader.set_value([
                    ("source.png", PNG_1X1, "image/png"),
                    ("broken.png", b"not-an-image", "image/png"),
                ]).run()
                mixed_send = next(
                    item for item in app.button if item.label == "Gửi mail domain này"
                )
                self.assertTrue(mixed_send.disabled)
                uploader = next(
                    item for item in app.file_uploader
                    if item.label == "Ảnh bằng chứng thủ công (1–3 ảnh PNG/JPEG)"
                )
                app = uploader.set_value([
                    ("source.png", PNG_1X1, "image/png"),
                ]).run()
                preview_button = next(
                    item for item in app.button
                    if item.label == "Tạo / cập nhật draft để xem"
                )
                self.assertFalse(preview_button.disabled)
                app = preview_button.click().run()
                self.assertTrue(any("Draft body" in block.value for block in app.code))
                confirmation = next(
                    item for item in app.checkbox
                    if item.label.startswith("Tôi đã đọc draft")
                )
                app = confirmation.check().run()
                send_button = next(
                    item for item in app.button if item.label == "Gửi mail domain này"
                )
                self.assertFalse(send_button.disabled)
                app = send_button.click().run()
            self.assertEqual([], list(app.exception))
            create_evidence.assert_called_once()
            prepare_preview.assert_called_once_with(
                str(job_dir), target, uploaded_evidence, ["sender@example.org"],
            )
            send_manual.assert_called_once_with(
                str(job_dir), target, uploaded_evidence, ["sender@example.org"],
                preview=preview,
            )
            self.assertTrue(any(
                "Đã gửi email thành công" in success.value for success in app.success
            ))

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

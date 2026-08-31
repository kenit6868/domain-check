import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

import cloaking_review_queue as review_queue
import cloaking_review_sender as review_sender
import phishing_toolkit as pt


ROOT = Path(__file__).resolve().parents[1]
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zp1sAAAAASUVORK5CYII="
)


class CloakingReviewUiTests(unittest.TestCase):
    def test_today_table_uses_per_row_actions_and_direct_send_workflow(self):
        source = (ROOT / "pages" / "10_Cloaking_Review.py").read_text(encoding="utf-8")
        build_spec = (ROOT / "PhishingTool.spec").read_text(encoding="utf-8")
        self.assertIn("st.column_config.ButtonColumn", source)
        self.assertIn('getattr(review_queue, "list_items_for_day", None)', source)
        self.assertIn('active_case_key = "cloaking_review_active_case"', source)
        self.assertNotIn('"Danh sách hiển thị"', source)
        self.assertNotIn(".metric(", source)
        self.assertNotIn('on_select="rerun"', source)
        self.assertNotIn("st.data_editor(", source)
        self.assertIn('"Email nhận"', source)
        self.assertIn('"Đã gửi từ"', source)
        self.assertIn('"Còn chờ"', source)
        self.assertIn('"Trạng thái gửi"', source)
        self.assertIn("review_queue.delivery_summary", source)
        self.assertIn('getattr(review_queue, "has_sendable_recipient", None)', source)
        self.assertIn("review_sender.prepare_review_delivery", source)
        self.assertIn("review_sender.send_prepared_review", source)
        self.assertIn("Draft mẫu trước khi gửi", source)
        self.assertIn("needs_manual_evidence", source)
        self.assertIn("confirmed_evidence_blocked", source)
        self.assertIn("Xác nhận ảnh & tạo draft để xem", source)
        self.assertIn("st.image(", source)
        self.assertNotIn("form_submit_button", source)
        self.assertNotIn('"Lưu ảnh thủ công"', source)
        self.assertNotIn("create_cloaking_review_job", source)
        self.assertNotIn("launch_job_process", source)
        self.assertNotIn("find_active_cloaking_job_dir", source)
        self.assertNotIn("import domain_worker", source)
        self.assertIn('("cloaking_review_sender.py", ".")', build_spec)

    def test_today_table_survives_stale_cached_queue_module(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as worker_dir,
        ):
            with patch.object(review_queue, "REVIEW_DIR", review_dir):
                item = review_queue.enqueue_worker_result(
                    job={"job_id": "ui-stale-module"},
                    job_dir=worker_dir,
                    prepared={
                        "target_url": "https://stale-module.example/path",
                        "domain": "stale-module.example",
                        "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                    },
                    domain_result={
                        "target_url": "https://stale-module.example/path",
                        "domain": "stale-module.example",
                        "skipped": "manual_review_required",
                    },
                )
                current_review_day = review_queue.current_review_day
                list_items_for_day = review_queue.list_items_for_day
                try:
                    delattr(review_queue, "current_review_day")
                    delattr(review_queue, "list_items_for_day")
                    with patch.object(
                        review_queue, "sync_from_worker_jobs", return_value=0,
                    ):
                        app = AppTest.from_file(
                            str(ROOT / "pages" / "10_Cloaking_Review.py"),
                            default_timeout=10,
                        ).run()
                finally:
                    review_queue.current_review_day = current_review_day
                    review_queue.list_items_for_day = list_items_for_day

            self.assertEqual(list(app.exception), [])
            table = app.dataframe[0].value
            self.assertEqual(len(table), 1)
            self.assertEqual(table.iloc[0]["Full URL"], item["target_url"])

    def test_today_table_hides_legacy_case_without_recipient(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as worker_dir,
        ):
            with patch.object(review_queue, "REVIEW_DIR", review_dir):
                review_queue.enqueue_worker_result(
                    job={"job_id": "ui-no-recipient"}, job_dir=worker_dir,
                    prepared={
                        "target_url": "https://hidden-no-email.example/",
                        "domain": "hidden-no-email.example", "recipients": [],
                    },
                    domain_result={
                        "target_url": "https://hidden-no-email.example/",
                        "domain": "hidden-no-email.example",
                        "skipped": "manual_review_required",
                    },
                )
                visible = review_queue.enqueue_worker_result(
                    job={"job_id": "ui-with-recipient"}, job_dir=worker_dir,
                    prepared={
                        "target_url": "https://visible-email.example/",
                        "domain": "visible-email.example",
                        "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                    },
                    domain_result={
                        "target_url": "https://visible-email.example/",
                        "domain": "visible-email.example",
                        "skipped": "manual_review_required",
                    },
                )
                with patch.object(review_queue, "sync_from_worker_jobs", return_value=0):
                    app = AppTest.from_file(
                        str(ROOT / "pages" / "10_Cloaking_Review.py"),
                        default_timeout=10,
                    ).run()

            self.assertEqual(list(app.exception), [])
            table = app.dataframe[0].value
            self.assertEqual(list(table["Full URL"]), [visible["target_url"]])

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
                    patch.object(review_queue, "sync_from_worker_jobs", return_value=0),
                    patch.object(pt, "load_config", return_value={
                        "smtp_accounts": [{"username": account} for account in accounts],
                    }),
                ):
                    app = AppTest.from_file(
                        str(ROOT / "pages" / "10_Cloaking_Review.py"),
                        default_timeout=10,
                    )
                    app.session_state["cloaking_review_active_case"] = item["queue_id"]
                    app.session_state["cloaking_review_active_day"] = item["review_day"]
                    app = app.run()

            self.assertEqual(list(app.exception), [])
            overview = app.dataframe[0].value
            self.assertEqual(overview.iloc[0]["Trạng thái gửi"], "⚠️ Gửi một phần (1/2)")
            self.assertEqual(overview.iloc[0]["Email nhận"], "abuse@example.net")
            self.assertEqual(overview.iloc[0]["Đã gửi từ"], accounts[0])
            self.assertEqual(overview.iloc[0]["Còn chờ"], accounts[1])
            self.assertIn("Gửi tiếp", overview.iloc[0]["Xử lý"])
            self.assertTrue(any(
                uploader.label == "Ảnh đối chiếu thủ công (2–4 ảnh)"
                for uploader in app.file_uploader
            ))
            prepare_button = next(
                button for button in app.button
                if button.label == "Tạo / cập nhật draft để xem"
            )
            self.assertTrue(prepare_button.disabled)

    def test_preview_is_visible_before_direct_send_and_no_job_is_created(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as worker_dir,
        ):
            with patch.object(review_queue, "REVIEW_DIR", review_dir):
                item = review_queue.enqueue_worker_result(
                    job={
                        "job_id": "ui-direct",
                        "allowed_accounts": ["sender@example.org"],
                    },
                    job_dir=worker_dir,
                    prepared={
                        "target_url": "https://ui-direct.example/",
                        "domain": "ui-direct.example",
                        "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                    },
                    domain_result={
                        "target_url": "https://ui-direct.example/",
                        "domain": "ui-direct.example",
                        "skipped": "manual_review_required",
                    },
                )
                preview = {
                    "version": 1,
                    "queue_id": item["queue_id"],
                    "target_url": item["target_url"],
                    "domain": item["domain"],
                    "decision": review_sender.NOT_CLOAKING,
                    "accounts": ["sender@example.org"],
                    "prepared_at": "2026-08-31T02:00:00+00:00",
                    "evidence_signature": "test",
                    "attachments": [],
                    "drafts_total": 1,
                    "drafts_sendable": 1,
                    "deliveries": [{
                        "account": "sender@example.org",
                        "to": "abuse@example.net",
                        "draft": "ui-direct.example_registry_report.txt",
                        "subject": "Abuse Report: ui-direct.example",
                        "body": "Dear Abuse Team,\n\nPlease suspend the reported phishing domain.",
                    }],
                }
                with (
                    patch.object(review_queue, "sync_from_worker_jobs", return_value=0),
                    patch.object(pt, "load_config", return_value={
                        "smtp_accounts": [{"username": "sender@example.org"}],
                    }),
                    patch.object(
                        review_sender, "prepare_review_delivery", return_value=preview,
                    ) as prepare,
                    patch.object(
                        review_sender, "preparation_is_current",
                        side_effect=lambda value, *_args, **_kwargs: bool(value),
                    ),
                    patch.object(
                        review_sender, "send_prepared_review", side_effect=lambda *_args: (
                            review_queue.update_item(
                                item["queue_id"], state=review_queue.SENT,
                                last_error="", completed_at="2026-09-01T02:05:00+00:00",
                            )
                            and {
                                "sent_ok": 1, "sent_failed": 0, "already_sent": 0,
                                "queue_state": review_queue.SENT,
                            }
                        ),
                    ) as send,
                ):
                    app = AppTest.from_file(
                        str(ROOT / "pages" / "10_Cloaking_Review.py"),
                        default_timeout=10,
                    )
                    app.session_state["cloaking_review_active_case"] = item["queue_id"]
                    app.session_state["cloaking_review_active_day"] = item["review_day"]
                    app = app.run()

                    decision = next(
                        control for control in app.segmented_control
                        if control.label == "Chế độ tạo draft và gửi"
                    )
                    app = decision.set_value("Xác nhận cloaking").run()
                    blocked_prepare = next(
                        button for button in app.button
                        if button.label == "Tạo / cập nhật draft để xem"
                    )
                    self.assertTrue(blocked_prepare.disabled)
                    decision = next(
                        control for control in app.segmented_control
                        if control.label == "Chế độ tạo draft và gửi"
                    )
                    app = decision.set_value("Không phải cloaking").run()
                    prepare_button = next(
                        button for button in app.button
                        if button.label == "Tạo / cập nhật draft để xem"
                    )
                    app = prepare_button.click().run()
                    confirmation = next(
                        checkbox for checkbox in app.checkbox
                        if checkbox.label.startswith("Tôi đã đọc draft")
                    )
                    app = confirmation.check().run()
                    self.assertEqual(
                        app.session_state["cloaking_review_active_case"],
                        item["queue_id"],
                    )
                    send_button = next(
                        button for button in app.button
                        if button.label == "Gửi report thường ngay"
                    )
                    self.assertFalse(send_button.disabled)
                    app = send_button.click().run()

            self.assertEqual(list(app.exception), [])
            prepare.assert_called_once()
            send.assert_called_once()
            self.assertTrue(any(
                "Gửi trực tiếp hoàn tất" in success.value for success in app.success
            ))
            overview = app.dataframe[0].value
            self.assertEqual(overview.iloc[0]["Trạng thái gửi"], "✅ Gửi thành công")
            self.assertTrue(pd.isna(overview.iloc[0]["Xử lý"]))
            self.assertNotIn(
                "cloaking_review_active_case", app.session_state.filtered_state,
            )
            self.assertFalse(any(Path(review_dir).glob("review_*")))

    def test_today_table_marks_failed_case_retryable_and_sent_case_unselectable(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as worker_dir,
        ):
            with patch.object(review_queue, "REVIEW_DIR", review_dir):
                sent_item = review_queue.enqueue_worker_result(
                    job={"job_id": "ui-sent"}, job_dir=worker_dir,
                    prepared={
                        "target_url": "https://sent-today.example/",
                        "domain": "sent-today.example",
                        "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                    },
                    domain_result={
                        "target_url": "https://sent-today.example/",
                        "domain": "sent-today.example",
                        "skipped": "manual_review_required",
                    },
                )
                failed_item = review_queue.enqueue_worker_result(
                    job={"job_id": "ui-failed"}, job_dir=worker_dir,
                    prepared={
                        "target_url": "https://failed-today.example/",
                        "domain": "failed-today.example",
                        "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                    },
                    domain_result={
                        "target_url": "https://failed-today.example/",
                        "domain": "failed-today.example",
                        "skipped": "manual_review_required",
                    },
                )
                review_queue.update_item(sent_item["queue_id"], state=review_queue.SENT)
                review_queue.update_item(
                    failed_item["queue_id"], state=review_queue.FAILED,
                    last_error="SMTP unavailable",
                )
                with patch.object(review_queue, "sync_from_worker_jobs", return_value=0):
                    app = AppTest.from_file(
                        str(ROOT / "pages" / "10_Cloaking_Review.py"),
                        default_timeout=10,
                    ).run()

            self.assertEqual(list(app.exception), [])
            table = app.dataframe[0].value.set_index("Full URL")
            self.assertEqual(
                table.loc["https://sent-today.example/", "Trạng thái gửi"],
                "✅ Gửi thành công",
            )
            self.assertTrue(pd.isna(table.loc["https://sent-today.example/", "Xử lý"]))
            self.assertEqual(
                table.loc["https://failed-today.example/", "Trạng thái gửi"],
                "❌ Gửi thất bại",
            )
            self.assertIn(
                "Thử lại", table.loc["https://failed-today.example/", "Xử lý"],
            )
            self.assertEqual(
                table.loc["https://failed-today.example/", "Lỗi gần nhất"],
                "SMTP unavailable",
            )

    def test_manual_upload_keeps_case_and_is_saved_with_draft_action(self):
        with (
            tempfile.TemporaryDirectory() as review_dir,
            tempfile.TemporaryDirectory() as worker_dir,
            tempfile.TemporaryDirectory() as evidence_dir,
        ):
            with patch.object(review_queue, "REVIEW_DIR", review_dir):
                first = review_queue.enqueue_worker_result(
                    job={"job_id": "ui-first", "allowed_accounts": ["sender@example.org"]},
                    job_dir=worker_dir,
                    prepared={
                        "target_url": "https://first-ui.example/",
                        "domain": "first-ui.example",
                        "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                    },
                    domain_result={
                        "target_url": "https://first-ui.example/",
                        "domain": "first-ui.example",
                        "skipped": "manual_review_required",
                    },
                )
                second = review_queue.enqueue_worker_result(
                    job={"job_id": "ui-second", "allowed_accounts": ["sender@example.org"]},
                    job_dir=worker_dir,
                    prepared={
                        "target_url": "https://second-ui.example/path",
                        "domain": "second-ui.example",
                        "recipients": [{"channel": "registry", "email": "abuse@example.net"}],
                    },
                    domain_result={
                        "target_url": "https://second-ui.example/path",
                        "domain": "second-ui.example",
                        "skipped": "manual_review_required",
                    },
                )
                self.assertNotEqual(first["queue_id"], second["queue_id"])

                def prepared_preview(queue_id, **kwargs):
                    persisted = review_queue.load_item(queue_id)
                    operator = persisted["result"]["cloaking_result"]["operator_evidence"]
                    self.assertEqual(len(operator["screenshots"]), 2)
                    return {
                        "version": 1,
                        "queue_id": queue_id,
                        "target_url": persisted["target_url"],
                        "domain": persisted["domain"],
                        "decision": kwargs["decision"],
                        "accounts": kwargs["account_names"],
                        "prepared_at": "2026-09-01T01:00:00+00:00",
                        "evidence_signature": "manual-pair",
                        "attachments": [
                            row["path"] for row in operator["screenshots"]
                        ],
                        "drafts_total": 1,
                        "drafts_sendable": 1,
                        "deliveries": [],
                    }

                with (
                    patch.object(review_queue, "sync_from_worker_jobs", return_value=0),
                    patch.object(pt, "CLOAKING_EVIDENCE_DIR", evidence_dir),
                    patch.object(pt, "load_config", return_value={
                        "smtp_accounts": [{"username": "sender@example.org"}],
                    }),
                    patch.object(
                        review_sender, "prepare_review_delivery",
                        side_effect=prepared_preview,
                    ) as prepare,
                    patch.object(
                        review_sender, "preparation_is_current",
                        side_effect=lambda value, *_args, **_kwargs: bool(value),
                    ),
                ):
                    app = AppTest.from_file(
                        str(ROOT / "pages" / "10_Cloaking_Review.py"),
                        default_timeout=10,
                    )
                    app.session_state["cloaking_review_active_case"] = second["queue_id"]
                    app.session_state["cloaking_review_active_day"] = second["review_day"]
                    app = app.run()
                    uploader = next(
                        uploader for uploader in app.file_uploader
                        if uploader.label == "Ảnh đối chiếu thủ công (2–4 ảnh)"
                    )
                    app = uploader.set_value([
                        ("desktop.png", PNG_1X1, "image/png"),
                        ("mobile.png", PNG_1X1, "image/png"),
                    ]).run()

                    self.assertEqual(
                        app.session_state["cloaking_review_active_case"],
                        second["queue_id"],
                    )
                    self.assertGreaterEqual(len(app.image), 2)

                    pair_confirmation = next(
                        checkbox for checkbox in app.checkbox
                        if checkbox.label.startswith("Tôi xác nhận các ảnh")
                    )
                    app = pair_confirmation.check().run()
                    decision = next(
                        control for control in app.segmented_control
                        if control.label == "Chế độ tạo draft và gửi"
                    )
                    app = decision.set_value("Xác nhận cloaking").run()
                    prepare_button = next(
                        button for button in app.button
                        if button.label == "Xác nhận ảnh & tạo draft để xem"
                    )
                    self.assertFalse(prepare_button.disabled)
                    app = prepare_button.click().run()

                    self.assertEqual(list(app.exception), [])
                    self.assertEqual(prepare.call_count, 1)
                    self.assertEqual(prepare.call_args.args[0], second["queue_id"])
                    persisted = review_queue.load_item(second["queue_id"])
                    cloaking = persisted["result"]["cloaking_result"]
                    self.assertEqual(cloaking["verdict"], "POSSIBLE")
                    self.assertEqual(len(cloaking["operator_evidence"]["screenshots"]), 2)
                    self.assertTrue(any(
                        "Đã tự lưu cặp ảnh và tạo draft" in success.value
                        for success in app.success
                    ))

                    app = app.run()
                    self.assertEqual(
                        app.session_state["cloaking_review_active_case"],
                        second["queue_id"],
                    )


if __name__ == "__main__":
    unittest.main()

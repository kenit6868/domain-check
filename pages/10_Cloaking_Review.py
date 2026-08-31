"""Dedicated review and send queue for cloaking cases isolated by Domain Worker."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

import cloaking_review_queue as review_queue
import cloaking_review_sender as review_sender
import phishing_toolkit as pt
from cloaking_ui import render_cloaking_result


def _local_review_day(timestamp: str | None = None) -> str:
    """Mirror the queue day rule when Streamlit still caches an older module."""
    if timestamp:
        try:
            value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone().date().isoformat()
        except (TypeError, ValueError):
            pass
    return datetime.now().astimezone().date().isoformat()


def _has_sendable_recipient(item: dict) -> bool:
    detector = getattr(review_queue, "has_sendable_recipient", None)
    if callable(detector):
        return bool(detector(item))
    prepared = item.get("prepared") if isinstance(item.get("prepared"), dict) else item
    recipients = prepared.get("recipients") or []
    return any(
        str(recipient.get("email") or "").strip()
        for recipient in recipients
        if isinstance(recipient, dict)
    )


def _load_today_review_items() -> tuple[str, list[dict]]:
    """Load today's JSON records across Streamlit module hot reloads."""
    current_day_fn = getattr(review_queue, "current_review_day", None)
    review_day = (
        str(current_day_fn()) if callable(current_day_fn) else _local_review_day()
    )

    list_for_day_fn = getattr(review_queue, "list_items_for_day", None)
    if callable(list_for_day_fn):
        return review_day, [
            item for item in list_for_day_fn(review_day)
            if _has_sendable_recipient(item)
        ]

    # A running Streamlit process can retain the pre-update module object while
    # hot-reloading this page. Its list_items() still reads the same JSON queue.
    visible_items = [
        item for item in review_queue.list_items()
        if str(
            item.get("review_day")
            or _local_review_day(item.get("created_at") or item.get("updated_at"))
        ) == review_day
        and _has_sendable_recipient(item)
    ]
    return review_day, visible_items


st.set_page_config(page_title="Cloaking Review", page_icon=":material/visibility:", layout="wide")
st.title("Cloaking Review", anchor=False)
st.caption(
    "Domain Worker tự chuyển case cloaking vào đây. Kiểm tra evidence, chọn domain "
    "và xem draft trước khi gửi trực tiếp. Trang này không tạo hoặc chờ worker job gửi mail."
)

for review_job_root in dict.fromkeys([
    pt._runtime_path("worker_jobs"), pt._runtime_path("cloaking_send_jobs"),
]):
    try:
        review_queue.sync_from_worker_jobs(review_job_root)
    except OSError:
        pass

flash = st.session_state.pop("cloaking_review_flash", "")
if isinstance(flash, dict) and flash.get("message"):
    renderer = {
        "error": st.error,
        "warning": st.warning,
        "success": st.success,
    }.get(flash.get("kind"), st.info)
    renderer(flash["message"])
elif flash:
    st.success(flash, icon=":material/check_circle:")

review_day, visible_items = _load_today_review_items()

if not visible_items:
    st.info(
        f"Hôm nay ({review_day}) chưa có domain cloaking có email nhận cần review. "
        "Hãy chạy lại bước check trong Domain Worker để tạo danh sách mới.",
        icon=":material/info:",
    )
    st.stop()


def _result(item: dict) -> dict:
    result = dict(item.get("result") or {})
    cloaking = result.get("cloaking_result") or {}
    if cloaking:
        normalized = review_sender.normalize_cloaking_result(cloaking)
        result.update({
            "cloaking_result": normalized,
            "cloaking_verdict": normalized.get("verdict", "INCONCLUSIVE"),
            "cloaking_score": normalized.get("score", 0),
            "cloaking_signals": normalized.get("signals") or [],
        })
    return result


def _verdict(item: dict) -> str:
    return str(_result(item).get("cloaking_verdict") or "INCONCLUSIVE")


def _score(item: dict) -> int:
    return int(_result(item).get("cloaking_score", 0) or 0)


def _state_label(item: dict) -> str:
    state = str(item.get("state") or "")
    delivery = review_queue.delivery_summary(item)
    return {
        review_queue.PENDING_REVIEW: "Chưa gửi",
        review_queue.PARTIAL: f"⚠️ Gửi một phần ({delivery['progress']})",
        review_queue.FAILED: "❌ Gửi thất bại",
        review_queue.QUEUED_CLOAKING: "Đang xử lý bằng job cũ",
        review_queue.QUEUED_NORMAL: "Đang xử lý bằng job cũ",
        review_queue.SENT: "✅ Gửi thành công",
        review_queue.SKIPPED: "Đã bỏ qua",
    }.get(state, state)


def _action_label(item: dict) -> str | None:
    return {
        review_queue.PENDING_REVIEW: ":material/visibility: Xử lý",
        review_queue.PARTIAL: ":material/outgoing_mail: Gửi tiếp",
        review_queue.FAILED: ":material/refresh: Thử lại",
    }.get(str(item.get("state") or ""))


rows = []
for item in visible_items:
    delivery = review_queue.delivery_summary(item)
    rows.append({
        "queue_id": item.get("queue_id", ""),
        "Xử lý": _action_label(item),
        "Full URL": item.get("target_url", ""),
        "Verdict": _verdict(item),
        "Điểm": _score(item),
        "Trạng thái gửi": _state_label(item),
        "Email nhận": ", ".join(delivery["recipients"]) or "—",
        "Đã gửi từ": ", ".join(delivery["completed_accounts"]) or "—",
        "Còn chờ": ", ".join(delivery["pending_accounts"]) or "—",
        "Lỗi gần nhất": str(item.get("last_error") or ""),
        "Cập nhật": item.get("updated_at", ""),
    })

review_df = pd.DataFrame(rows)
items_by_id = {item["queue_id"]: item for item in visible_items}
active_case_key = "cloaking_review_active_case"
active_day_key = "cloaking_review_active_day"
row_ids_key = "cloaking_review_today_row_ids"
action_click_key = "cloaking_review_case_action"
if st.session_state.get(active_day_key) != review_day:
    st.session_state[active_day_key] = review_day
    st.session_state.pop(active_case_key, None)
st.session_state[row_ids_key] = list(review_df["queue_id"])


def _open_case_from_table() -> None:
    click = st.session_state.get(action_click_key)
    try:
        row_index = int(click["row"])
        queue_id = st.session_state[row_ids_key][row_index]
    except (KeyError, IndexError, TypeError, ValueError):
        return
    item = review_queue.load_item(queue_id) or items_by_id.get(queue_id)
    if (
        not item
        or item.get("state") not in review_queue.ACTIVE_STATES
    ):
        st.session_state.pop(active_case_key, None)
        st.session_state["cloaking_review_selection_notice"] = (
            "Case này đã hoàn tất hoặc không còn thuộc danh sách hôm nay."
        )
        return
    st.session_state[active_case_key] = queue_id
    st.session_state.pop("cloaking_review_selection_notice", None)


table_config = {
    "queue_id": None,
    "Xử lý": st.column_config.ButtonColumn(
        "Xử lý",
        type="tertiary",
        key=action_click_key,
        on_click=_open_case_from_table,
        width="small",
    ),
    "Điểm": st.column_config.ProgressColumn("Điểm", min_value=0, max_value=100),
    "Full URL": st.column_config.LinkColumn("Full URL", display_text=r"https?://(.+)"),
}
st.caption(
    f"Danh sách cloaking ngày {review_day}. Bấm **Xử lý** trên domain chưa gửi; "
    "domain gửi thành công chỉ còn hiển thị trạng thái và không có nút chọn lại."
)
st.dataframe(
    review_df,
    key="cloaking_review_today_table",
    hide_index=True,
    width="stretch",
    column_config=table_config,
)
selection_notice = st.session_state.pop("cloaking_review_selection_notice", "")
if selection_notice:
    st.warning(selection_notice, icon=":material/info:")

detail_id = str(st.session_state.get(active_case_key) or "")
detail_item = items_by_id.get(detail_id)
if not detail_item or detail_item.get("state") not in review_queue.ACTIVE_STATES:
    st.session_state.pop(active_case_key, None)
    st.info(
        "Bấm **Xử lý**, **Gửi tiếp** hoặc **Thử lại** trong bảng để mở một domain.",
        icon=":material/touch_app:",
    )
    st.stop()

editable = True
st.info(f"Đang xem và xử lý: {detail_item.get('target_url', '')}")
detail_result = _result(detail_item)
stored_cloaking_result = detail_result.get("cloaking_result") or {}
cloaking_result = dict(stored_cloaking_result or {
    "target_url": detail_item.get("target_url", ""),
    "verdict": detail_result.get("cloaking_verdict") or "INCONCLUSIVE",
    "score": detail_result.get("cloaking_score", 0),
    "signals": detail_result.get("cloaking_signals") or [],
})
cloaking_result.setdefault("target_url", detail_item.get("target_url", ""))
evidence_status = review_sender.confirmed_evidence_status(cloaking_result)
detail_delivery = review_queue.delivery_summary(detail_item)
source_job_ids = list(filter(None, detail_item.get("source_job_ids") or [
    detail_item.get("source_job_id"),
]))
pending_manual_images: list[tuple[str, bytes]] = []
pending_manual_attempted = False
pending_manual_ready = False
pending_manual_error = ""
pending_manual_device = "desktop and mobile"
pending_manual_network = "direct and Google"

with st.container(border=True):
    st.subheader(detail_item.get("domain") or detail_item.get("target_url"), anchor=False)
    st.caption(
        f"URL: {detail_item.get('target_url', '')} · Ngày review: "
        f"{detail_item.get('review_day', '—')} · "
        f"{len(source_job_ids)} lần phát hiện"
    )
    if stored_cloaking_result:
        render_cloaking_result(cloaking_result)
    else:
        st.warning("Case cũ chưa có cloaking manifest đầy đủ; hãy kiểm tra tín hiệu trong bảng.")
    if evidence_status["ready"] and evidence_status["pair_source"] == "automatic":
        st.success(
            f"Playwright đã có {evidence_status['automatic_images']} ảnh hợp lệ. "
            "Có thể tạo draft cloaking mà không cần upload thủ công.",
            icon=":material/photo_camera:",
        )
    elif evidence_status["ready"]:
        st.success(
            f"Đã lưu {evidence_status['operator_images']} ảnh thủ công hợp lệ. "
            "Case hiện đủ evidence để tạo draft cloaking.",
            icon=":material/verified:",
        )
    else:
        st.warning(
            "Playwright chưa tạo được cặp evidence hoàn chỉnh. Uploader ảnh thủ công "
            f"đã được mở bên dưới. {evidence_status['reason']}",
            icon=":material/add_photo_alternate:",
        )

    st.subheader("Tiến độ gửi email", anchor=False)
    st.caption(
        f"Email nhận: {', '.join(detail_delivery['recipients']) or 'Chưa xác định'} · "
        f"Đã gửi từ: {', '.join(detail_delivery['completed_accounts']) or 'Chưa có'} · "
        f"Còn chờ: {', '.join(detail_delivery['pending_accounts']) or 'Không còn'}"
    )
    delivery_rows = []
    delivery_status_labels = {
        "sent": "Đã gửi",
        "already_sent": "Đã gửi trước đó hôm nay",
        "failed": "Gửi lỗi",
    }
    for delivery_row in detail_delivery["deliveries"]:
        delivery_rows.append({
            "Tài khoản gửi": delivery_row.get("account") or "—",
            "Email nhận": delivery_row.get("to") or "—",
            "Draft / kênh": delivery_row.get("draft") or "—",
            "Trạng thái": delivery_status_labels.get(
                delivery_row.get("status"), delivery_row.get("status") or "—",
            ),
            "Lỗi": delivery_row.get("error") or "",
            "Cập nhật": delivery_row.get("updated_at") or "",
        })
    accounts_with_rows = {
        str(row.get("account") or "").strip().lower()
        for row in detail_delivery["deliveries"]
    }
    for pending_account in detail_delivery["pending_accounts"]:
        if pending_account in accounts_with_rows:
            continue
        pending_recipients = detail_delivery["recipients"] or ["—"]
        for recipient in pending_recipients:
            delivery_rows.append({
                "Tài khoản gửi": pending_account,
                "Email nhận": recipient,
                "Draft / kênh": "Chờ tạo/gửi",
                "Trạng thái": "Chưa gửi",
                "Lỗi": "",
                "Cập nhật": "",
            })
    if delivery_rows:
        st.dataframe(
            pd.DataFrame(delivery_rows),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("Case này chưa có thông tin giao nhận email.", icon=":material/mail:")

    if editable:
        needs_manual_evidence = not evidence_status["ready"]
        manual_upload_enabled = needs_manual_evidence
        if not needs_manual_evidence:
            manual_toggle_key = f"cloaking_review_manual_{detail_id}"
            if st.session_state.pop(f"{manual_toggle_key}_close", False):
                st.session_state[manual_toggle_key] = False
            manual_upload_enabled = st.toggle(
                "Thay hoặc bổ sung ảnh thủ công",
                value=False,
                key=manual_toggle_key,
            )
        if manual_upload_enabled:
            with st.container(border=True):
                st.markdown("**Bổ sung cặp ảnh xác minh thủ công**")
                st.caption(
                    "Chọn 2–4 ảnh của đúng URL đang xử lý. Ảnh hiện ngay bên dưới và chỉ "
                    "được lưu khi bạn tạo draft; không còn bước ‘Lưu ảnh thủ công’ riêng."
                )
                uploads = st.file_uploader(
                    "Ảnh đối chiếu thủ công (2–4 ảnh)",
                    type=["png", "jpg", "jpeg", "webp"],
                    accept_multiple_files=True,
                    key=f"cloaking_review_uploads_{detail_id}",
                    help="Mỗi ảnh tối đa 10 MB; hỗ trợ PNG, JPEG và WebP.",
                )
                pending_manual_images = [
                    (upload.name, upload.getvalue()) for upload in uploads or []
                ]
                pending_manual_attempted = bool(pending_manual_images)

                preview_uploads = []
                for upload, image in zip(uploads or [], pending_manual_images):
                    try:
                        pt.validate_operator_cloaking_images([image])
                    except ValueError:
                        continue
                    preview_uploads.append(upload)
                if preview_uploads:
                    with st.container(horizontal=True, gap="small"):
                        for upload in preview_uploads:
                            st.image(
                                upload.getvalue(), caption=upload.name, width=160,
                            )

                pair_is_valid = False
                if pending_manual_attempted:
                    try:
                        pt.validate_operator_cloaking_images(
                            pending_manual_images, require_pair=True,
                        )
                        pair_is_valid = True
                    except ValueError as exc:
                        if len(pending_manual_images) < 2:
                            pending_manual_error = (
                                f"Đã chọn {len(pending_manual_images)}/2 ảnh tối thiểu. "
                                "Hãy chọn thêm ảnh đối chiếu."
                            )
                        elif len(pending_manual_images) > 4:
                            pending_manual_error = "Chỉ được chọn tối đa 4 ảnh đối chiếu."
                        else:
                            pending_manual_error = f"Ảnh chưa hợp lệ: {exc}"
                        st.warning(pending_manual_error, icon=":material/image_not_supported:")

                confirmed_pair = st.checkbox(
                    "Tôi xác nhận các ảnh thuộc cùng URL nhưng hiển thị nội dung khác nhau.",
                    key=f"cloaking_review_pair_confirmed_{detail_id}",
                    disabled=not pair_is_valid,
                )
                pending_manual_ready = bool(pair_is_valid and confirmed_pair)
                if pair_is_valid and not confirmed_pair:
                    st.info(
                        "Ảnh đã sẵn sàng. Tích xác nhận để mở bước tạo draft cloaking.",
                        icon=":material/fact_check:",
                    )
                elif pending_manual_ready:
                    st.success(
                        "Cặp ảnh đã sẵn sàng. Khi tạo draft, hệ thống sẽ tự lưu evidence "
                        "và dùng hai ảnh đại diện làm attachment.",
                        icon=":material/check_circle:",
                    )

                with st.expander("Thông tin lần chụp (tùy chọn)", expanded=False):
                    st.text_input(
                        "URL đã chụp",
                        value=detail_item.get("target_url", ""),
                        disabled=True,
                        key=f"cloaking_review_acquisition_url_{detail_id}",
                    )
                    pending_manual_device = st.selectbox(
                        "Thiết bị",
                        ["desktop and mobile", "desktop", "Android", "iPhone", "other"],
                        key=f"cloaking_review_device_{detail_id}",
                    )
                    pending_manual_network = st.selectbox(
                        "Mạng / nguồn truy cập",
                        [
                            "direct and Google", "direct", "Google referrer",
                            "mobile data", "other",
                        ],
                        key=f"cloaking_review_network_{detail_id}",
                    )

if editable:
    cfg = pt.load_config()
    accounts = cfg.get("smtp_accounts") or []
    account_names = [
        str(account.get("username") or "").strip()
        for account in accounts if str(account.get("username") or "").strip()
    ]
    account_names = list(dict.fromkeys(account_names))
    configured_by_name = {name.lower(): name for name in account_names}
    pending_for_selection = list(detail_delivery["pending_accounts"])
    missing_accounts = [
        account for account in pending_for_selection
        if account.lower() not in configured_by_name
    ]
    default_accounts = (
        [
            configured_by_name[account.lower()]
            for account in pending_for_selection
            if account.lower() in configured_by_name
        ]
        if pending_for_selection else account_names
    )
    sender_key = f"cloaking_review_sender_accounts_{detail_id}"
    if sender_key not in st.session_state:
        st.session_state[sender_key] = default_accounts
    else:
        st.session_state[sender_key] = [
            name for name in st.session_state.get(sender_key, [])
            if name in account_names
        ]
    selected_accounts = st.multiselect(
        "Tài khoản gửi email",
        options=account_names,
        key=sender_key,
        help=(
            "Mặc định chỉ chọn các tài khoản còn thiếu của case đang xem. "
            "Các lượt đã gửi được giữ trong sổ giao nhận và không bị gửi lại."
        ),
    )
    if pending_for_selection:
        st.caption(f"Tài khoản còn chờ của lựa chọn hiện tại: {', '.join(pending_for_selection)}")
    if missing_accounts:
        st.warning(
            "Các tài khoản còn chờ nhưng hiện không còn trong config.ini: "
            + ", ".join(missing_accounts),
            icon=":material/account_alert:",
        )

    decision_labels = {
        "Xác nhận cloaking": review_sender.CONFIRMED_CLOAKING,
        "Không phải cloaking": review_sender.NOT_CLOAKING,
    }
    labels_by_decision = {value: key for key, value in decision_labels.items()}
    delivered_rows = [
        row for row in detail_delivery["deliveries"]
        if row.get("status") in review_queue.DELIVERED_STATUSES
    ]
    existing_decision = str(detail_item.get("decision") or "")
    locked_decision = existing_decision if delivered_rows else ""
    decision_key = f"cloaking_review_decision_{detail_id}"
    decision_label = st.segmented_control(
        "Chế độ tạo draft và gửi",
        options=list(decision_labels),
        default=labels_by_decision.get(existing_decision),
        selection_mode="single",
        key=decision_key,
        disabled=bool(locked_decision),
        help=(
            "Xác nhận cloaking: dùng evidence đã duyệt và đính kèm manifest + hai ảnh. "
            "Không phải cloaking: loại toàn bộ evidence/attachment cloaking."
        ),
    )
    decision = (
        locked_decision
        if locked_decision in decision_labels.values()
        else decision_labels.get(decision_label)
    )
    if locked_decision:
        st.info(
            "Case đã gửi một phần nên chế độ được khóa để các tài khoản còn lại nhận "
            "cùng loại báo cáo.",
            icon=":material/lock:",
        )

    preview_key = f"cloaking_review_preview_{detail_id}"
    effective_evidence_ready = bool(evidence_status["ready"])
    if pending_manual_attempted:
        effective_evidence_ready = pending_manual_ready
    confirmed_evidence_blocked = bool(
        decision == review_sender.CONFIRMED_CLOAKING
        and not effective_evidence_ready
    )
    if confirmed_evidence_blocked:
        st.warning(
            "Chưa thể tạo draft cloaking. Hãy chọn 2–4 ảnh hợp lệ ở phía trên và "
            "tích xác nhận; không cần bấm lưu ảnh riêng.",
            icon=":material/photo_library:",
        )
    prepare_label = (
        "Xác nhận ảnh & tạo draft để xem"
        if decision == review_sender.CONFIRMED_CLOAKING and pending_manual_ready
        else "Tạo / cập nhật draft để xem"
    )
    prepare_clicked = st.button(
        prepare_label,
        icon=":material/draft:",
        disabled=(
            not bool(decision and selected_accounts)
            or confirmed_evidence_blocked
        ),
        help=(
            "Chọn đủ cặp ảnh và tích xác nhận; ảnh sẽ được lưu cùng lúc tạo draft."
            if confirmed_evidence_blocked
            else (
                "Tự lưu cặp ảnh đang xem rồi tạo draft; chưa gửi bất kỳ email nào."
                if pending_manual_ready
                else "Chạy pipeline tạo draft nhưng chưa gửi bất kỳ email nào."
            )
        ),
    )
    if prepare_clicked:
        manual_evidence_saved = False
        try:
            with st.spinner("Đang tạo draft và đối chiếu evidence; chưa gửi email..."):
                if (
                    decision == review_sender.CONFIRMED_CLOAKING
                    and pending_manual_ready
                ):
                    updated_cloaking_result = pt.add_operator_cloaking_evidence(
                        cloaking_result,
                        images=pending_manual_images,
                        acquisition_url=detail_item.get("target_url", ""),
                        device=pending_manual_device,
                        network=pending_manual_network,
                        confirmed_difference=True,
                    )
                    review_queue.update_cloaking_result(
                        detail_id, updated_cloaking_result,
                    )
                    refreshed_status = review_sender.confirmed_evidence_status(
                        updated_cloaking_result,
                    )
                    if not refreshed_status["ready"]:
                        raise ValueError(refreshed_status["reason"])
                    evidence_status = refreshed_status
                    manual_evidence_saved = True
                    pending_manual_attempted = False
                    pending_manual_ready = False
                    if not needs_manual_evidence:
                        st.session_state[
                            f"cloaking_review_manual_{detail_id}_close"
                        ] = True
                st.session_state[preview_key] = review_sender.prepare_review_delivery(
                    detail_id,
                    decision=decision,
                    account_names=selected_accounts,
                    cfg=cfg,
                )
            if manual_evidence_saved:
                st.success(
                    "Đã tự lưu cặp ảnh và tạo draft. Hãy đọc nội dung bên dưới trước khi gửi."
                )
            else:
                st.success("Đã tạo draft. Hãy đọc nội dung bên dưới trước khi gửi.")
        except (OSError, RuntimeError, ValueError) as exc:
            st.session_state.pop(preview_key, None)
            prefix = (
                "Đã lưu ảnh nhưng không thể chuẩn bị draft"
                if manual_evidence_saved else "Không thể chuẩn bị draft"
            )
            st.error(f"{prefix}: {exc}")

    current_item = review_queue.load_item(detail_id) or detail_item
    preview = st.session_state.get(preview_key)
    preview_current = review_sender.preparation_is_current(
        preview,
        current_item,
        decision=decision,
        account_names=selected_accounts,
    )
    if decision == review_sender.CONFIRMED_CLOAKING and pending_manual_attempted:
        preview_current = False

    st.subheader("Draft mẫu trước khi gửi", anchor=False)
    if preview and not preview_current:
        st.warning(
            "Draft preview không còn khớp với chế độ, tài khoản hoặc evidence hiện tại. "
            "Hãy bấm tạo/cập nhật draft lại.",
            icon=":material/refresh:",
        )
    if not preview_current:
        st.info(
            "Chọn chế độ và tài khoản, sau đó bấm **Tạo / cập nhật draft để xem**. "
            "Nút gửi chỉ mở sau khi draft hiện đầy đủ tại đây.",
            icon=":material/preview:",
        )
    else:
        attachment_names = [os.path.basename(path) for path in preview.get("attachments") or []]
        if decision == review_sender.CONFIRMED_CLOAKING:
            st.success(
                "Draft xác nhận cloaking sẽ dùng evidence đã duyệt và đính kèm: "
                + ", ".join(attachment_names),
                icon=":material/attachment:",
            )
        else:
            st.info(
                "Draft report thường: không có nội dung hoặc attachment cloaking.",
                icon=":material/mail:",
            )
        preview_token = str(preview.get("prepared_at") or "preview").replace(":", "_")
        for index, delivery in enumerate(preview.get("deliveries") or []):
            label = (
                f"{delivery.get('account', '—')} → {delivery.get('to', '—')} · "
                f"{delivery.get('draft', 'draft')}"
            )
            with st.expander(label, expanded=index == 0):
                st.text_input(
                    "Email nhận", value=delivery.get("to", ""), disabled=True,
                    key=f"preview_to_{detail_id}_{preview_token}_{index}",
                )
                st.text_input(
                    "Subject", value=delivery.get("subject", ""), disabled=True,
                    key=f"preview_subject_{detail_id}_{preview_token}_{index}",
                )
                st.text_area(
                    "Nội dung email tiếng Anh sẽ gửi",
                    value=delivery.get("body", ""), height=420, disabled=True,
                    key=f"preview_body_{detail_id}_{preview_token}_{index}",
                )

        reviewed = st.checkbox(
            "Tôi đã đọc draft, kiểm tra email nhận và xác nhận gửi nội dung đang hiển thị.",
            key=f"cloaking_review_confirm_{detail_id}_{preview_token}",
        )
        send_label = (
            "Gửi email cloaking ngay"
            if decision == review_sender.CONFIRMED_CLOAKING
            else "Gửi report thường ngay"
        )
        send_direct = st.button(
            send_label,
            type="primary",
            icon=":material/send:",
            disabled=not reviewed,
        )
        if send_direct:
            try:
                with st.spinner("Đang gửi trực tiếp và cập nhật sổ giao nhận..."):
                    send_result = review_sender.send_prepared_review(preview, cfg)
                failed = int(send_result.get("sent_failed", 0) or 0)
                sent = int(send_result.get("sent_ok", 0) or 0)
                already = int(send_result.get("already_sent", 0) or 0)
                state = send_result.get("queue_state") or "—"
                kind = "error" if failed and not sent else "warning" if failed else "success"
                st.session_state["cloaking_review_flash"] = {
                    "kind": kind,
                    "message": (
                        f"Gửi trực tiếp hoàn tất: {sent} thành công, {already} đã gửi trước đó, "
                        f"{failed} lỗi. Trạng thái case: {state}."
                    ),
                }
                st.session_state.pop(preview_key, None)
                if state == review_queue.SENT:
                    st.session_state.pop(active_case_key, None)
                st.rerun()
            except (OSError, RuntimeError, ValueError) as exc:
                st.error(f"Gửi trực tiếp thất bại: {exc}")

    if st.button(
        "Bỏ qua case đang xem",
        icon=":material/block:",
        key=f"cloaking_review_skip_{detail_id}",
    ):
        try:
            review_queue.mark_skipped([detail_id])
            st.session_state.pop(preview_key, None)
            st.session_state.pop(active_case_key, None)
            st.session_state["cloaking_review_flash"] = (
                f"Đã bỏ qua {detail_item.get('target_url', '')}."
            )
            st.rerun()
        except (OSError, ValueError) as exc:
            st.error(f"Không thể cập nhật queue: {exc}")

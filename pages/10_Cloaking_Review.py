"""Dedicated review and send queue for cloaking cases isolated by Domain Worker."""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

import cloaking_review_queue as review_queue
import domain_worker
import phishing_toolkit as pt
from cloaking_ui import render_cloaking_result


st.set_page_config(page_title="Cloaking Review", page_icon=":material/visibility:", layout="wide")
st.title("Cloaking Review", anchor=False)
st.caption(
    "Domain Worker tự chuyển case cloaking vào đây. Kiểm tra evidence, chọn domain "
    "rồi quyết định gửi kèm bằng chứng, gửi như report thường hoặc bỏ qua."
)

try:
    review_queue.sync_from_worker_jobs(domain_worker.WORKER_DIR)
except OSError:
    pass

flash = st.session_state.pop("cloaking_review_flash", "")
if flash:
    st.success(flash, icon=":material/check_circle:")

all_items = review_queue.list_items()
counts = {
    "pending": sum(item.get("state") in review_queue.ACTIVE_STATES for item in all_items),
    "queued": sum(item.get("state") in {review_queue.QUEUED_CLOAKING, review_queue.QUEUED_NORMAL} for item in all_items),
    "sent": sum(item.get("state") == review_queue.SENT for item in all_items),
    "skipped": sum(item.get("state") == review_queue.SKIPPED for item in all_items),
}
metrics = st.columns(4)
metrics[0].metric("Chờ xử lý", counts["pending"])
metrics[1].metric("Đang chờ gửi", counts["queued"])
metrics[2].metric("Đã gửi", counts["sent"])
metrics[3].metric("Đã bỏ qua", counts["skipped"])

view_mode = st.segmented_control(
    "Danh sách hiển thị",
    ["Chờ xử lý", "Đang gửi", "Đã xử lý"],
    default="Chờ xử lý",
    selection_mode="single",
)
state_filter = {
    "Chờ xử lý": review_queue.ACTIVE_STATES,
    "Đang gửi": {review_queue.QUEUED_CLOAKING, review_queue.QUEUED_NORMAL},
    "Đã xử lý": {review_queue.SENT, review_queue.SKIPPED},
}[view_mode or "Chờ xử lý"]
visible_items = [item for item in all_items if item.get("state") in state_filter]

if not visible_items:
    st.info(
        "Không có domain trong nhóm này. Hãy chạy Domain Worker; case cloaking sẽ tự xuất hiện tại đây.",
        icon=":material/info:",
    )
    st.stop()


def _result(item: dict) -> dict:
    return item.get("result") or {}


def _verdict(item: dict) -> str:
    return str(_result(item).get("cloaking_verdict") or "INCONCLUSIVE")


def _score(item: dict) -> int:
    return int(_result(item).get("cloaking_score", 0) or 0)


def _state_label(state: str) -> str:
    return {
        review_queue.PENDING_REVIEW: "Chờ xác nhận",
        review_queue.PARTIAL: "Gửi một phần — còn tài khoản chờ",
        review_queue.FAILED: "Gửi lỗi — có thể retry",
        review_queue.QUEUED_CLOAKING: "Đang gửi kèm evidence",
        review_queue.QUEUED_NORMAL: "Đang gửi report thường",
        review_queue.SENT: "Đã gửi",
        review_queue.SKIPPED: "Đã bỏ qua",
    }.get(state, state)


rows = []
for item in visible_items:
    result = _result(item)
    cloaking = result.get("cloaking_result") or {}
    delivery = review_queue.delivery_summary(item)
    rows.append({
        "queue_id": item.get("queue_id", ""),
        "Full URL": item.get("target_url", ""),
        "Domain": item.get("domain", ""),
        "Verdict": _verdict(item),
        "Điểm": _score(item),
        "Ảnh tự động": len(cloaking.get("screenshots") or []),
        "Email nhận": ", ".join(delivery["recipients"]) or "—",
        "Đã gửi từ": ", ".join(delivery["completed_accounts"]) or "—",
        "Còn chờ": ", ".join(delivery["pending_accounts"]) or "—",
        "Tiến độ gửi email": delivery["progress"],
        "Trạng thái": _state_label(str(item.get("state") or "")),
        "Cập nhật": item.get("updated_at", ""),
    })

review_df = pd.DataFrame(rows)
editable = view_mode == "Chờ xử lý"
table_config = {
    "queue_id": None,
    "Điểm": st.column_config.ProgressColumn("Điểm", min_value=0, max_value=100),
    "Full URL": st.column_config.LinkColumn("Full URL", display_text=r"https?://(.+)"),
}
if editable:
    st.caption("Tích ô chọn ở đầu mỗi dòng; có thể chọn nhiều domain cùng lúc.")
    selection = st.dataframe(
        review_df,
        key=f"cloaking_review_table_{view_mode}",
        hide_index=True,
        width="stretch",
        column_config=table_config,
        on_select="rerun",
        selection_mode="multi-row",
    )
    selected_rows = list(selection.selection.rows)
    selected_ids = [
        str(review_df.iloc[index]["queue_id"])
        for index in selected_rows if 0 <= index < len(review_df)
    ]
else:
    st.dataframe(
        review_df,
        key=f"cloaking_review_table_{view_mode}",
        hide_index=True,
        width="stretch",
        column_config=table_config,
    )
    selected_ids = []
st.caption(f"Đã chọn: {len(selected_ids)} domain")

items_by_id = {item["queue_id"]: item for item in visible_items}
detail_id = st.selectbox(
    "Xem bằng chứng của domain",
    options=list(items_by_id),
    format_func=lambda queue_id: (
        f"{items_by_id[queue_id].get('target_url', '')} — "
        f"{_verdict(items_by_id[queue_id])} ({_score(items_by_id[queue_id])} điểm)"
    ),
)
detail_item = items_by_id[detail_id]
detail_result = _result(detail_item)
cloaking_result = detail_result.get("cloaking_result") or {}
detail_delivery = review_queue.delivery_summary(detail_item)
source_job_ids = list(filter(None, detail_item.get("source_job_ids") or [
    detail_item.get("source_job_id"),
]))

with st.container(border=True):
    st.subheader(detail_item.get("domain") or detail_item.get("target_url"), anchor=False)
    st.caption(
        f"URL: {detail_item.get('target_url', '')} · Ngày review: "
        f"{detail_item.get('review_day', '—')} · "
        f"{len(source_job_ids)} lần phát hiện"
    )
    if cloaking_result:
        render_cloaking_result(cloaking_result)
    else:
        st.warning("Case cũ chưa có cloaking manifest đầy đủ; hãy kiểm tra tín hiệu trong bảng.")
    screenshots = cloaking_result.get("screenshots") or []
    if screenshots:
        st.success(
            f"Playwright đã tự chụp {len(screenshots)} ảnh đại diện. Không cần upload thủ công.",
            icon=":material/photo_camera:",
        )
    else:
        st.info(
            "Playwright chưa tái hiện được cặp ảnh khác nhau. Upload thủ công chỉ là phương án dự phòng.",
            icon=":material/info:",
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
        manual_upload_enabled = st.toggle(
            "Bổ sung ảnh thủ công (chỉ dự phòng)",
            value=False,
            key=f"cloaking_review_manual_{detail_id}",
        )
        if manual_upload_enabled:
            with st.form(f"cloaking_review_evidence_{detail_id}"):
                acquisition_url = st.text_input(
                    "URL đã chụp", value=detail_item.get("target_url", ""),
                )
                device = st.selectbox(
                    "Thiết bị", ["desktop and mobile", "desktop", "Android", "iPhone", "other"],
                )
                network = st.selectbox(
                    "Mạng / nguồn truy cập",
                    ["direct and Google", "direct", "Google referrer", "mobile data", "other"],
                )
                uploads = st.file_uploader(
                    "Ảnh đối chiếu thủ công (2–4 ảnh)",
                    type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True,
                )
                confirmed_pair = st.checkbox(
                    "Tôi xác nhận ảnh thuộc cùng URL nhưng hiển thị nội dung khác nhau.",
                )
                save_manual = st.form_submit_button(
                    "Lưu ảnh thủ công", icon=":material/add_photo_alternate:",
                )
            if save_manual:
                if not confirmed_pair:
                    st.warning("Bạn cần xác nhận đây là ảnh của cùng một URL.")
                elif not 2 <= len(uploads or []) <= 4:
                    st.warning("Hãy tải lên từ 2 đến 4 ảnh đối chiếu.")
                else:
                    try:
                        updated = pt.add_operator_cloaking_evidence(
                            cloaking_result,
                            images=[(upload.name, upload.getvalue()) for upload in uploads],
                            acquisition_url=acquisition_url.strip(), device=device,
                            network=network, confirmed_difference=True,
                        )
                        review_queue.update_cloaking_result(detail_id, updated)
                        st.session_state["cloaking_review_flash"] = "Đã lưu ảnh thủ công vào case review."
                        st.rerun()
                    except (OSError, ValueError) as exc:
                        st.error(f"Không thể lưu ảnh: {exc}")

if editable:
    cfg = pt.load_config()
    accounts = cfg.get("smtp_accounts") or []
    account_names = [
        str(account.get("username") or "").strip()
        for account in accounts if str(account.get("username") or "").strip()
    ]
    account_names = list(dict.fromkeys(account_names))
    configured_by_name = {name.lower(): name for name in account_names}
    pending_for_selection = list(dict.fromkeys(
        account
        for queue_id in selected_ids
        for account in review_queue.delivery_summary(items_by_id[queue_id])["pending_accounts"]
    ))
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
    selection_signature = "|".join(sorted(selected_ids))
    if st.session_state.get("cloaking_review_sender_scope") != selection_signature:
        st.session_state["cloaking_review_sender_scope"] = selection_signature
        st.session_state["cloaking_review_sender_accounts"] = default_accounts
    else:
        st.session_state["cloaking_review_sender_accounts"] = [
            name for name in st.session_state.get("cloaking_review_sender_accounts", [])
            if name in account_names
        ]
    selected_accounts = st.multiselect(
        "Tài khoản gửi email",
        options=account_names,
        key="cloaking_review_sender_accounts",
        help=(
            "Mặc định chỉ chọn các tài khoản còn thiếu của những domain đã tích. "
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
    confirmed_action = st.checkbox(
        "Tôi đã kiểm tra evidence và xác nhận hành động cho các domain được chọn.",
        value=False,
    )
    active_job_dir = domain_worker.find_active_job_dir()
    if active_job_dir:
        st.warning(
            "Một Domain Worker đang chạy. Hãy chờ job đó hoàn tất trước khi gửi queue cloaking.",
            icon=":material/schedule:",
        )

    with st.container(horizontal=True, gap="small"):
        send_cloaking = st.button(
            "Xác nhận cloaking và gửi kèm bằng chứng",
            type="primary", icon=":material/send:",
            disabled=bool(active_job_dir) or not account_names,
        )
        send_normal = st.button(
            "Không phải cloaking — gửi report thường",
            icon=":material/outgoing_mail:",
            disabled=bool(active_job_dir) or not account_names,
        )
        skip_items = st.button(
            "Bỏ qua domain đã chọn",
            icon=":material/block:",
            disabled=bool(active_job_dir),
        )

    def _validate_action() -> bool:
        if not selected_ids:
            st.warning("Hãy tích chọn ít nhất một dòng trong bảng.")
            return False
        if not confirmed_action:
            st.warning("Bạn cần xác nhận đã kiểm tra evidence.")
            return False
        return True

    if send_cloaking or send_normal:
        if _validate_action() and selected_accounts:
            job_path = ""
            try:
                decision = "confirmed_cloaking" if send_cloaking else "not_cloaking"
                job_path = domain_worker.create_cloaking_review_job(
                    selected_ids, decision=decision, allowed_accounts=selected_accounts,
                )
                domain_worker.launch_job_process(job_path)
                st.session_state["cloaking_review_flash"] = (
                    f"Đã đưa {len(selected_ids)} domain vào hàng gửi. "
                    "Chỉ các domain được tích chọn mới được xử lý."
                )
                st.rerun()
            except (OSError, ValueError) as exc:
                if job_path:
                    for queue_id in selected_ids:
                        try:
                            review_queue.update_item(
                                queue_id, state=review_queue.FAILED,
                                last_error=f"Could not launch review worker: {exc}",
                            )
                        except (OSError, ValueError):
                            pass
                st.error(f"Không thể tạo job gửi: {exc}")
        elif not selected_accounts:
            st.warning("Hãy chọn ít nhất một tài khoản gửi email.")
    elif skip_items and _validate_action():
        try:
            review_queue.mark_skipped(selected_ids)
            st.session_state["cloaking_review_flash"] = f"Đã bỏ qua {len(selected_ids)} domain."
            st.rerun()
        except (OSError, ValueError) as exc:
            st.error(f"Không thể cập nhật queue: {exc}")

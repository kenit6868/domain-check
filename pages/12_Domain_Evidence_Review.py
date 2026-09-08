"""Manual evidence review for normal Domain Worker reports.

The worker keeps a normal report out of the send list when browser capture
cannot produce a valid PNG + manifest.  This page is the single focused place
where an operator can review the affected URL, upload 1-3 screenshots, and
send the existing report directly.  It intentionally does not create a worker
job and remains usable while the Domain Worker is prechecking or sending other
domains.
"""

from __future__ import annotations

import hashlib
import os

import pandas as pd
import streamlit as st

import browser_evidence
import domain_worker
import phishing_toolkit as pt


def _key(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", "ignore")).hexdigest()[:16]


def _screenshot_paths(evidence: dict | None) -> list[str]:
    if not isinstance(evidence, dict):
        return []
    paths = evidence.get("screenshot_paths") or []
    if not paths and evidence.get("screenshot_path"):
        paths = [evidence.get("screenshot_path")]
    return [
        os.path.abspath(str(path)) for path in paths
        if path and os.path.isfile(str(path))
    ]


def _valid_existing_evidence(evidence: dict | None) -> bool:
    try:
        return bool(browser_evidence.evidence_attachment_paths(evidence or {}))
    except (OSError, ValueError, TypeError):
        return False


def _recipients(item: dict) -> list[str]:
    prepared = item.get("prepared") if isinstance(item.get("prepared"), dict) else item
    return list(dict.fromkeys(
        str(recipient.get("email") or "").strip()
        for recipient in (prepared.get("recipients") or [])
        if isinstance(recipient, dict) and str(recipient.get("email") or "").strip()
    ))


def _last_send_label(item: dict) -> str:
    result = item.get("last_send_result") or {}
    failed = int(result.get("sent_failed", 0) or 0)
    sent = int(result.get("sent_ok", 0) or 0)
    already = int(result.get("already_sent", 0) or 0)
    if failed and (sent or already):
        return "Gửi một phần — có thể retry"
    if failed:
        return "Gửi thất bại — có thể retry"
    if sent or already:
        return "Đang cập nhật trạng thái"
    return "Chờ bổ sung ảnh"


def _load_items() -> list[dict]:
    try:
        return list(domain_worker.list_evidence_review_items())
    except (OSError, ValueError, TypeError):
        return []


st.set_page_config(
    page_title="Domain Evidence Review",
    page_icon=":material/photo_library:",
    layout="wide",
)
st.title("Domain Evidence Review", anchor=False)
st.caption(
    "Các domain thường bị tách khỏi Domain Worker khi không capture được ảnh hợp lệ. "
    "Tải ảnh lên, kiểm tra preview rồi gửi trực tiếp; không tạo job mới."
)

if st.button("Mở Domain Worker", icon=":material/arrow_back:"):
    st.switch_page("pages/6_Domain_Worker.py")

flash = st.session_state.pop("domain_evidence_review_flash", "")
if flash:
    kind = flash.get("kind") if isinstance(flash, dict) else "success"
    message = flash.get("message") if isinstance(flash, dict) else str(flash)
    {"error": st.error, "warning": st.warning, "success": st.success}.get(
        kind, st.info,
    )(message)

items = _load_items()
if not items:
    st.info(
        "Hôm nay không có domain thường nào đang chờ bổ sung ảnh. "
        "Nếu worker vừa chạy xong, hãy bấm làm mới trạng thái ở Domain Worker rồi quay lại trang này.",
        icon=":material/info:",
    )
    if st.button("Làm mới danh sách", icon=":material/refresh:"):
        st.rerun()
    st.stop()

items_by_target = {
    str(item.get("target_url") or ""): item for item in items
    if str(item.get("target_url") or "")
}
row_ids_key = "domain_evidence_review_row_ids"
active_key = "domain_evidence_review_active_target"
click_key = "domain_evidence_review_action"
st.session_state[row_ids_key] = list(items_by_target)


def _open_case() -> None:
    click = st.session_state.get(click_key)
    try:
        target = st.session_state[row_ids_key][int(click["row"])]
    except (KeyError, IndexError, TypeError, ValueError):
        return
    if target not in items_by_target:
        st.session_state.pop(active_key, None)
        return
    st.session_state[active_key] = target


rows = []
for item in items:
    target = str(item.get("target_url") or "")
    rows.append({
        "Xử lý": "Mở case",
        "Full URL": target,
        "Domain": item.get("domain") or "",
        "Trạng thái": _last_send_label(item),
        "Capture": item.get("capture_strategy") or "failed",
        "Email nhận": ", ".join(_recipients(item)) or "—",
        "Lỗi capture": str(item.get("browser_evidence_error") or "")[:240],
        "Job": item.get("source_job_id") or "—",
    })
review_df = pd.DataFrame(rows)
table_config = {
    "Xử lý": st.column_config.ButtonColumn(
        "Xử lý", type="primary", key=click_key,
        on_click=_open_case, width="small",
    ),
    "Full URL": st.column_config.LinkColumn("Full URL", display_text=r"https?://(.+)"),
}
st.caption(
    f"Danh sách hôm nay: {len(items)} URL duy nhất. Chọn **Mở case** để upload ảnh và gửi. "
    "Case đã gửi hoàn tất sẽ tự biến mất khỏi danh sách."
)
st.dataframe(
    review_df,
    key="domain_evidence_review_table",
    hide_index=True,
    width="stretch",
    column_config=table_config,
)

active_target = str(st.session_state.get(active_key) or "")
active_item = items_by_target.get(active_target)
if not active_item:
    st.info(
        "Bấm **Mở case** trong bảng để xem URL, ảnh hiện có và gửi report.",
        icon=":material/touch_app:",
    )
    st.stop()

target_url = str(active_item.get("target_url") or "")
target_key = _key(target_url)
existing_evidence = active_item.get("browser_evidence") or {}
existing_paths = _screenshot_paths(existing_evidence)
existing_valid = _valid_existing_evidence(existing_evidence)
recipient_list = _recipients(active_item)

with st.container(border=True):
    st.subheader(active_item.get("domain") or target_url, anchor=False)
    st.write(f"**Full URL:** `{target_url}`")
    st.caption(
        f"Email nhận: {', '.join(recipient_list) or 'Chưa xác định'} · "
        f"Job nguồn: {active_item.get('source_job_id') or '—'}"
    )
    if active_item.get("browser_evidence_error"):
        st.warning(
            "Capture tự động chưa tạo được evidence hợp lệ: "
            + str(active_item["browser_evidence_error"]),
            icon=":material/photo_camera:",
        )
    if active_item.get("capture_strategy") == "passive_fallback":
        st.info(
            "DOM destination không mở được nên worker đã thử capture thụ động. "
            "Ảnh upload dưới đây chỉ bổ sung cho đúng URL này.",
            icon=":material/info:",
        )

    if existing_paths:
        st.caption("Ảnh đã lưu từ lần thử trước — có thể dùng lại khi retry:")
        with st.container(horizontal=True, gap="small"):
            for path in existing_paths:
                st.image(path, caption=os.path.basename(path), width=160)
        if existing_valid:
            st.success("Evidence hiện có hợp lệ và có thể gửi lại.", icon=":material/verified:")

    uploads = st.file_uploader(
        "Ảnh bằng chứng thủ công (1–3 ảnh PNG/JPEG)",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key=f"domain_evidence_uploads_{target_key}",
        help="Ảnh được preview ngay; hệ thống chỉ ghi file và gửi khi bạn xác nhận.",
    )
    uploads = list(uploads or [])
    valid_uploads = []
    invalid_uploads = []
    for upload in uploads:
        data = upload.getvalue()
        if (
            data
            and len(data) <= browser_evidence.MAX_SCREENSHOT_BYTES
            and (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff"))
        ):
            valid_uploads.append(upload)
        else:
            invalid_uploads.append(upload.name)
    if uploads:
        with st.container(horizontal=True, gap="small"):
            for upload in valid_uploads[:3]:
                st.image(upload.getvalue(), caption=upload.name, width=160)
    if invalid_uploads:
        st.error("Ảnh không hợp lệ hoặc quá 10 MB: " + ", ".join(invalid_uploads))
    if len(uploads) > browser_evidence.MAX_MANUAL_SCREENSHOTS:
        st.warning("Chỉ dùng tối đa 3 ảnh; hãy bỏ bớt ảnh trước khi gửi.")

    cfg = pt.load_config()
    accounts = [
        str(account.get("username") or "").strip()
        for account in (cfg.get("smtp_accounts") or [])
        if str(account.get("username") or "").strip()
    ]
    accounts = list(dict.fromkeys(accounts))
    allowed = {
        str(account).strip().lower() for account in (active_item.get("allowed_accounts") or [])
        if str(account).strip()
    }
    account_options = [
        account for account in accounts
        if not allowed or account.lower() in allowed
    ]
    sender_key = f"domain_evidence_sender_accounts_{target_key}"
    if sender_key not in st.session_state:
        st.session_state[sender_key] = account_options
    selected_accounts = st.multiselect(
        "Tài khoản gửi email",
        options=account_options,
        key=sender_key,
        help="Mặc định dùng các account đã chọn ở job nguồn.",
    )
    confirmed = st.checkbox(
        "Tôi đã kiểm tra đúng URL, email nhận và ảnh; xác nhận gửi report.",
        key=f"domain_evidence_confirm_{target_key}",
    )
    # Treat the upload as one atomic batch: a mixed valid/invalid selection or
    # more than three files must be corrected before an existing evidence set
    # can be sent. This prevents silently dropping a bad attachment.
    upload_batch_valid = bool(uploads) and bool(valid_uploads) and not invalid_uploads and (
        len(uploads) <= browser_evidence.MAX_MANUAL_SCREENSHOTS
    )
    evidence_ready = upload_batch_valid if uploads else existing_valid
    send = st.button(
        "Gửi mail domain này",
        type="primary",
        icon=":material/send:",
        disabled=not (confirmed and evidence_ready and bool(selected_accounts)),
        key=f"domain_evidence_send_{target_key}",
    )
    if not account_options:
        st.error("Không còn SMTP account hợp lệ trong cấu hình của job này.")
    elif not evidence_ready:
        st.info("Chọn ít nhất 1 ảnh hợp lệ hoặc dùng lại evidence đã lưu trước đó.")

if send:
    try:
        if valid_uploads:
            evidence = browser_evidence.create_manual_browser_evidence(
                target_url,
                [(upload.name, upload.getvalue()) for upload in valid_uploads],
                pt._runtime_path(os.path.join("evidence", "browser")),
                profile_name="domain_worker_manual",
            )
        else:
            evidence = existing_evidence
        with st.spinner("Đang tạo draft, kiểm tra evidence và gửi email..."):
            result = domain_worker.send_manual_evidence_item(
                str(active_item.get("job_dir") or ""),
                target_url,
                evidence,
                selected_accounts,
            )
        if int(result.get("sent_failed", 0) or 0):
            st.session_state["domain_evidence_review_flash"] = {
                "kind": "warning",
                "message": "Gửi chưa hoàn tất; evidence được giữ lại để retry không cần upload lại.",
            }
        elif result.get("skipped") == "manual_review_required":
            st.session_state["domain_evidence_review_flash"] = {
                "kind": "warning",
                "message": "Lần check lại phát hiện tín hiệu cloaking; case đã chuyển sang Cloaking Review.",
            }
        else:
            st.session_state["domain_evidence_review_flash"] = {
                "kind": "success",
                "message": "Đã gửi email thành công; domain đã được loại khỏi danh sách chờ ảnh.",
            }
        st.session_state.pop(active_key, None)
        st.rerun()
    except (OSError, ValueError, TypeError) as exc:
        st.error(f"Không thể gửi: {exc}")

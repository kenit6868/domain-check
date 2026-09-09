"""Daily incoming-mail count for the selected account."""

import importlib
from datetime import date, datetime

import pandas as pd
import streamlit as st

import mail_statistics
import phishing_toolkit as pt


EXPECTED_MAIL_STATISTICS_MODULE_VERSION = 9
if getattr(mail_statistics, "MODULE_VERSION", 0) != EXPECTED_MAIL_STATISTICS_MODULE_VERSION:
    # Streamlit may retain imported local modules across a hot reload.
    mail_statistics = importlib.reload(mail_statistics)


RESULT_SCHEMA_VERSION = 3

st.set_page_config(page_title="Thống kê email", page_icon=":material/mail:", layout="wide")
st.title("Thống kê email", anchor=False)
st.caption(
    "Trang này chỉ xem tổng mail nhận trong một ngày địa phương (Inbox + Thư rác). "
    "Muốn đối chiếu Sent, report và các tỷ lệ, hãy dùng menu Thống kê tổng quát."
)

selected_day = st.date_input("Ngày cần xem", value=date.today(), format="DD/MM/YYYY")
local_tz = datetime.now().astimezone().tzinfo
st.caption(f"Múi giờ đang dùng: {datetime.now().astimezone().tzname() or local_tz}")

cfg = pt.load_config()
accounts = list(cfg.get("smtp_accounts", []))
if not accounts:
    st.warning("Chưa có tài khoản email trong config.ini.")
    st.stop()

account_labels = [
    str(account.get("username") or f"Tài khoản {index + 1}")
    for index, account in enumerate(accounts)
]
selected_account_label = st.selectbox(
    "Tài khoản cần xem",
    account_labels,
    key="mail_statistics_account_select",
    help="Chỉ đếm Inbox và Thư rác của tài khoản được chọn.",
)
selected_account = accounts[account_labels.index(selected_account_label)]
selected_account_name = str(selected_account.get("username") or selected_account_label).strip()
st.caption(f"Đang xem riêng tài khoản: **{selected_account_name}**")

job_status = mail_statistics.latest_statistics_job(selected_day, selected_account_name)
job_is_active = bool(job_status and job_status.get("state") in mail_statistics.ACTIVE_JOB_STATES)

check_col, clear_col, note_col = st.columns([1, 1, 4])
with check_col:
    check_clicked = st.button(
        "Kiểm tra mail nhận",
        type="primary",
        icon=":material/refresh:",
        disabled=job_is_active,
    )
with clear_col:
    clear_clicked = st.button(
        "Xóa cache ngày đã chọn",
        icon=":material/delete:",
        disabled=job_is_active,
    )
with note_col:
    st.caption("Kết quả được cache theo ngày và tài khoản; không tải body email.")

if clear_clicked:
    try:
        removed = mail_statistics.clear_cached_statistics(selected_day)
        for key in (
            "mail_statistics_result", "mail_statistics_day",
            "mail_statistics_schema", "mail_statistics_account",
        ):
            st.session_state.pop(key, None)
        if removed:
            st.success(f"Đã xóa cache ngày {selected_day.strftime('%d/%m/%Y')}.")
        else:
            st.info("Ngày đã chọn chưa có cache.")
    except OSError as exc:
        st.error(f"Không xóa được cache thống kê: {exc}")

if check_clicked:
    try:
        job_path = mail_statistics.create_statistics_job(selected_day, [selected_account])
        mail_statistics.launch_statistics_job(job_path)
        for key in (
            "mail_statistics_result", "mail_statistics_day",
            "mail_statistics_schema", "mail_statistics_account",
        ):
            st.session_state.pop(key, None)
        st.success("Đã bắt đầu kiểm tra nền. Bạn có thể quay lại sau để xem tổng mail nhận.")
        job_status = {"state": "queued"}
        job_is_active = True
    except OSError as exc:
        st.error(f"Không khởi động được kiểm tra nền: {exc}")

if job_status and job_status.get("state") in mail_statistics.ACTIVE_JOB_STATES:
    st.info("Đang kiểm tra mail ở chế độ nền; quay lại trang để xem kết quả.")
elif job_status and job_status.get("state") == "complete":
    st.success("Kiểm tra đã hoàn tất; kết quả bên dưới được nạp từ cache.")
elif job_status and job_status.get("state") == "failed":
    st.error(f"Kiểm tra thất bại: {job_status.get('error') or 'Không rõ lỗi'}")

results = st.session_state.get("mail_statistics_result")
result_day = st.session_state.get("mail_statistics_day")
result_schema = st.session_state.get("mail_statistics_schema")
result_account = st.session_state.get("mail_statistics_account")
if not job_is_active and (
    result_day != selected_day.isoformat()
    or result_schema != RESULT_SCHEMA_VERSION
    or str(result_account or "").strip().lower() != selected_account_name.lower()
):
    cached_results = mail_statistics.load_cached_statistics(selected_day, selected_account_name)
    if cached_results:
        st.session_state.mail_statistics_result = cached_results
        st.session_state.mail_statistics_day = selected_day.isoformat()
        st.session_state.mail_statistics_schema = RESULT_SCHEMA_VERSION
        st.session_state.mail_statistics_account = selected_account_name
        results = cached_results
        result_day = selected_day.isoformat()
        result_schema = RESULT_SCHEMA_VERSION
        result_account = selected_account_name

if job_is_active:
    st.stop()
if (
    not results
    or result_day != selected_day.isoformat()
    or result_schema != RESULT_SCHEMA_VERSION
    or str(result_account or "").strip().lower() != selected_account_name.lower()
):
    st.info("Chọn ngày rồi bấm **Kiểm tra mail nhận** để đọc số lượng từ IMAP.")
    st.stop()

successful = [row for row in results if row.get("status", "ok") == "ok"]
total_inbox = sum(int(row.get("received", 0)) for row in successful)
total_junk = sum(int(row.get("junk", 0)) for row in successful)
total_received = total_inbox + total_junk

with st.container(horizontal=True):
    st.metric("Mail Inbox", total_inbox, border=True)
    st.metric("Thư rác", total_junk, border=True)
    st.metric("Tổng mail nhận", total_received, border=True)
    st.metric("Tài khoản đã đọc", f"{len(successful)}/{len(results)}", border=True)

table = pd.DataFrame([
    {
        "Tài khoản": row.get("account", ""),
        "Mail Inbox": str(row.get("received", 0)) if row.get("status", "ok") == "ok" else "—",
        "Thư rác": str(row.get("junk", 0)) if row.get("status", "ok") == "ok" else "—",
        "Tổng mail nhận": (
            str(int(row.get("received", 0)) + int(row.get("junk", 0)))
            if row.get("status", "ok") == "ok" else "—"
        ),
        "Trạng thái": {
            "ok": "Thành công",
            "not_configured": "Không có trong IMAP",
            "error": "Lỗi",
        }.get(row.get("status", "ok"), "Lỗi"),
        "Chi tiết": row.get("error", ""),
    }
    for row in results
])
st.dataframe(table, width="stretch", hide_index=True)

for row in results:
    if row.get("status") == "error" and row.get("error"):
        st.error(f"{row.get('account')}: {row.get('error')}")

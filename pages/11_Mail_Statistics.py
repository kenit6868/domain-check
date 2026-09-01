"""Daily Inbox/Sent/Junk message counts using local calendar dates."""

import importlib
from datetime import date, datetime

import pandas as pd
import streamlit as st

import mail_statistics
import phishing_toolkit as pt


EXPECTED_MAIL_STATISTICS_MODULE_VERSION = 6
if getattr(mail_statistics, "MODULE_VERSION", 0) != EXPECTED_MAIL_STATISTICS_MODULE_VERSION:
    # Streamlit reloads page scripts but can retain imported local modules in
    # the running process. Reload the core when its API/schema is stale.
    mail_statistics = importlib.reload(mail_statistics)

RESULT_SCHEMA_VERSION = 2

st.set_page_config(page_title="Thống kê email", page_icon=":material/mail:", layout="wide")
st.title("Thống kê email", anchor=False)
st.caption(
    "Đếm toàn bộ thư trong Inbox, thư mục Đã gửi và Thư rác theo ngày địa phương. "
    "Trang chỉ đọc metadata IMAP và không thay đổi trạng thái đã đọc của email."
)

selected_day = st.date_input("Chọn ngày", value=date.today(), format="DD/MM/YYYY")
local_tz = datetime.now().astimezone().tzinfo
st.caption(f"Múi giờ đang dùng: {datetime.now().astimezone().tzname() or local_tz}")

cfg = pt.load_config()
accounts = list(cfg.get("smtp_accounts", []))
if not accounts:
    st.warning("Chưa có tài khoản email trong config.ini.")
    st.stop()

job_status = mail_statistics.latest_statistics_job(selected_day)
job_is_active = bool(job_status and job_status.get("state") in mail_statistics.ACTIVE_JOB_STATES)

check_col, clear_col, note_col = st.columns([1, 1, 4])
with check_col:
    check_clicked = st.button("Kiểm tra", type="primary", icon=":material/refresh:", disabled=job_is_active)
with clear_col:
    clear_clicked = st.button("Xóa cache ngày đã chọn", icon=":material/delete:", disabled=job_is_active)
with note_col:
    st.caption("Kết quả được cache tự động sau mỗi lần kiểm tra.")

if clear_clicked:
    try:
        removed = mail_statistics.clear_cached_statistics(selected_day)
        for key in ("mail_statistics_result", "mail_statistics_day", "mail_statistics_schema"):
            st.session_state.pop(key, None)
        if removed:
            st.success(f"Đã xóa cache ngày {selected_day.strftime('%d/%m/%Y')}.")
        else:
            st.info("Ngày đã chọn chưa có cache.")
    except OSError as exc:
        st.error(f"Không xóa được cache thống kê: {exc}")

if check_clicked:
    try:
        job_path = mail_statistics.create_statistics_job(selected_day, accounts)
        mail_statistics.launch_statistics_job(job_path)
        for key in ("mail_statistics_result", "mail_statistics_day", "mail_statistics_schema"):
            st.session_state.pop(key, None)
        st.success("Đã bắt đầu kiểm tra nền. Bạn có thể chuyển sang menu khác và quay lại sau.")
        job_status = {"state": "queued"}
        job_is_active = True
    except OSError as exc:
        st.error(f"Không khởi động được kiểm tra nền: {exc}")

if job_status and job_status.get("state") in mail_statistics.ACTIVE_JOB_STATES:
    st.info("Đang kiểm tra email ở chế độ nền. Bạn có thể chuyển menu; quay lại trang để xem kết quả.")
elif job_status and job_status.get("state") == "complete":
    st.success("Kiểm tra nền đã hoàn tất; kết quả bên dưới được nạp từ cache.")
elif job_status and job_status.get("state") == "failed":
    st.error(f"Kiểm tra nền thất bại: {job_status.get('error') or 'Không rõ lỗi'}")

results = st.session_state.get("mail_statistics_result")
result_day = st.session_state.get("mail_statistics_day")
result_schema = st.session_state.get("mail_statistics_schema")
if not job_is_active and (result_day != selected_day.isoformat() or result_schema != RESULT_SCHEMA_VERSION):
    cached_results = mail_statistics.load_cached_statistics(selected_day)
    if cached_results:
        st.session_state.mail_statistics_result = cached_results
        st.session_state.mail_statistics_day = selected_day.isoformat()
        st.session_state.mail_statistics_schema = RESULT_SCHEMA_VERSION
        results = cached_results
        result_day = selected_day.isoformat()
        result_schema = RESULT_SCHEMA_VERSION
if job_is_active:
    st.stop()
if not results or result_day != selected_day.isoformat() or result_schema != RESULT_SCHEMA_VERSION:
    st.info("Chọn ngày rồi bấm **Kiểm tra** để đọc số lượng từ các hộp thư.")
    st.stop()

successful = [row for row in results if row.get("status", "ok") == "ok"]
total_received = sum(int(row.get("received", 0)) for row in successful)
total_sent = sum(int(row.get("sent", 0)) for row in successful)
total_junk = sum(int(row.get("junk", 0)) for row in successful)
total_received_and_junk = total_received + total_junk
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Mail nhận", total_received)
c2.metric("Mail gửi", total_sent)
c3.metric("Thư rác", total_junk)
c4.metric("Tổng nhận + rác", total_received_and_junk)
c5.metric("Tài khoản đã đọc", f"{len(successful)}/{len(results)}")

table = pd.DataFrame([
    {
        "Tài khoản": row.get("account", ""),
        "Mail nhận": str(row.get("received", 0)) if row.get("status", "ok") == "ok" else "—",
        "Mail gửi": str(row.get("sent", 0)) if row.get("status", "ok") == "ok" else "—",
        "Thư rác": str(row.get("junk", 0)) if row.get("status", "ok") == "ok" else "—",
        "Tổng nhận + rác": str(int(row.get("received", 0)) + int(row.get("junk", 0)))
        if row.get("status", "ok") == "ok" else "—",
        "Trạng thái": {
            "ok": "Thành công", "not_configured": "Không có trong IMAP", "error": "Lỗi",
        }.get(row.get("status", "ok"), "Lỗi"),
        "Chi tiết": row.get("error", ""),
    }
    for row in results
])
st.dataframe(table, width="stretch", hide_index=True)

for row in results:
    if row.get("status") == "error" and row.get("error"):
        st.error(f"{row.get('account')}: {row.get('error')}")

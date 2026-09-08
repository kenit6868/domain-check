"""Daily Inbox/Sent/Junk message counts using local calendar dates."""

import importlib
from datetime import date, datetime

import pandas as pd
import streamlit as st

import mail_statistics
import phishing_toolkit as pt
import report_statistics


EXPECTED_MAIL_STATISTICS_MODULE_VERSION = 7
if getattr(mail_statistics, "MODULE_VERSION", 0) != EXPECTED_MAIL_STATISTICS_MODULE_VERSION:
    # Streamlit reloads page scripts but can retain imported local modules in
    # the running process. Reload the core when its API/schema is stale.
    mail_statistics = importlib.reload(mail_statistics)

EXPECTED_REPORT_STATISTICS_MODULE_VERSION = 1
if getattr(report_statistics, "MODULE_VERSION", 0) != EXPECTED_REPORT_STATISTICS_MODULE_VERSION:
    report_statistics = importlib.reload(report_statistics)

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

account_labels = [
    str(account.get("username") or f"Tài khoản {index + 1}")
    for index, account in enumerate(accounts)
]
selected_account_label = st.selectbox(
    "Tài khoản cần thống kê",
    account_labels,
    key="mail_statistics_account_select",
    help="Chỉ đọc Sent/Inbox/Junk và dữ liệu report của tài khoản được chọn.",
)
selected_account = accounts[account_labels.index(selected_account_label)]
selected_account_name = str(selected_account.get("username") or selected_account_label).strip()
st.caption(f"Đang xem riêng tài khoản: **{selected_account_name}**")

job_status = mail_statistics.latest_statistics_job(selected_day, selected_account_name)
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
        for key in ("mail_statistics_result", "mail_statistics_day", "mail_statistics_schema", "mail_statistics_account"):
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
        for key in ("mail_statistics_result", "mail_statistics_day", "mail_statistics_schema", "mail_statistics_account"):
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

st.divider()
st.subheader("Hiệu quả report của tài khoản đang chọn")
st.caption(
    "Phần này chỉ đọc `sent_log.csv` và cache Provider Replies trên máy. "
    "Để nối phản hồi mới, hãy đồng bộ đúng tài khoản ở page **Phản hồi NCC** trước."
)
period_col1, period_col2 = st.columns(2)
with period_col1:
    report_date_from = st.date_input(
        "Report từ ngày", value=selected_day, key="report_statistics_date_from",
        format="DD/MM/YYYY",
    )
with period_col2:
    report_date_to = st.date_input(
        "Report đến ngày", value=selected_day, key="report_statistics_date_to",
        format="DD/MM/YYYY",
    )
if report_date_from > report_date_to:
    st.error("Ngày bắt đầu report không được lớn hơn ngày kết thúc.")
else:
    try:
        analytics = report_statistics.build_account_report(
            selected_account_name,
            report_date_from,
            report_date_to,
            local_tz=local_tz,
        )
    except Exception as exc:
        analytics = None
        st.error(f"Không tạo được thống kê report: {exc}")
    if analytics:
        a1, a2, a3, a4, a5, a6 = st.columns(6)
        a1.metric("Report đã ghi", analytics["sent_total"])
        a2.metric("Gửi thành công", analytics["sent_success"])
        a3.metric("Có phản hồi", analytics["linked_reply_total"])
        a4.metric("Đã xử lý", analytics["resolved_total"])
        a5.metric("Tỷ lệ phản hồi", f"{analytics['response_rate']:.2f}%")
        a6.metric("Tỷ lệ takedown", f"{analytics['takedown_rate']:.2f}%")
        st.caption(
            f"{analytics.get('domain_total', 0)} domain duy nhất trong kỳ; "
            f"{analytics.get('resolved_domain_total', 0)} domain có reply xác nhận đã xử lý."
        )

        evidence = analytics["evidence"]
        st.markdown("**Evidence**")
        st.dataframe(pd.DataFrame([
            {"Evidence": "Browser Evidence tự động", "Số report": evidence.get("automatic", 0)},
            {"Evidence": "Upload thủ công", "Số report": evidence.get("manual", 0)},
            {"Evidence": "Tự động + thủ công", "Số report": evidence.get("mixed", 0)},
            {"Evidence": "Không có ảnh", "Số report": evidence.get("none", 0)},
            {"Evidence": "Chưa có metadata", "Số report": evidence.get("unknown", 0)},
        ]), width="stretch", hide_index=True)

        channel_rows = analytics.get("by_channel") or []
        provider_rows = analytics.get("by_provider") or []
        if channel_rows or provider_rows:
            channel_col, provider_col = st.columns(2)
            with channel_col:
                st.markdown("**Theo kênh**")
                st.dataframe(pd.DataFrame([
                    {
                        "Kênh": row.get("channel_label", "—"),
                        "Đã gửi": row.get("sent", 0),
                        "Thành công": row.get("success", 0),
                        "Phản hồi": row.get("replies", 0),
                        "Đã xử lý": row.get("resolved", 0),
                        "Takedown %": f"{row.get('takedown_rate', 0):.2f}%",
                    }
                    for row in channel_rows
                ]), width="stretch", hide_index=True)
            with provider_col:
                st.markdown("**Theo provider/recipient**")
                st.dataframe(pd.DataFrame([
                    {
                        "Provider": row.get("provider_label", "—"),
                        "Đã gửi": row.get("sent", 0),
                        "Thành công": row.get("success", 0),
                        "Phản hồi": row.get("replies", 0),
                        "Đã xử lý": row.get("resolved", 0),
                        "Takedown %": f"{row.get('takedown_rate', 0):.2f}%",
                    }
                    for row in provider_rows
                ]), width="stretch", hide_index=True)

        subject_rows = analytics.get("by_subject") or []
        draft_rows = analytics.get("by_draft") or []
        if subject_rows or draft_rows:
            st.markdown("**Hiệu quả theo Subject / draft**")
            subject_col, draft_col = st.columns(2)
            with subject_col:
                st.dataframe(pd.DataFrame([
                    {
                        "Subject": row.get("subject", "—"),
                        "Đã gửi": row.get("sent", 0),
                        "Phản hồi": row.get("replies", 0),
                        "Đã xử lý": row.get("resolved", 0),
                        "Takedown %": f"{row.get('takedown_rate', 0):.2f}%",
                    }
                    for row in subject_rows
                ]), width="stretch", hide_index=True)
            with draft_col:
                st.dataframe(pd.DataFrame([
                    {
                        "Draft": row.get("draft", "—"),
                        "Đã gửi": row.get("sent", 0),
                        "Phản hồi": row.get("replies", 0),
                        "Đã xử lý": row.get("resolved", 0),
                        "Takedown %": f"{row.get('takedown_rate', 0):.2f}%",
                    }
                    for row in draft_rows
                ]), width="stretch", hide_index=True)

        outcomes = analytics.get("outcomes") or {}
        if outcomes:
            st.markdown("**Kết quả Provider Replies**")
            outcome_labels = {
                "resolved": "Đã xử lý/takedown",
                "acknowledged": "Đã tiếp nhận",
                "action_required": "Yêu cầu bổ sung",
                "delivery_failed": "Gửi thất bại / trả lại",
                "manual_review": "Cần đọc thủ công",
            }
            st.dataframe(pd.DataFrame([
                {"Kết quả": outcome_labels.get(key, key), "Số thư": value}
                for key, value in sorted(outcomes.items())
            ]), width="stretch", hide_index=True)

        if analytics.get("links"):
            st.markdown("**Đối chiếu Sent Mail → Provider Replies**")
            st.dataframe(pd.DataFrame(analytics["links"]), width="stretch", hide_index=True)
        for warning in analytics.get("warnings") or []:
            st.warning(warning)

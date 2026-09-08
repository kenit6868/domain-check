from datetime import date, datetime, timedelta
import time
import pandas as pd
import streamlit as st
import mail_statistics
import phishing_toolkit as pt

st.set_page_config(page_title="Phân tích phản hồi email", page_icon=":material/analytics:", layout="wide")
st.title("Phân tích phản hồi email", anchor=False)
st.caption("Đọc Inbox/Thư rác ở chế độ chỉ đọc để nhận diện NCC đã xử lý domain hoặc mail gửi bị trả/bị chặn.")
today = date.today(); c1, c2 = st.columns(2)
with c1: start = st.date_input("Từ ngày", today - timedelta(days=6), format="DD/MM/YYYY")
with c2: end = st.date_input("Đến ngày", today, format="DD/MM/YYYY")
accounts = list(pt.load_config().get("smtp_accounts", []))
job = mail_statistics.latest_provider_analysis_job(start, end)
active = bool(job and job.get("state") in mail_statistics.ACTIVE_JOB_STATES)
b1, b2, b3 = st.columns(3)
with b1: run = st.button("Phân tích ngầm", type="primary", disabled=active)
with b2: clear = st.button("Xóa cache khoảng ngày này", disabled=active)
with b3: refresh = st.button("Làm mới", icon=":material/refresh:")
if clear:
    mail_statistics.clear_provider_analysis_cache(start, end)
    st.session_state.pop("analysis_selected", None)
    st.rerun()
if refresh: st.rerun()
if run:
    if start > end: st.error("Từ ngày không được lớn hơn Đến ngày.")
    else:
        # A new run must never render results from the previous run while it
        # is still processing.
        mail_statistics.clear_provider_analysis_cache(start, end)
        path = mail_statistics.create_provider_analysis_job(start, end, accounts)
        mail_statistics.launch_provider_analysis_job(path); st.rerun()
results = mail_statistics.load_provider_analysis_cache(start, end)
if active:
    completed = int(job.get("completed_accounts", 0) or 0)
    total = max(int(job.get("total_accounts", len(accounts)) or 0), 1)
    st.info(f"Đang check ngầm: {completed}/{total} tài khoản. Cache tới đâu hiển thị tới đó.")
    st.progress(min(completed / total, 1.0), text=f"Tiến độ: {completed}/{total} tài khoản ({completed * 100 // total}%)")
    time.sleep(2); st.rerun()
if job and job.get("state") == "complete":
    st.success("Đã check xong. Có thể xóa cache để check lại.")
    st.progress(1.0, text="Tiến độ: 100%")
if job and job.get("state") == "failed": st.error(job.get("error") or "Job thất bại")
if not results: st.info("Chọn khoảng ngày rồi bấm Phân tích ngầm."); st.stop()
ok = [r for r in results if r.get("status") == "ok"]
st.metric("NCC xác nhận đã xử lý domain", sum(r.get("resolved", 0) for r in ok))
st.metric("Mail gửi bị trả / có dấu hiệu bị chặn", sum(r.get("delivery_failed", 0) for r in ok))
messages = [m | {"Tài khoản": r.get("account", "")} for r in ok for m in r.get("messages", [])]
if messages:
    st.session_state.analysis_rows = list(range(len(messages)))
    def open_message():
        try: st.session_state.analysis_selected = st.session_state.analysis_rows[int(st.session_state["analysis_eye"]["row"])]
        except (KeyError, TypeError, ValueError, IndexError): pass
    rows = [{k: v for k, v in m.items() if k not in {"body", "evidence"}} | {"Xem": "👁"} for m in messages]
    st.dataframe(pd.DataFrame(rows), key="analysis_table", width="stretch", hide_index=True, column_config={"Xem": st.column_config.ButtonColumn("Xem", type="tertiary", on_click=open_message, key="analysis_eye")})
    index = st.session_state.pop("analysis_selected", None)
    if isinstance(index, int) and 0 <= index < len(messages):
        m = messages[index]
        @st.dialog("Nội dung phản hồi NCC")
        def dialog():
            st.caption(f"{m.get('provider', '')} · {m.get('domain', '')} · {m.get('ticket', '')}")
            st.write(f"**Từ:** {m.get('sender', '')}  \n**Subject:** {m.get('subject', '')}")
            if m.get("evidence"): st.success(f"Bằng chứng xử lý: {m['evidence']}")
            left, right = st.columns(2)
            with left: st.text_area("Nội dung nguyên văn", m.get("body", ""), height=500, disabled=True)
            with right:
                st.text_area("Bản dịch tiếng Việt (hỗ trợ)", mail_statistics.translate_provider_body(m.get("body", "")), height=500, disabled=True)
        dialog()

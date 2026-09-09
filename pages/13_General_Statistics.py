"""One explicit IMAP sync and account-scoped effectiveness dashboard."""

from datetime import date, datetime
import importlib

import pandas as pd
import streamlit as st

import general_statistics
import phishing_toolkit as pt


EXPECTED_GENERAL_STATISTICS_MODULE_VERSION = 2
if getattr(general_statistics, "MODULE_VERSION", 0) != EXPECTED_GENERAL_STATISTICS_MODULE_VERSION:
    general_statistics = importlib.reload(general_statistics)


st.set_page_config(
    page_title="Thống kê tổng quát",
    page_icon=":material/analytics:",
    layout="wide",
)
st.title("Thống kê tổng quát", anchor=False)
st.caption(
    "Một nút đồng bộ cho đúng một tài khoản và khoảng ngày: Inbox, Sent, Thư rác, "
    "metadata ảnh evidence và phản hồi nhà cung cấp. Trang Phản hồi NCC vẫn chỉ dùng "
    "để lọc, xem và trả lời từng email."
)

cfg = pt.load_config()
accounts = [
    account for account in (cfg.get("smtp_accounts") or [])
    if str(account.get("imap_host") or "").strip()
]
if not accounts:
    st.warning("Chưa có tài khoản có imap_host trong config.ini.")
    st.stop()

labels = [
    str(account.get("username") or f"Tài khoản {index + 1}")
    for index, account in enumerate(accounts)
]
selected_label = st.selectbox(
    "Tài khoản cần thống kê",
    labels,
    key="general_statistics_account",
)
account = accounts[labels.index(selected_label)]
account_name = str(account.get("username") or selected_label).strip()
st.caption(f"Phạm vi tài khoản: **{account_name}**")

date_col1, date_col2 = st.columns(2)
with date_col1:
    date_from = st.date_input(
        "Từ ngày",
        value=date.today(),
        key="general_statistics_date_from",
        format="DD/MM/YYYY",
    )
with date_col2:
    date_to = st.date_input(
        "Đến ngày",
        value=date.today(),
        key="general_statistics_date_to",
        format="DD/MM/YYYY",
    )
local_tz = datetime.now().astimezone().tzinfo
st.caption(f"Múi giờ lọc ngày: {datetime.now().astimezone().tzname() or local_tz}")

selection_key = f"{account_name.strip().lower()}|{date_from.isoformat()}|{date_to.isoformat()}"
cached_snapshot = None if date_from > date_to else general_statistics.load_snapshot(
    account_name, date_from, date_to,
)
session_snapshot = st.session_state.get("general_statistics_snapshot")
if (
    st.session_state.get("general_statistics_selection") != selection_key
    or not isinstance(session_snapshot, dict)
    or int(session_snapshot.get("version", 0) or 0) != EXPECTED_GENERAL_STATISTICS_MODULE_VERSION
):
    session_snapshot = None
if isinstance(cached_snapshot, dict) and int(cached_snapshot.get("version", 0) or 0) != EXPECTED_GENERAL_STATISTICS_MODULE_VERSION:
    cached_snapshot = None
snapshot = session_snapshot or cached_snapshot

sync_col, clear_col, note_col = st.columns([1.35, 1, 3.65])
with sync_col:
    sync_clicked = st.button(
        "Đồng bộ & tính thống kê",
        type="primary",
        icon=":material/sync:",
        disabled=date_from > date_to,
    )
with clear_col:
    clear_clicked = st.button(
        "Xóa cache tài khoản",
        icon=":material/delete:",
        disabled=not bool(snapshot),
    )
with note_col:
    st.caption(
        "Không gửi email, không đổi cờ đã đọc và không lưu body/credential/bytes ảnh. "
        "Chỉ kết nối IMAP khi bấm Đồng bộ."
    )

if date_from > date_to:
    st.error("Từ ngày không được lớn hơn Đến ngày.")

if clear_clicked:
    try:
        removed = general_statistics.clear_snapshots(account_name)
        st.session_state.pop("general_statistics_snapshot", None)
        st.session_state.pop("general_statistics_selection", None)
        if removed:
            st.success("Đã xóa cache Thống kê tổng quát của tài khoản đang chọn.")
        else:
            st.info("Tài khoản này chưa có cache thống kê.")
        snapshot = None
    except OSError as exc:
        st.error(f"Không xóa được cache thống kê: {exc}")

if sync_clicked:
    progress = st.progress(0, text="Đang chuẩn bị đồng bộ IMAP...")
    stage_labels = {
        "mail": "Đang đếm Inbox, Sent và Thư rác",
        "sent": "Đang đọc Sent và metadata evidence",
        "replies": "Đang đọc phản hồi NCC trong Inbox + Thư rác",
        "done": "Đã hoàn tất đồng bộ",
    }

    def show_progress(stage, position, total):
        percent = int(position * 100 / total) if total else 100
        progress.progress(percent, text=stage_labels.get(stage, "Đang đồng bộ IMAP"))

    try:
        with st.spinner("Đang đồng bộ dữ liệu cho Thống kê tổng quát..."):
            snapshot = general_statistics.sync_account_general_statistics(
                account,
                date_from,
                date_to,
                local_tz=local_tz,
                progress_callback=show_progress,
            )
        progress.progress(100, text="Đã hoàn tất đồng bộ")
        st.session_state.general_statistics_snapshot = snapshot
        st.session_state.general_statistics_selection = selection_key
        if snapshot.get("status") == "complete":
            st.success("Đã đồng bộ đủ Inbox, Sent, Thư rác và dữ liệu report.")
        else:
            st.warning("Đồng bộ hoàn tất một phần; các số liệu có sẵn vẫn được hiển thị bên dưới.")
    except Exception as exc:
        st.error(f"Không đồng bộ được thống kê tổng quát: {exc}")

if not snapshot:
    st.info("Chọn tài khoản/khoảng ngày rồi bấm **Đồng bộ & tính thống kê**.")
    st.stop()

metrics = general_statistics.summary_metrics(snapshot)
report = snapshot.get("report") if isinstance(snapshot.get("report"), dict) else {}
mail = snapshot.get("mail") if isinstance(snapshot.get("mail"), dict) else {}
evidence = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}

st.caption(
    f"Lần đồng bộ gần nhất: {snapshot.get('updated_at') or '—'} · "
    f"Khoảng ngày: {snapshot.get('date_from')} → {snapshot.get('date_to')}"
)

with st.container(horizontal=True):
    st.metric("Mail Inbox", metrics["inbox"], border=True)
    st.metric("Mail Sent", metrics["sent"], border=True)
    st.metric("Thư rác", metrics["junk"], border=True)
    st.metric("Tổng mail nhận", metrics["total_incoming"], border=True)
    st.metric("Tỷ lệ thư rác", f"{metrics['spam_rate']:.2f}%", border=True)

with st.container(horizontal=True):
    st.metric("Report đã ghi", metrics["report_total"], border=True)
    st.metric("Gửi thành công", metrics["report_success"], f"{metrics['send_success_rate']:.2f}%", border=True)
    st.metric("Có phản hồi", metrics["replies"], f"{metrics['response_rate']:.2f}%", border=True)
    st.metric("Đã xử lý", metrics["resolved"], f"{metrics['takedown_rate']:.2f}%", border=True)
    st.metric("Report có ảnh", metrics["with_images"], f"{metrics['evidence_rate']:.2f}%", border=True)

st.subheader("Đối chiếu mailbox", anchor=False)
folder_rows = {
    str(row.get("folder") or "").strip().lower(): row
    for row in (snapshot.get("reply_sync", {}).get("folders") or [])
    if isinstance(row, dict)
}
inbox_sync = folder_rows.get("inbox", {})
junk_sync = folder_rows.get("thư rác", {})
mail_table = pd.DataFrame([
    {
        "Thư mục": "Inbox",
        "Tổng mail": metrics["inbox"],
        "Phản hồi/report đã nhận diện": int(inbox_sync.get("matched", 0) or 0),
        "Trạng thái đồng bộ": inbox_sync.get("status") or mail.get("status") or "—",
    },
    {
        "Thư mục": "Sent",
        "Tổng mail": metrics["sent"],
        "Phản hồi/report đã nhận diện": metrics["report_total"],
        "Trạng thái đồng bộ": "Thành công" if snapshot.get("sent_sync", {}).get("success") else "Chưa hoàn tất",
    },
    {
        "Thư mục": "Thư rác",
        "Tổng mail": metrics["junk"],
        "Phản hồi/report đã nhận diện": int(junk_sync.get("matched", 0) or 0),
        "Trạng thái đồng bộ": junk_sync.get("status") or mail.get("status") or "—",
    },
])
st.dataframe(mail_table, width="stretch", hide_index=True)
if snapshot.get("reply_sync", {}).get("used_cache_fallback"):
    st.warning("Không đọc được phản hồi NCC trong lần này; phần outcome đang dùng cache cũ của chính tài khoản này.")

st.subheader("Evidence và hiệu quả report", anchor=False)
evidence_table = pd.DataFrame([
    {"Evidence": "Browser Evidence tự động", "Số report": int(evidence.get("automatic", 0) or 0)},
    {"Evidence": "Upload thủ công", "Số report": int(evidence.get("manual", 0) or 0)},
    {"Evidence": "Tự động + thủ công", "Số report": int(evidence.get("mixed", 0) or 0)},
    {"Evidence": "Không có ảnh", "Số report": int(evidence.get("none", 0) or 0)},
    {"Evidence": "Chưa có metadata", "Số report": int(evidence.get("unknown", 0) or 0)},
])
st.dataframe(evidence_table, width="stretch", hide_index=True)

channel_rows = report.get("by_channel") or []
provider_rows = report.get("by_provider") or []
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
                "Takedown %": f"{float(row.get('takedown_rate', 0) or 0):.2f}%",
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
                "Takedown %": f"{float(row.get('takedown_rate', 0) or 0):.2f}%",
            }
            for row in provider_rows
        ]), width="stretch", hide_index=True)

subject_rows = report.get("by_subject") or []
draft_rows = report.get("by_draft") or []
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
                "Takedown %": f"{float(row.get('takedown_rate', 0) or 0):.2f}%",
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
                "Takedown %": f"{float(row.get('takedown_rate', 0) or 0):.2f}%",
            }
            for row in draft_rows
        ]), width="stretch", hide_index=True)

outcomes = report.get("outcomes") or {}
if outcomes:
    st.markdown("**Kết quả phản hồi NCC**")
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

if report.get("links"):
    st.markdown("**Đối chiếu Sent Mail → Provider Replies**")
    st.dataframe(pd.DataFrame(report["links"]), width="stretch", hide_index=True)

for warning in report.get("warnings") or []:
    st.warning(warning)
for error in snapshot.get("errors") or []:
    if not isinstance(error, dict):
        continue
    if error.get("error"):
        st.warning(f"{error.get('stage')}: {error['error']}")
    elif error.get("count"):
        st.warning(f"{error.get('stage')}: có {error['count']} email không đọc được đầy đủ metadata.")

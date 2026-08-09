"""pages/4_Case_Log.py — Xem/lọc/sửa case_log.csv (ghi bởi run_check()).

Đọc trực tiếp case_log.csv bằng pandas — không đi qua hàm nào trong
phishing_toolkit.py vì file này là dữ liệu (working data), không phải logic.
Chỉ cột "status" cho sửa; các cột còn lại chỉ đọc để tránh sửa nhầm bằng chứng.

T7: Tab "Cần follow-up" — hiện case gửi email >7 ngày chưa có phản hồi (ticket_ref trống).
"""

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

import phishing_toolkit as pt

st.set_page_config(page_title="Case Log", page_icon="📋", layout="wide")
st.title("📋 Case Log")

tab_cases, tab_followup = st.tabs(["📋 Tất cả case", "⏰ Cần follow-up (>7 ngày)"])

with tab_cases:
    st.caption(f"Toàn bộ case đã kiểm tra, đọc từ {pt.LOG_PATH}")

    if not os.path.exists(pt.LOG_PATH):
        st.info("Chưa có case nào. Vào trang **Check Domain** để chạy kiểm tra đầu tiên.")
    else:
        df = pd.read_csv(pt.LOG_PATH, on_bad_lines="skip")

        if df.empty:
            st.info("case_log.csv hiện đang trống.")
        else:
            if "status" in df.columns:
                status_options = sorted(df["status"].dropna().unique().tolist())
                selected_status = st.multiselect("Lọc theo status", options=status_options, default=status_options)
                filtered = df[df["status"].isin(selected_status)] if selected_status else df.iloc[0:0]
            else:
                filtered = df

            st.caption(f"Đang hiển thị {len(filtered)}/{len(df)} case. Click tiêu đề cột để sort. Chỉ cột 'status' sửa được.")

            other_cols = [c for c in df.columns if c != "status"]
            edited = st.data_editor(
                filtered,
                width="stretch",
                height=600,
                disabled=other_cols,
                key="case_log_editor",
            )

            if st.button("Lưu thay đổi status", type="primary"):
                df.loc[edited.index, "status"] = edited["status"]
                df.to_csv(pt.LOG_PATH, index=False)
                st.success(f"Đã ghi đè {pt.LOG_PATH}")
                st.rerun()

with tab_followup:
    st.caption("Các email đã gửi >7 ngày mà chưa có ticket/case number — cần gửi follow-up.")

    if not os.path.exists(pt.SENT_LOG_PATH):
        st.info("Chưa gửi email nào. sent_log.csv chưa tồn tại.")
        st.stop()

    try:
        sent_df = pd.read_csv(pt.SENT_LOG_PATH, on_bad_lines="skip")
    except Exception as e:
        st.error(f"Không đọc được sent_log.csv: {e}")
        st.stop()

    if sent_df.empty:
        st.info("sent_log.csv hiện đang trống.")
        st.stop()

    # Chỉ giữ dòng thành công, có to email, không phải ticket-update
    if "success" in sent_df.columns:
        sent_df = sent_df[sent_df["success"].astype(str).str.lower().isin(["true", "1", "yes"])]
    if "account" in sent_df.columns:
        sent_df = sent_df[sent_df["account"].fillna("") != "[ticket-update]"]

    if "timestamp" not in sent_df.columns:
        st.info("sent_log.csv chưa có cột timestamp.")
        st.stop()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)

    def _parse_ts(ts_str):
        try:
            dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    sent_df["_ts"] = sent_df["timestamp"].apply(_parse_ts)
    old_sent = sent_df[sent_df["_ts"].apply(lambda d: d is not None and d < cutoff)].copy()

    # Loại bỏ domain đã có ticket_ref
    if "ticket_ref" in old_sent.columns:
        # Domain đã có ít nhất 1 ticket ref trong sent_log
        ticketed_domains = set(
            sent_df[sent_df["ticket_ref"].notna() & (sent_df["ticket_ref"] != "")]["domain"].astype(str)
        )
        old_sent = old_sent[~old_sent["domain"].astype(str).isin(ticketed_domains)]

    # Dedup theo domain+to (chỉ hiện 1 dòng mới nhất)
    if not old_sent.empty and "domain" in old_sent.columns and "to" in old_sent.columns:
        old_sent = old_sent.sort_values("_ts", ascending=False).drop_duplicates(subset=["domain", "to"])

    old_sent = old_sent.drop(columns=["_ts"], errors="ignore")

    if old_sent.empty:
        st.success("✅ Không có case nào cần follow-up — tất cả đã phản hồi hoặc chưa quá 7 ngày.")
    else:
        st.warning(f"⚠️ {len(old_sent)} email gửi >7 ngày chưa có ticket phản hồi:")
        show_cols = [c for c in ["timestamp", "domain", "to", "subject", "draft_file"] if c in old_sent.columns]
        st.dataframe(old_sent[show_cols], width="stretch", hide_index=True)

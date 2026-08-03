"""pages/4_Case_Log.py — Xem/lọc/sửa case_log.csv (ghi bởi run_check()).

Đọc trực tiếp case_log.csv bằng pandas — không đi qua hàm nào trong
phishing_toolkit.py vì file này là dữ liệu (working data), không phải logic.
Chỉ cột "status" cho sửa; các cột còn lại chỉ đọc để tránh sửa nhầm bằng chứng.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

import phishing_toolkit as pt

st.set_page_config(page_title="Case Log", page_icon="📋", layout="wide")
st.title("📋 Case Log")
st.caption(f"Toàn bộ case đã kiểm tra, đọc từ {pt.LOG_PATH}")

if not os.path.exists(pt.LOG_PATH):
    st.info("Chưa có case nào. Vào trang **Check Domain** để chạy kiểm tra đầu tiên.")
    st.stop()

df = pd.read_csv(pt.LOG_PATH)

if df.empty:
    st.info("case_log.csv hiện đang trống.")
    st.stop()

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

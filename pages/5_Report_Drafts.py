"""pages/5_Report_Drafts.py — Xem/tải các email báo cáo do generate_email_drafts() sinh ra,
và gửi thật 1 draft qua SMTP nếu draft đó có địa chỉ "To:" hợp lệ.

Đọc file .txt trong reports/ như trước; phần gửi email dùng chung
email_send_ui.render_send_email_ui() với pages/1_Check_Domain.py — xem CLAUDE.md mục "Gửi báo
cáo qua email thật (SMTP)".
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

import phishing_toolkit as pt
from email_send_ui import render_send_email_ui

st.set_page_config(page_title="Report Drafts", page_icon="✉️", layout="wide")
st.title("✉️ Report Drafts")
st.caption(f"Email báo cáo abuse đã được tool tự sinh sẵn trong {pt.REPORTS_DIR}")

if not os.path.isdir(pt.REPORTS_DIR):
    st.info("Chưa có draft nào. Vào trang **Check Domain** để tool tự sinh.")
    st.stop()

files = sorted(f for f in os.listdir(pt.REPORTS_DIR) if f.endswith(".txt"))
if not files:
    st.info("Chưa có draft nào. Vào trang **Check Domain** để tool tự sinh.")
    st.stop()

selected = st.selectbox("Chọn file báo cáo", files)
path = os.path.join(pt.REPORTS_DIR, selected)
with open(path, encoding="utf-8") as f:
    content = f.read()

st.caption("Di chuột vào khung dưới để hiện nút copy-to-clipboard ở góc trên phải.")
st.code(content, language=None)

st.download_button("Tải xuống .txt", data=content, file_name=selected, mime="text/plain")

st.divider()
st.subheader("Gửi báo cáo qua email thật")
render_send_email_ui(path, pt.load_config(), key_prefix="drafts")

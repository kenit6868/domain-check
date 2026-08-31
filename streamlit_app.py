"""streamlit_app.py — Trang chủ / dashboard của Phishing Takedown Toolkit.

Chạy: streamlit run streamlit_app.py
Dùng st.navigation() để kiểm soát sidebar — comment/bỏ comment từng st.Page
bên dưới để ẩn/hiện page tương ứng.

Toàn bộ logic nghiệp vụ (SSL/WHOIS/VirusTotal/Safe Browsing/log/email draft)
nằm trong phishing_toolkit.py — trang này chỉ gọi thẳng phishing_toolkit.run_check()
chứ không viết lại pipeline, để CLI và web UI không bao giờ lệch kết quả nhau.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import streamlit as st

import phishing_toolkit as pt

# ── Khai báo sidebar navigation ───────────────────────────────────────────────
# Comment bất kỳ st.Page nào để ẩn page đó khỏi sidebar.
_pages = st.navigation([
    st.Page("streamlit_home.py",              title="Trang chủ",        icon="🏠"),
    st.Page("pages/1_Check_Domain.py",        title="Check Domain",     icon="🔍"),
    st.Page("pages/7_Quick_Report.py",        title="Quick Report",     icon="⚡"),
#     st.Page("pages/2_Related_Domains.py",     title="Related Domains",  icon="🕸️"),
#     st.Page("pages/3_Brand_Scan.py",          title="Brand Scan",       icon="🛡️"),
    st.Page("pages/6_Domain_Worker.py",       title="Domain Worker",    icon="⚙️"),
    st.Page("pages/10_Cloaking_Review.py",    title="Cloaking Review",  icon=":material/visibility:"),
    st.Page("pages/8_Check_Link_Status.py",   title="Check Link Status",icon="🔗"),
    st.Page("pages/4_Case_Log.py",            title="Case Log",         icon="📋"),
    st.Page("pages/5_Report_Drafts.py",       title="Report Drafts",    icon="✉️"),
    st.Page("pages/9_Provider_Replies.py",    title="Phản hồi NCC",     icon="📨"),
])
_pages.run()

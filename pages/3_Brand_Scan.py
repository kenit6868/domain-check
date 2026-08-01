"""pages/3_Brand_Scan.py — Chủ động dò biến thể gõ nhầm/giống domain thật.

Gọi thẳng phishing_toolkit.brand_scan() (dnstwist), tương đương CLI `brandscan`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

import phishing_toolkit as pt

st.set_page_config(page_title="Brand Scan", page_icon="🛡️", layout="wide")
st.title("🛡️ Brand Scan (dnstwist)")
st.caption("Sinh các biến thể gõ nhầm/giống domain thật và lọc ra những cái đã bị đăng ký")

st.warning(
    "Quét có thể mất **vài phút, brand phổ biến có thể tới 10 phút** — dnstwist phải "
    "resolve DNS cho hàng nghìn biến thể. Đừng đóng tab trong lúc quét."
)

with st.form("brandscan_form"):
    target = st.text_input("Domain thật cần bảo vệ", placeholder="vd: github.io")
    limit = st.number_input("Giới hạn số kết quả hiển thị", min_value=1, max_value=500, value=50, step=10)
    go = st.form_submit_button("Quét", type="primary")

if go:
    if not target.strip():
        st.warning("Nhập domain trước khi quét.")
        st.stop()

    domain = pt.normalize_domain(target)
    with st.spinner(f"Đang quét biến thể của {domain}... (có thể mất tới 10 phút)"):
        res = pt.brand_scan(domain, int(limit))

    if "error" in res:
        st.error(res["error"])
    else:
        st.success(f"Tìm thấy {res['count']} domain biến thể ĐÃ ĐĂNG KÝ")
        rows = res["results"]
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, width="stretch", height=600)
        else:
            st.info("Không tìm thấy biến thể nào đã đăng ký.")

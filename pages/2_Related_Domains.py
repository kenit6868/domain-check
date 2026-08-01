"""pages/2_Related_Domains.py — Tìm domain "anh em" cùng chiến dịch phishing.

Gọi thẳng phishing_toolkit.crtsh_related(), tương đương CLI `related`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

import phishing_toolkit as pt

st.set_page_config(page_title="Related Domains", page_icon="🕸️", layout="wide")
st.title("🕸️ Related Domains (crt.sh)")
st.caption("Tìm các domain khác có chứng chỉ SSL chứa từ khóa qua Certificate Transparency log")

with st.form("related_form"):
    keyword = st.text_input("Từ khóa / tên thương hiệu", placeholder="vd: openai")
    go = st.form_submit_button("Tìm kiếm", type="primary")

if go:
    if not keyword.strip():
        st.warning("Nhập từ khóa trước khi tìm.")
        st.stop()

    with st.spinner(f"Đang tra crt.sh cho '{keyword}'..."):
        res = pt.crtsh_related(keyword)

    if "error" in res:
        st.error(res["error"])
        st.caption("crt.sh là dịch vụ public, đôi khi trả lỗi 502 tạm thời — thử lại sau vài giây.")
    else:
        st.success(f"Tìm thấy {res['count']} domain có chứng chỉ chứa '{keyword}'")
        df = pd.DataFrame({"domain": res["domains"]})
        st.dataframe(df, width="stretch", height=600)

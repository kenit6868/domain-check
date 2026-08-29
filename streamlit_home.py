"""streamlit_home.py — Nội dung trang chủ dashboard."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import streamlit as st

import phishing_toolkit as pt
from cloaking_ui import render_cloaking_result

st.title("🎣 Phishing Takedown Toolkit")
st.caption("Công cụ nội bộ phát hiện, xác minh và báo cáo domain phishing giả mạo thương hiệu")

st.info(
    "Kết quả tool chỉ là tín hiệu kỹ thuật, chưa phải xác nhận phishing. "
    "Luôn xác minh thủ công (screenshot, nội dung trang) trước khi báo cáo — "
    "xem `plan_phishing_takedown.md` bước 2."
)

st.subheader("Kiểm tra nhanh 1 domain")
with st.form("quick_check_form"):
    target = st.text_input("Domain hoặc URL", placeholder="vd: chass.ru.com")
    submit_vt = st.checkbox(
        "Submit lên VirusTotal nếu chưa có dữ liệu",
        help="Tương ứng cờ --submit của CLI.",
    )
    go = st.form_submit_button("Kiểm tra", type="primary")

if go:
    if not target.strip():
        st.warning("Nhập domain trước khi kiểm tra.")
        st.stop()
    cfg = pt.load_config()
    with st.spinner(f"Đang kiểm tra {target}..."):
        result = pt.run_check(target, submit_vt, cfg)

    st.success(f"Đã kiểm tra xong: {result['domain']} — xem đầy đủ ở trang **Check Domain**.")

    rep = result["reputation"]
    rep_fn = {"flagged": st.error, "suspicious": st.warning, "unknown": st.info}.get(rep["verdict"], st.success)
    rep_fn(f"**Uy tín:** {rep['label']}" + ("".join(f"\n- {r}" for r in rep["reasons"])))
    render_cloaking_result(result.get("cloaking", {}), compact=True)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("VT Malicious", result["virustotal"].get("malicious", "N/A"))
    col2.metric("VT Suspicious", result["virustotal"].get("suspicious", "N/A"))
    col3.metric("Cloudflare", "Có" if result["cloudflare"] else "Không")
    col4.metric("Registrar", result["whois"].get("registrar") or "N/A")
    if result["log_error"]:
        st.error(f"Lỗi khi ghi log: {result['log_error']}")

st.divider()

st.subheader("10 case gần nhất")
if os.path.exists(pt.LOG_PATH):
    df = pd.read_csv(pt.LOG_PATH, on_bad_lines="skip")
    st.dataframe(df.tail(10).iloc[::-1], width="stretch")
else:
    st.info("Chưa có case nào được ghi log. Chạy kiểm tra 1 domain ở trên hoặc vào trang Check Domain.")

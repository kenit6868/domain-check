"""Shared Streamlit links to Vietnamese community phishing report forms."""

from __future__ import annotations

import streamlit as st


COMMUNITY_REPORT_FORMS = (
    ("Chống Lừa Đảo", "https://chongluadao.vn/report/reportphishing"),
    ("Cốc Cốc Safe", "https://safe.coccoc.com/"),
)


def render_community_report_buttons() -> None:
    """Render explicit external links; opening a form never submits a report."""
    st.caption("Form báo cáo cộng đồng Việt Nam — mở ở tab mới, không tự gửi:")
    with st.container(horizontal=True, gap="small"):
        for label, url in COMMUNITY_REPORT_FORMS:
            st.link_button(label, url, icon=":material/report:")

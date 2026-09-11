"""Shared Streamlit links to Vietnamese community phishing report forms."""

from __future__ import annotations

import streamlit as st


COMMUNITY_REPORT_FORMS = (
    ("Chống Lừa Đảo", "https://chongluadao.vn/report/reportphishing"),
    ("Cốc Cốc Safe", "https://safe.coccoc.com/"),
)


def render_community_report_buttons(
    *, target_url: str = "", draft: str = "", cfg: dict | None = None,
    key_scope: str = "community",
) -> None:
    """Render manual links or fill-only extension actions for community forms."""
    auto_fill = bool(target_url and cfg is not None)
    st.caption("Form báo cáo cộng đồng Việt Nam — tự điền nếu có URL, không tự gửi:")
    with st.container(horizontal=True, gap="small"):
        for index, (label, url) in enumerate(COMMUNITY_REPORT_FORMS):
            if not auto_fill:
                st.link_button(label, url, icon=":material/report:")
                continue
            provider = "chongluadao" if "chongluadao.vn" in url else "coccoc_safe"
            report_type = "Phishing" if provider == "chongluadao" else "Trang web lừa đảo"
            if st.button(
                f"Mở & tự điền {label}",
                key=f"{key_scope}_{provider}_{index}",
                icon=":material/report:",
            ):
                import cloudflare_form_worker as form_worker

                result = form_worker.open_profile_form(
                    provider, url, target_url, draft, cfg,
                    report_type=report_type,
                )
                if "error" in result:
                    st.error(result["error"])
                else:
                    st.success(f"Đã mở {label} và gửi task tự điền.")

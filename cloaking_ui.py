"""Shared Streamlit rendering for cloaking detector results."""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st


VERDICT_LABELS = {
    "LIKELY": "Có khả năng cloaking cao",
    "POSSIBLE": "Có dấu hiệu cloaking — cần xác minh",
    "INCONCLUSIVE": "Chưa đủ dữ liệu — cần xác minh",
    "NO_SIGNAL": "Chưa phát hiện dấu hiệu cloaking",
}
COMPACT_VERDICT_LABELS = {
    "LIKELY": "Khả năng cao",
    "POSSIBLE": "Nghi ngờ",
    "INCONCLUSIVE": "Chưa đủ dữ liệu",
    "NO_SIGNAL": "Không phát hiện",
}
CONTENT_LABELS = {
    "GAMBLING_EXPOSED": "Phát hiện nội dung cờ bạc công khai trên nhiều profile",
    "PROFILE_DEPENDENT": "Nội dung nhạy cảm chỉ xuất hiện trên một số profile",
    "NO_SIGNAL": "Chưa phát hiện nội dung nhạy cảm",
}


def verdict_label(result: dict) -> str:
    verdict = (result or {}).get("verdict", "INCONCLUSIVE")
    return VERDICT_LABELS.get(verdict, verdict)


def cloaking_details_label(result: dict) -> str:
    """Build the compact verdict summary shown on a collapsed details panel."""
    result = result or {}
    verdict = result.get("verdict", "INCONCLUSIVE")
    summary = COMPACT_VERDICT_LABELS.get(verdict, verdict_label(result))
    score = int(result.get("score", 0) or 0)
    return f"Chi tiết kiểm tra cloaking · {summary} · {score}/100 điểm"


def render_cloaking_result(result: dict, *, compact: bool = False) -> None:
    """Render a redacted summary; raw response text is never sent to frontend."""
    result = result or {}
    verdict = result.get("verdict", "INCONCLUSIVE")
    score = int(result.get("score", 0) or 0)
    message = f"**Cloaking:** {verdict_label(result)} · điểm {score}/100"
    renderer = {
        "LIKELY": st.error,
        "POSSIBLE": st.warning,
        "INCONCLUSIVE": st.warning,
        "NO_SIGNAL": st.success,
    }.get(verdict, st.info)
    renderer(message)
    content = result.get("content") or {}
    content_verdict = content.get("verdict")
    if content_verdict == "GAMBLING_EXPOSED":
        st.error(f"**Nội dung:** {CONTENT_LABELS[content_verdict]}")
    elif content_verdict == "PROFILE_DEPENDENT":
        st.warning(f"**Nội dung:** {CONTENT_LABELS[content_verdict]}")
    if result.get("error"):
        st.caption(f"Detector: {result['error']}")
    site_state = result.get("site_state") or {}
    if site_state.get("all_profiles_terminal"):
        st.info(
            "Các profile trình duyệt đều đang hiển thị trang cảnh báo/chặn hoặc lỗi "
            "không thể truy cập. Trạng thái này được bỏ khỏi phép tính cloaking."
        )
    if compact:
        signals = result.get("signals") or []
        if signals:
            st.caption("; ".join(str(item.get("detail", "")) for item in signals[:3]))
        coverage = result.get("coverage") or {}
        if coverage.get("multi_vantage_recommended"):
            st.caption("Server khai báo thay đổi theo quốc gia/IP; nên kiểm tra thêm vantage mạng.")
        return

    rows = []
    for profile in (result.get("profiles") or {}).values():
        rows.append({
            "Hồ sơ": profile.get("label") or profile.get("name"),
            "Vantage": profile.get("vantage") or "local",
            "HTTP": profile.get("status_code") if not profile.get("error") else "—",
            "Tiêu đề": profile.get("title") or "—",
            "Dung lượng": profile.get("body_bytes", 0),
            "URL cuối": profile.get("final_url") or "—",
            "Từ khóa": ", ".join(profile.get("keyword_hits") or []) or "—",
            "Lỗi": profile.get("error") or "—",
        })
    if rows:
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            column_config={
                "Dung lượng": st.column_config.NumberColumn(format="%d bytes"),
                "URL cuối": st.column_config.LinkColumn(),
            },
        )
    signals = result.get("signals") or []
    if signals:
        st.markdown("**Tín hiệu quan sát được:**")
        for signal in signals:
            st.write(f"- {signal.get('detail', '')}")
    path_probes = result.get("path_probes") or []
    if path_probes:
        st.markdown("**Khám phá đường dẫn (không cộng điểm cloaking):**")
        st.dataframe(
            pd.DataFrame([{
                "Đường dẫn": item.get("variant_url"),
                "Kết quả": item.get("status"),
                "Từ khóa bổ sung": ", ".join(item.get("additional_terms") or []) or "—",
            } for item in path_probes]),
            hide_index=True,
            width="stretch",
            column_config={"Đường dẫn": st.column_config.LinkColumn()},
        )
    coverage = result.get("coverage") or {}
    if coverage.get("multi_vantage_recommended"):
        attempted = ", ".join(coverage.get("vantages_attempted") or []) or "chưa cấu hình"
        available = ", ".join(coverage.get("vantages_available") or []) or "không có"
        st.warning(
            "Response khai báo phụ thuộc quốc gia/IP. Kết quả từ một mạng chưa đủ phủ; "
            f"vantage đã thử: {attempted}; vantage có dữ liệu: {available}."
        )
    evidence_path = result.get("evidence_path")
    if evidence_path and os.path.isfile(evidence_path):
        with open(evidence_path, "rb") as file:
            st.download_button(
                "Tải manifest bằng chứng cloaking",
                data=file.read(),
                file_name=os.path.basename(evidence_path),
                mime="application/json",
                icon=":material/download:",
            )
    playwright = result.get("playwright") or {}
    if playwright:
        if playwright.get("available"):
            st.caption(
                f"Playwright: {playwright.get('verdict', 'INCONCLUSIVE')} · "
                f"{playwright.get('score', 0)} điểm"
            )
        elif playwright.get("error"):
            st.caption(f"Playwright chưa chạy được: {playwright['error']}")
    screenshots = playwright.get("screenshots") or []
    if screenshots:
        st.markdown("**Ảnh xác minh bằng trình duyệt:**")
        with st.container(horizontal=True, gap="small"):
            for screenshot in screenshots:
                path = screenshot.get("path", "")
                if path and os.path.isfile(path):
                    st.image(
                        path,
                        caption=screenshot.get("label") or os.path.basename(path),
                        width=160,
                    )
    operator = result.get("operator_evidence") or {}
    operator_screenshots = operator.get("screenshots") or []
    if operator_screenshots:
        st.markdown("**Ảnh do người vận hành cung cấp:**")
        for screenshot in operator_screenshots:
            path = screenshot.get("path", "")
            if path and os.path.isfile(path):
                st.image(path, caption=os.path.basename(path), width="stretch")


def render_cloaking_details(result: dict) -> None:
    """Render one collapsed panel; keep the verdict visible in its label."""
    result = result or {}
    with st.expander(cloaking_details_label(result), expanded=False):
        render_cloaking_result(result)

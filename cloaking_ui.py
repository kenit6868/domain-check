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


def verdict_label(result: dict) -> str:
    verdict = (result or {}).get("verdict", "INCONCLUSIVE")
    return VERDICT_LABELS.get(verdict, verdict)


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
    if result.get("error"):
        st.caption(f"Detector: {result['error']}")
    if compact:
        signals = result.get("signals") or []
        if signals:
            st.caption("; ".join(str(item.get("detail", "")) for item in signals[:3]))
        return

    rows = []
    for profile in (result.get("profiles") or {}).values():
        rows.append({
            "Hồ sơ": profile.get("label") or profile.get("name"),
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
        for screenshot in screenshots:
            path = screenshot.get("path", "")
            if path and os.path.isfile(path):
                st.image(path, caption=screenshot.get("label") or os.path.basename(path), width="stretch")

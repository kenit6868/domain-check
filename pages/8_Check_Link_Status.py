"""Check whether a list of URLs is live and show its HTTP redirect chain."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import streamlit as st

from domain_utils import extract_branded_domains, extract_domains_from_text
from link_status import check_links, format_result_note, should_show_result


requests.packages.urllib3.disable_warnings()

st.set_page_config(page_title="Check Link Status", page_icon="🔗", layout="wide")
st.title("🔗 Check danh sách link")
st.caption(
    "Dán toàn bộ danh sách một lần, hệ thống check đồng thời tất cả link và trả kết quả "
    "theo từng hậu đài. Đây không phải worker, không chia batch và không tạo job nền."
)

with st.expander("🧹 Lọc link từ nội dung thô", expanded=True):
    st.caption(
        "Dán nguyên nội dung có tên hậu đài, ghi chú và URL. Công cụ giữ nguyên tên "
        "hậu đài cùng link đầy đủ, loại link trùng và đưa danh sách sạch xuống ô check."
    )
    with st.form("link_filter_form"):
        filter_input = st.text_area(
            "Nội dung cần lọc",
            height=220,
            placeholder="789win\nhttps://example.com/vi-vn/ (top3)\nGhi chú khác...",
        )
        filter_clicked = st.form_submit_button("Lọc link", type="primary")
    if filter_clicked:
        filtered_items = extract_branded_domains(filter_input)
        if filtered_items:
            filtered_lines = []
            previous_brand = None
            for item in filtered_items:
                brand = item["brand"]
                if brand and brand != previous_brand:
                    if filtered_lines:
                        filtered_lines.append("")
                    filtered_lines.append(brand)
                filtered_lines.append(item["target"])
                previous_brand = brand
            filtered_text = "\n".join(filtered_lines)
            st.session_state["link_status_input"] = filtered_text
            st.success(f"Đã lọc được {len(filtered_items)} link và đưa xuống ô check.")
            st.code(filtered_text, language=None)
        elif extract_domains_from_text(filter_input):
            # Fallback phòng trường hợp nội dung chỉ có URL, không có tên hậu đài.
            filtered_text = "\n".join(extract_domains_from_text(filter_input))
            st.session_state["link_status_input"] = filtered_text
            st.success("Đã lọc link và đưa xuống ô check.")
            st.code(filtered_text, language=None)
        else:
            st.warning("Không tìm thấy link/domain hợp lệ trong nội dung.")

with st.form("link_status_form"):
    raw_input = st.text_area(
        "Danh sách link",
        height=260,
        key="link_status_input",
        placeholder=(
            "789win\n"
            "https://example.com/vi-vn/ (top3)\n"
            "domain-khac.example/login"
        ),
        help=(
            "Input giống Domain Worker: có thể dán nội dung thô, ghi chú hoặc mỗi dòng một link. "
            "Tool tự lọc link hợp lệ, giữ nguyên path/query và loại link trùng."
        ),
    )
    submitted = st.form_submit_button("▶ Check toàn bộ danh sách", type="primary")

if submitted:
    branded_targets = extract_branded_domains(raw_input)
    targets = [item["target"] for item in branded_targets]
    if not targets:
        st.warning("Không tìm thấy link/domain hợp lệ trong nội dung.")
        st.stop()

    with st.spinner(f"Đang check đồng thời toàn bộ {len(targets)} link..."):
        results = check_links(targets, timeout=10.0, max_workers=20)

    shown_results = [
        (item, source)
        for item, source in zip(results, branded_targets)
        if should_show_result(item)
    ]
    die_count = sum(item["status"] == "DIE" for item, _ in shown_results)
    blocked_count = sum(item["status"] == "BLOCKED" for item, _ in shown_results)
    unreachable_count = sum(item["status"] == "UNREACHABLE" for item, _ in shown_results)
    redirected_count = sum(
        any(hop["status"] in {301, 302} for hop in item["redirect_chain"])
        for item, _ in shown_results
    )
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Đã check", len(results))
    m2.metric("DIE", die_count)
    m3.metric("BLOCKED", blocked_count)
    m4.metric("UNREACHABLE", unreachable_count)
    m5.metric("Có 301/302", redirected_count)

    result_groups: dict[str, list[tuple[str, str]]] = {}
    group_order = []
    for result, source in shown_results:
        brand = source["brand"]
        if brand not in result_groups:
            result_groups[brand] = []
            group_order.append(brand)
        result_groups[brand].append((source["target"], format_result_note(result)))

    st.subheader("Kết quả DIE / BLOCKED / UNREACHABLE / 301-302")
    st.caption(
        "Trạng thái và toàn bộ chuỗi HTTP nằm ngay trên cùng dòng với link."
    )
    if result_groups:
        output_lines = []
        for brand in group_order:
            if brand:
                output_lines.append(brand)
            output_lines.extend(
                f"{target} - {note}"
                for target, note in result_groups[brand]
            )
            output_lines.append("")
        dead_text = "\n".join(output_lines).rstrip()
        st.code(dead_text, language=None)
        st.download_button(
            "⬇ Tải danh sách domain die",
            dead_text.encode("utf-8"),
            file_name="filtered_link_results.txt",
            mime="text/plain",
        )
    else:
        st.success("Không có link phù hợp điều kiện xuất.")

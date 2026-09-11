"""Cloudflare phishing-form batch preparation and visible Chrome operation."""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

import cloudflare_form_worker as cfw
import phishing_toolkit as pt


st.title("Cloudflare Form Worker")
st.caption(
    "Lọc URL dùng Cloudflare, xem trước nội dung và mở Chrome hiển thị để tự động điền "
    "form phishing chính thức. CAPTCHA luôn do người dùng xử lý."
)

with st.expander("Thiết lập Chrome profile cá nhân (bắt buộc lần đầu)", expanded=True):
    st.markdown(
        "1. Trên đúng profile Chrome bạn muốn dùng, mở `chrome://extensions`.\n"
        "2. Bật **Developer mode** → **Load unpacked**.\n"
        "3. Chọn đúng thư mục extension bên dưới. Sau khi cài, không cần đóng Chrome.\n"
        "4. Nếu đã cài từ trước, bấm **Reload** và kiểm tra extension đang ở bản **1.0.5**.\n"
        "5. Trước khi chạy, hãy click/focus một cửa sổ của profile đó để Chrome dùng nó làm profile hiện tại."
    )
    st.code(str(cfw.extension_directory()), language=None)
    extension_ready = st.checkbox(
        "Tôi đã cài/bật extension PhishingTool Cloudflare Form Worker trên profile Chrome hiện tại.",
        key="cfw_extension_ready",
    )


def _parse(raw: str) -> tuple[list[str], list[str]]:
    valid, invalid, seen = [], [], set()
    for value in re.split(r"[\n,;]+", raw):
        value = value.strip()
        if not value:
            continue
        try:
            normalized = cfw.normalize_target(value)
        except ValueError:
            invalid.append(value)
            continue
        if normalized not in seen:
            seen.add(normalized)
            valid.append(normalized)
    return valid, invalid


cfg = pt.load_config()
with st.form("cfw_prepare_form"):
    raw = st.text_area(
        "Danh sách URL/domain", height=150,
        placeholder="https://example.com/login\nexample.net",
        help="Mỗi dòng một URL. Nên giữ đúng path đang chứa nội dung phishing.",
    )
    prepare = st.form_submit_button("Kiểm tra & lọc Cloudflare", type="primary")

if prepare:
    values, invalid = _parse(raw)
    if invalid:
        st.warning("Không hợp lệ: " + ", ".join(invalid))
    if not values:
        st.error("Chưa có URL hợp lệ.")
    else:
        progress = st.progress(0, text="Đang kiểm tra Cloudflare...")
        for index, value in enumerate(values, start=1):
            cfw.prepare_urls([value], cfg)
            progress.progress(index / len(values), text=f"Đã kiểm tra {index}/{len(values)}")
        progress.empty()
        st.rerun()

records = cfw.today_records()
cloudflare_records = [item for item in records if item.get("cloudflare")]
excluded = [item for item in records if not item.get("cloudflare")]
if not records:
    st.info("Nhập danh sách và bấm kiểm tra để tạo hàng chờ trong ngày.")
    st.stop()

if excluded:
    with st.expander(f"Không phát hiện Cloudflare ({len(excluded)})"):
        st.dataframe(pd.DataFrame([
            {"URL": x.get("target_url"), "Lý do": x.get("last_error")} for x in excluded
        ]), hide_index=True, width="stretch")

selectable = [item for item in cloudflare_records if item.get("state") != "SUBMITTED"]
submitted = [item for item in cloudflare_records if item.get("state") == "SUBMITTED"]
st.subheader("URL Cloudflare có thể xử lý")
selected_ids = []
if not selectable:
    st.success("Không còn URL Cloudflare nào đang chờ trong ngày.")
else:
    frame = pd.DataFrame([{
        "id": item["id"], "URL": item["target_url"], "Trạng thái": item.get("state"),
        "Số lần thử": item.get("attempts", 0),
        "Kết quả/lỗi": item.get("result") or item.get("last_error") or "",
    } for item in selectable])
    event = st.dataframe(
        frame.drop(columns=["id"]), hide_index=True, width="stretch",
        on_select="rerun", selection_mode="multi-row", key="cfw_table",
    )
    rows = list(event.selection.rows) if event and event.selection else []
    selected_ids = [frame.iloc[index]["id"] for index in rows if index < len(frame)]
    st.caption(f"Đã chọn {len(selected_ids)}/{len(selectable)} URL.")

selected = [item for item in selectable if item.get("id") in selected_ids]
if selected:
    st.subheader("Preview trước khi chạy")
    for item in selected:
        with st.expander(f"{item['target_url']} · {item.get('state')}", expanded=len(selected) == 1):
            st.link_button("Mở form Cloudflare", cfw.FORM_URL)
            st.code(item.get("draft") or "", language=None)

mode_label = st.radio(
    "Chế độ", ["Chỉ điền", "Điền và submit sau xác nhận"], horizontal=True,
    help="Chỉ điền giữ tab mở để bạn tự kiểm tra. Submit chỉ chạy sau xác nhận.",
)
mode = "fill_only" if mode_label == "Chỉ điền" else "submit"
delay = st.number_input("Delay giữa các URL (giây)", 0, 120, 8, 1)
confirmed = st.checkbox(
    "Tôi đã kiểm tra URL và draft; tôi cho phép submit các form đã chọn.",
    disabled=mode != "submit",
)

worker = cfw.browser_worker()
status = worker.snapshot()
st.caption(status["status"])
if st.button(
    "Mở Chrome và chạy danh sách đã chọn", type="primary",
    disabled=(not selected or not extension_ready or status["busy"]
              or (mode == "submit" and not confirmed)),
):
    runtime_records = []
    for item in selected:
        runtime_item = dict(item)
        runtime_item["_contact_name"] = cfg.get("contact_name", "")
        runtime_item["_contact_email"] = cfg.get("contact_email", "")
        runtime_item["_brand_name"] = cfg.get("brand_name", "")
        runtime_records.append(runtime_item)
    result = worker.start(runtime_records, mode=mode, delay_seconds=int(delay), confirmed=confirmed)
    if result.get("error"):
        st.error(result["error"])
    else:
        st.success(f"Đã bắt đầu xử lý {result['count']} URL. Chrome sẽ mở ở chế độ hiển thị.")
        st.rerun()

if status["busy"]:
    st.info("Worker đang chạy. Có thể reload page; tiến độ từng URL vẫn được lưu.")
if submitted:
    with st.expander(f"Đã submit thành công hôm nay ({len(submitted)})"):
        st.dataframe(pd.DataFrame([{
            "URL": x.get("target_url"), "Kết quả": x.get("result"), "Cập nhật": x.get("updated_at")
        } for x in submitted]), hide_index=True, width="stretch")

st.caption(
    "Form được mở bằng trình duyệt mặc định và được extension chạy ngay trong profile Chrome hiện tại; "
    "không tạo profile Playwright. Ledger chỉ lưu URL, draft và trạng thái, không lưu CAPTCHA/cookie."
)

"""Cloudflare batch report page with API-first delivery and form fallback."""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

import cloudflare_abuse_api as cf_api
import cloudflare_form_worker as cfw
import phishing_toolkit as pt


st.title("Cloudflare Worker")
st.caption(
    "Lọc URL đang dùng Cloudflare, preview nội dung và gửi report phishing qua "
    "Cloudflare Abuse Reports API. Extension Chrome được giữ làm phương án dự phòng."
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
api_configured = bool(cfg.get("cloudflare_api_token") and cfg.get("cloudflare_account_id"))

with st.container(border=True):
    st.subheader("Kết nối Cloudflare API")
    if api_configured:
        st.success("Đã tìm thấy API Token và Account ID trong config.ini.")
    else:
        st.warning(
            "Chưa cấu hình [cloudflare] api_token và account_id. "
            "Bạn vẫn có thể dùng extension ở chế độ dự phòng."
        )
    if st.button(
        "Kiểm tra kết nối API",
        icon=":material/lan:",
        disabled=not api_configured,
        key="cfw_verify_api",
    ):
        try:
            checked = cf_api.verify_access(cfg)
            st.session_state["cfw_api_verified"] = True
            st.success(
                f"Token {checked['token_status']}; API truy cập được. "
                f"Cloudflare đang trả về {checked['report_count']} report."
            )
        except cf_api.CloudflareAbuseApiError as exc:
            st.session_state["cfw_api_verified"] = False
            st.error(str(exc))
        except Exception as exc:
            st.session_state["cfw_api_verified"] = False
            st.error(f"Không thể kiểm tra Cloudflare API: {exc}")

with st.expander("Extension Chrome dự phòng"):
    st.markdown(
        "Dùng khi API không khả dụng. Mở trang quản lý extension của Chrome, bật "
        "**Developer mode**, chọn **Load unpacked** và nạp thư mục dưới đây. "
        "Nếu đã cài, bấm **Reload** để nhận phiên bản mới."
    )
    st.code(str(cfw.extension_directory()), language=None)
    extension_ready = st.checkbox(
        "Tôi đã cài và bật Web Form Assistant trên Chrome profile hiện tại.",
        key="cfw_extension_ready",
    )

with st.form("cfw_prepare_form"):
    raw = st.text_area(
        "Danh sách URL/domain",
        height=150,
        placeholder="https://example.com/login\nexample.net",
        help="Mỗi dòng một URL. Nên giữ đúng path chứa nội dung phishing.",
    )
    prepare = st.form_submit_button(
        "Kiểm tra và lọc Cloudflare",
        type="primary",
        icon=":material/filter_alt:",
    )

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
    st.info("Nhập danh sách và bấm kiểm tra để tạo danh sách Cloudflare trong ngày.")
    st.stop()

if excluded:
    with st.expander(f"Không phát hiện Cloudflare ({len(excluded)})"):
        st.dataframe(
            pd.DataFrame([
                {"URL": item.get("target_url"), "Lý do": item.get("last_error")}
                for item in excluded
            ]),
            hide_index=True,
            width="stretch",
        )

selectable = [item for item in cloudflare_records if item.get("state") != "SUBMITTED"]
submitted = [item for item in cloudflare_records if item.get("state") == "SUBMITTED"]
st.subheader("URL Cloudflare")
selected_ids = []
if not selectable:
    st.success("Không còn URL Cloudflare nào đang chờ trong ngày.")
else:
    frame = pd.DataFrame([
        {
            "id": item["id"],
            "URL": item["target_url"],
            "Trạng thái": item.get("state"),
            "Kênh": item.get("channel") or "Chưa gửi",
            "Số lần thử": item.get("attempts", 0),
            "Kết quả/lỗi": item.get("result") or item.get("last_error") or "",
        }
        for item in selectable
    ])
    event = st.dataframe(
        frame.drop(columns=["id"]),
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="multi-row",
        key="cfw_table",
    )
    rows = list(event.selection.rows) if event and event.selection else []
    selected_ids = [frame.iloc[index]["id"] for index in rows if index < len(frame)]
    st.caption(f"Đã chọn {len(selected_ids)}/{len(selectable)} URL.")

selected = [item for item in selectable if item.get("id") in selected_ids]
if selected:
    st.subheader("Preview report")
    for item in selected:
        with st.expander(
            f"{item['target_url']} · {item.get('state')}",
            expanded=len(selected) == 1,
        ):
            st.code(item.get("draft") or "", language=None)
            try:
                payload = cf_api.build_phishing_payload(
                    item["target_url"], item.get("draft") or "", cfg
                )
                st.json(payload)
            except cf_api.CloudflareAbuseApiError as exc:
                st.error(str(exc))

channel = st.segmented_control(
    "Kênh xử lý",
    ["Cloudflare API", "Extension dự phòng"],
    default="Cloudflare API",
    key="cfw_channel",
)

if channel == "Cloudflare API":
    confirmed = st.checkbox(
        "Tôi đã kiểm tra URL và nội dung; tôi xác nhận gửi các report đã chọn qua Cloudflare API.",
        key="cfw_api_confirmed",
    )
    if st.button(
        "Gửi danh sách đã chọn qua API",
        type="primary",
        icon=":material/send:",
        disabled=not (selected and api_configured and confirmed),
        key="cfw_submit_api",
    ):
        progress = st.progress(0, text="Đang gửi Cloudflare API...")
        results = []
        for index, item in enumerate(selected, start=1):
            results.extend(cfw.submit_api_records([item], cfg))
            progress.progress(index / len(selected), text=f"Đã xử lý {index}/{len(selected)}")
        progress.empty()
        succeeded = sum(bool(item.get("ok")) for item in results)
        failed = len(results) - succeeded
        if succeeded:
            st.success(f"Đã gửi thành công {succeeded} report.")
        if failed:
            st.error(f"Có {failed} report gửi thất bại; trạng thái đã được lưu để retry.")
        st.rerun()
else:
    mode_label = st.segmented_control(
        "Chế độ extension",
        ["Chỉ điền", "Điền và submit sau xác nhận"],
        default="Chỉ điền",
        key="cfw_extension_mode",
    )
    mode = "fill_only" if mode_label == "Chỉ điền" else "submit"
    delay = st.number_input("Delay giữa các URL (giây)", 0, 120, 8, 1)
    confirmed = st.checkbox(
        "Tôi cho phép extension submit các form đã chọn sau khi CAPTCHA được xác nhận.",
        disabled=mode != "submit",
        key="cfw_extension_confirmed",
    )
    worker = cfw.browser_worker()
    status = worker.snapshot()
    st.caption(status["status"])
    if st.button(
        "Mở Chrome và chạy danh sách",
        type="primary",
        icon=":material/open_in_browser:",
        disabled=(
            not selected or not extension_ready or status["busy"]
            or (mode == "submit" and not confirmed)
        ),
        key="cfw_run_extension",
    ):
        runtime_records = []
        for item in selected:
            runtime_item = dict(item)
            runtime_item["_contact_name"] = cfg.get("contact_name", "")
            runtime_item["_contact_email"] = cfg.get("contact_email", "")
            runtime_item["_brand_name"] = cfg.get("brand_name", "")
            runtime_records.append(runtime_item)
        result = worker.start(
            runtime_records,
            mode=mode,
            delay_seconds=int(delay),
            confirmed=confirmed,
        )
        if result.get("error"):
            st.error(result["error"])
        else:
            st.success(f"Đã bắt đầu xử lý {result['count']} URL trên Chrome.")
            st.rerun()
    if status["busy"]:
        st.info("Extension Worker đang chạy; checkpoint từng URL vẫn được lưu.")

if submitted:
    with st.expander(f"Đã gửi thành công hôm nay ({len(submitted)})"):
        st.dataframe(
            pd.DataFrame([
                {
                    "URL": item.get("target_url"),
                    "Kênh": item.get("channel") or item.get("mode"),
                    "Report ID": item.get("report_id") or "",
                    "Kết quả": item.get("result"),
                    "Cập nhật": item.get("updated_at"),
                }
                for item in submitted
            ]),
            hide_index=True,
            width="stretch",
        )

st.caption(
    "Ledger chỉ lưu URL, draft, kênh, Report ID và trạng thái. "
    "API Token, CAPTCHA, cookie và nội dung trình duyệt không được ghi vào ledger."
)

"""Persistent Cookie-only Cloudflare Dashboard batch worker."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

import cloudflare_form_worker as cfw
import phishing_toolkit as pt


st.set_page_config(
    page_title="Cloudflare Worker",
    page_icon=":material/cloud:",
    layout="wide",
)
st.title("Cloudflare Worker")
st.caption(
    "Kiểm tra URL dùng Cloudflare, lưu danh sách trong ngày và gửi tuần tự qua "
    "Dashboard bằng Cookie do người vận hành cung cấp."
)


def _parse(raw: str) -> tuple[list[str], list[str], list[str]]:
    return cfw.parse_target_input(raw)


def _state_label(value: str) -> str:
    return {
        "READY": "🟢 Sẵn sàng", "QUEUED": "⏳ Chờ gửi",
        "SUBMITTING": "▶ Đang gửi", "SUBMITTED": "✅ Đã gửi",
        "ALREADY_SUBMITTED": "✅ Đã gửi gần đây", "UNKNOWN": "❔ Chưa rõ kết quả",
        "WAITING_FOR_SESSION": "🔑 Chờ Cookie mới", "PAUSED": "⏸ Đã tạm dừng",
        "FAILED": "❌ Lỗi — có thể thử lại", "NEEDS_REVIEW": "⚠ Cần rà soát",
        "NOT_CLOUDFLARE": "➖ Không dùng Cloudflare",
    }.get(str(value or ""), str(value or ""))


def _save_input() -> None:
    cfw.save_daily_input(st.session_state.get("cfw_domain_lines", ""))


worker = cfw.session_batch_worker()
initial_status = worker.snapshot()
st.session_state.setdefault("cfw_domain_lines", cfw.load_daily_input())
st.session_state.setdefault("cfw_invalid", [])
st.session_state.setdefault("cfw_duplicates", [])
st.session_state.setdefault("cfw_cookie_value", "")
st.session_state.setdefault("cfw_cookie_visible", False)
if st.session_state.pop("cfw_clear_cookie_on_rerun", False):
    st.session_state["cfw_cookie_value"] = ""

cfg = pt.load_config()

with st.container(border=True):
    st.subheader("Kiểm tra URL")
    raw = st.text_area(
        "Danh sách domain/URL", height=180,
        placeholder="https://example.com/login\nexample.net",
        help="Mỗi dòng một URL. Danh sách được lưu cục bộ theo ngày.",
        key="cfw_domain_lines", on_change=_save_input,
        disabled=bool(initial_status.get("busy")),
    )
    prepare = st.button(
        "Kiểm tra Cloudflare", type="primary", icon=":material/domain_verification:",
        disabled=bool(initial_status.get("busy")), key="cfw_prepare_batch",
    )

if prepare:
    cfw.save_daily_input(raw)
    values, invalid, duplicates = _parse(raw)
    st.session_state["cfw_invalid"] = invalid
    st.session_state["cfw_duplicates"] = duplicates
    if not values:
        st.warning("Không tìm thấy URL hợp lệ.")
    else:
        progress = st.progress(0, text="Đang kiểm tra Cloudflare...")
        for index, value in enumerate(values, start=1):
            cfw.prepare_urls([value], cfg)
            progress.progress(index / len(values), text=f"Đã kiểm tra {index}/{len(values)}")
        progress.empty()
        st.rerun()

if st.session_state["cfw_invalid"]:
    st.warning("URL không hợp lệ: " + ", ".join(st.session_state["cfw_invalid"]))
if st.session_state["cfw_duplicates"]:
    st.info("URL trùng trong nội dung nhập đã được bỏ qua: " + ", ".join(st.session_state["cfw_duplicates"]))


scope_urls, _, _ = _parse(st.session_state.get("cfw_domain_lines", ""))
scope_ids = {cfw.record_id(url) for url in scope_urls}
scope_order = {url: index for index, url in enumerate(scope_urls)}


def _scoped_records() -> list[dict]:
    return [item for item in cfw.today_records() if item.get("id") in scope_ids]


scoped_records = _scoped_records()
precheck_complete = bool(scope_ids) and scope_ids.issubset(
    {str(item.get("id") or "") for item in scoped_records}
)


def _result_row(item: dict) -> dict:
    return {
        "URL": item.get("target_url") or "",
        "Trạng thái": _state_label(item.get("state")),
        "Lần thử": int(item.get("attempts") or 0),
        "Cập nhật": item.get("submitted_at") or item.get("updated_at") or "",
        "Mã report": item.get("report_id") or "",
        "Kết quả/lỗi": item.get("last_error") or item.get("result") or "",
    }


@st.fragment(run_every=2 if initial_status.get("busy") else None)
def _render_tables() -> None:
    snapshot = cfw.session_batch_worker().snapshot()
    if initial_status.get("busy") and not snapshot.get("busy"):
        # The polling fragment can observe completion without rerunning the
        # surrounding page. Refresh the full app once so input/actions stop
        # using the stale busy=True value from the job's previous render.
        st.rerun(scope="app")
    scoped = _scoped_records()
    if not snapshot.get("record_ids"):
        persisted = cfw.load_job_status()
        if persisted and not snapshot.get("job_id"):
            for key in ("job_id", "state", "status", "current_id", "processed", "total"):
                snapshot[key] = persisted.get(key, snapshot.get(key))
        snapshot["record_ids"] = persisted.get("record_ids") or cfw.recover_job_record_ids()
    job_id = str(snapshot.get("job_id") or "")
    job_ids = {str(value) for value in (snapshot.get("record_ids") or []) if value}
    if job_id:
        job_ids.update(
            str(item.get("id")) for item in scoped
            if item.get("id") and item.get("job_id") == job_id
        )
    current_id = str(snapshot.get("current_id") or "")
    progress_rows = [item for item in scoped if item.get("id") in job_ids]
    progress_rows.sort(key=lambda item: scope_order.get(item.get("target_url") or "", len(scope_order)))
    actionable = [
        item for item in scoped
        if item.get("id") not in job_ids and item.get("cloudflare")
        and item.get("state") in {"READY", "FAILED", "NEEDS_REVIEW", "QUEUED"}
    ]
    actionable.sort(key=lambda item: scope_order.get(item.get("target_url") or "", len(scope_order)))
    visible_rows = [*progress_rows, *actionable]
    visible_rows.sort(key=lambda item: scope_order.get(item.get("target_url") or "", len(scope_order)))
    excluded = [
        item for item in scoped
        if item.get("id") not in job_ids and (
            not item.get("cloudflare")
            or item.get("state") in {"SUBMITTED", "ALREADY_SUBMITTED", "UNKNOWN"}
        )
    ]
    excluded.sort(key=lambda item: scope_order.get(item.get("target_url") or "", len(scope_order)))

    with st.container(border=True):
        st.subheader("Kết quả URL")
        st.caption("Một danh sách duy nhất sau precheck; trạng thái gửi được cập nhật trực tiếp trên từng URL.")
        if snapshot.get("state") == "COMPLETED" and progress_rows:
            succeeded = sum(
                item.get("state") in {"SUBMITTED", "ALREADY_SUBMITTED"}
                for item in progress_rows
            )
            failed = sum(item.get("state") == "FAILED" for item in progress_rows)
            notice = st.warning if failed else st.success
            notice(
                f"Job đã hoàn thành: **{succeeded}** URL thành công/đã gửi gần đây"
                + (f", **{failed}** URL lỗi có thể thử lại." if failed else ".")
            )
        if progress_rows and snapshot.get("total"):
            total = int(snapshot.get("total") or 0)
            processed = int(snapshot.get("processed") or 0)
            st.progress(
                processed / total if total else 0.0,
                text=f"{processed}/{total} · {snapshot.get('status') or 'Sẵn sàng'}",
            )
        current = next((item for item in visible_rows if item.get("id") == current_id), None)
        if current:
            st.info(f"▶ Đang xử lý: {current.get('target_url')}")
        if visible_rows:
            st.dataframe(
                pd.DataFrame([_result_row(item) for item in visible_rows]),
                hide_index=True, width="stretch",
            )
        else:
            st.info("Không có URL đủ điều kiện để thực hiện.")

    if excluded:
        show_excluded = st.toggle(
            f"Hiện URL bị loại ({len(excluded)})",
            value=False,
            key="cfw_show_excluded",
        )
        if show_excluded:
            with st.container(border=True):
                st.subheader("Bảng loại")
                st.caption(
                    "Kết quả bị loại ngay sau precheck: đã gửi từ trước, Cloudflare báo trùng, "
                    "không dùng Cloudflare hoặc chưa rõ kết quả. Record đã vào job không chuyển sang đây."
                )
                st.dataframe(
                    pd.DataFrame([_result_row(item) for item in excluded]),
                    hide_index=True, width="stretch",
                )

records = [item for item in _scoped_records() if item.get("cloudflare")]
ready_records = [
    item for item in records
    if item.get("state") in {"READY", "QUEUED"} and str(item.get("draft") or "").strip()
]
failed_records = [
    item for item in records
    if item.get("state") == "FAILED" and str(item.get("draft") or "").strip()
]
status = worker.snapshot()

if precheck_complete:
    with st.container(border=True):
        st.subheader("Cấu hình gửi")
        st.caption(
            f"Sẵn sàng gửi mới: **{len(ready_records)}** · Có thể thử lại: **{len(failed_records)}**. "
            "Account ID lấy từ config hoặc Cookie curr-account; Cookie chỉ giữ trong RAM."
        )
        st.toggle("Hiện Cookie", key="cfw_cookie_visible")
        st.text_input(
            "Cookie Cloudflare",
            type="default" if st.session_state["cfw_cookie_visible"] else "password",
            key="cfw_cookie_value",
            help="Chấp nhận giá trị thuần hoặc chuỗi bắt đầu bằng Cookie:.",
        )
        delay = st.number_input(
            "Thời gian chờ trước URL tiếp theo (giây)", min_value=0,
            max_value=cfw.MAX_BATCH_DELAY_SECONDS, value=30, step=1,
            key="cfw_delay_seconds",
        )
        confirmed = st.checkbox(
            "Tôi đã kiểm tra danh sách và xác nhận gửi report.",
            key="cfw_dashboard_confirmed",
        )

        with st.container(horizontal=True):
            if st.button(
                "Bắt đầu gửi", type="primary", icon=":material/send:",
                disabled=not (
                    ready_records and confirmed
                    and st.session_state.get("cfw_cookie_value") and not status.get("busy")
                ), key="cfw_start_dashboard_batch",
            ):
                try:
                    result = worker.start(
                        ready_records, cfg, cookie_value=st.session_state["cfw_cookie_value"],
                        delay_seconds=int(delay), confirmed=confirmed,
                    )
                except OSError as exc:
                    result = {"error": f"Không thể lưu trạng thái job: {exc}"}
                if result.get("error"):
                    st.error(result["error"])
                else:
                    st.session_state["cfw_clear_cookie_on_rerun"] = True
                    st.rerun()

            if st.button(
                "Thử lại URL lỗi", icon=":material/replay:",
                disabled=not (
                    failed_records and confirmed
                    and st.session_state.get("cfw_cookie_value") and not status.get("busy")
                ), key="cfw_retry_failed_batch",
            ):
                try:
                    result = worker.start(
                        failed_records, cfg, cookie_value=st.session_state["cfw_cookie_value"],
                        delay_seconds=int(delay), confirmed=confirmed,
                    )
                except OSError as exc:
                    result = {"error": f"Không thể lưu trạng thái job: {exc}"}
                if result.get("error"):
                    st.error(result["error"])
                else:
                    st.session_state["cfw_clear_cookie_on_rerun"] = True
                    st.rerun()

            if st.button(
                "Tiếp tục", icon=":material/play_arrow:",
                disabled=not status.get("busy") or status.get("state") not in {"PAUSED", "WAITING_FOR_SESSION"},
                key="cfw_resume_dashboard_batch",
            ):
                result = worker.resume(cookie_value=st.session_state.get("cfw_cookie_value", ""))
                if result.get("error"):
                    st.error(result["error"])
                else:
                    st.session_state["cfw_clear_cookie_on_rerun"] = True
                st.rerun()

            if st.button(
                "Dừng", icon=":material/stop:", disabled=not status.get("busy"),
                key="cfw_stop_dashboard_batch",
            ):
                worker.stop()
                st.rerun()

        if any(item.get("state") == "UNKNOWN" for item in records):
            st.warning(
                "Có URL chưa rõ kết quả do mất response. Các URL này không được tự retry; "
                "hãy đối chiếu với Cloudflare trước."
            )

    _render_tables()

    if st.button(
        "Xóa cache kiểm tra hôm nay", icon=":material/delete_sweep:",
        disabled=bool(status.get("busy")), key="cfw_clear_daily_cache",
        help="Chỉ xóa ô nhập và kết quả chưa gửi; lịch sử đã gửi/chưa rõ vẫn được giữ.",
    ):
        removed = cfw.clear_daily_cache()
        st.session_state["cfw_domain_lines"] = ""
        st.session_state["cfw_invalid"] = []
        st.session_state["cfw_duplicates"] = []
        st.success(f"Đã xóa {removed} kết quả kiểm tra chưa gửi. Lịch sử gửi được giữ nguyên.")
        st.rerun()

st.caption(
    "Cache và job status chỉ lưu URL, trạng thái, fingerprint, thời gian và mã report. "
    "Cookie không được ghi vào file, ledger hoặc giao diện kết quả."
)

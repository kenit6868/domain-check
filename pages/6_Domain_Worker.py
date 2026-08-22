"""Streamlit UI for the background domain reporting worker."""

import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

import phishing_toolkit as pt
from domain_worker import _successfully_reported_domain_accounts, stop_job_process
from domain_utils import extract_domains_from_text

WORKER_DIR = os.path.join(pt.BASE_DIR, "worker_jobs")


st.set_page_config(page_title="Domain Worker", page_icon="⚙️", layout="wide")
st.title("⚙️ Domain Report Worker")
st.caption("Nhận danh sách domain, xử lý theo batch và tự gửi các email report có địa chỉ người nhận hợp lệ.")
cached_sends = _successfully_reported_domain_accounts()
st.caption(
    f"💾 Cache gửi thành công: {len(cached_sends)} cặp domain/tài khoản. "
    "Worker mới tự bỏ qua các cặp đã gửi; lần gửi lỗi vẫn được thử lại."
)

with st.expander("🧹 Lọc domain từ nội dung thô", expanded=True):
    st.caption(
        "Dán nguyên nội dung có tiêu đề, ghi chú và URL. Công cụ giữ nguyên link đầy đủ "
        "(gồm đường dẫn), bỏ ghi chú như `(top3)`, loại link trùng và đưa kết quả xuống worker."
    )
    with st.form("domain_filter_form"):
        filter_input = st.text_area(
            "Nội dung cần lọc",
            height=220,
            placeholder="789win\nhttps://example.com/vi-vn/ (top3)\nGhi chú khác...",
        )
        filter_clicked = st.form_submit_button("Lọc domain", type="primary")
    if filter_clicked:
        filtered_domains = extract_domains_from_text(filter_input)
        if filtered_domains:
            filtered_text = "\n".join(filtered_domains)
            st.session_state["worker_domain_input"] = filtered_text
            st.success(f"Đã lọc được {len(filtered_domains)} link và đưa xuống danh sách worker.")
            st.code(filtered_text, language=None)
        else:
            st.warning("Không tìm thấy domain hợp lệ trong nội dung.")


def normalize_list(raw: str) -> tuple[list, list]:
    targets, invalid = [], []
    seen = set()
    for item in re.split(r"[\s,;]+", raw):
        item = item.strip()
        if not item:
            continue
        domain = pt.normalize_domain(item).lower().rstrip(".")
        if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", domain):
            invalid.append(item)
        elif item.lower() not in seen:
            seen.add(item.lower())
            targets.append(item)
    return targets, invalid


def latest_job_dir():
    if not os.path.isdir(WORKER_DIR):
        return None
    dirs = [os.path.join(WORKER_DIR, n) for n in os.listdir(WORKER_DIR) if os.path.isdir(os.path.join(WORKER_DIR, n))]
    return max(dirs, key=os.path.getmtime) if dirs else None


def load_status(job_dir):
    path = os.path.join(job_dir, "status.json") if job_dir else ""
    for _ in range(3):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            import time
            time.sleep(0.05)
    return None


with st.form("worker_form"):
    raw_domains = st.text_area(
        "Danh sách domain",
        height=220,
        key="worker_domain_input",
        placeholder="example-one.com\nexample-two.net\nhttps://example-three.org/login",
        help="Mỗi dòng một URL đầy đủ hoặc domain. Worker giữ nguyên đường dẫn URL để kiểm tra đúng trang.",
    )
    c1, c2 = st.columns(2)
    batch_size = c1.number_input("Số domain mỗi batch", min_value=1, max_value=100, value=5)
    interval_minutes = c2.number_input("Nghỉ giữa hai batch (phút)", min_value=0, max_value=1440, value=5)
    include_vncert = st.checkbox(
        "Tự gửi cả draft VNCERT",
        value=False,
        help="Chỉ bật khi toàn bộ domain trong danh sách nhắm tới nạn nhân tại Việt Nam.",
    )
    confirmed = st.checkbox(
        "Tôi xác nhận danh sách đã được kiểm tra và cho phép worker gửi email report thật tự động.",
        value=False,
    )
    start = st.form_submit_button("▶ Khởi chạy worker", type="primary")

if start:
    domains, invalid = normalize_list(raw_domains)
    existing_dir = latest_job_dir()
    existing_status = load_status(existing_dir)
    active_job = existing_status and existing_status.get("state") in ("running", "waiting")
    if active_job:
        st.error(
            f"Job `{existing_status.get('job_id')}` vẫn đang chạy. "
            "Hãy chờ hoàn tất hoặc yêu cầu dừng trước khi tạo job mới."
        )
    elif invalid:
        st.error("Domain không hợp lệ: " + ", ".join(invalid[:10]))
    elif not domains:
        st.warning("Danh sách chưa có domain hợp lệ.")
    elif not confirmed:
        st.warning("Bạn cần xác nhận trước khi cho phép gửi email tự động.")
    elif not pt.load_config().get("smtp_accounts"):
        st.error("Chưa cấu hình SMTP account trong config.ini.")
    else:
        os.makedirs(WORKER_DIR, exist_ok=True)
        job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        job_dir = os.path.join(WORKER_DIR, job_id)
        os.makedirs(job_dir)
        job_path = os.path.join(job_dir, "job.json")
        job = {
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "domains": domains,
            "batch_size": int(batch_size),
            "interval_seconds": int(interval_minutes * 60),
            "include_vncert": include_vncert,
        }
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=2)

        if getattr(sys, "frozen", False):
            command = [sys.executable, "--worker-job", job_path]
        else:
            worker_script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "domain_worker.py")
            command = [sys.executable, worker_script, job_path]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(
            command,
            cwd=pt.BASE_DIR,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        st.session_state["worker_job_dir"] = job_dir
        st.success(f"Đã khởi chạy job {job_id} với {len(domains)} domain.")

st.divider()
st.subheader("Trạng thái job gần nhất")
job_dir = st.session_state.get("worker_job_dir") or latest_job_dir()
status = load_status(job_dir)

if not job_dir:
    st.info("Chưa có worker job nào.")
elif not status:
    st.info("Worker đang khởi động. Bấm Làm mới sau vài giây.")
else:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trạng thái", status.get("state", "?"))
    c2.metric("Tiến độ", f"{status.get('processed', 0)}/{status.get('total', 0)}")
    c3.metric("Batch", f"{status.get('current_batch', 0)}/{status.get('total_batches', 0)}")
    c4.metric("Batch tiếp theo", f"{status.get('next_batch_in_seconds', 0)} giây")
    if status.get("current_domain"):
        st.info(f"Đang xử lý: `{status['current_domain']}`")
    if status.get("error"):
        st.error(status["error"])
    if status.get("results"):
        results_list = status["results"]
        # Bảng tóm tắt
        summary_rows = []
        for r in results_list:
            sent_to_list = r.get("sent_to") or []
            sent_addresses = "; ".join(
                f"{'✅' if s['ok'] else '❌'} {s['to']} (via {s['account']})"
                for s in sent_to_list
            ) if sent_to_list else ("(skip)" if r.get("skipped") else "—")
            summary_rows.append({
                "Domain": r.get("domain", ""),
                "Verdict": r.get("reputation") or ("skipped" if r.get("skipped") else "—"),
                "Drafts": r.get("drafts_total", 0),
                "Sendable": r.get("drafts_sendable", 0),
                "✅ Sent": r.get("sent_ok", 0),
                "❌ Failed": r.get("sent_failed", 0),
                "Địa chỉ đã gửi": sent_addresses,
                "Lỗi": r.get("error") or "",
            })
        results_df = pd.DataFrame(summary_rows)
        results_df.insert(0, "STT", range(1, len(results_df) + 1))
        st.dataframe(results_df, width="stretch", hide_index=True)

        # Chi tiết email từng domain
        with st.expander("📧 Chi tiết email đã gửi", expanded=False):
            for r in results_list:
                sent_to_list = r.get("sent_to") or []
                if not sent_to_list:
                    continue
                st.markdown(f"**{r['domain']}**")
                for s in sent_to_list:
                    icon = "✅" if s["ok"] else "❌"
                    err = f" — {s['error']}" if s.get("error") else ""
                    st.caption(f"{icon} `{s['to']}` via `{s['account']}` ({s['draft']}){err}")

    a, b = st.columns(2)
    if a.button("🔄 Làm mới trạng thái"):
        st.rerun()
    if b.button("⏹ Dừng hẳn tiến trình", disabled=status.get("state") in ("completed", "failed", "stopped")):
        stopped, message = stop_job_process(job_dir)
        (st.success if stopped else st.warning)(message)
        st.rerun()

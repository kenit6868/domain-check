"""Streamlit UI for the background domain reporting worker."""

import csv
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
import domain_worker
from domain_worker import stop_job_process
from domain_utils import extract_domains_from_text

WORKER_DIR = domain_worker.WORKER_DIR


def _sent_domain_accounts_today() -> dict[str, set[str]]:
    """Return successful sender accounts per domain for the current local day."""
    sent: dict[str, set[str]] = {}
    if not os.path.exists(pt.SENT_LOG_PATH):
        return sent
    local_tz = datetime.now().astimezone().tzinfo
    today = datetime.now(local_tz).date()
    try:
        with open(pt.SENT_LOG_PATH, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if str(row.get("success", "")).strip().lower() not in {"true", "1", "yes"}:
                    continue
                timestamp = str(row.get("timestamp", "")).strip()
                try:
                    sent_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    if sent_at.tzinfo is None:
                        sent_at = sent_at.replace(tzinfo=timezone.utc)
                    if sent_at.astimezone(local_tz).date() != today:
                        continue
                except (TypeError, ValueError):
                    continue
                domain = pt.normalize_domain(str(row.get("domain", "")).strip()).lower().rstrip(".")
                account = str(row.get("account", "")).strip().lower()
                if domain:
                    sent.setdefault(domain, set()).add(account)
    except (OSError, csv.Error):
        pass
    return sent


def _no_email_domains_today() -> set[str]:
    """Return domains checked without a sendable recipient on the current local day."""
    domains: set[str] = set()
    path = domain_worker.NO_EMAIL_LOG_PATH
    if not os.path.exists(path):
        return domains
    local_tz = datetime.now().astimezone().tzinfo
    today = datetime.now(local_tz).date()
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                timestamp = str(row.get("timestamp", "")).strip()
                try:
                    logged_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    if logged_at.tzinfo is None:
                        logged_at = logged_at.replace(tzinfo=timezone.utc)
                    if logged_at.astimezone(local_tz).date() != today:
                        continue
                except (TypeError, ValueError):
                    continue
                domain = pt.normalize_domain(str(row.get("domain", "")).strip()).lower().rstrip(".")
                if domain:
                    domains.add(domain)
    except (OSError, csv.Error):
        pass
    return domains


def _render_skipped_domain_tables(fully_sent: list[str], no_email_found: list[str]):
    """Render filter results that must survive Streamlit form reruns."""
    if fully_sent:
        with st.expander(f"⏭ {len(fully_sent)} domain đã gửi đủ hôm nay — bị bỏ qua", expanded=True):
            st.caption("Các domain này đã được gửi thành công bằng tất cả tài khoản cấu hình trong ngày hôm nay.")
            sent_table = pd.DataFrame({
                "STT": range(1, len(fully_sent) + 1),
                "Full link": fully_sent,
                "Domain": [pt.normalize_domain(entry).lower().rstrip(".") for entry in fully_sent],
                "Status": ["✅ Đã gửi đủ hôm nay"] * len(fully_sent),
            })
            st.dataframe(sent_table, width="stretch", hide_index=True)
            st.download_button("⬇️ Tải danh sách đã gửi", "\n".join(fully_sent) + "\n", "domains_already_sent.txt")

    if no_email_found:
        with st.expander(f"⏭ {len(no_email_found)} domain không có email hôm nay — bị bỏ qua", expanded=True):
            st.caption("Các domain này đã được kiểm tra trong hôm nay nhưng không có email hợp lệ. Sang ngày mới cache tự reset và chúng được phép kiểm tra lại.")
            no_email_table = pd.DataFrame({
                "STT": range(1, len(no_email_found) + 1),
                "Full link": no_email_found,
                "Domain": [pt.normalize_domain(entry).lower().rstrip(".") for entry in no_email_found],
                "Status": ["⚠️ Không có email để gửi"] * len(no_email_found),
            })
            st.dataframe(no_email_table, width="stretch", hide_index=True)
            st.download_button("⬇️ Tải danh sách không có email", "\n".join(no_email_found) + "\n", "domains_without_email.txt")


st.set_page_config(page_title="Domain Worker", page_icon="⚙️", layout="wide")
st.title("⚙️ Domain Report Worker")
st.caption("Nhận danh sách domain, xử lý theo batch và tự gửi các email report có địa chỉ người nhận hợp lệ.")
cached_sends = {
    (domain, account)
    for domain, accounts in _sent_domain_accounts_today().items()
    for account in accounts
}
st.caption(
    f"💾 Cache gửi thành công hôm nay: {len(cached_sends)} cặp domain/tài khoản. "
    "Qua ngày mới cache tự reset; lần gửi lỗi vẫn được thử lại."
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
            sent_map = _sent_domain_accounts_today()
            no_email_domains = _no_email_domains_today()
            # Lấy tất cả tài khoản đã cấu hình để so sánh
            all_configured_accounts = {
                str(acc.get("username", "")).strip().lower()
                for acc in (pt.load_config().get("smtp_accounts") or [])
                if acc.get("username")
            }

            new_domains = []       # chưa account nào gửi hôm nay
            partial_domains = []   # 1 số account đã gửi hôm nay, còn account khác chưa
            fully_sent = []        # tất cả account đều đã gửi hôm nay → skip
            no_email_found = []    # đã check hôm nay nhưng không có email để gửi → skip

            for entry in filtered_domains:
                domain_key = pt.normalize_domain(entry).lower().rstrip(".")
                sent_accounts = sent_map.get(domain_key, set())
                if domain_key in no_email_domains:
                    no_email_found.append(entry)
                elif not sent_accounts:
                    new_domains.append(entry)
                elif all_configured_accounts and sent_accounts >= all_configured_accounts:
                    fully_sent.append(entry)
                else:
                    # Còn ít nhất 1 account chưa gửi hôm nay → vẫn đưa vào worker
                    partial_domains.append((entry, sent_accounts))

            keep_domains = new_domains + [e for e, _ in partial_domains]
            st.session_state["worker_domain_input"] = "\n".join(keep_domains)
            st.session_state["worker_filter_skipped"] = {
                "date": datetime.now().astimezone().date().isoformat(),
                "fully_sent": fully_sent,
                "no_email_found": no_email_found,
            }

            # Thông báo tóm tắt
            parts = [f"tổng **{len(filtered_domains)}** link đầu vào"]
            if new_domains:
                parts.append(f"**{len(new_domains)}** domain mới")
            if partial_domains:
                parts.append(f"**{len(partial_domains)}** domain còn account chưa gửi hôm nay")
            if fully_sent:
                parts.append(f"bỏ qua **{len(fully_sent)}** domain đã gửi đủ tất cả tài khoản hôm nay")
            if no_email_found:
                parts.append(f"bỏ qua **{len(no_email_found)}** domain đã check hôm nay nhưng không có email để gửi")
            if parts:
                st.success("Kết quả lọc: " + ", ".join(parts) + ".")

            if keep_domains:
                st.code("\n".join(keep_domains), language=None)

            if partial_domains:
                with st.expander(f"⚠️ {len(partial_domains)} domain đã gửi một phần hôm nay — vẫn đưa vào worker", expanded=True):
                    st.caption("Domain còn sống sẽ được worker kiểm tra; worker bỏ qua tài khoản đã gửi hôm nay và chỉ gửi bằng tài khoản còn lại.")
                    for entry, accs in partial_domains:
                        st.markdown(f"- `{entry}` — đã gửi hôm nay qua: {', '.join(sorted(accs)) or '(không rõ)'}")

            _render_skipped_domain_tables(fully_sent, no_email_found)


            if not keep_domains:
                st.warning("Tất cả domain đã được xử lý hôm nay (đã gửi đủ hoặc không có email để gửi). Danh sách worker trống.")
        else:
            st.warning("Không tìm thấy domain hợp lệ trong nội dung.")
    else:
        skipped_snapshot = st.session_state.get("worker_filter_skipped") or {}
        today = datetime.now().astimezone().date().isoformat()
        if skipped_snapshot.get("date") == today:
            _render_skipped_domain_tables(
                skipped_snapshot.get("fully_sent") or [],
                skipped_snapshot.get("no_email_found") or [],
            )


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


def launch_job_process(job_path):
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--worker-job", job_path]
    else:
        worker_script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "domain_worker.py")
        command = [sys.executable, worker_script, job_path]
    subprocess.Popen(
        command,
        cwd=pt.BASE_DIR,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )


prepared_dir_ui = st.session_state.get("worker_job_dir") or latest_job_dir()
prepared_status_ui = load_status(prepared_dir_ui)
try:
    with open(os.path.join(prepared_dir_ui, "job.json"), encoding="utf-8") as f:
        prepared_job_ui = json.load(f)
except (OSError, ValueError, TypeError):
    prepared_job_ui = {}
worker_ready = bool(
    prepared_status_ui
    and prepared_status_ui.get("state") == "ready"
    and prepared_status_ui.get("ready_total", 0) > 0
    and prepared_job_ui.get("preflight_version") == 2
)


with st.form("worker_form"):
    raw_domains = st.text_area(
        "Danh sách domain",
        height=220,
        key="worker_domain_input",
        placeholder="example-one.com\nexample-two.net\nhttps://example-three.org/login",
        help="Mỗi dòng một URL đầy đủ hoặc domain. Worker giữ nguyên đường dẫn URL để kiểm tra đúng trang.",
    )
    preview_targets, preview_invalid = normalize_list(raw_domains)
    if preview_targets or preview_invalid:
        preview_rows = [
            {
                "Full link": entry,
                "Domain": pt.normalize_domain(entry).lower().rstrip("."),
                "Status": "🔎 Chờ precheck email",
            }
            for entry in preview_targets
        ]
        preview_rows.extend(
            {
                "Full link": entry,
                "Domain": pt.normalize_domain(entry).lower().rstrip("."),
                "Status": "❌ Không hợp lệ",
            }
            for entry in preview_invalid
        )
        preview_table = pd.DataFrame(preview_rows)
        preview_table.insert(0, "STT", range(1, len(preview_table) + 1))
        st.caption(f"Danh sách chuẩn bị chạy: {len(preview_targets)} hợp lệ, {len(preview_invalid)} không hợp lệ.")
        st.dataframe(preview_table, width="stretch", hide_index=True)
    precheck_slot = st.container()
    c1, c2 = st.columns(2)
    batch_size = c1.number_input("Số domain mỗi batch", min_value=1, max_value=100, value=5)
    interval_minutes = c2.number_input("Nghỉ giữa hai batch (phút)", min_value=0, max_value=1440, value=5)
    include_vncert = st.checkbox(
        "Tự gửi cả draft VNCERT",
        value=False,
        help="Chỉ bật khi toàn bộ domain trong danh sách nhắm tới nạn nhân tại Việt Nam.",
    )

    # --- Chọn tài khoản gửi mail ---
    _cfg_ui = pt.load_config()
    _all_accounts = _cfg_ui.get("smtp_accounts") or []
    _account_labels = [acc.get("username", f"account_{i+1}") for i, acc in enumerate(_all_accounts)]
    if _all_accounts:
        selected_accounts = st.multiselect(
            "📨 Tài khoản email được phép gửi",
            options=_account_labels,
            default=_account_labels,
            help="Chọn một hoặc nhiều tài khoản SMTP để gửi email report. Phải chọn ít nhất một tài khoản.",
        )
    else:
        selected_accounts = []
        st.error("⚠️ Chưa cấu hình SMTP account trong config.ini. Worker sẽ không thể gửi email.")

    confirmed = st.checkbox(
        "Tôi xác nhận danh sách đã được kiểm tra và cho phép worker gửi email report thật tự động.",
        value=True,
    )
    with precheck_slot:
        st.caption("Bước 1: check toàn bộ danh sách liên tục, không chia batch và chưa gửi email.")
        precheck = st.form_submit_button("🔎 Check toàn bộ & lọc email", type="primary")
    st.caption("Bước 2: worker chỉ gửi các domain đã check có email; cấu hình batch chỉ áp dụng ở bước này.")
    start = st.form_submit_button(
        "▶ Khởi chạy worker",
        type="primary",
        disabled=not worker_ready,
        help=None if worker_ready else "Nút sẽ được mở sau khi check hoàn tất và có domain gửi được.",
    )

if precheck:
    domains, invalid = normalize_list(raw_domains)
    existing_dir = latest_job_dir()
    existing_status = load_status(existing_dir)
    active_job = existing_status and existing_status.get("state") in ("prechecking", "running", "waiting")
    if active_job:
        st.error(
            f"Job `{existing_status.get('job_id')}` vẫn đang chạy. "
            "Hãy chờ hoàn tất hoặc yêu cầu dừng trước khi tạo job mới."
        )
    elif invalid:
        st.error("Domain không hợp lệ: " + ", ".join(invalid[:10]))
    elif not domains:
        st.warning("Danh sách chưa có domain hợp lệ.")
    elif not selected_accounts:
        st.warning("⚠️ Bạn phải chọn ít nhất một tài khoản email trước khi khởi chạy worker.")
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
            "allowed_accounts": selected_accounts,
            "precheck_only": True,
            "preflight_version": 2,
        }
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=2)
        launch_job_process(job_path)
        st.session_state["worker_job_dir"] = job_dir
        st.success(
            f"Đã bắt đầu precheck **{len(domains)}** domain. Pha này chưa gửi email."
        )

if start:
    domains, invalid = normalize_list(raw_domains)
    prepared_dir = st.session_state.get("worker_job_dir") or latest_job_dir()
    prepared_status = load_status(prepared_dir)
    prepared_job_path = os.path.join(prepared_dir, "job.json") if prepared_dir else ""
    try:
        with open(prepared_job_path, encoding="utf-8") as f:
            prepared_job = json.load(f)
    except (OSError, ValueError):
        prepared_job = {}
    if prepared_status and prepared_status.get("state") in ("prechecking", "running", "waiting"):
        st.error("Precheck hoặc worker vẫn đang chạy. Hãy chờ hoàn tất.")
    elif not prepared_status or prepared_status.get("state") != "ready":
        st.error("Bạn phải bấm Precheck email và chờ hoàn tất trước khi chạy worker.")
    elif invalid or domains != (prepared_job.get("domains") or []):
        st.error("Danh sách đã thay đổi sau precheck. Hãy precheck lại danh sách hiện tại.")
    elif bool(include_vncert) != bool(prepared_job.get("include_vncert")):
        st.error("Tùy chọn VNCERT đã thay đổi. Hãy precheck lại.")
    elif selected_accounts != (prepared_job.get("allowed_accounts") or []):
        st.error("Danh sách tài khoản gửi đã thay đổi. Hãy precheck lại.")
    elif not confirmed:
        st.warning("Bạn cần xác nhận trước khi cho phép gửi email tự động.")
    elif prepared_status.get("ready_total", 0) <= 0:
        st.warning("Precheck không tìm thấy domain nào có email để gửi.")
    else:
        prepared_job["precheck_only"] = False
        with open(prepared_job_path, "w", encoding="utf-8") as f:
            json.dump(prepared_job, f, ensure_ascii=False, indent=2)
        launch_job_process(prepared_job_path)
        st.success(
            f"Đã khởi chạy worker với **{prepared_status.get('ready_total', 0)}** domain đã xác nhận có email."
        )

st.divider()
st.subheader("Trạng thái job gần nhất")
job_dir = st.session_state.get("worker_job_dir") or latest_job_dir()
status = load_status(job_dir)

if not job_dir:
    st.info("Chưa có worker job nào.")
elif not status:
    st.status("Đang khởi động tiến trình check...", state="running", expanded=True)
else:
    if status.get("state") == "prechecking":
        st.status(
            f"Đang check email toàn bộ danh sách — "
            f"{status.get('precheck_processed', 0)}/{status.get('precheck_total', 0)} domain...",
            state="running",
            expanded=True,
        )
    elif status.get("state") == "ready":
        st.status(
            f"Check hoàn tất — {status.get('ready_total', 0)} domain có email và sẵn sàng gửi.",
            state="complete",
            expanded=False,
        )
    elif status.get("state") == "failed":
        st.status("Check/worker gặp lỗi.", state="error", expanded=True)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trạng thái", status.get("state", "?"))
    c2.metric("Tiến độ", f"{status.get('processed', 0)}/{status.get('total', 0)}")
    c3.metric("Batch", f"{status.get('current_batch', 0)}/{status.get('total_batches', 0)}")
    c4.metric("Batch tiếp theo", f"{status.get('next_batch_in_seconds', 0)} giây")
    if status.get("current_domain"):
        action = "Đang precheck email" if status.get("state") == "prechecking" else "Đang xử lý"
        st.info(f"{action}: `{status['current_domain']}`")
    if status.get("error"):
        st.error(status["error"])
    if status.get("state") == "prechecking":
        st.caption(
            f"Đã precheck {status.get('precheck_processed', 0)}/{status.get('precheck_total', 0)} domain; "
            f"tìm thấy {status.get('ready_total', 0)} domain có email để gửi. Chưa gửi email trong pha này."
        )

    excluded_no_email = status.get("excluded_no_email") or []
    if excluded_no_email:
        excluded_table = pd.DataFrame({
            "STT": range(1, len(excluded_no_email) + 1),
            "Full link": [item.get("target_url", "") for item in excluded_no_email],
            "Domain": [item.get("domain", "") for item in excluded_no_email],
            "Status": [
                "❌ Lỗi precheck" if item.get("status") == "precheck_error" else "⚠️ Không có email để gửi"
                for item in excluded_no_email
            ],
        })
        with st.expander(f"⏭ {len(excluded_no_email)} domain bị loại sau precheck", expanded=True):
            st.dataframe(excluded_table, width="stretch", hide_index=True)

    if status.get("state") == "ready":
        try:
            with open(os.path.join(job_dir, "preflight.json"), encoding="utf-8") as f:
                ready_domains = json.load(f).get("ready") or []
        except (OSError, ValueError, TypeError):
            ready_domains = []
        if ready_domains:
            ready_table = pd.DataFrame({
                "STT": range(1, len(ready_domains) + 1),
                "Full link": [item.get("target_url", "") for item in ready_domains],
                "Domain": [item.get("domain", "") for item in ready_domains],
                "Email gửi tới": [
                    ", ".join(
                        recipient.get("email", "")
                        for recipient in item.get("recipients", [])
                        if recipient.get("email")
                    )
                    for item in ready_domains
                ],
                "Status": ["✅ Đã precheck — sẵn sàng gửi"] * len(ready_domains),
            })
            st.subheader("Danh sách đã precheck và sẵn sàng gửi")
            st.dataframe(ready_table, width="stretch", hide_index=True)
            st.success("Precheck hoàn tất. Bấm Khởi chạy worker để chỉ gửi danh sách trong bảng này.")
    if status.get("results"):
        results_list = status["results"]
        # Bảng tóm tắt
        summary_rows = []
        _SKIP_LABELS = {
            "already_sent": "⏭ đã gửi trước đó",
            "no_sendable_email": "⏭ không có email để gửi",
        }
        for r in results_list:
            sent_to_list = r.get("sent_to") or []
            skip_reason = r.get("skipped")
            sent_addresses = "; ".join(
                f"{'✅' if s['ok'] else '❌'} {s['to']} (via {s['account']})"
                for s in sent_to_list
            ) if sent_to_list else (_SKIP_LABELS.get(skip_reason, "(skip)") if skip_reason else "—")
            summary_rows.append({
                "Domain": r.get("domain", ""),
                "Verdict": r.get("reputation") or (_SKIP_LABELS.get(skip_reason, "skipped") if skip_reason else "—"),
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
    if b.button("⏹ Dừng hẳn tiến trình", disabled=status.get("state") in ("ready", "completed", "failed", "stopped")):
        stopped, message = stop_job_process(job_dir)
        (st.success if stopped else st.warning)(message)
        st.rerun()

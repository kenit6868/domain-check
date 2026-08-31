"""Streamlit UI for the background domain reporting worker."""

import csv
import base64
import html
import json
import os
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

import phishing_toolkit as pt
import domain_worker
import cloaking_review_queue as review_queue
from domain_worker import stop_job_process
from domain_utils import extract_domains_from_text

WORKER_DIR = domain_worker.WORKER_DIR


def _review_page_link(label: str) -> None:
    """Render the multipage link, with a harmless fallback for standalone AppTest."""
    try:
        st.page_link(
            "pages/10_Cloaking_Review.py",
            label=label,
            icon=":material/visibility:",
        )
    except KeyError:
        st.caption(f"👁️ {label} — chọn trang Cloaking Review trong menu bên trái.")


def _dataframe_with_copy(df: pd.DataFrame):
    """Render a table with an in-cell copy button for Full link and Domain."""
    columns = list(df.columns)
    headers = "".join(f"<th>{html.escape(str(column))}</th>" for column in columns)
    rows = []
    for _, row in df.iterrows():
        cells = []
        for column in columns:
            value = "" if pd.isna(row[column]) else str(row[column])
            escaped_value = html.escape(value).replace("\n", "<br>")
            if column in ("Full link", "Domain") and value:
                encoded = base64.b64encode(value.encode()).decode()
                cell = (
                    '<td><div class="copy-cell"><span>' + escaped_value + '</span>'
                    f'<button title="Copy {html.escape(column)}" '
                    f'onclick="copyValue(\'{encoded}\', this)">⧉</button></div></td>'
                )
            else:
                cell = f"<td>{escaped_value}</td>"
            cells.append(cell)
        rows.append("<tr>" + "".join(cells) + "</tr>")
    table_height = min(620, max(110, 48 * (len(df) + 1) + 8))
    st.html(
        f"""
        <style>
          .worker-copy-table {{
            height: {table_height - 8}px; overflow: auto; color: #fafafa;
            font-family: sans-serif; border: 1px solid #33363f; border-radius: 10px;
          }}
          .worker-copy-table table {{ width: 100%; min-width: 900px; border-collapse: separate; border-spacing: 0; font-size: 15px; }}
          .worker-copy-table th {{ position: sticky; top: 0; z-index: 1; background: #1c1e26; color: #aeb0b8; text-align: left; }}
          .worker-copy-table th, .worker-copy-table td {{ padding: 11px 12px; border-right: 1px solid #303238; border-bottom: 1px solid #303238; white-space: nowrap; }}
          .worker-copy-table th:last-child, .worker-copy-table td:last-child {{ border-right: 0; }}
          .worker-copy-table tr:last-child td {{ border-bottom: 0; }}
          .worker-copy-table .copy-cell {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; }}
          .worker-copy-table .copy-cell button {{
            flex: 0 0 auto; color: #dbeafe; background: #252831; border: 1px solid #596171;
            border-radius: 6px; padding: 3px 7px; cursor: pointer; font-size: 15px;
          }}
          .worker-copy-table .copy-cell button:hover {{ color: white; background: #2563eb; border-color: #60a5fa; }}
        </style>
        <div class="worker-copy-table"><table><thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
        <script>
          async function copyValue(encodedValue, button) {{
            const bytes = Uint8Array.from(atob(encodedValue), c => c.charCodeAt(0));
            const value = new TextDecoder().decode(bytes);
            try {{
              await navigator.clipboard.writeText(value);
            }} catch (_) {{
              const area = document.createElement('textarea');
              area.value = value; document.body.appendChild(area); area.select();
              document.execCommand('copy'); area.remove();
            }}
            button.textContent = '✓';
            setTimeout(() => button.textContent = '⧉', 1000);
          }}
        </script>
        """,
        unsafe_allow_javascript=True,
    )


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


def _sent_domain_details_today() -> dict[str, list[dict]]:
    """Return successful sender/recipient details per domain for the local day."""
    details: dict[str, list[dict]] = {}
    if not os.path.exists(pt.SENT_LOG_PATH):
        return details
    local_tz = datetime.now().astimezone().tzinfo
    today = datetime.now(local_tz).date()
    try:
        with open(pt.SENT_LOG_PATH, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if str(row.get("success", "")).strip().lower() not in {"true", "1", "yes"}:
                    continue
                try:
                    sent_at = datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00"))
                    if sent_at.tzinfo is None:
                        sent_at = sent_at.replace(tzinfo=timezone.utc)
                    if sent_at.astimezone(local_tz).date() != today:
                        continue
                except (TypeError, ValueError):
                    continue
                domain = pt.normalize_domain(str(row.get("domain", ""))).lower().rstrip(".")
                if domain:
                    details.setdefault(domain, []).append({
                        "account": str(row.get("account", "")).strip(),
                        "to": str(row.get("to", "")).strip(),
                        "sent_at": sent_at.astimezone(local_tz).strftime("%H:%M:%S"),
                    })
    except (OSError, csv.Error):
        pass
    return details


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


def _no_email_links_today() -> list[str]:
    """Return the original URLs logged without a sendable email today."""
    links: dict[str, str] = {}
    path = domain_worker.NO_EMAIL_LOG_PATH
    if not os.path.exists(path):
        return []
    local_tz = datetime.now().astimezone().tzinfo
    today = datetime.now(local_tz).date()
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                try:
                    logged_at = datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00"))
                    if logged_at.tzinfo is None:
                        logged_at = logged_at.replace(tzinfo=timezone.utc)
                    if logged_at.astimezone(local_tz).date() != today:
                        continue
                except (TypeError, ValueError):
                    continue
                domain = pt.normalize_domain(str(row.get("domain", ""))).lower().rstrip(".")
                target_url = str(row.get("target_url", "")).strip()
                if domain:
                    links.setdefault(domain, target_url or f"https://{domain}/")
    except (OSError, csv.Error):
        pass
    return list(links.values())


def _worker_target_urls_today() -> dict[str, str]:
    """Recover original full URLs from today's persisted worker jobs."""
    targets: dict[str, str] = {}
    if not os.path.isdir(WORKER_DIR):
        return targets
    local_tz = datetime.now().astimezone().tzinfo
    today = datetime.now(local_tz).date()
    for name in os.listdir(WORKER_DIR):
        job_path = os.path.join(WORKER_DIR, name, "job.json")
        try:
            with open(job_path, encoding="utf-8") as f:
                job = json.load(f)
            created_at = datetime.fromisoformat(str(job.get("created_at", "")).replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if created_at.astimezone(local_tz).date() != today:
                continue
            for target in job.get("domains") or []:
                domain = pt.normalize_domain(str(target)).lower().rstrip(".")
                if domain:
                    targets[domain] = str(target)
        except (OSError, ValueError, TypeError):
            continue
    return targets


def _render_skipped_domain_tables(fully_sent: list[str], no_email_found: list[str]):
    """Render one compact table for all domains skipped by the filter."""
    if not fully_sent and not no_email_found:
        return
    sent_details = _sent_domain_details_today()
    rows = []
    for entry in fully_sent:
        domain = pt.normalize_domain(entry).lower().rstrip(".")
        details = sent_details.get(domain, [])
        rows.append({
            "Full link": entry,
            "Domain": domain,
            "Lý do": "✅ Đã gửi đủ bằng account đang chọn",
            "Account đã gửi": "; ".join(dict.fromkeys(
                item.get("account", "") for item in details if item.get("account")
            )) or "—",
        })
    rows.extend({
        "Full link": entry,
        "Domain": pt.normalize_domain(entry).lower().rstrip("."),
        "Lý do": "⚠️ Không có email để gửi",
        "Account đã gửi": "—",
    } for entry in no_email_found)
    skipped_table = pd.DataFrame(rows)
    skipped_table.insert(0, "STT", range(1, len(skipped_table) + 1))
    with st.expander(f"⏭ Domain bị bỏ qua — {len(rows)}", expanded=False):
        _dataframe_with_copy(skipped_table)


st.set_page_config(page_title="Domain Worker", page_icon="⚙️", layout="wide")
# Streamlit làm mờ toàn bộ element cũ bằng class `stale` trong mỗi lần rerun.
# Trang này đã có spinner/trạng thái riêng, nên giữ UI rõ để tránh cảm giác bị treo.
st.markdown(
    """
    <style>
    .stale {
        opacity: 1 !important;
        transition: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("⚙️ Domain Report Worker")
st.caption("Nhận danh sách domain, xử lý theo batch và tự gửi các email report có địa chỉ người nhận hợp lệ.")
try:
    review_queue.sync_from_worker_jobs(WORKER_DIR)
except OSError:
    pass
pending_review_count = len(review_queue.list_items(review_queue.ACTIVE_STATES))
if pending_review_count:
    st.warning(
        f"Có **{pending_review_count}** domain cloaking đã được cách ly khỏi luồng gửi tự động."
    )
    _review_page_link(f"Mở Cloaking Review ({pending_review_count})")
else:
    st.caption("Không có domain cloaking nào đang chờ duyệt.")
cached_sends = {
    (domain, account)
    for domain, accounts in _sent_domain_accounts_today().items()
    for account in accounts
}
st.caption(
    f"💾 Cache gửi thành công hôm nay: {len(cached_sends)} cặp domain/tài khoản. "
    "Qua ngày mới cache tự reset; lần gửi lỗi vẫn được thử lại."
)

_cfg_ui = pt.load_config()
_all_accounts = _cfg_ui.get("smtp_accounts") or []
_account_labels = [acc.get("username", f"account_{i+1}") for i, acc in enumerate(_all_accounts)]
selected_precheck_accounts = st.multiselect(
    "📨 Tài khoản email dùng để lọc và gửi",
    options=_account_labels,
    default=_account_labels,
    key="worker_selected_accounts",
    help=(
        "Mặc định chọn toàn bộ tài khoản trong config.ini. Danh sách này được dùng thống nhất "
        "khi lọc domain, precheck và gửi email."
    ),
    disabled=not _account_labels,
)
if not _account_labels:
    st.error("⚠️ Chưa cấu hình SMTP account trong config.ini.")

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
        if not selected_precheck_accounts:
            st.warning("Hãy chọn ít nhất một tài khoản email trước khi lọc domain.")
        elif filtered_domains:
            sent_map = _sent_domain_accounts_today()
            no_email_domains = _no_email_domains_today()
            selected_account_keys = {
                str(account).strip().lower()
                for account in selected_precheck_accounts
                if str(account).strip()
            }

            new_domains = []       # chưa account nào gửi hôm nay
            partial_domains = []   # (domain, account đã gửi, account còn thiếu)
            fully_sent = []        # tất cả account đều đã gửi hôm nay → skip
            no_email_found = []    # đã check hôm nay nhưng không có email để gửi → skip

            for entry in filtered_domains:
                domain_key = pt.normalize_domain(entry).lower().rstrip(".")
                sent_accounts = sent_map.get(domain_key, set())
                selected_sent_accounts = sent_accounts & selected_account_keys
                remaining_accounts = selected_account_keys - selected_sent_accounts
                if domain_key in no_email_domains:
                    no_email_found.append(entry)
                elif not selected_sent_accounts:
                    new_domains.append(entry)
                elif not remaining_accounts:
                    fully_sent.append(entry)
                else:
                    # Còn ít nhất 1 account chưa gửi hôm nay → vẫn đưa vào worker
                    partial_domains.append((entry, selected_sent_accounts, remaining_accounts))

            keep_domains = new_domains + [entry for entry, _sent, _remaining in partial_domains]
            st.session_state["worker_domain_input"] = "\n".join(keep_domains)
            st.session_state["worker_filter_skipped"] = {
                "date": datetime.now().astimezone().date().isoformat(),
                "fully_sent": fully_sent,
                "no_email_found": no_email_found,
            }

            # Thông báo tóm tắt
            parts = [
                f"**{len(filtered_domains)}** link đầu vào",
                f"**{len(keep_domains)}** domain đưa vào worker",
            ]
            if new_domains:
                parts.append(f"**{len(new_domains)}** domain chưa account nào đang chọn gửi")
            if partial_domains:
                parts.append(
                    f"**{len(partial_domains)}** domain đã gửi một phần và vẫn còn account cần gửi"
                )
            if fully_sent:
                parts.append(f"bỏ qua **{len(fully_sent)}** domain đã gửi đủ bằng các account đang chọn")
            if no_email_found:
                parts.append(f"bỏ qua **{len(no_email_found)}** domain đã check hôm nay nhưng không có email để gửi")
            if parts:
                st.success("Kết quả lọc: " + "; ".join(parts) + ".")

            if partial_domains:
                with st.expander(f"⚠️ {len(partial_domains)} domain đã gửi một phần hôm nay — vẫn đưa vào worker", expanded=True):
                    st.caption("Worker sẽ kiểm tra lại và chỉ bỏ qua đúng draft/người nhận đã gửi thành công hôm nay.")
                    for entry, sent_accs, remaining_accs in partial_domains:
                        st.markdown(
                            f"- `{entry}` — đã gửi: {', '.join(sorted(sent_accs))}; "
                            f"còn cần gửi: {', '.join(sorted(remaining_accs))}"
                        )

            if not keep_domains:
                st.warning("Tất cả domain đã được xử lý hôm nay (đã gửi đủ hoặc không có email để gửi). Danh sách worker trống.")
        else:
            st.warning("Không tìm thấy domain hợp lệ trong nội dung.")


# Hai bảng tracking luôn được dựng lại từ cache trong ngày, không phụ thuộc
# session_state nên vẫn hiện sau F5, Streamlit rerun hoặc chạy lại source.
_known_worker_urls = _worker_target_urls_today()
_selected_account_keys = {
    str(account).strip().lower() for account in selected_precheck_accounts if str(account).strip()
}
_sent_accounts_for_tracking = _sent_domain_accounts_today()
_daily_sent_links = [
    _known_worker_urls.get(domain, f"https://{domain}/")
    for domain in sorted(_sent_domain_details_today())
    if _selected_account_keys and _sent_accounts_for_tracking.get(domain, set()) >= _selected_account_keys
]
_daily_no_email_links = _no_email_links_today()
_render_skipped_domain_tables(_daily_sent_links, _daily_no_email_links)


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


def _render_job_metrics(status, total_sent=None):
    """Render the compact progress bar beside the table it describes."""
    state = status.get("state", "?")
    if state == "prechecking":
        columns = st.columns(4)
        columns[0].metric("Trạng thái", "Đang precheck")
        columns[1].metric("Đã kiểm tra", f"{status.get('precheck_processed', 0)}/{status.get('precheck_total', 0)}")
        columns[2].metric("Dùng cache", status.get("precheck_cached", 0))
        columns[3].metric("Có email", status.get("ready_total", 0))
        return
    if state == "ready":
        columns = st.columns(4)
        columns[0].metric("Trạng thái", "Sẵn sàng")
        columns[1].metric("Domain sẵn sàng", status.get("ready_total", 0))
        columns[2].metric("Dùng cache", status.get("precheck_cached", 0))
        columns[3].metric("Bị loại", len(status.get("excluded_no_email") or []))
        return
    columns = st.columns(5 if total_sent is not None else 4)
    c1, c2, c3, c4 = columns[:4]
    c1.metric("Trạng thái", state)
    c2.metric("Tiến độ", f"{status.get('processed', 0)}/{status.get('total', 0)}")
    c3.metric("Batch", f"{status.get('current_batch', 0)}/{status.get('total_batches', 0)}")
    c4.metric("Batch tiếp theo", f"{status.get('next_batch_in_seconds', 0)} giây")
    if total_sent is not None:
        columns[4].metric("Tổng gửi thành công", total_sent)


def launch_job_process(job_path):
    return domain_worker.launch_job_process(job_path)


prepared_dir_ui = st.session_state.get("worker_job_dir") or latest_job_dir()
try:
    with open(os.path.join(prepared_dir_ui, "job.json"), encoding="utf-8") as f:
        prepared_job_ui = json.load(f)
except (OSError, ValueError, TypeError):
    prepared_job_ui = {}
def _load_preflight(job_dir):
    if not job_dir:
        return {}
    try:
        with open(os.path.join(job_dir, "preflight.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if data.get("version") == 2 else {}
    except (OSError, ValueError, TypeError):
        return {}


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
        st.caption(
            f"Chuẩn bị chạy: {len(preview_targets)} domain hợp lệ"
            + (f" · {len(preview_invalid)} không hợp lệ" if preview_invalid else "")
        )
        if preview_invalid:
            with st.expander(f"❌ Domain không hợp lệ — {len(preview_invalid)}", expanded=False):
                st.code("\n".join(preview_invalid), language=None)
    force_precheck = st.checkbox(
        "Bỏ qua cache và check lại toàn bộ domain",
        value=False,
        help="Mặc định tái sử dụng kết quả precheck trong ngày. Bật khi cần làm mới email abuse/hosting.",
    )
    st.caption("Bước 1: dùng cache precheck trong ngày, chỉ check domain chưa có cache và chưa gửi email.")
    precheck = st.form_submit_button("🔎 Check toàn bộ & lọc email", type="primary")

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
    elif not selected_precheck_accounts:
        st.warning("Bạn phải chọn ít nhất một tài khoản email cho job này.")
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
            "batch_size": 5,
            "interval_seconds": 300,
            "include_vncert": False,
            "allowed_accounts": selected_precheck_accounts,
            "force_precheck": bool(force_precheck),
            "precheck_only": True,
            "preflight_version": 2,
        }
        domain_worker._atomic_json(job_path, job)
        launch_job_process(job_path)
        st.session_state["worker_job_dir"] = job_dir
        st.success(
            f"Đã bắt đầu precheck **{len(domains)}** domain. Pha này chưa gửi email."
        )

st.divider()
job_dir = st.session_state.get("worker_job_dir") or latest_job_dir()
status = load_status(job_dir)

if not job_dir:
    st.info("Chưa có worker job nào.")
elif not status:
    st.status("Đang khởi động tiến trình check...", state="running", expanded=True)
    if st.button("🔄 Làm mới trạng thái", key="refresh_starting_check"):
        st.rerun()
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
    if status.get("state") == "prechecking":
        if st.button("🔄 Làm mới trạng thái", key="refresh_active_check"):
            st.rerun()
    if status.get("current_domain"):
        action = "Đang precheck email" if status.get("state") == "prechecking" else "Đang xử lý"
        st.info(f"{action}: `{status['current_domain']}`")
    if status.get("error"):
        st.error(status["error"])
    if status.get("state") == "prechecking":
        st.subheader("Tiến độ precheck")
        _render_job_metrics(status)
        st.caption(
            f"Đã precheck {status.get('precheck_processed', 0)}/{status.get('precheck_total', 0)} domain; "
            f"{status.get('precheck_cached', 0)} domain lấy từ cache hôm nay; "
            f"tìm thấy {status.get('ready_total', 0)} domain có email để gửi. Chưa gửi email trong pha này."
        )

    excluded_no_email = status.get("excluded_no_email") or []
    precheck_errors = [item for item in excluded_no_email if item.get("status") == "precheck_error"]
    if precheck_errors:
        excluded_table = pd.DataFrame({
            "STT": range(1, len(precheck_errors) + 1),
            "Full link": [item.get("target_url", "") for item in precheck_errors],
            "Domain": [item.get("domain", "") for item in precheck_errors],
            "Lỗi": [item.get("error", "Lỗi precheck") for item in precheck_errors],
        })
        with st.expander(f"⚠️ Lỗi precheck — {len(precheck_errors)}", expanded=False):
            _dataframe_with_copy(excluded_table)

    cached_preflight = _load_preflight(job_dir)
    ready_domains = cached_preflight.get("ready") or []
    worker_state = status.get("state")
    active_worker = worker_state in ("prechecking", "running", "waiting")
    if ready_domains:
            if status.get("state") == "ready":
                st.subheader("Kết quả precheck")
                _render_job_metrics(status)
            if status.get("state") == "ready":
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
                })
                with st.expander(f"📋 Domain sẵn sàng gửi — {len(ready_domains)}", expanded=False):
                    _dataframe_with_copy(ready_table)
            if active_worker:
                st.info("Cache precheck vẫn được giữ. Worker hiện đang chạy nên chưa thể khởi chạy thêm tiến trình.")
            elif worker_state == "completed":
                completed_results = status.get("results") or []
                completed_sent = sum(int(item.get("sent_ok", 0) or 0) for item in completed_results)
                completed_cached = sum(int(item.get("already_sent", 0) or 0) for item in completed_results)
                completed_failed = sum(int(item.get("sent_failed", 0) or 0) for item in completed_results)
                st.success(
                    f"✅ Job đã chạy xong cho các account đã chọn: gửi mới **{completed_sent}** email; "
                    f"bỏ qua **{completed_cached}** lượt đã gửi thành công hôm nay; "
                    f"còn **{completed_failed}** lượt gửi lỗi."
                )
                st.caption(
                    "Chạy lại chỉ retry phần còn thiếu hoặc bị lỗi; email đã thành công hôm nay không được gửi trùng."
                )
            else:
                st.success(
                    "Cache precheck hợp lệ. Bạn có thể chạy worker ngay bằng danh sách này, "
                    "hoặc bấm Check toàn bộ để cập nhật cache trước khi chạy."
                )
            st.subheader("Retry phần còn thiếu" if worker_state == "completed" else "Cấu hình gửi worker")
            job_selected_accounts = [
                account for account in (prepared_job_ui.get("allowed_accounts") or [])
                if account in _account_labels
            ]
            st.caption(
                "Tài khoản gửi đã chọn từ đầu job: "
                + (", ".join(job_selected_accounts) if job_selected_accounts else "—")
            )
            with st.form("send_worker_form"):
                c1, c2 = st.columns(2)
                batch_size = c1.number_input("Số domain mỗi batch", min_value=1, max_value=100, value=5)
                interval_minutes = c2.number_input("Nghỉ giữa hai batch (phút)", min_value=0, max_value=1440, value=5)
                include_vncert = st.checkbox(
                    "Tự gửi cả draft VNCERT",
                    value=False,
                    help="Chỉ bật khi toàn bộ domain trong danh sách nhắm tới nạn nhân tại Việt Nam.",
                )
                selected_accounts = job_selected_accounts
                if not _all_accounts:
                    st.error("⚠️ Chưa cấu hình SMTP account trong config.ini.")
                confirmed = st.checkbox(
                    "Tôi xác nhận danh sách đã được kiểm tra và cho phép worker gửi email report thật tự động.",
                    value=True,
                )
                start = st.form_submit_button(
                    "↻ Retry phần còn thiếu" if worker_state == "completed" else "▶ Khởi chạy worker",
                    type="primary",
                    disabled=not _all_accounts,
                )

            if start:
                prepared_job_path = os.path.join(job_dir, "job.json")
                try:
                    with open(prepared_job_path, encoding="utf-8") as f:
                        prepared_job = json.load(f)
                except (OSError, ValueError):
                    prepared_job = {}
                latest_status = load_status(job_dir) or {}
                if latest_status.get("state") in ("prechecking", "running", "waiting"):
                    st.warning("Worker đang chạy hoặc đang chờ batch; không thể khởi chạy thêm tiến trình.")
                elif prepared_job.get("preflight_version") != 2:
                    st.error("Kết quả precheck đã cũ. Hãy check lại danh sách.")
                elif not selected_accounts:
                    st.warning("Bạn phải chọn ít nhất một tài khoản email.")
                elif not confirmed:
                    st.warning("Bạn cần xác nhận trước khi cho phép gửi email tự động.")
                else:
                    prepared_job.update({
                        "batch_size": int(batch_size),
                        "interval_seconds": int(interval_minutes * 60),
                        "include_vncert": bool(include_vncert),
                        "allowed_accounts": selected_accounts,
                        "precheck_only": False,
                    })
                    prepared_job.pop("retry_targets", None)
                    domain_worker._atomic_json(prepared_job_path, prepared_job)
                    launch_job_process(prepared_job_path)
                    action_text = "retry" if worker_state == "completed" else "xử lý"
                    st.success(
                        f"Đã yêu cầu worker {action_text} {len(ready_domains)} domain. "
                        "Các email đã thành công hôm nay sẽ tự động được bỏ qua."
                    )
    preflight_ready = (_load_preflight(job_dir).get("ready") or [])
    results_list = status.get("results") or []
    if preflight_ready or results_list:
        # Luôn hiện toàn bộ danh sách worker ngay lần render đầu tiên. Kết quả mới
        # được ghép vào đúng dòng khi người dùng bấm Làm mới trạng thái.
        result_by_target = {r.get("target_url"): r for r in results_list if r.get("target_url")}
        worker_items = preflight_ready or results_list
        summary_rows = []
        _SKIP_LABELS = {
            "manual_review_required": "⏸ Chờ xác nhận cloaking",
            "already_sent": "✅ Đã gửi trước đó",
            "no_sendable_email": "⏭ không có email để gửi",
        }
        sent_accounts_ui = _sent_domain_accounts_today()
        sent_details_ui = _sent_domain_details_today()
        for prepared in worker_items:
            target_url = prepared.get("target_url", "")
            r = result_by_target.get(target_url, prepared if not preflight_ready else {})
            domain_key = (r.get("domain") or prepared.get("domain", "")).lower().rstrip(".")
            cached_accounts_for_domain = sent_accounts_ui.get(domain_key, set())
            fully_sent_by_selected_accounts = r.get("skipped") == "already_sent"
            sent_to_list = r.get("sent_to") or []
            skip_reason = r.get("skipped")
            sender_addresses = "; ".join(dict.fromkeys(
                str(s.get("account", "")).strip() for s in sent_to_list
                if str(s.get("account", "")).strip()
            )) or "—"
            successful_account_names = set(cached_accounts_for_domain)
            successful_account_names.update(
                str(s.get("account", "")).strip().lower()
                for s in sent_to_list
                if s.get("ok") and str(s.get("account", "")).strip()
            )
            successful_sender_addresses = "; ".join(sorted(successful_account_names)) or "—"
            recipient_addresses = "; ".join(dict.fromkeys(
                str(s.get("to", "")).strip() for s in sent_to_list
                if str(s.get("to", "")).strip()
            )) or "—"
            if fully_sent_by_selected_accounts and not r.get("sent_ok", 0):
                row_status = "✅ Đã gửi trước đó"
                cached_details = sent_details_ui.get(domain_key, [])
                sender_addresses = "; ".join(dict.fromkeys(
                    detail.get("account", "") for detail in cached_details if detail.get("account")
                )) or "; ".join(sorted(cached_accounts_for_domain)) or "—"
                recipient_addresses = "; ".join(dict.fromkeys(
                    detail.get("to", "") for detail in cached_details if detail.get("to")
                )) or "—"
                display_sent_ok = len(cached_accounts_for_domain)
            elif r:
                if r.get("error"):
                    row_status = "❌ Lỗi"
                elif skip_reason:
                    row_status = _SKIP_LABELS.get(skip_reason, "⏭ Bỏ qua")
                elif r.get("sent_failed", 0) and not r.get("sent_ok", 0):
                    row_status = "❌ Thất bại"
                elif r.get("sent_ok", 0):
                    row_status = "✅ Đã gửi"
                else:
                    row_status = "✅ Hoàn tất"
            elif status.get("current_domain") == target_url:
                row_status = "🔄 Đang chạy"
            else:
                row_status = "⏳ Chờ"
            if not fully_sent_by_selected_accounts or r.get("sent_ok", 0):
                display_sent_ok = r.get("sent_ok", 0)
            summary_rows.append({
                "Full link": target_url,
                "Domain": r.get("domain") or prepared.get("domain", ""),
                "Status": row_status,
                "Verdict": r.get("reputation") or (_SKIP_LABELS.get(skip_reason, "skipped") if skip_reason else "—"),
                "Cloaking": (
                    f"{r.get('cloaking_verdict')} ({r.get('cloaking_score', 0)})"
                    if r.get("cloaking_verdict") else "—"
                ),
                "Drafts": r.get("drafts_total", 0),
                "Sendable": r.get("drafts_sendable", 0),
                "✅ Sent": display_sent_ok,
                "❌ Failed": r.get("sent_failed", 0),
                "Account đã gửi thành công": successful_sender_addresses,
                "Địa chỉ gửi": sender_addresses,
                "Địa chỉ nhận": recipient_addresses,
            })
        results_df = pd.DataFrame(summary_rows)
        results_df.insert(0, "STT", range(1, len(results_df) + 1))
        if status.get("state") != "ready":
            st.subheader("Theo dõi worker")
            total_sent = sum(int(row.get("✅ Sent", 0) or 0) for row in summary_rows)
            _render_job_metrics(status, total_sent=total_sent)
        current_target = status.get("current_domain")
        if current_target:
            st.info(
                f"🔄 Domain đang xử lý: `{pt.normalize_domain(current_target).lower().rstrip('.')}`\n\n"
                f"Full link: `{current_target}`"
            )
        elif status.get("state") == "waiting":
            completed_result_targets = set(result_by_target)
            next_item = next(
                (
                    item for item in worker_items
                    if item.get("target_url") not in completed_result_targets
                ),
                None,
            )
            if next_item:
                st.info(
                    f"⏳ Đang nghỉ giữa batch. Domain xử lý tiếp theo: "
                    f"`{next_item.get('domain', '')}` — `{next_item.get('target_url', '')}`"
                )
        _dataframe_with_copy(results_df)

        manual_review_count = sum(
            1 for item in result_by_target.values()
            if item.get("skipped") == "manual_review_required"
        )
        if manual_review_count:
            with st.container(border=True):
                st.subheader("Cloaking đã được cách ly")
                st.write(
                    f"**{manual_review_count}** domain trong job này không được gửi tự động. "
                    "Hãy duyệt bằng chứng và chọn hành động tại trang riêng."
                )
                _review_page_link("Mở Cloaking Review")

        error_rows = []
        for item in results_list:
            errors = []
            if item.get("error"):
                errors.append(str(item["error"]))
            errors.extend(
                f"{sent.get('to', '')} (via {sent.get('account', '')}): {sent.get('error')}"
                for sent in (item.get("sent_to") or [])
                if sent.get("error")
            )
            if errors:
                error_rows.append({
                    "Full link": item.get("target_url", ""),
                    "Domain": item.get("domain", ""),
                    "Lỗi": "\n".join(errors),
                })
        if error_rows:
            for index, row in enumerate(error_rows, start=1):
                row["STT"] = index
            with st.expander(f"⚠️ Xem lỗi ({len(error_rows)})", expanded=False):
                error_df = pd.DataFrame(error_rows)[["STT", "Full link", "Domain", "Lỗi"]]
                _dataframe_with_copy(error_df)

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
    if status.get("state") != "prechecking":
        if a.button("🔄 Làm mới trạng thái", key="refresh_finished_check"):
            st.rerun()
    if b.button("⏹ Dừng hẳn tiến trình", disabled=status.get("state") in ("ready", "completed", "failed", "stopped")):
        stopped, message = stop_job_process(job_dir)
        (st.success if stopped else st.warning)(message)
        st.rerun()

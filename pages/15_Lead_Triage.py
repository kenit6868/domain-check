"""Review infrastructure leads pasted from an operator message."""

import hashlib
import importlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st

import domain_worker
import lead_triage
import lead_triage_cloaking_ui
import phishing_toolkit as pt
import provider_replies

if getattr(lead_triage, "MODULE_VERSION", 0) < 24 or not callable(
    getattr(lead_triage, "send_investigation_draft", None)
):
    lead_triage = importlib.reload(lead_triage)


st.set_page_config(page_title="Xử lý đầu mối", page_icon="🧭", layout="wide")
st.title("Xử lý đầu mối")
st.caption("Dán ghi chú → Phân tích danh sách → Chọn domain để Check → Duyệt báo cáo.")


_CACHE_WIDGETS = {"lead_triage_additional_english", "lead_triage_sender_account", "lead_triage_selection"}
_CACHE_PREFIXES = ("lead_triage_approved_", "lead_triage_sent_", "lead_triage_log_warning_")


def _cache_widget_key(key: str) -> bool:
    return (key in _CACHE_WIDGETS or key.startswith(_CACHE_PREFIXES)
            or (key.startswith("lead_triage_recipient_")
                and not key.startswith("lead_triage_recipient_refresh_")))


def _review_snapshot():
    raw_text = st.session_state.get("lead_triage_input", st.session_state.get("lead_triage_cached_raw", ""))
    return {
        "raw": raw_text,
        "parsed": st.session_state.get("lead_triage_parsed"),
        "results": st.session_state.get("lead_triage_results"),
        "visible_targets": st.session_state.get("lead_triage_visible_targets"),
        "backend_results": st.session_state.get("lead_triage_backend_results"),
        "delivery_status": st.session_state.get("lead_triage_delivery_status"),
        "evidence": {key: value for key, value in st.session_state.items()
                     if key.startswith("lead_triage_evidence_")},
        "cloaking_previews": {key: value for key, value in st.session_state.items()
                              if key.startswith("lead_triage_cloaking_preview_")
                              and isinstance(value, dict)},
        "widgets": {key: value for key, value in st.session_state.items()
                    if _cache_widget_key(key)},
    }


def _persist_review():
    snapshot = _review_snapshot()
    st.session_state["lead_triage_cached_raw"] = snapshot["raw"]
    if not snapshot["raw"] and not snapshot["results"]:
        return
    digest = hashlib.sha256(repr(snapshot).encode("utf-8")).hexdigest()
    if digest != st.session_state.get("lead_triage_cache_digest"):
        if lead_triage.save_review_cache(snapshot):
            st.session_state["lead_triage_cache_digest"] = digest
            st.session_state.pop("lead_triage_cache_save_error", None)
        else:
            st.session_state["lead_triage_cache_save_error"] = True


def _clear_cached_review():
    if not lead_triage.clear_review_cache():
        st.session_state["lead_triage_cache_error"] = True
        return
    for key in list(st.session_state):
        if key.startswith("lead_triage_"):
            del st.session_state[key]
    st.session_state["lead_triage_cache_loaded"] = True
    st.session_state["lead_triage_cache_cleared"] = True


if not st.session_state.get("lead_triage_cache_loaded"):
    cached = lead_triage.load_review_cache()
    cached_evidence = cached.get("evidence") if isinstance(cached.get("evidence"), dict) else {}
    cached_widgets = cached.get("widgets") if isinstance(cached.get("widgets"), dict) else {}
    cached_previews = cached.get("cloaking_previews") if isinstance(cached.get("cloaking_previews"), dict) else {}
    restored_backend = cached.get("backend_results")
    if isinstance(restored_backend, dict):
        restored_backend["checked_monotonic"] = 0
    for key, value in {
        "lead_triage_input": cached.get("raw", ""),
        "lead_triage_parsed": cached.get("parsed"),
        "lead_triage_results": cached.get("results"),
        "lead_triage_visible_targets": cached.get("visible_targets"),
        "lead_triage_backend_results": restored_backend,
        "lead_triage_delivery_status": cached.get("delivery_status"),
        **{key: value for key, value in cached_evidence.items()
           if key.startswith("lead_triage_evidence_")},
        **{key: value for key, value in cached_widgets.items()
           if _cache_widget_key(key)},
        **{key: value for key, value in cached_previews.items()
           if key.startswith("lead_triage_cloaking_preview_") and isinstance(value, dict)},
    }.items():
        if value is not None and key not in st.session_state:
            st.session_state[key] = value
    st.session_state["lead_triage_cache_loaded"] = True
if "lead_triage_input" not in st.session_state:
    st.session_state["lead_triage_input"] = st.session_state.get("lead_triage_cached_raw", "")


def _invalidate_parsed_lead():
    st.session_state.pop("lead_triage_parsed", None)
    _persist_review()


def _show_email_preview(body: str, logo_path: str, *, vietnamese: bool = False):
    """Mirror the shared email MIME formatter's logo position in native UI."""
    signature = re.search(
        r"(?im)^\s*(?:Trân trọng|Regards|Kind regards),?\s*$" if vietnamese
        else r"(?im)^\s*(?:Regards|Kind regards),?\s*$",
        body,
    )
    with st.container(height=420, border=True):
        if logo_path and signature:
            if body[:signature.start()].strip():
                st.text(body[:signature.start()].rstrip())
            st.image(logo_path, width=64)
            st.text(body[signature.start():].lstrip())
        else:
            st.text(body)
            if logo_path:
                st.image(logo_path, width=64)


raw = st.text_area(
    "Thông tin đầu mối",
    key="lead_triage_input",
    height=160,
    placeholder="example.com reg=? hold=none\n=== backend scam (bare IP?) ===\nbackend.example A: 192.0.2.1",
    on_change=_invalidate_parsed_lead,
)
left_action, right_action = st.columns([1, 1])
with left_action:
    parse_clicked = st.button("Phân tích ghi chú", type="primary", disabled=not raw.strip())
with right_action:
    st.button("Xóa cache", on_click=_clear_cached_review, icon=":material/delete:",
              help="Xóa bản nháp cục bộ để bắt đầu lại; không xóa lịch sử gửi hoặc file evidence.")
st.caption("Bản nháp được lưu cục bộ và tự khôi phục khi quay lại menu hoặc F5.")
if st.session_state.pop("lead_triage_cache_cleared", False):
    st.success("Đã xóa bản nháp cục bộ. Có thể dán ghi chú mới và phân tích lại.")
if st.session_state.pop("lead_triage_cache_error", False):
    st.error("Chưa xóa được cache cục bộ; dữ liệu hiện tại vẫn được giữ.")
if st.session_state.get("lead_triage_cache_save_error"):
    st.warning("Chưa lưu được cache cục bộ; dữ liệu vẫn còn trong phiên hiện tại.")
if parse_clicked:
    new_source = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if (st.session_state.get("lead_triage_results") or {}).get("source") != new_source:
        st.session_state.pop("lead_triage_results", None)
        st.session_state.pop("lead_triage_visible_targets", None)
        st.session_state.pop("lead_triage_backend_results", None)
        for key in list(st.session_state):
            if key.startswith(("lead_triage_evidence_", "lead_triage_recipient_", "lead_triage_approved_", "lead_triage_cloaking_preview_")) or key == "lead_triage_selection":
                del st.session_state[key]
    st.session_state["lead_triage_parsed"] = {
        "raw": raw,
        "items": lead_triage.parse_lead(raw),
    }
    _persist_review()
parsed = st.session_state.get("lead_triage_parsed") or {}
if parsed.get("raw") != raw:
    if raw.strip():
        st.caption("Bấm Phân tích ghi chú để xem danh sách; chưa chạy kiểm tra mạng.")
    _persist_review()
    st.stop()

items = parsed["items"]
report_items = [item for item in items if not item["backend_lead"]]
backend_items = [item for item in items if item["backend_lead"]]
st.caption(f"Đã phân tích: {len(report_items)} domain cần báo cáo · {len(backend_items)} đầu mối backend. Check chỉ chạy khi bấm nút.")
source_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
delivery_status = st.session_state.get("lead_triage_delivery_status") or {}
sent_targets = lead_triage.sent_lead_targets()
row_click_key = "lead_triage_row_check"
row_targets_key = "lead_triage_row_targets"


def _was_sent(item):
    check_url = item["target"] if item["full_url"] else f"https://{item['domain']}/"
    return bool(delivery_status.get(item["target"]) or check_url in sent_targets)


def _check_table_row():
    click = st.session_state.get(row_click_key) or {}
    targets = st.session_state.get(row_targets_key) or []
    try:
        target = targets[int(click["row"])]
    except (KeyError, TypeError, ValueError, IndexError):
        return
    if any(item["target"] == target and _was_sent(item) for item in report_items):
        return
    st.session_state["lead_triage_pending_row"] = (source_hash, target)

if report_items:
    st.session_state[row_targets_key] = [item["target"] for item in report_items]
    st.dataframe(pd.DataFrame([
        {
            "Check": None if _was_sent(item) else ":material/search: Check",
            "Domain/URL": item["target"],
            "Trạng thái gửi": "Đã gửi" if _was_sent(item) else "Chưa gửi",
            "Nhóm": "Domain cần báo cáo",
            "IP trong ghi chú": ", ".join(item["supplied_ips"]) or "—",
            "Phạm vi": "Full URL" if item["full_url"] else "Chỉ có domain",
        }
        for item in report_items
    ]), hide_index=True, width="stretch", key=f"lead_targets_status_{source_hash[:12]}",
        column_order=["Check", "Domain/URL", "Trạng thái gửi", "Nhóm", "IP trong ghi chú", "Phạm vi"], column_config={
        "Check": st.column_config.ButtonColumn(
            "Check", key=row_click_key, on_click=_check_table_row, type="primary", width="small", pinned=True
        ),
        "Domain/URL": st.column_config.TextColumn("Domain/URL", pinned=True),
    })
    st.caption("Domain trần được kiểm tra tại trang chủ HTTPS; draft sẽ ghi rõ giới hạn này.")
    choices = [item["target"] for item in report_items if not _was_sent(item)]
    cached_selection = st.session_state.get("lead_triage_selection")
    if isinstance(cached_selection, list):
        st.session_state["lead_triage_selection"] = [target for target in cached_selection if target in choices]
    elif cached_selection is not None:
        st.session_state.pop("lead_triage_selection", None)
    selected = st.multiselect("Chọn URL/domain để check", choices, default=choices, key="lead_triage_selection")
    check = st.button("🔎 Check đầu mối đã chọn", type="primary", disabled=not selected)
else:
    st.warning("Chưa tìm thấy domain/URL cần báo cáo trước mục backend. Kiểm tra lại ghi chú rồi bấm Phân tích ghi chú.")
    check = False

backend_table = st.container()

pending_row = st.session_state.pop("lead_triage_pending_row", None)
row_target = pending_row[1] if pending_row and pending_row[0] == source_hash and pending_row[1] in [item["target"] for item in report_items if not _was_sent(item)] else None
if check or row_target:
    cfg = pt.load_config()
    progress = st.progress(0, text="Đang kiểm tra...")
    selected_items = [item for item in report_items if item["target"] == row_target] if row_target else [
        item for item in report_items if item["target"] in selected and not _was_sent(item)
    ]
    visible_targets = [item["target"] for item in selected_items]
    if st.session_state.get("lead_triage_visible_targets") != visible_targets:
        # A shared observation belongs to the previous review scope, not this
        # single-domain or batch check.
        st.session_state["lead_triage_additional_english"] = ""
    st.session_state["lead_triage_visible_targets"] = visible_targets
    saved_backend = st.session_state.get("lead_triage_backend_results") or {}
    with ThreadPoolExecutor(max_workers=1) as backend_pool:
        backend_future = (
            backend_pool.submit(lead_triage.check_backend_context, backend_items)
            if backend_items and (
                saved_backend.get("source") != source_hash
                or time.monotonic() - saved_backend.get("checked_monotonic", 0) >= 300
            ) else None
        )
        results = lead_triage.check_leads(
            selected_items, cfg,
            on_progress=lambda done, total: progress.progress(done / total, text=f"Đã kiểm tra {done}/{total}"),
        )
        if backend_future:
            st.session_state["lead_triage_backend_results"] = {
                "source": source_hash, "items": backend_future.result(), "checked_monotonic": time.monotonic(),
            }
    progress.empty()
    for item in selected_items:
        target_key = hashlib.sha256(item["target"].encode("utf-8")).hexdigest()[:16]
        st.session_state.pop(f"lead_triage_evidence_{source_hash[:12]}_{target_key}", None)
        st.session_state.pop(f"lead_triage_cloaking_preview_{source_hash[:12]}_{target_key}", None)
    previous = st.session_state.get("lead_triage_results") or {}
    by_target = {result["target"]: result for result in previous.get("items") or []} if previous.get("source") == source_hash else {}
    by_target.update({result["target"]: result for result in results})
    st.session_state["lead_triage_results"] = {
        "source": source_hash,
        "items": [by_target[item["target"]] for item in report_items if item["target"] in by_target],
    }
    _persist_review()

if backend_items:
    with backend_table:
        st.caption("Đầu mối backend trong ghi chú: chỉ dùng làm thông tin hạ tầng bổ sung; không tạo report riêng.")
        backend_snapshot = st.session_state.get("lead_triage_backend_results") or {}
        checked_backends = {
            item["target"]: item for item in backend_snapshot.get("items") or []
        } if backend_snapshot.get("source") == source_hash else {}
        st.dataframe(pd.DataFrame([
            {
                "Domain đầu mối": item["domain"],
                "IP A trong ghi chú": ", ".join(item["supplied_ips"]) or "—",
                "DNS A hiện tại": ", ".join((checked_backends.get(item["target"], {}).get("dns") or {}).get("ips") or []) or "Chưa xác định",
                "Đơn vị quản lý IP (RDAP)": "; ".join(
                    f"{contact['ip']}: {contact['org']}"
                    for contact in checked_backends.get(item["target"], {}).get("ip_contacts") or []
                    if contact.get("org")
                ) or "Chưa xác định",
            }
            for item in backend_items
        ]), hide_index=True, width="stretch")

saved = st.session_state.get("lead_triage_results") or {}
saved_backend = st.session_state.get("lead_triage_backend_results") or {}
backend_results = (saved_backend.get("items") or []) if saved_backend.get("source") == source_hash else []
if saved.get("source") == source_hash and saved.get("items"):
    report_targets = {item["target"] for item in report_items}
    visible_targets = st.session_state.get("lead_triage_visible_targets")
    if visible_targets is None:
        visible_targets = [result["target"] for result in saved["items"] if result["target"] in report_targets]
    visible_set = set(visible_targets)
    results = [result for result in saved["items"] if result["target"] in report_targets and result["target"] in visible_set]
else:
    results = []
if saved.get("source") == source_hash and saved.get("items") and results:
    st.subheader("Kết quả precheck")
    rows = []
    for result in results:
        dns = result.get("dns") or {}
        http = result.get("http") or {}
        cloaking = result.get("cloaking") or {}
        recipients = result.get("recipients") or []
        rows.append({
            "Domain/URL": result["target"],
            "DNS A hiện tại": ", ".join(dns.get("ips") or []) or "—",
            "So IP ghi chú": result["ip_match"],
            "HTTP": http.get("status_code") or "Chưa xác định",
            "Trang đích": http.get("final_url") or "—",
            "Email nhận báo cáo": "; ".join(
                f"{entry.get('channel')}: {entry.get('email')}"
                for entry in recipients if isinstance(entry, dict) and entry.get("email")
            ) or "Chưa tìm thấy",
            "Cloaking": cloaking.get("verdict") or "Chưa xác định",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for result in results:
        with st.expander(result["target"]):
            st.write(f"URL đã check: `{result['check_url']}`")
            if not result["full_url"]:
                st.warning("Chỉ có domain; kết quả trang chủ chưa chứng minh nội dung tại URL vi phạm.")
            if result.get("dns", {}).get("error"):
                st.caption(f"Lỗi DNS: {result['dns']['error']}")
            http = result.get("http") or {}
            if http.get("page_title"):
                st.write(f"Tiêu đề trang: {http['page_title']}")
            if http.get("error"):
                st.caption(f"Lỗi HTTP: {http['error']}")
            for key in ("http_error", "recipients_error", "cloaking_error"):
                if result.get(key):
                    st.caption(f"{key}: {result[key]}")
            for contact in result.get("ip_contacts") or []:
                st.write(
                    f"IP `{contact['ip']}`: {contact.get('org') or 'Chưa rõ tổ chức'}; "
                    f"abuse: {contact.get('abuse_email') or 'chưa tìm thấy'}"
                )
            st.caption("RDAP cho biết bên quản lý dải IP; DNS/IP không tự chứng minh đây là origin hoặc backend chung.")

    st.subheader("Nội dung báo cáo để duyệt")
    st.caption(
        "Report dùng nội dung Domain Worker, bổ sung dữ kiện mạng/hosting đã kiểm tra. "
        "Ghi chú nội bộ được giữ ở phần precheck."
    )
    additional_english = st.text_area(
        "Mô tả bổ sung đã xác minh để đưa vào report (English)",
        key="lead_triage_additional_english",
        height=100,
        help="Chỉ ghi điều đã quan sát được, ví dụ URL chuyển hướng và nội dung hiển thị."
    )
    cfg = pt.load_config()
    accounts = [account for account in cfg.get("smtp_accounts") or [] if account.get("username")]
    account_names = list(dict.fromkeys(str(account["username"]) for account in accounts))
    if st.session_state.get("lead_triage_sender_account") not in [None, "Chọn tài khoản"] + account_names:
        st.session_state.pop("lead_triage_sender_account", None)
    account_name = st.selectbox("Tài khoản gửi", ["Chọn tài khoản"] + account_names,
                                key="lead_triage_sender_account")
    selected_account = next((account for account in accounts if account["username"] == account_name), None)
    if not accounts:
        st.warning("Chưa cấu hình tài khoản SMTP; có thể tải draft để duyệt, chưa thể gửi.")
    for result in results:
        with st.container(border=True):
            st.markdown(f"**{result['target']}**")
            target_key = hashlib.sha256(result["target"].encode("utf-8")).hexdigest()[:16]
            needs_cloaking_review = any(
                domain_worker._cloaking_requires_review(cloaking)
                for cloaking in (result.get("cloaking") or {}, result.get("worker_cloaking") or {})
            )
            if needs_cloaking_review:
                lead_triage_cloaking_ui.render_review(
                    result, cfg, selected_account, raw, backend_results, additional_english,
                    f"lead_triage_evidence_{source_hash[:12]}_{target_key}",
                    f"lead_triage_cloaking_preview_{source_hash[:12]}_{target_key}",
                    persist=_persist_review, show_preview=_show_email_preview,
                )
                _persist_review()
                continue
            if not result.get("worker_draft"):
                if st.button("Tạo nội dung report Domain Worker", key=f"lead_triage_report_{target_key}"):
                    with st.spinner("Đang tạo report bằng pipeline Domain Worker..."):
                        lead_triage.prepare_worker_report(result, cfg)
                    _persist_review()
                if not result.get("worker_draft"):
                    st.info("Precheck đã xong. Bấm Tạo nội dung report Domain Worker để chuẩn bị draft cho đầu mối này.")
                    if result.get("worker_report_error"):
                        st.warning(result["worker_report_error"])
                    _persist_review()
                    continue
            evidence_key = f"lead_triage_evidence_{source_hash[:12]}_{target_key}"
            evidence = st.session_state.get(evidence_key) or {}
            if st.button("Chụp URL nguồn + URL đích từ DOM", key=f"lead_triage_capture_{target_key}", icon=":material/photo_camera:"):
                with st.spinner("Đang tạo ảnh DOM bằng luồng Phản hồi NCC..."):
                    evidence = provider_replies.capture_dom_link_evidence(result["check_url"], result["domain"])
                st.session_state[evidence_key] = evidence
                _persist_review()
            attachments = lead_triage.validated_dom_attachments(evidence, result["check_url"])
            if attachments:
                st.success("Đã xác thực hai ảnh nguồn–đích từ DOM và manifest; có thể duyệt để gửi.")
                for path in attachments[:-1]:
                    st.image(path, caption=os.path.basename(path), width=240)
            elif evidence.get("error"):
                st.warning(f"Chụp DOM chưa thành công: {evidence['error']}")
            else:
                st.info("Cần chụp thành công hai ảnh nguồn–đích từ DOM để mở nút gửi. Draft vẫn có thể tải xuống để xem trước.")
            needs_cloaking_review = any(
                domain_worker._cloaking_requires_review(cloaking)
                for cloaking in (result.get("cloaking") or {}, result.get("worker_cloaking") or {})
            )
            if needs_cloaking_review:
                st.warning("Precheck cloaking cần duyệt riêng; không gửi từ menu đầu mối này.")
            if st.button("🔎 Kiểm tra lại email nhận (Domain Worker)", key=f"lead_triage_recipient_refresh_{target_key}"):
                with st.spinner("Đang tra lại email nhận theo Domain Worker..."):
                    lead_triage.refresh_worker_recipients(result)
                _persist_review()
            if result.get("recipients_error"):
                st.warning(f"Không tra lại được email nhận: {result['recipients_error']}")
            try:
                draft = lead_triage.build_investigation_draft(
                    result, raw, additional_english=additional_english,
                    evidence=evidence if attachments else None,
                    backend_results=backend_results,
                )
            except ValueError as exc:
                st.error(str(exc))
                _persist_review()
                continue
            recipient = st.text_input(
                "Email nhận report (chỉnh sau khi xác minh)",
                value=draft["to"],
                key=f"lead_triage_recipient_{target_key}_{hashlib.sha256(draft['to'].encode()).hexdigest()[:12]}",
            ).strip()
            st.caption(f"Nguồn địa chỉ nhận: {draft['recipient_source']}")
            if result.get("recipients_checked_at"):
                st.caption(f"Tra lại lúc (UTC): {result['recipients_checked_at']}")
            for option in result.get("recipients") or []:
                if option.get("email"):
                    st.caption(f"{option.get('channel') or 'khác'}: {option['email']}")
            preview_account = dict(selected_account or {"username": cfg.get("contact_email")})
            for signature_field in (
                "contact_name", "company_name", "signature_role",
                "business_registration_no", "signature_logo",
            ):
                if not preview_account.get(signature_field):
                    preview_account[signature_field] = cfg.get(signature_field)
            english_body = pt.personalize_email_body(draft["body"], cfg, preview_account)
            vietnamese_body = draft["body_vi"]
            vietnamese_body = pt.personalize_email_body(
                vietnamese_body.replace("Trân trọng,", "Regards,"), cfg, preview_account,
            ).replace("Regards,", "Trân trọng,")
            signature_logo = pt._signature_logo_path(preview_account)
            english, vietnamese = st.columns(2)
            with english:
                with st.container(border=True):
                    st.markdown("**English — nội dung sẽ gửi**")
                    st.write(f"**Subject:** {draft['subject']}")
                    if not selected_account:
                        st.caption("Chữ ký chung; chọn tài khoản gửi để xem đúng người ký.")
                    _show_email_preview(english_body, signature_logo)
                    with st.expander("Xem / sao chép nội dung văn bản tiếng Anh"):
                        st.text_area("Bản tiếng Anh", value=english_body, height=300, disabled=True,
                                     key=f"lead_triage_english_{target_key}_{hashlib.sha256(english_body.encode()).hexdigest()[:12]}")
            with vietnamese:
                with st.container(border=True):
                    st.markdown("**Tiếng Việt — bản đối chiếu, không gửi**")
                    st.write(f"**Tiêu đề:** {draft['subject_vi']}")
                    _show_email_preview(vietnamese_body, signature_logo, vietnamese=True)
                    with st.expander("Xem / sao chép nội dung văn bản tiếng Việt"):
                        st.text_area("Bản tiếng Việt", value=vietnamese_body, height=300, disabled=True,
                                     key=f"lead_triage_vietnamese_{target_key}_{hashlib.sha256(vietnamese_body.encode()).hexdigest()[:12]}")
            if not recipient:
                st.warning("Domain Worker chưa tìm thấy email nhận hợp lệ cho kênh report này; hãy xác minh trước khi gửi.")
            elif not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", recipient):
                st.warning("Email nhận chưa đúng định dạng; hãy kiểm tra lại trước khi gửi.")
            st.download_button(
                "Tải draft .txt",
                data=f"To: {recipient}\nSubject: {draft['subject']}\n\n{english_body}\n",
                file_name=f"{result['domain']}_backend_report.txt",
                mime="text/plain",
                key=f"lead_triage_download_{target_key}",
            )
            try:
                preview_fingerprint = lead_triage.delivery_fingerprint(
                    result["check_url"], recipient, draft["subject"], english_body,
                    account_name, attachments,
                )
            except OSError:
                preview_fingerprint = ""
                attachments = []
            approval_digest = preview_fingerprint[:16]
            approved = st.checkbox(
                "Tôi đã đối chiếu nội dung tố cáo tiếng Anh, ảnh và email nhận; xác nhận gửi report này",
                key=f"lead_triage_approved_{target_key}_{approval_digest}",
            )
            sent_key = f"lead_triage_sent_{target_key}_{approval_digest}"
            if st.session_state.get(sent_key):
                st.info("Delivery này đã được gửi trong phiên hiện tại.")
            if st.session_state.get(f"lead_triage_log_warning_{source_hash[:12]}_{target_key}"):
                st.warning("Đã gửi nhưng chưa ghi được nhật ký; kiểm tra Sent trước khi thử lại.")
            can_send = bool(
                selected_account and approved and attachments and not needs_cloaking_review
                and not result.get("recipients_error")
                and not st.session_state.get(sent_key)
                and re.fullmatch(r"[^\s@,]+@[^\s@,]+\.[^\s@,]+", recipient)
            )
            if st.button("Gửi report", key=f"lead_triage_send_{target_key}",
                         type="primary", disabled=not can_send):
                proxy_list = cfg.get("smtp_proxies") or []
                account_index = accounts.index(selected_account)
                proxy = proxy_list[account_index % len(proxy_list)] if proxy_list else None
                outcome = lead_triage.send_investigation_draft(
                    target=result["check_url"], recipient=recipient, subject=draft["subject"],
                    body=english_body, account=preview_account, proxy=proxy,
                    attachments=attachments, evidence=evidence,
                    expected_fingerprint=preview_fingerprint,
                    draft_file=draft["draft_file"],
                )
                if outcome.get("already_sent"):
                    st.session_state[sent_key] = True
                    delivery_status[result["target"]] = True
                    st.session_state["lead_triage_delivery_status"] = delivery_status
                    _persist_review()
                    st.rerun()
                elif outcome.get("success"):
                    st.session_state[sent_key] = True
                    delivery_status[result["target"]] = True
                    st.session_state["lead_triage_delivery_status"] = delivery_status
                    if outcome.get("log_error"):
                        st.session_state[f"lead_triage_log_warning_{source_hash[:12]}_{target_key}"] = True
                    _persist_review()
                    st.rerun()
                else:
                    st.error(f"Gửi chưa thành công: {outcome.get('error') or 'lỗi không xác định'}")
    st.caption("Report dùng nội dung Domain Worker, bổ sung thông tin backend và gửi bản tiếng Anh kèm đúng ảnh đã duyệt.")

_persist_review()

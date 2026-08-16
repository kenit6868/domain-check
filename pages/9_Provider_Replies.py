"""Inbox and reply-draft UI for registrar/provider responses."""

import os
import sys
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st
import phishing_toolkit as pt
from provider_replies import (
    ACTION_REQUIRED_TYPES, build_reply, clear_mail_cache, download_evidence_image,
    extract_reply_context, fetch_provider_mail,
    instructed_reply_address, is_delivery_failure, load_mail_cache, mark_mails_seen,
    received_datetime, save_mail_cache,
    send_threaded_reply,
)

st.set_page_config(page_title="Phản hồi NCC", page_icon="📨", layout="wide")
st.title("📨 Phản hồi NCC")
st.caption("Đọc inbox, nhận diện yêu cầu và tạo phản hồi đúng thread. Không tự gửi khi chưa được bạn duyệt.")

cfg = pt.load_config()
accounts = [a for a in cfg.get("smtp_accounts", []) if a.get("imap_host") or a.get("host")]
if not accounts:
    st.warning("Chưa có tài khoản IMAP. Hãy cấu hình imap_host và imap_port trong config.ini."); st.stop()

labels = [a.get("username", f"Tài khoản {i + 1}") for i, a in enumerate(accounts)]
c1, c2, c3 = st.columns([3, 1, 1])
with c1: selected_label = st.selectbox("Hộp thư", labels)
with c2: limit = st.number_input("Số mail gần nhất", 10, 500, 100, 10)
with c3: unread_only = st.checkbox("Chỉ mail chưa đọc")
account = accounts[labels.index(selected_label)]
account_name = account.get("username", selected_label)
if st.session_state.get("provider_cache_account") != account_name:
    st.session_state.provider_cache_account = account_name
    st.session_state.provider_mails = load_mail_cache(account_name)
    st.session_state.show_action_required = False

d1, d2 = st.columns(2)
with d1: date_from = st.date_input("Từ ngày", value=date.today() - timedelta(days=30))
with d2: date_to = st.date_input("Đến ngày", value=date.today())
if date_from > date_to:
    st.error("Từ ngày không được lớn hơn Đến ngày.")

sync_col, clear_col, cache_col = st.columns([1, 1, 3])
with clear_col:
    if st.button("Clear cache", help="Xóa cache email của hộp thư đang chọn"):
        clear_mail_cache(account_name)
        st.session_state.provider_mails = []
        st.session_state.show_action_required = False
        st.rerun()
with cache_col:
    cached_count = len(st.session_state.get("provider_mails", []))
    st.caption(f"Cache hiện có: {cached_count} email. Cache được giữ khi F5 và không chứa mật khẩu.")

with sync_col:
    sync_clicked = st.button("Đồng bộ Inbox theo ngày", type="primary", disabled=date_from > date_to)
if sync_clicked:
    try:
        progress_bar = st.progress(0, text="Đang chuẩn bị đọc inbox...")
        live_table = st.empty()
        def show_progress(current, position, total):
            percent = int(position * 100 / total) if total else 100
            progress_bar.progress(percent, text=f"Đang xử lý {position}/{total} email — đã nhận diện {len(current)}")
            if current:
                live_table.dataframe(pd.DataFrame([{
                    "NCC": m.provider_label, "Domain": m.domain or "—", "Phân loại": m.request_label,
                    "Tiêu đề": m.subject, "Ngày": m.date,
                } for m in current]), width="stretch", hide_index=True)
        with st.spinner("Email sẽ xuất hiện dần trong bảng bên dưới..."):
            st.session_state.provider_mails = fetch_provider_mail(
                account, int(limit), unread_only, date_from=date_from, date_to=date_to,
                progress_callback=show_progress,
            )
            save_mail_cache(account_name, st.session_state.provider_mails)
            st.session_state.show_action_required = False
        progress_bar.progress(100, text="Đồng bộ hoàn tất")
        live_table.empty()
        st.success(f"Đã nhận diện {len(st.session_state.provider_mails)} email liên quan.")
    except Exception as exc: st.error(f"Không đọc được inbox: {exc}")

all_mails = st.session_state.get("provider_mails", [])
if not all_mails:
    st.info("Chưa có email trong khoảng ngày đã chọn. Hãy đổi ngày hoặc bấm đồng bộ lại."); st.stop()

all_filtered = []
for item in all_mails:
    received_at = received_datetime(item)
    if date_from and (received_at is None or not (date_from <= received_at.date() <= date_to)):
        continue
    all_filtered.append(item)
if not all_filtered:
    st.warning("Không có email trong khoảng ngày đã chọn."); st.stop()

st.subheader("1. Tất cả email NCC theo ngày")
all_table = pd.DataFrame([{"NCC": m.provider_label, "Domain": m.domain or "—", "Phân loại": m.request_label,
    "Cách phản hồi": {"email": "Email", "portal": "Portal", "no_reply": "Không reply", "manual": "Thủ công"}.get(m.channel, m.channel),
    "Ticket": m.ticket or "—", "Tiêu đề": m.subject, "Ngày": m.date} for m in all_filtered])
st.dataframe(all_table, width="stretch", hide_index=True)

seen_col, count_col = st.columns([1, 4])
with seen_col:
    if st.button(f"Seen all ({len(all_filtered)})", type="secondary", help="Đánh dấu đã đọc toàn bộ danh sách email phía trên"):
        result = mark_mails_seen(account, [m.uid for m in all_filtered])
        if result["success"]:
            st.success(f"Đã đánh dấu Seen {result['marked']} email.")
            if unread_only:
                seen_ids = {m.uid for m in all_filtered}
                st.session_state.provider_mails = [m for m in st.session_state.provider_mails if m.uid not in seen_ids]
                save_mail_cache(account_name, st.session_state.provider_mails)
                st.rerun()
        else:
            st.error(f"Không thể đánh dấu Seen: {result['error']}")
with count_col:
    st.caption(f"Danh sách phía trên có {len(all_filtered)} email trong khoảng ngày đã chọn.")

if st.button("Lọc thư NCC yêu cầu cung cấp bằng chứng", type="primary"):
    st.session_state.show_action_required = True
if not st.session_state.get("show_action_required", False):
    st.info("Bấm **Lọc thư NCC yêu cầu cung cấp bằng chứng** để chỉ lấy email có yêu cầu bổ sung URL, ảnh, thông tin hoặc tài liệu.")
    st.stop()

actionable = [m for m in all_filtered if m.request_type in ACTION_REQUIRED_TYPES]
st.subheader("2. Thư NCC yêu cầu phản hồi bằng chứng")
if not actionable:
    st.success("Không có email nào cần phản hồi trong danh sách theo ngày này."); st.stop()
providers = ["Tất cả"] + sorted({m.provider_label for m in actionable})
provider_filter = st.selectbox("Lọc NCC cần phản hồi", providers)
filtered = [m for m in actionable if provider_filter == "Tất cả" or m.provider_label == provider_filter]
st.caption(f"Tìm thấy {len(filtered)} email cần xử lý. Click một dòng để xem email gốc và câu trả lời riêng.")
mail_table = pd.DataFrame([{"NCC": m.provider_label, "Domain": m.domain or "—", "Yêu cầu": m.request_label,
    "Cách phản hồi": {"email": "Email", "portal": "Portal", "no_reply": "Không reply", "manual": "Thủ công"}.get(m.channel, m.channel),
    "Ticket": m.ticket or "—", "Tiêu đề": m.subject, "Ngày": m.date} for m in filtered])
st.caption("Click vào một dòng trong bảng để xem email NCC và nội dung phản hồi dự kiến.")
table_event = st.dataframe(
    mail_table, width="stretch", hide_index=True, key="provider_reply_table",
    on_select="rerun", selection_mode="single-row",
)

row_labels = [f"{m.provider_label} · {m.subject[:80]} · {m.date}" for m in filtered]
selected_rows = table_event.selection.rows
if selected_rows:
    idx = selected_rows[0]
    st.info(f"Đang xem: {row_labels[idx]}")
else:
    idx = st.selectbox(
        "Email đang xem", range(len(filtered)),
        format_func=lambda i: row_labels[i], help="Click một dòng trong bảng hoặc chọn email tại đây.",
    )
mail = filtered[idx]; left, right = st.columns(2)
if is_delivery_failure(mail.sender, mail.subject):
    st.error("Đây là thư báo gửi thất bại (bounce/MAILER-DAEMON), không phải yêu cầu của NCC. Tool đã chặn tạo và gửi phản hồi.")
    st.stop()
instructed_address = instructed_reply_address(mail.provider, mail.body)
if instructed_address:
    mail.reply_to = instructed_address
    mail.channel = "email"
with left:
    st.subheader("1. Nội dung NCC phản hồi"); st.write(f"**From:** {mail.sender}"); st.write(f"**Reply-To:** {mail.reply_to or '—'}"); st.write(f"**Ticket:** {mail.ticket or '—'}")
    st.text_area("Nội dung gốc", mail.body, height=380, disabled=True)
    if mail.urls:
        with st.expander("Các URL tìm thấy trong email"):
            for url in mail.urls: st.code(url, language=None)

with right:
    st.subheader("2. Nội dung trả lời NCC")
    if mail.risk == "approval_required": st.error("Có yếu tố pháp lý/định danh — bắt buộc duyệt thủ công và không được tạo chứng cứ giả.")
    elif mail.channel != "email": st.warning("NCC yêu cầu portal hoặc email không cho reply. Chỉ dùng draft để copy thủ công.")
    key = f"reply_{mail.account}_{mail.uid}"
    extracted = extract_reply_context(mail)
    auto_url_key = f"{key}_auto_url"
    if f"{key}_url" not in st.session_state:
        st.session_state[f"{key}_url"] = extracted["reported_url"]
    elif auto_url_key in st.session_state and st.session_state[f"{key}_url"] == st.session_state[auto_url_key]:
        st.session_state[f"{key}_url"] = extracted["reported_url"]
    st.session_state[auto_url_key] = extracted["reported_url"]
    if f"{key}_official" not in st.session_state: st.session_state[f"{key}_official"] = extracted["official_url"]
    if f"{key}_evidence" not in st.session_state: st.session_state[f"{key}_evidence"] = extracted["evidence"]
    if st.button("Lấy lại URL và bằng chứng từ email gốc", key=f"{key}_reextract"):
        st.session_state[f"{key}_url"] = extracted["reported_url"]
        st.session_state[f"{key}_official"] = extracted["official_url"]
        st.session_state[f"{key}_evidence"] = extracted["evidence"]
        st.session_state[auto_url_key] = extracted["reported_url"]
    reported_url = st.text_input("URL vi phạm đầy đủ", key=f"{key}_url")
    official_url = st.text_input("Website chính thức", key=f"{key}_official")
    evidence = st.text_area("Thông tin/bằng chứng bổ sung đã xác minh", height=120, key=f"{key}_evidence")
    attachment_key = f"{key}_screenshot_path"
    if mail.request_type == "screenshot":
        st.markdown("**Ảnh chụp NCC yêu cầu**")
        if not cfg.get("urlscan_api_key"):
            st.warning("Chưa có URLScan API key trong config.ini nên chưa thể tự chụp ảnh.")
        if st.button("Chụp ảnh bằng URLScan", key=f"{key}_capture", disabled=not bool(reported_url and cfg.get("urlscan_api_key"))):
            with st.spinner("Đang quét và tạo ảnh bằng URLScan..."):
                scan = pt.urlscan_submit_and_wait(reported_url, cfg.get("urlscan_api_key", ""), timeout=65)
                if scan.get("screenshot_url"):
                    try:
                        st.session_state[attachment_key] = download_evidence_image(scan["screenshot_url"], mail.domain or reported_url)
                        st.session_state[f"{key}_urlscan_result"] = scan.get("result_url", "")
                    except Exception as exc:
                        st.error(f"Tải ảnh thất bại: {exc}")
                else:
                    st.error(f"URLScan chưa tạo được ảnh: {scan.get('error') or scan.get('warning') or 'Không rõ lỗi'}")
        screenshot_path = st.session_state.get(attachment_key, "")
        if screenshot_path and os.path.isfile(screenshot_path):
            st.image(screenshot_path, caption="Ảnh sẽ được đính kèm email", use_container_width=True)
            st.caption(f"File: {screenshot_path}")
            result_url = st.session_state.get(f"{key}_urlscan_result", "")
            if result_url: st.link_button("Mở báo cáo URLScan", result_url)
    else:
        screenshot_path = st.session_state.get(attachment_key, "")
    details = {"reported_url": reported_url, "official_url": official_url, "evidence": evidence,
               "screenshot_attached": bool(screenshot_path and os.path.isfile(screenshot_path)),
               "urlscan_result": st.session_state.get(f"{key}_urlscan_result", ""),
               "contact_name": cfg.get("contact_name", ""), "contact_email": cfg.get("contact_email", "")}
    default_subject, default_body, warnings = build_reply(mail, details)
    subject_key, body_key = f"{key}_subject", f"{key}_body"
    if subject_key not in st.session_state: st.session_state[subject_key] = default_subject
    if body_key not in st.session_state: st.session_state[body_key] = default_body
    elif details["screenshot_attached"] and "[ATTACH VERIFIED SCREENSHOTS" in st.session_state[body_key]:
        st.session_state[body_key] = default_body
    if st.button("Tạo / cập nhật draft theo dữ liệu trên", key=f"{key}_refresh"):
        st.session_state[subject_key] = default_subject; st.session_state[body_key] = default_body
    subject = st.text_input("Subject", key=subject_key)
    body = st.text_area("Nội dung phản hồi (có thể sửa)", height=340, key=body_key)
    for warning in warnings: st.warning(warning)
    reviewed = st.checkbox("Tôi đã đọc email gốc và xác nhận nội dung phản hồi chính xác", key=f"{key}_reviewed")
    legal_ok = True
    if mail.risk == "approval_required": legal_ok = st.checkbox("Người có thẩm quyền đã duyệt nội dung pháp lý/định danh", key=f"{key}_legal")
    screenshot_ok = mail.request_type != "screenshot" or bool(screenshot_path and os.path.isfile(screenshot_path))
    can_send = reviewed and legal_ok and screenshot_ok and mail.channel == "email" and bool(mail.reply_to) and "[PLEASE" not in body
    if not can_send:
        blocked_reasons = []
        if not reviewed: blocked_reasons.append("chưa xác nhận đã kiểm tra nội dung")
        if not legal_ok: blocked_reasons.append("chưa duyệt nội dung pháp lý")
        if not screenshot_ok: blocked_reasons.append("chưa có ảnh chụp đính kèm")
        if mail.channel != "email" or not mail.reply_to: blocked_reasons.append("không có địa chỉ email phản hồi hợp lệ")
        if "[PLEASE" in body: blocked_reasons.append("draft còn placeholder cần điền")
        st.caption("Chưa thể gửi: " + "; ".join(blocked_reasons) + ".")
    if st.button("Gửi phản hồi đúng thread", type="primary", disabled=not can_send, key=f"{key}_send"):
        attachments = [screenshot_path] if screenshot_path and os.path.isfile(screenshot_path) else []
        result = send_threaded_reply(account, mail, subject, body, attachments=attachments)
        if result["success"]:
            st.success("SMTP server đã chấp nhận và gửi phản hồi.")
            if result.get("sent_copy_saved"):
                st.success("Đã lưu một bản sao trong thư mục Đã gửi trên mail server.")
            else:
                st.warning(f"Thư đã gửi nhưng chưa lưu được vào thư mục Đã gửi: {result.get('sent_copy_error') or 'không rõ lỗi'}")
        else: st.error(f"Gửi thất bại: {result['error']}")

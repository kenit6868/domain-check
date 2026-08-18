"""Inbox and reply-draft UI for registrar/provider responses."""

import os
import sys
from datetime import date, datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st
import phishing_toolkit as pt
from provider_replies import (
    ACTION_REQUIRED_TYPES, build_reply, build_reply_vi, capture_dom_link_evidence, clear_mail_cache, download_evidence_image,
    extract_reply_context, fetch_provider_mail,
    instructed_reply_address, is_delivery_failure, load_mail_cache, mark_mails_seen,
    load_reply_log, needs_reply, provider_message_vi, received_datetime, record_reply_sent,
    reply_log_key, save_mail_cache, save_uploaded_evidence, sync_sent_reply_status,
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
with c2:
    limit_input = st.text_input(
        "Số mail gần nhất", value="", placeholder="Để trống = tất cả",
        help="Để trống để lấy tất cả email trong khoảng ngày; hoặc nhập số lượng muốn lấy.",
    ).strip()
with c3: unread_only = st.checkbox("Chỉ mail chưa đọc")
limit = None
limit_error = ""
if limit_input:
    try:
        limit = int(limit_input)
        if limit <= 0:
            raise ValueError
    except ValueError:
        limit_error = "Số lượng email phải là số nguyên dương hoặc để trống để lấy tất cả."
        st.error(limit_error)
account = accounts[labels.index(selected_label)]
account_name = account.get("username", selected_label)
if st.session_state.pop("provider_reply_sent_notice", None):
    st.success("Đã gửi phản hồi và cập nhật trạng thái trong danh sách.")
if st.session_state.get("provider_cache_account") != account_name:
    st.session_state.provider_cache_account = account_name
    st.session_state.provider_mails = load_mail_cache(account_name)
    st.session_state.show_action_required = False

d1, d2 = st.columns(2)
with d1: date_from = st.date_input("Từ ngày", value=date.today())
with d2: date_to = st.date_input("Đến ngày", value=date.today())
if date_from > date_to:
    st.error("Từ ngày không được lớn hơn Đến ngày.")

# Luôn dùng thời gian nhận thư của IMAP server và đổi sang múi giờ địa phương
# trước khi lọc/hiển thị, kể cả trong bảng tiến trình đang đồng bộ.
_local_tz = datetime.now().astimezone().tzinfo

def local_received_datetime(mail):
    value = received_datetime(mail)
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=_local_tz)
    return value.astimezone(_local_tz)

def display_received_date(mail):
    value = local_received_datetime(mail)
    return value.strftime("%Y/%m/%d %H:%M:%S %z") if value else (mail.date or "—")

def mail_is_in_selected_dates(mail):
    value = local_received_datetime(mail)
    return value is not None and date_from <= value.date() <= date_to

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
    sync_clicked = st.button("Đồng bộ Inbox theo ngày", type="primary", disabled=date_from > date_to or bool(limit_error))
if sync_clicked:
    try:
        progress_bar = st.progress(0, text="Đang chuẩn bị đọc inbox...")
        def show_progress(current, position, total):
            percent = int(position * 100 / total) if total else 100
            visible = [mail for mail in current if mail_is_in_selected_dates(mail)]
            progress_bar.progress(
                percent,
                text=f"Đang quét Inbox {position}/{total} — tìm thấy {len(visible)} email đúng ngày đã chọn",
            )
        with st.spinner("Đang đồng bộ và lọc email theo ngày server..."):
            st.session_state.provider_mails = fetch_provider_mail(
                account, limit, unread_only, date_from=date_from, date_to=date_to,
                progress_callback=show_progress,
            )
            save_mail_cache(account_name, st.session_state.provider_mails)
            sent_sync = sync_sent_reply_status(
                account, st.session_state.provider_mails,
                date_from=date_from, date_to=date_to,
            )
            st.session_state.show_action_required = False
        progress_bar.progress(100, text="Đồng bộ hoàn tất")
        synced_for_day = [mail for mail in st.session_state.provider_mails if mail_is_in_selected_dates(mail)]
        sent_note = f"; nhận diện thêm {sent_sync['matched']} thư đã phản hồi từ Sent" if sent_sync.get("success") else ""
        st.success(f"Đã đồng bộ {len(synced_for_day)} email đúng ngày đã chọn{sent_note}.")
        if not sent_sync.get("success"):
            st.warning(f"Không đối soát được thư mục Đã gửi: {sent_sync.get('error')}")
    except Exception as exc: st.error(f"Không đọc được inbox: {exc}")

all_mails = st.session_state.get("provider_mails", [])
if not all_mails:
    st.info("Chưa có email trong khoảng ngày đã chọn. Hãy đổi ngày hoặc bấm đồng bộ lại."); st.stop()

all_filtered = []
for item in all_mails:
    if mail_is_in_selected_dates(item):
        all_filtered.append(item)
if not all_filtered:
    st.warning("Không có email trong khoảng ngày đã chọn."); st.stop()

st.subheader("1. Tất cả email NCC theo ngày")
all_table = pd.DataFrame([{"NCC": m.provider_label, "Domain": m.domain or "—", "Phân loại": m.request_label,
    "Cách phản hồi": {"email": "Email", "portal": "Portal", "no_reply": "Không reply", "manual": "Thủ công"}.get(m.channel, m.channel),
    "Ticket": m.ticket or "—", "Tiêu đề": m.subject, "Ngày": display_received_date(m)} for m in all_filtered])
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

if st.button("Lọc email cần phản hồi", type="primary"):
    reply_candidates = [mail for mail in all_filtered if needs_reply(mail)]
    with st.spinner("Đang đối soát trạng thái với thư mục Đã gửi để tránh gửi trùng..."):
        sent_filter_sync = sync_sent_reply_status(
            account, reply_candidates, date_from=date_from, date_to=date_to,
        )
    if sent_filter_sync.get("success"):
        st.success(
            f"Đã kiểm tra thư mục Đã gửi; cập nhật thêm {sent_filter_sync['matched']} email đã phản hồi."
        )
    else:
        st.error(
            "Không đối soát được thư mục Đã gửi nên chưa mở danh sách phản hồi, "
            f"để tránh gửi trùng: {sent_filter_sync.get('error')}"
        )
        st.stop()
    st.session_state.show_action_required = True
if not st.session_state.get("show_action_required", False):
    st.info("Bấm **Lọc email cần phản hồi** để chỉ lấy thư NCC yêu cầu bổ sung URL, ảnh, thông tin hoặc bằng chứng. Thư Cloudflare chỉ xác nhận đã chuyển tiếp báo cáo sẽ được bỏ qua.")
    st.stop()

actionable = [m for m in all_filtered if needs_reply(m)]
reply_log = load_reply_log()
st.subheader("2. Thư NCC yêu cầu phản hồi bằng chứng")
if not actionable:
    st.success("Không có email nào cần phản hồi trong danh sách theo ngày này."); st.stop()
providers = ["Tất cả"] + sorted({m.provider_label for m in actionable})
provider_filter = st.selectbox("Lọc NCC cần phản hồi", providers)
filtered = [m for m in actionable if provider_filter == "Tất cả" or m.provider_label == provider_filter]
sent_count = sum(reply_log_key(item) in reply_log for item in filtered)
st.caption(
    f"Tìm thấy {len(filtered)} email: {len(filtered) - sent_count} chưa phản hồi, "
    f"{sent_count} đã phản hồi. Thứ tự và STT được giữ nguyên sau khi gửi."
)
mail_table = pd.DataFrame([{"STT": position, "Trạng thái": "✅ Đã phản hồi" if reply_log_key(m) in reply_log else "Chưa phản hồi",
    "NCC": m.provider_label, "Domain": m.domain or "—", "Yêu cầu": m.request_label,
    "Cách phản hồi": {"email": "Email", "portal": "Portal", "no_reply": "Không reply", "manual": "Thủ công"}.get(m.channel, m.channel),
    "Ticket": m.ticket or "—", "Tiêu đề": m.subject, "Ngày": display_received_date(m)} for position, m in enumerate(filtered, start=1)])
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
sent_record = reply_log.get(reply_log_key(mail))
if sent_record:
    st.warning(
        f"Email này đã được phản hồi lúc {sent_record.get('sent_at', 'không rõ thời gian')} "
        f"tới {sent_record.get('recipient', mail.reply_to)}. Mặc định hệ thống không cho gửi lại."
    )
if is_delivery_failure(mail.sender, mail.subject):
    st.error("Đây là thư báo gửi thất bại (bounce/MAILER-DAEMON), không phải yêu cầu của NCC. Tool đã chặn tạo và gửi phản hồi.")
    st.stop()
instructed_address = instructed_reply_address(mail.provider, mail.body)
if instructed_address:
    mail.reply_to = instructed_address
    mail.channel = "email"
with left:
    st.subheader("1. Nội dung NCC phản hồi"); st.write(f"**From:** {mail.sender}"); st.write(f"**Reply-To:** {mail.reply_to or '—'}"); st.write(f"**Ticket:** {mail.ticket or '—'}")
    original_col, translated_col = st.columns(2)
    with original_col:
        st.text_area("Nội dung gốc", mail.body, height=430, disabled=True)
    with translated_col:
        st.text_area("Bản tiếng Việt để đọc", provider_message_vi(mail), height=430, disabled=True)
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
    detected_redirect_key = f"{key}_detected_redirect"
    detected_button_key = f"{key}_detected_button"
    redirect_widget_key = f"{key}_redirect_url"
    button_widget_key = f"{key}_button_label"
    if detected_redirect_key in st.session_state:
        st.session_state[redirect_widget_key] = st.session_state.pop(detected_redirect_key)
    if detected_button_key in st.session_state:
        st.session_state[button_widget_key] = st.session_state.pop(detected_button_key)
    button_label = st.text_input(
        "Tên nút đã kiểm tra", value="Đăng ký/Đăng nhập", key=button_widget_key,
        help="Tên nút/link hiển thị trên trang mà bạn đã kiểm tra bằng DevTools.",
    )
    redirect_url = st.text_input(
        "URL trong href hoặc URL sau redirect", key=redirect_widget_key,
        help="Dán chính xác URL quan sát được trong href hoặc sau khi bấm nút. Chỉ nhập dữ liệu đã xác minh.",
    )
    official_url = st.text_input("Website chính thức", key=f"{key}_official")
    evidence = st.text_area("Thông tin/bằng chứng bổ sung đã xác minh", height=120, key=f"{key}_evidence")
    attachment_key = f"{key}_screenshot_path"
    requires_redirect_evidence = mail.provider == "cloudflare" and mail.request_type in ("clarification", "technical_evidence")
    if mail.request_type == "screenshot" or requires_redirect_evidence:
        st.markdown("**Ảnh chụp bằng chứng**")
        st.caption("Ưu tiên ảnh DOM tự động. Công cụ chỉ đọc element và href, không click nút hoặc gửi form.")
        if st.button(
            "Tạo ảnh DOM + href tự động", key=f"{key}_capture_dom",
            disabled=not bool(reported_url), type="primary",
        ):
            with st.spinner("Đang mở trang trong Chromium cô lập và tìm nút Đăng ký/Đăng nhập..."):
                dom_capture = capture_dom_link_evidence(reported_url, mail.domain or reported_url)
            if dom_capture["success"]:
                st.session_state[attachment_key] = dom_capture["path"]
                if dom_capture.get("href"):
                    st.session_state[detected_redirect_key] = dom_capture["href"]
                if dom_capture.get("label"):
                    st.session_state[detected_button_key] = dom_capture["label"]
                st.session_state[f"{key}_capture_notice"] = "Đã tạo ảnh DOM và tự điền href từ element."
                st.rerun()
            else:
                st.error(f"Không tạo được ảnh DOM: {dom_capture['error']}")
        if st.session_state.pop(f"{key}_capture_notice", None):
            st.success("Đã tạo ảnh DOM và tự điền href từ element.")
        uploaded_image = st.file_uploader(
            "Upload ảnh chụp sau khi bấm nút đăng ký / kiểm tra redirect",
            type=["png", "jpg", "jpeg"], key=f"{key}_evidence_upload",
        )
        if uploaded_image is not None and st.button("Dùng ảnh upload này", key=f"{key}_save_upload"):
            try:
                st.session_state[attachment_key] = save_uploaded_evidence(
                    uploaded_image.name, uploaded_image.getvalue(), mail.domain or reported_url,
                )
                st.success("Đã lưu ảnh để đính kèm vào email phản hồi.")
            except Exception as exc:
                st.error(f"Không lưu được ảnh: {exc}")
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
    details = {"reported_url": reported_url, "button_label": button_label, "redirect_url": redirect_url, "official_url": official_url, "evidence": evidence,
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
    english_col, vietnamese_col = st.columns(2)
    with english_col:
        body = st.text_area("Nội dung phản hồi tiếng Anh (có thể sửa)", height=420, key=body_key)
    with vietnamese_col:
        st.text_area(
            "Bản tiếng Việt để kiểm tra (không gửi)", build_reply_vi(mail, details),
            height=420, disabled=True,
        )
    for warning in warnings: st.warning(warning)
    reviewed = st.checkbox("Tôi đã đọc email gốc và xác nhận nội dung phản hồi chính xác", key=f"{key}_reviewed")
    resend_ok = True
    if sent_record:
        resend_ok = st.checkbox(
            "Tôi xác nhận muốn gửi lại email đã được phản hồi trước đó",
            key=f"{key}_confirm_resend",
        )
    legal_ok = True
    if mail.risk == "approval_required": legal_ok = st.checkbox("Người có thẩm quyền đã duyệt nội dung pháp lý/định danh", key=f"{key}_legal")
    screenshot_ok = (mail.request_type != "screenshot" and not requires_redirect_evidence) or bool(screenshot_path and os.path.isfile(screenshot_path))
    redirect_ok = not requires_redirect_evidence or bool(redirect_url)
    can_send = reviewed and resend_ok and legal_ok and screenshot_ok and redirect_ok and mail.channel == "email" and bool(mail.reply_to) and "[PLEASE" not in body
    if not can_send:
        blocked_reasons = []
        if not reviewed: blocked_reasons.append("chưa xác nhận đã kiểm tra nội dung")
        if not resend_ok: blocked_reasons.append("email đã phản hồi; chưa xác nhận gửi lại")
        if not legal_ok: blocked_reasons.append("chưa duyệt nội dung pháp lý")
        if not screenshot_ok: blocked_reasons.append("chưa có ảnh chụp đính kèm")
        if not redirect_ok: blocked_reasons.append("chưa có URL href/redirect đã xác minh")
        if mail.channel != "email" or not mail.reply_to: blocked_reasons.append("không có địa chỉ email phản hồi hợp lệ")
        if "[PLEASE" in body: blocked_reasons.append("draft còn placeholder cần điền")
        st.caption("Chưa thể gửi: " + "; ".join(blocked_reasons) + ".")
    if st.button("Gửi phản hồi đúng thread", type="primary", disabled=not can_send, key=f"{key}_send"):
        attachments = [screenshot_path] if screenshot_path and os.path.isfile(screenshot_path) else []
        proxies = cfg.get("smtp_proxies", [])
        proxy_str = proxies[0] if proxies else None
        with st.spinner("Đang gửi phản hồi và lưu bản sao vào thư mục Đã gửi..."):
            result = send_threaded_reply(
                account, mail, subject, body,
                attachments=attachments, proxy_str=proxy_str,
            )
        if result["success"]:
            record_reply_sent(mail, subject, mail.reply_to)
            if result.get("sent_copy_saved"):
                st.session_state["provider_reply_sent_notice"] = "sent-and-saved"
            else:
                st.warning(f"Thư đã gửi nhưng chưa lưu được vào thư mục Đã gửi: {result.get('sent_copy_error') or 'không rõ lỗi'}")
                st.session_state["provider_reply_sent_notice"] = "sent"
            st.rerun()
        else: st.error(f"Gửi thất bại: {result['error']}")

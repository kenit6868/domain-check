"""pages/1_Check_Domain.py — Kiểm tra đầy đủ 1 domain, tương đương CLI `check`.

Gọi thẳng phishing_toolkit.run_check() — không viết lại pipeline SSL/WHOIS/
VirusTotal/Safe Browsing ở đây, để kết quả luôn khớp với CLI.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

import phishing_toolkit as pt
from email_send_ui import render_send_email_ui, render_send_all_ui

st.set_page_config(page_title="Check Domain", page_icon="🔍", layout="wide")
st.title("🔍 Check Domain")
st.caption("SSL issuer/serial, WHOIS, Cloudflare, VirusTotal, Google Safe Browsing — ghi log + sinh hướng dẫn báo cáo (email hoặc web form)")


def show_dict(d: dict):
    if not d:
        st.write("(không có dữ liệu)")
        return
    rows = [(k, "" if v is None else v) for k, v in d.items()]
    st.table(pd.DataFrame(rows, columns=["Trường", "Giá trị"]).astype(str))


with st.form("check_domain_form"):
    target = st.text_input("Domain hoặc URL cần kiểm tra", placeholder="vd: chass.ru.com")
    submit_vt = st.checkbox(
        "Submit lên VirusTotal nếu chưa có dữ liệu",
        help="Tương ứng cờ --submit của CLI. Chỉ submit khi VirusTotal chưa từng quét domain này.",
    )
    go = st.form_submit_button("Kiểm tra", type="primary")

# Kết quả được lưu vào st.session_state thay vì chỉ tồn tại trong biến cục bộ của lần chạy
# script khi form submit — CẦN THIẾT vì các widget bên dưới (checkbox xác nhận, nút gửi email
# trong render_send_email_ui) nằm NGOÀI st.form, nên mỗi lần người dùng tick/bấm chúng sẽ kích
# hoạt Streamlit chạy lại toàn bộ script từ đầu. Ở lần chạy lại đó, `go` (giá trị của
# st.form_submit_button) luôn là False vì form không được submit lại — nếu phần hiển thị kết quả
# vẫn nằm trong `if go:` thì toàn bộ kết quả (và cả chính checkbox/nút vừa bấm) sẽ biến mất ngay
# lập tức, chỉ còn lại form trống. Lưu vào session_state để phần hiển thị đọc lại được ở MỌI lần
# chạy lại, không chỉ lần vừa submit.
if go:
    if not target.strip():
        st.warning("Nhập domain trước khi kiểm tra.")
    else:
        cfg = pt.load_config()
        with st.spinner(f"Đang kiểm tra {target}..."):
            result = pt.run_check(target, submit_vt, cfg)
        st.session_state["check_domain_result"] = result
        st.session_state["check_domain_cfg"] = cfg

if "check_domain_result" in st.session_state:
    result = st.session_state["check_domain_result"]
    cfg = st.session_state["check_domain_cfg"]

    domain = result["domain"]
    cert = result["cert"]
    who = result["whois"]
    cf = result["cloudflare"]
    cdn_detected = result["cdn_detected"]
    origin_ip_scan = result["origin_ip_scan"]
    vt = result["virustotal"]
    gsb = result["safebrowsing"]
    ca_note = result["ca_note"]
    http_check = result.get("http_check", {})
    domain_age_days = result.get("domain_age_days")
    mx_records = result.get("mx_records", {})
    urlscan_auto = result.get("urlscan", {})

    st.success(f"Đã kiểm tra xong: {domain}")

    # ── Metrics row ───────────────────────────────────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("VT Malicious", vt.get("malicious", "N/A"))
    col2.metric("VT Suspicious", vt.get("suspicious", "N/A"))
    col3.metric("Cloudflare", "Có" if cf else "Không rõ / Không")
    gsb_label = "Có" if gsb.get("flagged") else ("N/A" if ("skipped" in gsb or "error" in gsb) else "Không")
    col4.metric("Google Safe Browsing", gsb_label)

    # Domain age — highlight đỏ nếu < 30 ngày (mới đăng ký → dấu hiệu phishing)
    if domain_age_days is not None:
        if domain_age_days < 30:
            col5.metric("Tuổi domain", f"{domain_age_days} ngày 🚨")
        elif domain_age_days < 180:
            col5.metric("Tuổi domain", f"{domain_age_days} ngày ⚠️")
        else:
            years = domain_age_days // 365
            months = (domain_age_days % 365) // 30
            label = f"{years}y {months}m" if years else f"{months} tháng"
            col5.metric("Tuổi domain", label)
    else:
        col5.metric("Tuổi domain", "N/A")

    rep = result["reputation"]
    rep_text = "**Uy tín (tổng hợp VirusTotal + Safe Browsing):** " + rep["label"]
    if rep["reasons"]:
        rep_text += "\n\n" + "\n".join(f"- {r}" for r in rep["reasons"])
    if rep["verdict"] == "flagged":
        st.error(rep_text)
    elif rep["verdict"] == "suspicious":
        st.warning(rep_text)
    elif rep["verdict"] == "unknown":
        st.info(rep_text)
    else:
        st.success(rep_text)
    if rep["verdict"] in ("clean", "unknown"):
        st.caption(
            "Chưa bị gắn cờ KHÔNG có nghĩa là an toàn — domain mới/ít traffic thường chưa kịp "
            "bị cộng đồng báo cáo. Vẫn cần xác minh nội dung trang thủ công."
        )

    # ── A1: HTTP Check ────────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("**🌐 HTTP Check — trang còn sống không?**")
        if "error" in http_check:
            st.warning(f"⚠️ {http_check['error']}")
        else:
            hc1, hc2, hc3, hc4 = st.columns(4)
            status = http_check.get("status_code")
            if status == 200:
                hc1.metric("HTTP Status", f"✅ {status} OK")
            elif status:
                hc1.metric("HTTP Status", f"⚠️ {status}")
            else:
                hc1.metric("HTTP Status", "❌ Down / N/A")

            title = http_check.get("page_title") or "—"
            hc2.metric("Page Title", title[:40] + "…" if len(title) > 40 else title)

            has_pw = http_check.get("has_password_input")
            hc3.metric("Login form", "🚨 Có input password" if has_pw else "Không phát hiện")

            if http_check.get("redirected") and http_check.get("final_url"):
                hc4.metric("Redirect", "Có")
                st.caption(f"→ Redirect tới: `{http_check['final_url']}`")
            else:
                hc4.metric("Redirect", "Không")

            if has_pw:
                st.error("🚨 Trang có form nhập password — dấu hiệu rõ ràng của login page giả mạo. Xác minh ảnh chụp màn hình trước khi report.")

    # ── B2: MX Record check ───────────────────────────────────────────────────
    mx_recs = mx_records.get("records", [])
    mx_providers = mx_records.get("providers", [])
    if "error" in mx_records:
        with st.container(border=True):
            st.markdown("**📧 MX Records — khả năng gửi email phishing**")
            st.caption(f"⚠️ Lỗi tra MX: {mx_records['error']}")
    elif "skipped" in mx_records:
        pass  # dnspython không có — bỏ qua block này
    elif mx_recs:
        with st.container(border=True):
            st.markdown("**📧 MX Records — domain này CÓ cấu hình email**")
            st.warning(
                f"Domain có **{len(mx_recs)} MX record(s)** → có khả năng đang được dùng để **gửi phishing email**. "
                "Báo cáo thêm tới abuse team của mail provider bên dưới."
            )
            mx_c1, mx_c2 = st.columns(2)
            with mx_c1:
                st.markdown("**MX Records:**")
                for rec in mx_recs:
                    st.code(f"Priority {rec['priority']:3d}: {rec['host']}", language=None)
            with mx_c2:
                if mx_providers:
                    st.markdown("**Mail Providers được nhận diện:**")
                    for p in mx_providers:
                        st.markdown(f"**{p['name']}**")
                        if p.get("abuse_url"):
                            st.link_button(f"🔗 Report abuse {p['name']}", p["abuse_url"])
                        if p.get("email"):
                            st.markdown(f"Email: `{p['email']}`")
                else:
                    st.info("Không nhận diện được provider — tra MX host thủ công để tìm abuse contact.")
    else:
        with st.container(border=True):
            st.markdown("**📧 MX Records**")
            st.success("✅ Không có MX record — domain này không được cấu hình để gửi email.")

    tab_whois, tab_ssl, tab_vt, tab_gsb = st.tabs(["📋 WHOIS", "🔒 SSL Certificate", "🦠 VirusTotal", "🛡️ Safe Browsing"])
    with tab_whois:
            if "error" in who:
                st.error(who["error"])
            else:
                show_dict(who)
    with tab_ssl:
        if "error" in cert:
            st.error(cert["error"])
        else:
            show_dict(cert)
    with tab_vt:
        if "error" in vt:
            st.error(vt["error"])
        elif "skipped" in vt:
            st.warning(vt["skipped"])
        else:
            show_dict(vt)
        if result["virustotal_submit"]:
            st.info(result["virustotal_submit"])
    with tab_gsb:
        if "error" in gsb:
            st.error(gsb["error"])
        elif "skipped" in gsb:
            st.warning(gsb["skipped"])
        else:
            show_dict(gsb)

    # ── Helper: tìm draft file theo tên miền trong list drafts đã sinh ──────────
    def find_draft(suffix):
        """Tìm file draft khớp suffix trong result['drafts'], trả None nếu không có."""
        for p in (result.get("drafts") or []):
            if os.path.basename(p).endswith(suffix):
                return p
        return None

    def show_draft_block(draft_path, key_prefix_extra):
        """Hiển thị nội dung draft + nút gửi email trong 1 khối gọn."""
        if not draft_path or not os.path.isfile(draft_path):
            st.caption("_(file draft chưa được tạo hoặc không tìm thấy)_")
            return
        with open(draft_path, encoding="utf-8") as f:
            content = f.read()
        st.code(content, language=None)
        render_send_email_ui(draft_path, cfg, key_prefix=f"check_{key_prefix_extra}")

    # ── Section chính ─────────────────────────────────────────────────────────
    st.divider()

    # ── A3: URLScan.io ────────────────────────────────────────────────────────
    # Auto-scan đã chạy song song trong run_check() — hiển thị kết quả ngay.
    # Giữ nút retry thủ công cho trường hợp auto-scan thất bại (no API key, DNS error, timeout).
    urlscan_key = f"urlscan_{domain}"
    _auto_done = urlscan_auto.get("status") == "done"
    _auto_has_url = bool(urlscan_auto.get("screenshot_url"))
    _auto_error = urlscan_auto.get("error") or urlscan_auto.get("warning")
    _manual_result = st.session_state.get(f"{urlscan_key}_result")
    _active_result = _manual_result if (_manual_result and _manual_result.get("status") == "done") else (urlscan_auto if _auto_done else None)

    with st.expander("📸 URLScan.io — Screenshot & Phân tích trang", expanded=(_auto_done or bool(_active_result))):
        if _auto_done and _active_result:
            screenshot_url = _active_result.get("screenshot_url")
            result_url = _active_result.get("result_url")
            us1, us2, us3 = st.columns(3)
            us1.metric("URLScan Verdict", "🚨 Malicious" if _active_result.get("malicious") else "✅ Not flagged")
            us2.metric("Score", _active_result.get("score", 0))
            us3.metric("Page IP", _active_result.get("page_ip") or "N/A")
            tags = _active_result.get("tags") or []
            brands = _active_result.get("brands") or []
            if tags:
                st.markdown(f"**Tags:** {', '.join(tags)}")
            if brands:
                st.markdown(f"**Brands detected:** {', '.join(brands)}")
            if screenshot_url:
                st.image(screenshot_url, caption="Screenshot từ URLScan.io (tự động)", use_container_width=True)
            st.caption("✅ Bằng chứng URLScan đã được tự động gắn vào tất cả draft. Link screenshot đã thay thế placeholder trong email.")
            evidence_text = (
                f"Evidence (URLScan.io):\n"
                f"- Screenshot: {screenshot_url}\n"
                f"- Full analysis: {result_url}\n"
                f"- Verdict: {'MALICIOUS' if _active_result.get('malicious') else 'Not flagged'} "
                f"(score: {_active_result.get('score', 0)})"
            )
            st.code(evidence_text, language=None)
        elif _auto_has_url and not _auto_done:
            # Timeout nhưng đã có screenshot URL (PNG khả năng vẫn render được)
            st.warning("⏳ URLScan chưa hoàn thành verdict trong thời gian cho phép — screenshot URL đã được gắn vào draft, verdict có thể xem sau.")
            st.code(f"Screenshot: {urlscan_auto['screenshot_url']}\nFull report: {urlscan_auto.get('result_url','')}", language=None)
        elif _auto_error:
            if "urlscan_api_key" not in cfg or not cfg.get("urlscan_api_key"):
                st.info("🔑 Chưa cấu hình `urlscan_api_key` trong config.ini — scan tự động bị bỏ qua.")
            else:
                st.warning(f"Auto-scan: {_auto_error}")

        # Retry thủ công (khi auto thất bại hoặc muốn scan lại)
        if not _auto_done:
            st.divider()
            st.caption("Retry thủ công:")
            col_us1, col_us2 = st.columns([1, 3])
            with col_us1:
                use_http = st.checkbox("Dùng http://", key=f"urlscan_http_{domain}")
                if st.button("🔍 Submit URLScan", key=f"urlscan_submit_{domain}"):
                    with st.spinner("Đang submit và chờ kết quả (~30 giây)..."):
                        retry_res = pt.urlscan_submit_and_wait(domain, cfg.get("urlscan_api_key", ""), timeout=65)
                    if "warning" in retry_res:
                        st.warning(retry_res["warning"])
                    elif "error" in retry_res:
                        st.error(retry_res["error"])
                    else:
                        st.session_state[f"{urlscan_key}_result"] = retry_res
                        if retry_res.get("screenshot_url"):
                            pt.append_urlscan_evidence_to_drafts(result.get("drafts", []), retry_res)
                        st.rerun()
            with col_us2:
                pending_data = st.session_state.get(urlscan_key)
                if pending_data and pending_data.get("scan_id"):
                    st.info(f"Link kết quả: {pending_data.get('result_url')}")

    # ── B3: Wayback Machine Archive ───────────────────────────────────────────
    wayback_key = f"wayback_{domain}"
    _wayback_done = wayback_key in st.session_state and "archive_url" in st.session_state[wayback_key]
    with st.expander("🗄️ Wayback Machine — Lưu bằng chứng tĩnh", expanded=_wayback_done):
        st.caption("Archive trang phishing ngay bây giờ — link tĩnh tồn tại mãi kể cả khi domain bị gỡ. Nên archive trước khi report.")
        wb_col1, wb_col2 = st.columns([1, 3])
        with wb_col1:
            if st.button("📦 Archive trên Wayback Machine", key=f"wayback_submit_{domain}", type="primary"):
                with st.spinner("Đang gửi yêu cầu archive (~15–30 giây)..."):
                    wb_res = pt.wayback_archive(domain)
                if "error" in wb_res:
                    st.error(f"❌ {wb_res['error']}")
                    if wb_res.get("manual_url"):
                        st.link_button("🔍 Tra snapshot thủ công trên Wayback", wb_res["manual_url"])
                else:
                    st.session_state[wayback_key] = wb_res
                    if wb_res.get("status") == "existing_snapshot":
                        st.success("✅ Tìm thấy snapshot có sẵn!")
                    elif wb_res.get("status") == "save_failed":
                        st.warning("⚠️ Archive lỗi — link ước tính bên dưới (cần verify thủ công).")
                    else:
                        st.success("✅ Archive thành công!")

        with wb_col2:
            wb_data = st.session_state.get(wayback_key)
            if wb_data and "archive_url" in wb_data:
                archive_url = wb_data["archive_url"]
                st.markdown("**🔗 Link archive:**")
                st.code(archive_url, language=None)
                if wb_data.get("note"):
                    st.caption(f"_{wb_data['note']}_")
                st.link_button("🌐 Mở link archive", archive_url)

                wb_append_key = f"{wayback_key}_appended"
                if wb_append_key not in st.session_state:
                    if st.button("📎 Thêm link archive vào tất cả draft emails", key=f"wayback_append_{domain}", type="primary"):
                        updated = pt.append_wayback_evidence_to_drafts(result.get("drafts", []), wb_data)
                        if updated:
                            st.session_state[wb_append_key] = updated
                            st.rerun()
                        else:
                            st.warning("Không tìm thấy draft file nào để cập nhật.")
                else:
                    st.success(f"✅ Link archive đã được thêm vào {len(st.session_state[wb_append_key])} draft files.")

    st.divider()

    # ── Section chính ─────────────────────────────────────────────────────────
    st.subheader("📋 Tiến hành báo cáo")
    st.caption("Thực hiện theo thứ tự ưu tiên từ trên xuống — mỗi mục có sẵn nội dung để copy hoặc nút gửi mail.")

    # ── Nút gửi tất cả ────────────────────────────────────────────────────────
    all_drafts = result.get("drafts") or []
    if all_drafts:
        st.subheader("🚀 Gửi tất cả báo cáo cùng lúc")
        st.caption("Gửi đồng loạt tất cả draft có địa chỉ email hợp lệ. Draft dạng web form (CA report, Google...) sẽ được bỏ qua tự động.")
        render_send_all_ui(all_drafts, cfg, key_prefix="check_all")
        st.divider()

    # 1. Browser blocking ─────────────────────────────────────────────────────
    with st.expander("1️⃣  Browser Blocking — GSB / SmartScreen / Netcraft / OpenPhish", expanded=True):
        st.caption("Gửi song song, không thay thế báo registrar. Có hiệu quả nhanh: trình duyệt hiện màn cảnh báo đỏ trước khi người dùng vào site.")
        gsb_text = pt.generate_safebrowsing_report_text(domain, cfg)
        domain_url = result.get("target_url") or f"https://{domain}"
        bl1, bl2, bl3 = st.columns(3)
        bl1.link_button("🔗 Google Safe Browsing", f"https://safebrowsing.google.com/safebrowsing/report_phish/?url={domain_url}", use_container_width=True)
        bl2.link_button("🔗 Microsoft SmartScreen", "https://www.microsoft.com/wdsi/support/report-unsafe-site-guest/", use_container_width=True)
        bl3.link_button("🔗 Netcraft", f"https://report.netcraft.com/report?url={domain_url}", use_container_width=True)
        st.caption("Nội dung mô tả mẫu (paste vào ô Additional details của GSB):")
        st.code(gsb_text, language=None)
        # OpenPhish — nhận report qua email submit@openphish.com
        _openphish = find_draft("_openphish_report.txt")
        if _openphish:
            st.divider()
            st.markdown("**📧 OpenPhish** — gửi email tới `submit@openphish.com`:")
            show_draft_block(_openphish, "openphish")

    # 2. CDN (Cloudflare / Fastly / Akamai…) ──────────────────────────────────
    if cf or cdn_detected:
        cdn_label = []
        if cf:
            cdn_label.append("Cloudflare")
        cdn_label += [n.title() for n in cdn_detected]
        with st.expander(f"2️⃣  CDN: {', '.join(cdn_label)} (form thủ công)", expanded=True):
            if cf:
                st.link_button("🔗 Mở form Cloudflare Abuse", pt.CDN_ABUSE_CONTACTS["cloudflare"]["report_url"])
                st.caption(f"_{pt.CDN_ABUSE_CONTACTS['cloudflare']['note']}_")
                st.caption("Nội dung mô tả mẫu Cloudflare — hover vào khung để copy:")
                st.code(pt.generate_cloudflare_report_text(domain, cfg), language=None)
            for name in cdn_detected:
                info = pt.CDN_ABUSE_CONTACTS.get(name)
                if info:
                    st.link_button(f"🔗 Mở form {name.title()} Abuse", info["report_url"])

    # 3. CA (Certificate Authority) ───────────────────────────────────────────
    if ca_note:
        can_revoke = ca_note.get("can_revoke", True)
        ca_icon = "3️⃣" if can_revoke else "3️⃣ ⚠️"
        ca_title = f"{ca_icon}  CA: {ca_note['ca'].title()}"
        with st.expander(ca_title, expanded=True):
            if can_revoke:
                st.info(
                    "📌 **Báo CA để revoke chứng chỉ SSL** — khi CA thu hồi cert, "
                    "trình duyệt hiện cảnh báo 'Certificate Revoked' ngay cả khi domain vẫn còn sống. "
                    "Hiệu quả phụ, không thay thế báo registrar."
                )
            st.caption(ca_note["note"])
            _ca_url = ca_note.get("report_url")
            _ca_email = ca_note.get("abuse_email")
            if _ca_url or _ca_email:
                c_ca1, c_ca2 = st.columns(2)
                if _ca_url:
                    c_ca1.link_button("🔗 Mở form report CA", _ca_url, type="primary" if can_revoke else "secondary")
                if _ca_email:
                    c_ca2.markdown(f"**Abuse email:** `{_ca_email}`")
            if can_revoke:
                show_draft_block(find_draft("_ca_report.txt"), "ca")

    # 4. Registrar ────────────────────────────────────────────────────────────
    if who.get("registrar"):
        registrar_name = who["registrar"]
        _r_lower = registrar_name.lower()
        webform_url_r = next(
            (url for key, url in pt.WEB_FORM_REGISTRARS.items() if key in _r_lower),
            None
        )
        with st.expander(f"4️⃣  Registrar: {registrar_name}", expanded=True):
            abuse_email_source = result.get("registrar_abuse_email_source")
            abuse_email_used = result.get("registrar_abuse_email_used")

            if webform_url_r:
                st.info(f"**{registrar_name} chỉ nhận report qua web form.**")
                st.link_button(f"🔗 Mở form {registrar_name}", webform_url_r, type="primary")
                # Draft pre-filled theo template riêng của registrar
                target_url_val = result.get("target_url") or f"https://{domain}"
                vt_link_val = vt.get("link") or ""
                draft_text = pt.get_webform_draft_text(
                    domain=domain,
                    registrar=registrar_name,
                    webform_url=webform_url_r,
                    cfg=cfg,
                    target_url=target_url_val,
                    vt_link=vt_link_val,
                )
                st.caption("Mở form ở tab khác → copy từng field bên dưới vào form:")
                st.code(draft_text, language=None)
            elif abuse_email_source == "whois":
                st.markdown(f"**Abuse contact (từ WHOIS):** `{abuse_email_used}`")
            elif abuse_email_source == "rdap":
                st.markdown(f"**Abuse contact (từ RDAP — ICANN standard):** `{abuse_email_used}`")
                st.caption("✅ Email lấy trực tiếp từ RDAP — đáng tin cậy.")
            elif abuse_email_source == "static_table":
                st.markdown(f"**Abuse contact (fallback từ bảng tĩnh):** `{abuse_email_used}`")
                st.caption("⚠️ WHOIS và RDAP không trả về email — tra từ bảng tĩnh. Kiểm tra lại trước khi gửi.")
            else:
                st.warning("Không tìm được abuse email — tra thủ công tại https://lookup.icann.org/")
            # Email draft (nếu không phải web-form)
            if not webform_url_r:
                show_draft_block(find_draft("_registrar_report.txt"), "registrar")

    # 5. Registry ──────────────────────────────────────────────────────────────
    registry_contact = result["registry_contact"]
    tld = domain.rsplit(".", 1)[-1]
    if registry_contact.get("source") != "not_found":
        reg_label = registry_contact.get("registry") or "Registry"
        has_webform = bool(registry_contact.get("report_webform"))
        has_email = bool(registry_contact.get("abuse_email"))
        channel_hint = " (web form)" if has_webform and not has_email else " (email)" if has_email and not has_webform else " (web form + email)" if has_webform and has_email else ""
        with st.expander(f"5️⃣  Registry: {reg_label}{channel_hint} — leo thang khi registrar không phản hồi >7 ngày", expanded=True):
            st.caption("⏳ Dùng kênh này khi đã báo registrar (mục 5) nhưng sau 7 ngày vẫn không có phản hồi.")
            if registry_contact["source"] == "static_table":
                if has_webform and not has_email:
                    # Web form only — hiện nút to, không cần hiện file draft
                    st.markdown(f"**{reg_label} chỉ nhận report qua web form:**")
                    st.link_button(f"🔗 Mở web form {reg_label}", registry_contact["report_webform"], type="primary", use_container_width=True)
                    st.markdown("**Cách điền form:**")
                    st.markdown(
                        f"1. Chọn loại vi phạm: **Phishing** hoặc **Brand Abuse**\n"
                        f"2. Nhập domain: `{domain}`\n"
                        f"3. Điền URL phishing cụ thể\n"
                        f"4. Đính kèm ảnh chụp màn hình **(bắt buộc có thanh địa chỉ)**\n"
                        f"5. Mô tả: copy nội dung từ draft bên dưới vào ô description"
                    )
                    if registry_contact.get("note"):
                        st.warning(f"⚠️ {registry_contact['note']}")
                    # Hiện draft để copy nội dung mô tả paste vào form
                    draft_path = find_draft("_registry_report.txt")
                    if draft_path and os.path.isfile(draft_path):
                        with st.expander("📋 Nội dung mô tả — copy paste vào ô description của form", expanded=True):
                            with open(draft_path, encoding="utf-8") as f:
                                st.code(f.read(), language=None)
                elif has_email:
                    # Email (có hoặc không có web form)
                    st.markdown(f"**Abuse email:** `{registry_contact['abuse_email']}`")
                    if has_webform:
                        st.link_button(f"🔗 Web form {reg_label}", registry_contact["report_webform"])
                    if registry_contact.get("note"):
                        st.info(registry_contact["note"])
                    show_draft_block(find_draft("_registry_report.txt"), "registry")
                else:
                    st.info("TLD này không có kênh registry riêng — chỉ báo nhà đăng ký (mục 5).")
            else:
                # IANA referral — cần đọc WHOIS thô để tìm abuse email
                st.info(f"🔍 Registry của `.{tld}` được tra qua IANA — xem nội dung WHOIS thô trong draft để tìm abuse email.")
                st.markdown(f"**WHOIS server:** `{registry_contact.get('whois_server')}`")
                show_draft_block(find_draft("_registry_report.txt"), "registry")
    else:
        with st.expander(f"5️⃣  Registry — TLD `.{tld}` chưa hỗ trợ tự động", expanded=True):
            st.caption("⏳ Dùng khi registrar không phản hồi sau 7 ngày.")
            st.link_button("🔗 Tra abuse contact tại IANA", f"https://www.iana.org/domains/root/db/{tld}.html")

    # 6. VNCERT ───────────────────────────────────────────────────────────────
    with st.expander("6️⃣  VNCERT (chỉ gửi nếu domain nhắm vào nạn nhân Việt Nam)", expanded=False):
        st.warning("Tool không tự xác định được nạn nhân là người VN hay không — xác minh thủ công trước khi gửi.")
        show_draft_block(find_draft("_vncert_report.txt"), "vncert")

    # 7. Hosting/ISP (IP gốc) ─────────────────────────────────────────────────
    origin_ip_whois = result["origin_ip_whois"]
    origin_candidates = {
        sub: ip for sub, ip in origin_ip_scan.items() if ip and ip != cert.get("ip")
    }
    if origin_ip_whois:
        ip, ipw = next(iter(origin_ip_whois.items()))
        org = ipw.get("org") or "N/A"
        with st.expander(f"7️⃣  Hosting/ISP — IP gốc {ip} ({org})", expanded=False):
            if "error" in ipw:
                st.error(f"Lỗi tra IP WHOIS: {ipw['error']}")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Tổ chức", ipw.get("org") or "N/A")
                c2.metric("Abuse email", ipw.get("abuse_email") or "N/A")
                c3.metric("ASN", f"AS{ipw.get('asn')} {ipw.get('asn_description') or ''}")
            if origin_candidates:
                st.caption("Các subdomain lộ IP gốc:")
                st.table(pd.DataFrame(
                    [(f"{sub}.{domain}", ip_c) for sub, ip_c in origin_candidates.items()],
                    columns=["Subdomain", "IP"],
                ))
            show_draft_block(find_draft("_hosting_report.txt"), "hosting")
    else:
        st.caption("7️⃣  Hosting/ISP: không phát hiện IP gốc qua subdomain scan (mail, cpanel, ftp…).")

    # ── Footer log ────────────────────────────────────────────────────────────
    st.divider()
    if result["log_error"]:
        st.error(f"Lỗi ghi log: {result['log_error']}")
    else:
        st.caption(f"✅ Đã ghi log vào `{pt.LOG_PATH}`")
    if result["drafts_error"]:
        st.error(f"Lỗi sinh email draft: {result['drafts_error']}")

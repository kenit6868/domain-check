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
from email_send_ui import render_send_email_ui

st.set_page_config(page_title="Check Domain", page_icon="🔍", layout="wide")
st.title("🔍 Check Domain")
st.caption("SSL issuer/serial, WHOIS, Cloudflare, VirusTotal, Google Safe Browsing — ghi log + sinh email báo cáo")


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

    col_ssl, col_whois = st.columns(2)
    with col_ssl:
        st.subheader("🔒 SSL Certificate")
        if "error" in cert:
            st.error(cert["error"])
        else:
            show_dict(cert)
    with col_whois:
        st.subheader("📋 WHOIS")
        if "error" in who:
            st.error(who["error"])
        else:
            show_dict(who)

    col_vt, col_gsb = st.columns(2)
    with col_vt:
        st.subheader("🦠 VirusTotal")
        if "error" in vt:
            st.error(vt["error"])
        elif "skipped" in vt:
            st.warning(vt["skipped"])
        else:
            show_dict(vt)
        if result["virustotal_submit"]:
            st.info(result["virustotal_submit"])
    with col_gsb:
        st.subheader("🛡️ Google Safe Browsing")
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
    st.subheader("📸 URLScan.io — Screenshot & Phân tích trang")
    st.caption("Screenshot trang không cần truy cập thủ công — dùng làm bằng chứng đính kèm abuse report. Scan mất ~30 giây.")

    urlscan_key = f"urlscan_{domain}"

    col_us1, col_us2 = st.columns([1, 3])
    with col_us1:
        use_http = st.checkbox("Dùng http:// (nếu https bị từ chối)", key=f"urlscan_http_{domain}")
        if st.button("🔍 Submit URLScan", key=f"urlscan_submit_{domain}", type="primary"):
            with st.spinner("Đang submit lên URLScan.io..."):
                sub = pt.urlscan_submit(domain, cfg.get("urlscan_api_key", ""), use_http=use_http)
            if "warning" in sub:
                st.warning(sub["warning"])
                st.info("💡 Thử tick **'Dùng http://'** rồi submit lại, hoặc domain này đã down.")
            elif "error" in sub:
                st.error(sub["error"])
            else:
                st.session_state[urlscan_key] = sub
                st.success(f"Đã submit! Scan ID: `{sub['scan_id']}`")

        if urlscan_key in st.session_state and st.session_state[urlscan_key].get("scan_id"):
            scan_id = st.session_state[urlscan_key]["scan_id"]
            if st.button("🔄 Lấy kết quả", key=f"urlscan_fetch_{domain}"):
                with st.spinner("Đang lấy kết quả (~30 giây sau khi submit)..."):
                    res_data = pt.urlscan_result(scan_id, cfg.get("urlscan_api_key", ""))
                if res_data.get("status") == "pending":
                    st.warning("Scan chưa xong — đợi thêm ~15 giây rồi thử lại.")
                elif "error" in res_data:
                    st.error(res_data["error"])
                else:
                    st.session_state[f"{urlscan_key}_result"] = res_data

    with col_us2:
        result_data = st.session_state.get(f"{urlscan_key}_result")
        pending_data = st.session_state.get(urlscan_key)

        if result_data and result_data.get("status") == "done":
            rc1, rc2, rc3 = st.columns(3)
            rc1.metric("URLScan Verdict", "🚨 Malicious" if result_data.get("malicious") else "✅ Not flagged")
            rc2.metric("Score", result_data.get("score", 0))
            rc3.metric("Page IP", result_data.get("page_ip") or "N/A")

            tags = result_data.get("tags") or []
            brands = result_data.get("brands") or []
            if tags:
                st.markdown(f"**Tags:** {', '.join(tags)}")
            if brands:
                st.markdown(f"**Brands detected:** {', '.join(brands)}")

            screenshot_url = result_data.get("screenshot_url")
            result_url = result_data.get("result_url")
            if screenshot_url:
                st.image(screenshot_url, caption="Screenshot từ URLScan.io", use_container_width=True)

            # Block copy-ready evidence để paste vào form/email
            st.markdown("**📋 Copy bằng chứng — paste vào bất kỳ email/form nào:**")
            evidence_text = (
                f"Evidence (URLScan.io):\n"
                f"- Screenshot: {screenshot_url}\n"
                f"- Full analysis: {result_url}\n"
                f"- Verdict: {'MALICIOUS' if result_data.get('malicious') else 'Not flagged'} "
                f"(score: {result_data.get('score', 0)})"
            )
            st.code(evidence_text, language=None)

            # Nút append bằng chứng vào tất cả draft emails đã sinh
            append_key = f"{urlscan_key}_appended"
            if append_key not in st.session_state:
                if st.button("📎 Thêm bằng chứng URLScan vào tất cả draft emails", key=f"urlscan_append_{domain}", type="primary"):
                    updated = pt.append_urlscan_evidence_to_drafts(result.get("drafts", []), result_data)
                    if updated:
                        st.session_state[append_key] = updated
                        st.rerun()
                    else:
                        st.warning("Không tìm thấy draft file nào để cập nhật.")
            else:
                st.success(f"✅ Bằng chứng đã được thêm vào {len(st.session_state[append_key])} draft files.")
        elif pending_data and pending_data.get("scan_id"):
            st.info(f"Scan đã submit — bấm **'Lấy kết quả'** sau ~30 giây.  \n"
                    f"Link kết quả: {pending_data.get('result_url')}")

    st.divider()

    # ── B3: Wayback Machine Archive ───────────────────────────────────────────
    st.subheader("🗄️ Wayback Machine — Lưu bằng chứng tĩnh")
    st.caption("Archive trang phishing ngay bây giờ — link tĩnh tồn tại mãi kể cả khi domain bị gỡ. Nên archive trước khi report.")

    wayback_key = f"wayback_{domain}"
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
            st.markdown(f"**🔗 Link archive:**")
            archive_url = wb_data["archive_url"]
            st.code(archive_url, language=None)
            if wb_data.get("note"):
                st.caption(f"_{wb_data['note']}_")
            st.link_button("🌐 Mở link archive", archive_url)

            # Append vào tất cả draft emails
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

    # 1. Google Safe Browsing ─────────────────────────────────────────────────
    with st.expander("1️⃣  Google Safe Browsing + Microsoft SmartScreen (form thủ công)", expanded=True):
        col_a, col_b = st.columns(2)
        col_a.link_button("🔗 Mở form Google Safe Browsing", "https://safebrowsing.google.com/safebrowsing/report_phish/")
        col_b.link_button("🔗 Mở form Microsoft SmartScreen", "https://www.microsoft.com/wdsi/support/report-unsafe-site/")
        st.caption("Nội dung mô tả mẫu — hover vào khung để copy:")
        st.code(pt.generate_safebrowsing_report_text(domain, cfg), language=None)

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

    # 3. Cộng đồng bảo mật (VirusTotal / PhishTank / APWG / Netcraft) ─────────
    with st.expander("3️⃣  Cộng đồng bảo mật — VirusTotal · PhishTank · APWG · Netcraft", expanded=True):
        vt_link = result.get("virustotal_submit") or vt.get("link")
        if vt_link:
            st.link_button("🔗 Xem/Submit VirusTotal", vt_link)
        else:
            st.info("VirusTotal: chưa từng quét domain này — tick ô \"Submit lên VirusTotal\" ở form trên rồi kiểm tra lại.")
        st.link_button("🔗 Report PhishTank (thủ công)", "https://www.phishtank.com/add_web_phish.php")
        st.link_button("🔗 Report Netcraft (form)", "https://report.netcraft.com/report")
        st.markdown("**APWG eCrime** — gửi qua email draft đã tạo sẵn:")
        show_draft_block(find_draft("_apwg_report.txt"), "apwg")

    # 4. CA (Certificate Authority) ───────────────────────────────────────────
    if ca_note:
        ca_title = f"4️⃣  CA: {ca_note['ca']}"
        with st.expander(ca_title, expanded=True):
            st.info(ca_note["note"])
            if ca_note.get("report_url"):
                # Nếu là URL thật thì link_button, không phải email address
                url = ca_note["report_url"]
                if url.startswith("http"):
                    st.link_button("🔗 Mở form report CA", url)
                else:
                    st.markdown(f"Abuse email: `{url}`")
            show_draft_block(find_draft("_ca_report.txt"), "ca")

    # 5. Registrar ────────────────────────────────────────────────────────────
    if who.get("registrar"):
        with st.expander(f"5️⃣  Registrar: {who['registrar']}", expanded=True):
            abuse_email_source = result.get("registrar_abuse_email_source")
            abuse_email_used = result.get("registrar_abuse_email_used")
            if abuse_email_source == "whois":
                st.markdown(f"**Abuse contact (từ WHOIS):** `{abuse_email_used}`")
            elif abuse_email_source == "static_table":
                st.markdown(f"**Abuse contact (fallback từ bảng tĩnh):** `{abuse_email_used}`")
                st.caption("⚠️ WHOIS không trả về email — email trên được tra từ bảng tĩnh theo tên registrar. Đã ghi vào draft.")
            else:
                st.warning("Không tìm được abuse email — tra thủ công tại https://lookup.icann.org/")
            show_draft_block(find_draft("_registrar_report.txt"), "registrar")

    # 6. Registry ccTLD ───────────────────────────────────────────────────────
    registry_contact = result["registry_contact"]
    if registry_contact.get("source") != "not_found":
        with st.expander("6️⃣  Registry (ccTLD) — leo thang khi registrar không phản hồi", expanded=True):
            if registry_contact["source"] == "static_table":
                st.markdown(f"**Registry:** {registry_contact.get('registry')}")
                st.markdown(f"**Abuse email:** `{registry_contact.get('abuse_email')}`")
                if registry_contact.get("note"):
                    st.warning(registry_contact["note"])
            else:
                st.markdown(f"**WHOIS server:** {registry_contact.get('whois_server')} (qua IANA referral)")
                st.caption("Tra abuse email trong nội dung WHOIS thô bên dưới draft.")
            show_draft_block(find_draft("_registry_report.txt"), "registry")
    else:
        with st.expander(f"6️⃣  Registry (ccTLD) — TLD `.{domain.rsplit('.', 1)[-1]}` chưa hỗ trợ tự động", expanded=True):
            st.link_button("🔗 Tra thủ công tại IANA", f"https://www.iana.org/domains/root/db/{domain.rsplit('.', 1)[-1]}.html")

    # 7. VNCERT ───────────────────────────────────────────────────────────────
    with st.expander("7️⃣  VNCERT (chỉ gửi nếu domain nhắm vào nạn nhân Việt Nam)", expanded=True):
        st.warning("Tool không tự xác định được nạn nhân là người VN hay không — xác minh thủ công trước khi gửi.")
        show_draft_block(find_draft("_vncert_report.txt"), "vncert")

    # 8. Hosting/ISP (IP gốc) ─────────────────────────────────────────────────
    origin_ip_whois = result["origin_ip_whois"]
    origin_candidates = {
        sub: ip for sub, ip in origin_ip_scan.items() if ip and ip != cert.get("ip")
    }
    if origin_ip_whois:
        ip, ipw = next(iter(origin_ip_whois.items()))
        org = ipw.get("org") or "N/A"
        with st.expander(f"8️⃣  Hosting/ISP — IP gốc {ip} ({org})", expanded=True):
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
        with st.expander("8️⃣  Hosting/ISP — không phát hiện IP gốc", expanded=True):
            st.info(
                "Không tìm thấy IP gốc qua subdomain scan. Có thể domain không dùng CDN, "
                "hoặc IP gốc không lộ qua các subdomain thông dụng (mail, cpanel, ftp…)."
            )

    # ── Footer log ────────────────────────────────────────────────────────────
    st.divider()
    if result["log_error"]:
        st.error(f"Lỗi ghi log: {result['log_error']}")
    else:
        st.caption(f"✅ Đã ghi log vào `{pt.LOG_PATH}`")
    if result["drafts_error"]:
        st.error(f"Lỗi sinh email draft: {result['drafts_error']}")

"""pages/7_Quick_Report.py — Check nhanh + nội dung báo cáo Google Safe Browsing và Cloudflare.

Trang tối giản: chỉ gọi run_cdn_check() (WHOIS + DNS, không API key nào), rồi hiển thị
2 nội dung mẫu cần copy vào form cạnh nhau:
  - Google Safe Browsing (+ Microsoft SmartScreen)
  - Cloudflare Abuse (nếu phát hiện domain dùng Cloudflare)
"""

import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

import phishing_toolkit as pt


st.set_page_config(page_title="Quick Report", page_icon="⚡", layout="wide")
st.title("⚡ Quick Report")
st.caption("Check domain + lấy ngay nội dung mẫu report. Chỉ dùng WHOIS/DNS — không gọi VirusTotal hay GSB API.")

with st.form("quick_report_form"):
    target = st.text_input("Domain hoặc URL cần kiểm tra", placeholder="vd: chass.ru.com")
    go = st.form_submit_button("Kiểm tra", type="primary")

if go:
    if not target.strip():
        st.warning("Nhập domain trước khi kiểm tra.")
    else:
        cfg = pt.load_config()
        with st.spinner(f"Đang kiểm tra {target}..."):
            result = pt.run_cdn_check(target)
        st.session_state["qr_result"] = result
        st.session_state["qr_cfg"] = cfg

if "qr_result" in st.session_state:
    result = st.session_state["qr_result"]
    cfg = st.session_state["qr_cfg"]

    domain = result["domain"]
    cf = result["cloudflare"]
    cdn_detected = result.get("cdn_detected", [])

    st.success(f"✅ Domain: **{domain}**")
    mc1, mc2 = st.columns(2)
    mc1.metric("Cloudflare", "Có ✓" if cf else "Không")
    mc2.metric("CDN khác", ", ".join(n.title() for n in cdn_detected) if cdn_detected else "Không phát hiện")

    st.divider()

    has_cdn = cf or bool(cdn_detected)

    # ── Layout: 2 cột cạnh nhau (GSB bên trái, Cloudflare bên phải) ──────────
    _pw_ok = pt.playwright_available()
    col_gsb, col_cf = st.columns(2)

    # ── Cột trái: Google Safe Browsing + Microsoft SmartScreen ─────────────────
    with col_gsb:
        st.markdown("### 1️⃣ Google Safe Browsing")
        _domain_url = f"https://{domain}"
        _gsb_url = "https://safebrowsing.google.com/safebrowsing/report_phish/?url=" + urllib.parse.quote(_domain_url, safe="")

        gsb_text = pt.generate_safebrowsing_report_text(domain, cfg)

        if _pw_ok:
            # Mapping l1 → l3 options (confirmed by debug)
            _L3_OPTIONS = {
                "Social Engineering": [
                    "None", "Bank / Financial Phishing", "Crypto Exchange Phishing",
                    "Social Media Platform Phishing", "Retail Phishing",
                    "Email Provider Phishing", "Entertainment Phishing",
                    "Government Agency Phishing", "Other Phishing",
                    "Package Tracking Scam", "Fake Support Scam",
                    "Government Fines Scam", "Fake Prize/Giveaway Scam", "Other Scam",
                ],
                "Malware": ["None", "Desktop Malware", "Mobile Malware", "Web Malware"],
                "Unwanted Software": ["None", "Unwanted Desktop Software", "Unwanted Mobile Software"],
            }
            _threat = st.selectbox(
                "Threat type",
                ["Social Engineering", "Malware", "Unwanted Software"],
                index=1,
                key="gsb_threat_type",
                help="Social Engineering = phishing. Malware = trang phát tán mã độc.",
            )
            _l3_opts = _L3_OPTIONS.get(_threat, ["None"])
            _default_cat = "Web Malware" if _threat == "Malware" else "None"
            _cat_idx = _l3_opts.index(_default_cat) if _default_cat in _l3_opts else 0
            _category = st.selectbox("Threat category", _l3_opts, index=_cat_idx, key="gsb_threat_category")

            if st.button("🤖 Google Safe Browsing — Mở & điền tự động", key="gsb_auto", use_container_width=True, type="primary"):
                res = pt.open_gsb_form_playwright(domain, gsb_text, threat_type=_threat, threat_category=_category)
                if "error" in res:
                    st.error(res["error"])
                else:
                    st.success("✅ Chrome đang mở — điền xong, bấm Submit (có reCAPTCHA).")

            if st.button("🤖 Microsoft SmartScreen — Mở & điền tự động", key="ms_auto", use_container_width=True, type="primary"):
                res = pt.open_microsoft_form_playwright(domain)
                if "error" in res:
                    st.error(res["error"])
                else:
                    st.success("✅ Chrome đang mở — URL + Vietnamese đã điền, bấm Submit.")
        else:
            st.info("💡 Cài Playwright để tự động điền form:\n```\npip install playwright\npython -m playwright install chromium\n```")
            st.link_button("🔗 Mở form Google Safe Browsing (URL đã điền sẵn)", _gsb_url, use_container_width=True)
            st.link_button("🔗 Mở form Microsoft SmartScreen", "https://www.microsoft.com/en-us/wdsi/support/report-unsafe-site-guest", use_container_width=True)

        st.caption("Nội dung dán vào ô Additional details (Google):")
        st.code(gsb_text, language=None)

    # ── Cột phải: Cloudflare Abuse (chỉ hiện nếu phát hiện) ───────────────────
    with col_cf:
        if has_cdn:
            cdn_names = (["Cloudflare"] if cf else []) + [n.title() for n in cdn_detected]
            st.markdown(f"### 2️⃣ CDN: {', '.join(cdn_names)}")

            if cf:
                info_cf = pt.CDN_ABUSE_CONTACTS["cloudflare"]
                cf_text = pt.generate_cloudflare_report_text(domain, cfg)

                if st.button("🌐 Mở form Cloudflare Abuse", key="cf_open", use_container_width=True, type="primary"):
                    pt.open_cloudflare_form_browser(domain)
                    st.info("✅ Đã mở browser — Copy nội dung bên dưới rồi paste vào form.")
                st.caption("_Cloudflare dùng bot-detection để bảo vệ chính form của họ — không thể tự điền. Dùng nút Copy để paste nhanh._")
                st.caption(f"_{info_cf['note']}_")
                st.code(cf_text, language=None)

            for name in cdn_detected:
                info = pt.CDN_ABUSE_CONTACTS.get(name)
                if info:
                    st.link_button(
                        f"🔗 Mở form {name.title()} Abuse",
                        info["report_url"],
                        use_container_width=True,
                    )
        else:
            st.markdown("### 2️⃣ Cloudflare / CDN")
            st.info("Không phát hiện Cloudflare hay CDN — bỏ qua mục này.")

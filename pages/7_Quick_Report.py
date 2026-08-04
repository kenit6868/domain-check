"""pages/7_Quick_Report.py — Check nhanh danh sách domain + nội dung báo cáo Google Safe Browsing và Cloudflare.

Trang tối giản: chỉ gọi run_cdn_check() (WHOIS + DNS, không API key nào), rồi hiển thị
từng domain theo thứ tự với 2 form cạnh nhau:
  - Google Safe Browsing + Microsoft SmartScreen (bên trái)
  - Cloudflare Abuse (bên phải, chỉ hiện nếu phát hiện Cloudflare/CDN)
"""

import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

import phishing_toolkit as pt


# ── Mapping l1 → l3 cho Google Safe Browsing (confirmed selectors) ────────────
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


def _parse_domains(raw: str) -> tuple[list[str], list[str]]:
    """Tách danh sách domain từ text (mỗi dòng/dấu phẩy/chấm phẩy).
    Trả về (valid_list, invalid_list).
    """
    valid, invalid = [], []
    seen: set[str] = set()
    for item in re.split(r"[\n,;]+", raw):
        item = item.strip()
        if not item:
            continue
        domain = pt.normalize_domain(item).lower().rstrip(".")
        pattern = r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        if not re.fullmatch(pattern, domain):
            invalid.append(item)
        elif domain not in seen:
            seen.add(domain)
            valid.append(item)
    return valid, invalid


def _render_domain_block(idx: int, total: int, result: dict, cfg: dict, pw_ok: bool) -> None:
    """Render khối kết quả + 2 form cho 1 domain."""
    domain = result["domain"]
    cf = result["cloudflare"]
    cdn_detected = result.get("cdn_detected", [])
    has_cdn = cf or bool(cdn_detected)

    with st.container(border=True):
        original_url = result.get("_original_url", f"https://{domain}")
        h_num, h_url, h2, h3 = st.columns([1, 4, 1, 1])
        h_num.markdown(f"### #{idx + 1}/{total}")
        h_url.code(original_url, language=None)
        h2.metric("Cloudflare", "✓ Có" if cf else "Không")
        h3.metric("CDN khác", ", ".join(n.title() for n in cdn_detected) if cdn_detected else "—")


        col_gsb, col_cf = st.columns(2)

        # ── Cột trái: Google Safe Browsing + Microsoft SmartScreen ────────────
        with col_gsb:
            st.markdown("**1️⃣ Google Safe Browsing / Microsoft SmartScreen**")
            gsb_text = pt.generate_safebrowsing_report_text(domain, cfg)
            _domain_url = f"https://{domain}"
            _gsb_url = "https://safebrowsing.google.com/safebrowsing/report_phish/?url=" + urllib.parse.quote(_domain_url, safe="")

            if pw_ok:
                c1, c2 = st.columns(2)
                _threat = c1.selectbox(
                    "Threat type",
                    ["Social Engineering", "Malware", "Unwanted Software"],
                    index=1,
                    key=f"threat_{idx}",
                )
                _l3_opts = _L3_OPTIONS.get(_threat, ["None"])
                _def = "Web Malware" if _threat == "Malware" else "None"
                _cat = c2.selectbox(
                    "Category",
                    _l3_opts,
                    index=_l3_opts.index(_def) if _def in _l3_opts else 0,
                    key=f"cat_{idx}",
                )
                b1, b2 = st.columns(2)
                if b1.button("🤖 Google Safe Browsing", key=f"gsb_{idx}", use_container_width=True, type="primary"):
                    res = pt.open_gsb_form_playwright(domain, gsb_text, threat_type=_threat, threat_category=_cat)
                    if "error" in res:
                        st.error(res["error"])
                    else:
                        st.success("✅ Chrome mở — bấm Submit sau khi điền reCAPTCHA.")
                if b2.button("🤖 Microsoft SmartScreen", key=f"ms_{idx}", use_container_width=True, type="primary"):
                    res = pt.open_microsoft_form_playwright(domain)
                    if "error" in res:
                        st.error(res["error"])
                    else:
                        st.success("✅ Chrome mở — URL + Vietnamese đã điền.")
            else:
                st.link_button("🔗 Google Safe Browsing (URL điền sẵn)", _gsb_url, use_container_width=True)
                st.link_button("🔗 Microsoft SmartScreen", "https://www.microsoft.com/en-us/wdsi/support/report-unsafe-site-guest", use_container_width=True)

            st.caption("Nội dung dán vào ô Additional details:")
            st.code(gsb_text, language=None)

        # ── Cột phải: Cloudflare / CDN ────────────────────────────────────────
        with col_cf:
            if has_cdn:
                cdn_names = (["Cloudflare"] if cf else []) + [n.title() for n in cdn_detected]
                st.markdown(f"**2️⃣ CDN: {', '.join(cdn_names)}**")
                if cf:
                    info_cf = pt.CDN_ABUSE_CONTACTS["cloudflare"]
                    cf_text = pt.generate_cloudflare_report_text(domain, cfg)
                    if st.button("🌐 Mở form Cloudflare Abuse", key=f"cf_{idx}", use_container_width=True, type="primary"):
                        pt.open_cloudflare_form_browser(domain)
                        st.info("✅ Đã mở browser — paste nội dung bên dưới vào form.")
                    st.caption(f"_{info_cf['note']}. Bot-detection của Cloudflare chặn Playwright — chỉ mở browser thật._")
                    st.code(cf_text, language=None)
                for name in cdn_detected:
                    info = pt.CDN_ABUSE_CONTACTS.get(name)
                    if info:
                        st.link_button(f"🔗 {name.title()} Abuse", info["report_url"], use_container_width=True)
            else:
                st.markdown("**2️⃣ Cloudflare / CDN**")
                st.caption("Không phát hiện Cloudflare hay CDN.")


# ── Page layout ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Quick Report", page_icon="⚡", layout="wide")
st.title("⚡ Quick Report")
st.caption("Nhập danh sách domain → check CDN/Cloudflare → hiện form báo cáo từng cái. Không gọi VirusTotal hay GSB API.")

# Lọc từ nội dung thô
with st.expander("🧹 Lọc domain từ nội dung thô (tùy chọn)", expanded=False):
    st.caption("Dán nguyên văn bản hỗn hợp — tool tách domain/URL rồi đưa xuống ô bên dưới.")
    raw_paste = st.text_area("Nội dung thô", height=120, placeholder="789win\nhttps://example.com/vi-vn/ (top3)\nGhi chú...", key="raw_paste")
    if st.button("🔍 Lọc domain", key="btn_filter"):
        try:
            from domain_utils import extract_domains_from_text
            found = extract_domains_from_text(raw_paste)
        except ImportError:
            found, _ = _parse_domains(raw_paste)
        if found:
            st.session_state["qr_domain_input"] = "\n".join(found)
            st.success(f"Đã lọc {len(found)} domain — đưa xuống danh sách.")
        else:
            st.warning("Không tìm thấy domain hợp lệ.")

with st.form("quick_report_form"):
    raw_domains = st.text_area(
        "Danh sách domain (mỗi dòng 1 domain hoặc URL)",
        height=150,
        key="qr_domain_input",
        placeholder="example-one.com\nexample-two.net\nhttps://example-three.org/login",
    )
    go = st.form_submit_button("⚡ Kiểm tra tất cả", type="primary")

if go:
    domains, invalid = _parse_domains(raw_domains)
    if invalid:
        st.warning("Domain không hợp lệ (bỏ qua): " + ", ".join(invalid[:10]))
    if not domains:
        st.warning("Chưa có domain hợp lệ để kiểm tra.")
    else:
        cfg = pt.load_config()
        results = []
        prog = st.progress(0, text=f"Đang kiểm tra 0/{len(domains)}...")
        for i, t in enumerate(domains):
            prog.progress((i) / len(domains), text=f"Đang kiểm tra {i + 1}/{len(domains)}: {t}")
            r = pt.run_cdn_check(t)
            # Lưu URL gốc (có path) để hiển thị/copy
            raw = t.strip()
            r["_original_url"] = raw if "://" in raw else f"https://{pt.normalize_domain(raw)}"
            results.append(r)
        prog.progress(1.0, text=f"✅ Hoàn tất {len(domains)} domain.")
        st.session_state["qr_results"] = results
        st.session_state["qr_cfg"] = cfg

if "qr_results" in st.session_state:
    results: list = st.session_state["qr_results"]
    cfg: dict = st.session_state["qr_cfg"]
    pw_ok = pt.playwright_available()
    total = len(results)

    st.divider()
    st.markdown(f"### Kết quả — {total} domain")
    for i, result in enumerate(results):
        _render_domain_block(i, total, result, cfg, pw_ok)


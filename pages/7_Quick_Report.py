"""pages/7_Quick_Report.py — Check nhanh danh sách domain + nội dung báo cáo Google Safe Browsing và Cloudflare.

Trang tối giản: chỉ gọi run_cdn_check() (WHOIS + DNS, không API key nào), rồi hiển thị
từng domain theo thứ tự với 2 form cạnh nhau:
  - Google Safe Browsing + Microsoft SmartScreen (bên trái)
  - Cloudflare Abuse (bên phải, chỉ hiện nếu phát hiện Cloudflare/CDN)
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

import phishing_toolkit as pt


# ── Mapping l1 → l3 cho Google Safe Browsing ──────────────────────────────────
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


def _render_domain_block(idx: int, total: int, result: dict, cfg: dict, dark_mode: bool = True) -> None:
    """Render khối kết quả + form cho 1 domain."""
    domain = result["domain"]
    cf = result["cloudflare"]
    cdn_detected = result.get("cdn_detected", [])
    has_cdn = cf or bool(cdn_detected)
    registrar = result.get("registrar") or ""

    # Phát hiện registrar web-form
    r_lower = registrar.lower()
    webform_url_r = next(
        (url for key, url in pt.WEB_FORM_REGISTRARS.items() if key in r_lower),
        None
    ) if registrar else None
    # Registrar email-only (có trong REGISTRAR_ABUSE_EMAILS hoặc có thể tra RDAP sau)
    registrar_email = pt.lookup_registrar_abuse_email(registrar) if registrar else None

    with st.container(border=True):
        original_url = result.get("_original_url", f"https://{domain}")
        h_num, h_url, h2, h3, h4 = st.columns([1, 4, 1, 1, 2])
        h_num.markdown(f"### #{idx + 1}/{total}")
        h_url.code(original_url, language=None)
        h2.metric("Cloudflare", "✓ Có" if cf else "Không")
        h3.metric("CDN khác", ", ".join(n.title() for n in cdn_detected) if cdn_detected else "—")
        if webform_url_r:
            h4.metric("Registrar", f"⚠️ {registrar[:20]}…" if len(registrar) > 20 else f"⚠️ {registrar}")
        elif registrar:
            h4.metric("Registrar", registrar[:24] if len(registrar) > 24 else registrar)
        else:
            h4.metric("Registrar", "—")

        col_gsb, col_right = st.columns(2)

        # ── Cột trái: Google Safe Browsing + Microsoft SmartScreen ────────────
        with col_gsb:
            st.markdown("**1️⃣ Browser Blocking: GSB / SmartScreen / Netcraft / PhishTank**")
            gsb_text = pt.generate_safebrowsing_report_text(domain, cfg)
            c1, c2 = st.columns(2)
            threat = c1.selectbox(
                "Threat type",
                list(_L3_OPTIONS),
                key=f"threat_{idx}",
            )
            categories = _L3_OPTIONS[threat]
            default_category = "Other Phishing" if threat == "Social Engineering" else "None"
            category = c2.selectbox(
                "Category",
                categories,
                index=categories.index(default_category),
                key=f"cat_{idx}",
            )
            b1, b2, b3 = st.columns(3)
            if b1.button("🤖 Google Safe Browsing", key=f"gsb_{idx}", use_container_width=True, type="primary"):
                res = pt.open_gsb_form_playwright(
                    original_url,
                    gsb_text,
                    threat_type=threat,
                    threat_category=category,
                    dark_mode=dark_mode,
                )
                if "error" in res:
                    st.error(res["error"])
                else:
                    st.success("✅ Đã mở tab và tự điền form — kiểm tra CAPTCHA rồi Submit.")
            if b2.button("🤖 Microsoft SmartScreen", key=f"ms_{idx}", use_container_width=True, type="primary"):
                res = pt.open_microsoft_form_playwright(original_url, dark_mode=dark_mode)
                if "error" in res:
                    st.error(res["error"])
                else:
                    st.success("✅ Đã mở tab và tự điền URL đầy đủ + Vietnamese.")
            if b3.link_button(
                "↗ Netcraft",
                f"https://report.netcraft.com/report?url={original_url}",
                use_container_width=True,
            ):
                pass
            st.link_button(
                "↗ PhishTank",
                "https://www.phishtank.com/add_web_phish.php",
                use_container_width=True,
            )

            st.caption("Nội dung dán vào ô Additional details:")
            st.code(gsb_text, language=None)

        # ── Cột phải: CDN + Registrar ─────────────────────────────────────────
        with col_right:
            # CDN
            if has_cdn:
                cdn_names = (["Cloudflare"] if cf else []) + [n.title() for n in cdn_detected]
                st.markdown(f"**2️⃣ CDN: {', '.join(cdn_names)}**")
                if cf:
                    info_cf = pt.CDN_ABUSE_CONTACTS["cloudflare"]
                    cf_text = pt.generate_cloudflare_report_text(domain, cfg)
                    st.link_button(
                        "↗ Mở form Cloudflare Abuse",
                        info_cf["report_url"],
                        use_container_width=True,
                        type="primary",
                    )
                    st.caption(f"_{info_cf['note']}. Dán nội dung bên dưới vào form._")
                    st.code(cf_text, language=None)
                for name in cdn_detected:
                    info = pt.CDN_ABUSE_CONTACTS.get(name)
                    if info:
                        st.link_button(f"🔗 {name.title()} Abuse", info["report_url"], use_container_width=True)
            else:
                st.caption("_(Không phát hiện CDN/proxy)_")

            # Registrar
            if registrar:
                st.markdown(f"**3️⃣ Registrar: {registrar}**")
                if webform_url_r:
                    st.link_button(
                        f"↗ Mở form {registrar}",
                        webform_url_r,
                        use_container_width=True,
                        type="primary",
                    )
                    # Hiện draft inline để copy vào form — không cần chạy run_check()
                    draft_text = pt.get_webform_draft_text(
                        domain=domain,
                        registrar=registrar,
                        webform_url=webform_url_r,
                        cfg=cfg,
                        target_url=original_url,
                    )
                    st.caption("Nội dung điền vào form — mở form ở tab khác rồi copy từng field:")
                    st.code(draft_text, language=None)
                elif registrar_email:
                    st.markdown(f"Abuse email: `{registrar_email}`")
                else:
                    st.caption("Tra abuse contact tại [lookup.icann.org](https://lookup.icann.org/)")


# ── Page layout ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Quick Report", page_icon="⚡", layout="wide")
st.title("⚡ Quick Report")
st.caption("Nhập danh sách domain → check CDN/Cloudflare → hiện form báo cáo từng cái. Không gọi VirusTotal hay GSB API.")

# Banner hướng dẫn cài Playwright (chỉ hiện khi chưa cài)
if not pt.playwright_available():
    with st.expander("⚠️ Playwright chưa được cài — các nút tự động điền form chưa hoạt động", expanded=True):
        st.markdown("""
Playwright dùng để **tự động mở Chrome và điền sẵn** form Google Safe Browsing / Microsoft SmartScreen.
Khi chưa cài, các nút này sẽ thay bằng link mở form thủ công.

**Cài đặt** (chạy 2 lệnh sau trong terminal, rồi restart app):
```
pip install playwright
python -m playwright install chromium
```
> Nếu lệnh `playwright` không nhận, thử: `python -m playwright install chromium`
""")

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

chrome_dark = st.toggle("🌙 Mở Chrome ở chế độ tối", value=True, key="chrome_dark_mode")

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
    total = len(results)

    st.divider()
    st.markdown(f"### Kết quả — {total} domain")

    # T8: nic.top Excel export cho domain .top
    top_domains = [r["domain"] for r in results if r["domain"].lower().endswith(".top")]
    if top_domains:
        with st.expander(f"📊 Export Excel cho nic.top ({len(top_domains)} domain .top)", expanded=True):
            st.caption(
                "nic.top nhận báo cáo hàng loạt qua file Excel (tối đa 200 domain/lần). "
                "Tải file rồi gửi kèm ảnh chụp màn hình đến **abuse@nic.top** hoặc qua form "
                "[en.nic.top/about/anti_phishing.html](https://en.nic.top/about/anti_phishing.html)."
            )
            brand_name = cfg.get("brand_name", "")
            if st.button("📊 Tạo file Excel nic.top", key="btn_nictop_excel", type="primary"):
                try:
                    path = pt.export_nictop_excel(top_domains, brand_name=brand_name)
                    with open(path, "rb") as f:
                        st.download_button(
                            label=f"⬇️ Tải xuống ({len(top_domains)} domain)",
                            data=f,
                            file_name=os.path.basename(path),
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    st.success(f"✅ Đã tạo {os.path.basename(path)}")
                except RuntimeError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"Lỗi tạo Excel: {e}")

    for i, result in enumerate(results):
        _render_domain_block(i, total, result, cfg, dark_mode=st.session_state.get("chrome_dark_mode", True))

"""pages/7_Quick_Report.py — Check nhanh danh sách domain + nội dung báo cáo Google Safe Browsing và Cloudflare.

Trang tối giản: chỉ gọi run_cdn_check() (WHOIS + DNS, không API key nào), rồi hiển thị
từng domain theo thứ tự với 2 form cạnh nhau:
  - Google Safe Browsing + Microsoft SmartScreen (bên trái)
  - Cloudflare Abuse (bên phải, chỉ hiện nếu phát hiện Cloudflare/CDN)
"""

import os
import re
import sys
import json
from html import escape
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import streamlit.components.v1 as components

import phishing_toolkit as pt
from cloaking_ui import render_cloaking_result

_MAX_CHECK_WORKERS = 1
_FILTER_CACHE_PATH = pt._runtime_path("quick_report_filter_cache.json")


def _render_copy_domain_button(displayed_url: str) -> None:
    """Show the URL and a compact adjacent button that copies it verbatim."""
    value = json.dumps(displayed_url)
    displayed = escape(displayed_url)
    components.html(
        f"""
        <div class="copy-row">
            <code>{displayed}</code>
            <button id="copy-domain" type="button" onclick="copyDomain()">📋 Copy</button>
        </div>
        <script>
        async function copyDomain() {{
            const value = {value};
            const button = document.getElementById("copy-domain");
            try {{
                await navigator.clipboard.writeText(value);
            }} catch (error) {{
                const input = document.createElement("textarea");
                input.value = value;
                input.style.position = "fixed";
                input.style.opacity = "0";
                document.body.appendChild(input);
                input.select();
                document.execCommand("copy");
                input.remove();
            }}
            button.textContent = "✅ Đã copy";
            setTimeout(() => button.textContent = "📋 Copy", 1400);
        }}
        </script>
        <style>
        body {{
            margin: 0;
            background: transparent;
            color: light-dark(#31333f, #fafafa);
            font-family: sans-serif;
        }}
        .copy-row {{ display: flex; align-items: center; gap: 8px; min-height: 38px; }}
        code {{
            padding: 3px 6px; border-radius: 4px; font-family: monospace;
            color: inherit; background: rgba(128,128,128,.14);
            font-size: 16px; font-weight: 700; overflow-wrap: anywhere;
        }}
        button {{
            flex: 0 0 auto; min-height: 30px; padding: 4px 9px;
            border: 1px solid rgba(128,128,128,.45); border-radius: 8px;
            background: transparent; color: inherit; cursor: pointer;
            font-size: 13px; font-weight: 600;
        }}
        button:hover {{ border-color: #ff4b4b; color: #ff4b4b; }}
        :root {{ color-scheme: light dark; }}
        </style>
        """,
        height=42,
    )


@st.cache_resource
def _check_executor() -> ThreadPoolExecutor:
    """Executor sống qua các lần rerun để UI không bị khóa lúc check domain."""
    return ThreadPoolExecutor(max_workers=_MAX_CHECK_WORKERS, thread_name_prefix="quick-report")


@st.cache_resource
def _quick_report_cache() -> dict:
    """Cache sống qua F5, cho tới khi bấm Xóa cache hoặc restart server."""
    return {}


@st.cache_resource
def _quick_report_filter_cache() -> set[str]:
    """URLs accepted by the filter, restored from disk after app restarts."""
    try:
        from domain_utils import domain_cache_key
        with open(_FILTER_CACHE_PATH, encoding="utf-8") as file:
            data = json.load(file)
        seen = {
            domain_cache_key(str(item))
            for item in data.get("seen", [])
            if str(item).strip()
        }
        seen.discard("")
        # Ghi lại ngay để cache cũ dạng domain/path được nâng cấp thành URL.
        _save_quick_report_filter_cache(seen)
        return seen
    except (OSError, ValueError, TypeError):
        return set()


def _save_quick_report_filter_cache(seen: set[str]) -> None:
    os.makedirs(os.path.dirname(_FILTER_CACHE_PATH), exist_ok=True)
    temp_path = f"{_FILTER_CACHE_PATH}.tmp.{os.getpid()}"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump({"version": 1, "seen": sorted(seen)}, file, ensure_ascii=False, indent=2)
    os.replace(temp_path, _FILTER_CACHE_PATH)


def _netcraft_status_text(result: dict) -> str:
    """Tạo trạng thái ngắn gọn từ response API, không lộ payload/email."""
    status_code = result.get("status_code")
    response = result.get("response")
    detail = ""
    if isinstance(response, dict):
        detail = str(
            response.get("detail")
            or response.get("message")
            or response.get("error")
            or ""
        )
    elif response:
        detail = str(response).strip()
    detail = " ".join(detail.split())[:300]
    prefix = f"HTTP {status_code}" if status_code is not None else str(result.get("error", "Lỗi không xác định"))
    return f"{prefix}: {detail}" if detail else prefix


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
    domain = result["domain"]
    cf = result["cloudflare"]
    cdn_detected = result.get("cdn_detected", [])
    has_cdn = cf or bool(cdn_detected)
    registrar = result.get("registrar") or ""
    original_url = result.get("_original_url", f"https://{domain}")
    gsb_text = pt.generate_safebrowsing_report_text(domain, cfg)

    # Chỉ hiện khi có web form
    r_lower = registrar.lower()
    webform_url_r = next(
        (url for key, url in pt.WEB_FORM_REGISTRARS.items() if key in r_lower), None
    ) if registrar else None

    registry_info = result.get("registry_contact")
    show_registry = bool(registry_info and registry_info.get("report_webform"))

    with st.container(border=True):
        # ── Header: URL + nhãn nhỏ ───────────────────────────────────────────
        h_num, h_url = st.columns([1, 11])
        h_num.markdown(f"#### #{idx + 1}")
        with h_url:
            _render_copy_domain_button(original_url)

        tags = []
        if cf: tags.append("☁️ Cloudflare")
        for n in cdn_detected: tags.append(n.title())
        if webform_url_r: tags.append(f"📋 {registrar[:20]}")
        if show_registry: tags.append(f"🌐 {registry_info.get('registry','Registry')[:20]}")
        if tags:
            st.caption("  ·  ".join(tags))

        render_cloaking_result(result.get("cloaking", {}), compact=True)
        if (result.get("cloaking") or {}).get("profiles"):
            with st.expander("Chi tiết kiểm tra cloaking", expanded=False):
                render_cloaking_result(result["cloaking"])
        cloaking = result.get("cloaking") or {}
        if cloaking.get("verdict") in {"POSSIBLE", "INCONCLUSIVE"}:
            if st.button(
                "Xác minh cloaking bằng Playwright",
                key=f"cloaking_playwright_{idx}",
                icon=":material/screenshot_monitor:",
                help="Chỉ tải và chụp trang bằng 2 profile; không click hay gửi form.",
            ):
                with st.spinner("Đang xác minh thụ động..."):
                    result["cloaking"] = pt.run_cloaking_browser_check(original_url, cloaking)
                st.rerun()

        st.divider()

        # ── Browser Blocking ──────────────────────────────────────────────────
        st.markdown("##### 🛡️ Browser Blocking")

        # Dropdowns nhỏ gọn cùng hàng
        dc1, dc2, _sp = st.columns([3, 4, 5])
        threat = dc1.selectbox("", list(_L3_OPTIONS), key=f"threat_{idx}",
                               label_visibility="collapsed")
        categories = _L3_OPTIONS[threat]
        default_cat = "Other Phishing" if threat == "Social Engineering" else "None"
        category = dc2.selectbox("", categories, index=categories.index(default_cat),
                                 key=f"cat_{idx}", label_visibility="collapsed")

        # Nút action — không dùng use_container_width để trông nhỏ hơn
        bc1, bc2, bc3, _bsp = st.columns([2, 2, 2, 6])
        if bc1.button("🤖 GSB", key=f"gsb_{idx}", type="primary",
                      help="Google Safe Browsing — tự điền form bằng Playwright"):
            res = pt.open_gsb_form_playwright(original_url, gsb_text,
                                              threat_type=threat, threat_category=category,
                                              dark_mode=dark_mode)
            st.success("✅ Đã mở Chrome và tự điền GSB.") if "error" not in res else st.error(res["error"])

        if bc2.button("🤖 SmartScreen", key=f"ms_{idx}", type="primary",
                      help="Microsoft SmartScreen — tự điền form"):
            res = pt.open_microsoft_form_playwright(original_url, dark_mode=dark_mode)
            st.success("✅ Đã mở SmartScreen.") if "error" not in res else st.error(res["error"])

        if bc3.button("📡 Netcraft", key=f"nc_{idx}", type="primary",
                      help="Gửi thẳng qua Netcraft API"):
            nc_email = cfg.get("contact_email", "")
            if not nc_email:
                res = {"error": "Chưa có contact_email trong config.ini"}
            else:
                with st.spinner("Đang gửi..."):
                    res = pt.report_netcraft_api(original_url, gsb_text, nc_email)
            _quick_report_cache().setdefault("netcraft_status", {})[original_url] = res

        netcraft_status = _quick_report_cache().get("netcraft_status", {}).get(original_url)
        if netcraft_status:
            status_text = _netcraft_status_text(netcraft_status)
            if netcraft_status.get("success"):
                st.success(f"✅ Netcraft {status_text}")
            else:
                st.error(f"❌ Netcraft {status_text}")

        st.caption("Nội dung dán vào ô Additional details:")
        st.code(gsb_text, language=None)

        # ── CDN / Registrar / Registry (chỉ khi có webform) ──────────────────
        sections = []
        if has_cdn: sections.append("cdn")
        if webform_url_r: sections.append("reg")
        if show_registry: sections.append("tld")

        if sections:
            st.divider()
            cols = st.columns(len(sections))
            col_map = {k: cols[i] for i, k in enumerate(sections)}

            if "cdn" in col_map:
                with col_map["cdn"]:
                    cdn_names = (["Cloudflare"] if cf else []) + [n.title() for n in cdn_detected]
                    st.markdown(f"##### ☁️ CDN")
                    st.caption(", ".join(cdn_names))
                    if cf:
                        info_cf = pt.CDN_ABUSE_CONTACTS["cloudflare"]
                        cf_text = pt.generate_cloudflare_report_text(domain, cfg)
                        st.link_button("↗ Cloudflare Abuse", info_cf["report_url"],
                                       type="primary")
                        st.code(cf_text, language=None)
                    for name in cdn_detected:
                        info = pt.CDN_ABUSE_CONTACTS.get(name)
                        if info:
                            st.link_button(f"↗ {name.title()} Abuse", info["report_url"])

            if "reg" in col_map:
                with col_map["reg"]:
                    st.markdown(f"##### 📋 Registrar")
                    st.caption(registrar)
                    st.link_button(f"↗ Form {registrar[:18]}", webform_url_r, type="primary")
                    draft_text = pt.get_webform_draft_text(
                        domain=domain, registrar=registrar, webform_url=webform_url_r,
                        cfg=cfg, target_url=original_url, urlscan=result.get("urlscan"),
                    )
                    st.code(draft_text, language=None)

            if "tld" in col_map:
                with col_map["tld"]:
                    reg_name = registry_info.get("registry", "Registry")
                    st.markdown(f"##### 🌐 TLD Registry")
                    st.caption(reg_name)
                    if registry_info.get("note"):
                        st.caption(f"ℹ️ {registry_info['note']}")
                    st.link_button(f"↗ Form {reg_name[:18]}", registry_info["report_webform"],
                                   type="primary")


def _run_one_cdn_check(target: str, urlscan_api_key: str = "") -> dict:
    """Check one target and always return a renderable result."""
    raw = target.strip()
    domain = pt.normalize_domain(raw)
    try:
        result = pt.run_cdn_check(raw)
    except Exception as exc:
        result = {
            "domain": domain,
            "cloudflare": False,
            "cdn_detected": [],
            "_error": str(exc),
        }
    result["_original_url"] = raw if "://" in raw else f"https://{domain}"
    if urlscan_api_key:
        try:
            result["urlscan"] = pt.urlscan_submit_and_wait(domain, urlscan_api_key)
        except Exception as exc:
            result["urlscan"] = {"error": str(exc)}
    return result


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
    st.info(
        f"Cache lọc đang ghi nhớ **{len(_quick_report_filter_cache())} URL** "
        "theo domain + subpath. Dùng nút Xóa cache bên dưới để làm lại từ đầu."
    )
    raw_paste = st.text_area("Nội dung thô", height=120, placeholder="789win\nhttps://example.com/vi-vn/ (top3)\nGhi chú...", key="raw_paste")
    if st.button("🔍 Lọc domain", key="btn_filter"):
        try:
            from domain_utils import extract_domains_from_text, filter_unseen_domains
            found = extract_domains_from_text(raw_paste)
            fresh, duplicate = filter_unseen_domains(found, _quick_report_filter_cache())
            _save_quick_report_filter_cache(_quick_report_filter_cache())
        except ImportError:
            found, _ = _parse_domains(raw_paste)
            seen = _quick_report_filter_cache()
            fresh = [item for item in found if pt.normalize_domain(item).lower().rstrip(".") not in seen]
            duplicate = [item for item in found if item not in fresh]
            seen.update(pt.normalize_domain(item).lower().rstrip(".") for item in fresh)
            _save_quick_report_filter_cache(seen)
        if fresh:
            st.session_state["qr_domain_input"] = "\n".join(fresh)
            message = f"Đã đưa xuống **{len(fresh)}** URL mới."
            if duplicate:
                message += f" Đã loại **{len(duplicate)}** URL trùng domain + subpath trong cache."
            st.success(message)
        elif found:
            st.session_state["qr_domain_input"] = ""
            st.warning(f"Toàn bộ {len(duplicate)} URL đã có cùng domain + subpath trong cache nên được loại bỏ.")
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

cache = _quick_report_cache()
filter_cache = _quick_report_filter_cache()
cache_col, cache_info_col = st.columns([1, 4])
if cache_col.button(
    "🗑️ Xóa cache",
    use_container_width=True,
    disabled=not cache and not filter_cache,
):
    for future in cache.get("pending", {}).values():
        future.cancel()
    cache.clear()
    filter_cache.clear()
    try:
        os.remove(_FILTER_CACHE_PATH)
    except FileNotFoundError:
        pass
    st.success("Đã xóa cache Quick Report. Bạn có thể dán danh sách mới.")
if cache or filter_cache:
    result_count = sum(r is not None for r in cache.get("results", []))
    result_total = len(cache.get("results", []))
    cache_info_col.caption(
        f"💾 Cache lọc: {len(filter_cache)} URL · "
        f"kết quả kiểm tra: {result_count}/{result_total}. Cache vẫn còn sau khi F5."
    )

if go:
    domains, invalid = _parse_domains(raw_domains)
    if invalid:
        st.warning("Domain không hợp lệ (bỏ qua): " + ", ".join(invalid[:10]))
    if not domains:
        st.warning("Chưa có domain hợp lệ để kiểm tra.")
    else:
        cfg = pt.load_config()
        signature = tuple(t.strip() for t in domains)
        total = len(domains)
        executor = _check_executor()
        for future in cache.get("pending", {}).values():
            future.cancel()
        cache.clear()
        cache.update({
            "signature": signature,
            "targets": domains,
            "results": [None] * total,
            "cfg": cfg,
        })
        urlscan_api_key = cfg.get("urlscan_api_key") or ""
        cache["pending"] = {
            i: executor.submit(_run_one_cdn_check, target, urlscan_api_key)
            for i, target in enumerate(domains)
        }
        st.info(
            f"Đã bắt đầu kiểm tra mới {total} domain ở nền. "
            "Các nút report dùng được ngay khi từng kết quả xuất hiện."
        )


@st.fragment(run_every=1)
def _render_results() -> None:
    cache = _quick_report_cache()
    if "results" not in cache:
        return

    results: list = cache["results"]
    cfg: dict = cache["cfg"]
    total = len(results)
    pending: dict = cache.get("pending", {})

    for index, future in list(pending.items()):
        if future.done():
            try:
                results[index] = future.result()
            except Exception as exc:
                target = cache.get("targets", [""] * total)[index]
                results[index] = {
                    "domain": pt.normalize_domain(target),
                    "cloudflare": False,
                    "cdn_detected": [],
                    "_original_url": target,
                    "_error": str(exc),
                }
            del pending[index]

    completed = sum(result is not None for result in results)
    if total:
        label = (
            f"✅ Hoàn tất {total}/{total} domain."
            if not pending
            else f"Đã xong {completed}/{total} domain — đang kiểm tra nền..."
        )
        st.progress(completed / total, text=label)

    if not pending:
        cache.pop("pending", None)

    st.divider()
    st.markdown(f"### Kết quả trực tiếp — {completed}/{total} domain")

    # T8: nic.top Excel export cho domain .top
    top_domains = [
        r["domain"] for r in results
        if r is not None and r["domain"].lower().endswith(".top")
    ]
    if top_domains:
        with st.expander(f"📊 Export Excel cho nic.top ({len(top_domains)} domain .top)", expanded=True):
            st.caption(
                "nic.top nhận báo cáo hàng loạt qua file Excel (tối đa 200 domain/lần). "
                "Tải file rồi gửi kèm ảnh chụp màn hình đến **abuse@nic.top** hoặc qua form "
                "[nic.top/cn/Complaintsnew.asp](https://www.nic.top/cn/Complaintsnew.asp)."
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
        if result is None:
            target = cache.get("targets", [""] * total)[i]
            st.info(f"⏳ #{i + 1}/{total} Đang chờ: {target}")
            continue
        if result.get("_error"):
            st.warning(f"Không thể check đầy đủ {result['domain']}: {result['_error']}")
        _render_domain_block(i, total, result, cfg, dark_mode=st.session_state.get("chrome_dark_mode", True))


_render_results()

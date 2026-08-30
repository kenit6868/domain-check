"""Automatic evidence, provider resolution, draft and explicit-send cloaking workflow."""

import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

import phishing_toolkit as pt
from cloaking_probe import collect_cloaking_evidence
from cloaking_render_probe import collect_rendered_evidence
from cloaking_workflow import build_attachments, build_cloaking_email, resolve_provider, select_html_pair


def _zip_evidence(email: dict, attachments: list[dict]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report_email.txt", f"Subject: {email['subject']}\n\n{email['body']}")
        for attachment in attachments:
            archive.writestr(attachment["filename"], attachment["data"])
    return output.getvalue()


class _MemoryUpload:
    def __init__(self, value: dict):
        self.name, self.type, self._data = value["name"], value["type"], value["data"]

    def getvalue(self):
        return self._data


st.set_page_config(page_title="Cloaking Report", page_icon="🫥", layout="wide")
st.title("🫥 Cloaking Report")
st.caption("Nhập URL + keyword và tải hai ảnh đối chứng; hệ thống tự lấy HTML, xác định NCC, tạo report và chỉ gửi khi bạn bấm Gửi.")
st.warning("Chỉ dùng với case đã được phép kiểm tra. Công cụ không đăng nhập, không bấm nút trên site và không tự submit report.")

left, right = st.columns(2)
with left:
    reported_url = st.text_input("URL cloaking *", placeholder="https://example.com/vi-vn/")
    desktop_image = st.file_uploader("Ảnh PC — trang bình phong *", type=["png", "jpg", "jpeg", "webp"])
with right:
    keyword = st.text_input("Keyword Google *", placeholder="98win")
    mobile_image = st.file_uploader("Ảnh mobile từ Google — nội dung vi phạm *", type=["png", "jpg", "jpeg", "webp"])

authorized = st.checkbox("Tôi xác nhận hai ảnh là cùng URL và cho phép gửi các request curl read-only để lấy HTML/evidence.")
inputs_ready = bool(reported_url.strip() and keyword.strip() and desktop_image and mobile_image and authorized)
collect = st.button("Tạo đầy đủ hồ sơ", type="primary", disabled=not inputs_ready)

if collect:
    with st.status("Đang lấy HTML và xác định nhà cung cấp...", expanded=True) as status:
        raw_probe = collect_cloaking_evidence(reported_url)
        probe = raw_probe
        pair = select_html_pair(probe)
        render_error = None
        if not pair:
            status.update(label="Curl chưa tái hiện — đang render JavaScript bằng mobile giả lập...")
            try:
                rendered_probe = collect_rendered_evidence(reported_url, keyword)
                if rendered_probe.get("reportable_cloaking"):
                    rendered_probe["technical_summary"] = (
                        rendered_probe.get("technical_summary", "")
                        + "\n\nSupporting raw curl evidence:\n"
                        + raw_probe.get("curl_summary", "")
                    )
                    probe = rendered_probe
                    pair = ("desktop_direct", "mobile_google")
                else:
                    render_error = rendered_probe.get("error") or "Rendered DOM vẫn không có khác biệt đủ mạnh."
            except Exception as exc:
                render_error = str(exc)
        provider = resolve_provider(reported_url)
        status.update(label="Đã hoàn tất thu thập", state="complete")
    st.session_state["cloaking_case"] = {
        "url": reported_url.strip(), "keyword": keyword.strip(),
        "probe": probe, "raw_probe": raw_probe, "provider": provider, "pair": pair,
        "render_error": render_error,
        "desktop_image": {"name": desktop_image.name, "type": desktop_image.type, "data": desktop_image.getvalue()},
        "mobile_image": {"name": mobile_image.name, "type": mobile_image.type, "data": mobile_image.getvalue()},
    }
    st.session_state.pop("cloaking_send_result", None)

case = st.session_state.get("cloaking_case")
if case and case.get("url") == reported_url.strip() and case.get("keyword") == keyword.strip():
    probe, raw_probe, provider, pair = case["probe"], case.get("raw_probe", case["probe"]), case["provider"], case["pair"]
    st.divider()
    st.subheader("1. Kết quả tự động")
    a, b, c, d = st.columns(4)
    a.metric("Curl thành công", f"{raw_probe.get('successful_count', 0)}/4")
    b.metric("Curl PC/mobile", "Có" if raw_probe.get("device_difference") else "Không")
    c.metric("Curl direct/Google", "Có" if raw_probe.get("referrer_difference") else "Không")
    d.metric("Google click fallback", "Đối chứng được" if probe.get("capture_method") == "rendered_google_click" else "Không cần")

    p1, p2, p3 = st.columns(3)
    p1.write(f"**Registrar:** {provider.get('registrar')}")
    p2.write(f"**Kênh email:** {provider.get('recipient') or 'Không có — dùng web form'}")
    p3.write(f"**Cloudflare:** {'Có' if provider.get('cloudflare') else 'Không'}")

    if not pair:
        st.error("Curl và DOM sau render đều chưa tạo được cặp đối chứng. Chưa tạo report và không cho gửi để tránh hồ sơ bị bác hoặc bị xem là spam.")
        if case.get("render_error"):
            st.warning(f"Rendered fallback: {case['render_error']}")
        with st.expander("Xem curl evidence"):
            st.code(probe.get("curl_summary", ""), language=None)
        st.stop()

    desktop = _MemoryUpload(case["desktop_image"])
    mobile = _MemoryUpload(case["mobile_image"])
    cfg = pt.load_config()
    attachments = build_attachments(probe=probe, pair=pair, desktop_image=desktop, mobile_image=mobile)
    email = build_cloaking_email(
        reported_url=reported_url, keyword=keyword, provider=provider, probe=probe,
        pair=pair, desktop_image_name=desktop.name, mobile_image_name=mobile.name,
        reporter_name=cfg.get("contact_name", ""), reporter_email=cfg.get("contact_email", ""),
    )

    method = "Google click thật + DOM sau render" if probe.get("capture_method") == "rendered_google_click" else "HTML curl"
    st.success(f"Đã chọn cặp đối chứng bằng {method}: {pair[0]} ↔ {pair[1]}")
    col1, col2 = st.columns(2)
    col1.image(desktop.getvalue(), caption=f"PC: {desktop.name}", use_container_width=True)
    col2.image(mobile.getvalue(), caption=f"Mobile/Google: {mobile.name}", use_container_width=True)

    st.subheader("2. Report và evidence")
    st.text_input("Subject", value=email["subject"], disabled=True)
    st.text_area("Nội dung gửi NCC", value=email["body"], height=430, disabled=True)
    st.download_button(
        "Tải toàn bộ hồ sơ ZIP", _zip_evidence(email, attachments),
        file_name=f"{provider['domain']}_cloaking_report.zip", mime="application/zip",
    )
    with st.expander("Xem evidence kỹ thuật"):
        st.code(probe.get("technical_summary") or probe.get("curl_summary", ""), language=None)

    st.subheader("3. Gửi NCC")
    if provider.get("webform"):
        st.info(f"{provider['registrar']} chỉ nhận report qua web form; không gửi SMTP tới một địa chỉ không được hỗ trợ.")
        st.link_button("Mở form registrar", provider["webform"], type="primary")
    elif provider.get("recipient"):
        accounts = cfg.get("smtp_accounts", [])
        if not accounts:
            st.error("Chưa cấu hình smtp_accounts trong config.ini nên chưa thể gửi.")
        else:
            sent_key = f"{provider['domain']}|{email['captured_at']}"
            already_sent = st.session_state.get("cloaking_sent_key") == sent_key
            if st.button(
                f"Gửi report tới {provider['recipient']}", type="primary", disabled=already_sent,
                help="Cú bấm này gửi email thật cùng 5 file evidence.",
            ):
                proxy = (cfg.get("smtp_proxies") or [None])[0]
                with st.spinner("Đang gửi email và file evidence..."):
                    send_result = pt.send_report_email_single_with_attachments(
                        provider["recipient"], email["subject"], email["body"],
                        attachments, accounts[0], proxy,
                    )
                st.session_state["cloaking_send_result"] = send_result
                try:
                    pt.log_sent({
                        "timestamp": email["captured_at"], "domain": provider["domain"],
                        "draft": "cloaking_report", "recipient": provider["recipient"],
                        "account": accounts[0].get("username", ""),
                        "success": bool(send_result.get("success")),
                        "error": send_result.get("error") or "",
                    })
                except Exception:
                    pass
                if send_result.get("success"):
                    st.session_state["cloaking_sent_key"] = sent_key
                    st.rerun()
            send_result = st.session_state.get("cloaking_send_result")
            if send_result:
                if send_result.get("success"):
                    st.success(f"Đã gửi thành công tới {provider['recipient']}.")
                else:
                    st.error(f"Gửi thất bại: {send_result.get('error')}")
    else:
        st.error("Không tìm thấy abuse email hoặc web form đáng tin cậy. Nút gửi bị khóa; hãy kiểm tra registrar trước khi report.")

    if provider.get("cloudflare_form"):
        st.caption("Domain dùng Cloudflare: có thể nộp thêm cùng evidence qua kênh Cloudflare.")
        st.link_button("Mở Cloudflare Abuse Form", provider["cloudflare_form"])

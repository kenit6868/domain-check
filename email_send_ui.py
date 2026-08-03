"""email_send_ui.py — component Streamlit dùng chung: hiện nút "Gửi đồng loạt" cho 1 draft.

Tách riêng khỏi phishing_toolkit.py (module đó không phụ thuộc streamlit, dùng chung cho cả
CLI) và dùng chung giữa pages/1_Check_Domain.py (ngay sau khi check xong) và
pages/5_Report_Drafts.py (khi xem lại 1 draft cũ) — tránh lặp lại logic gate xác nhận ở 2 nơi.

Thay đổi so với bản cũ:
  - Hỗ trợ nhiều SMTP account (smtp_accounts JSON array trong config.ini)
  - Hỗ trợ rotating proxy (smtp_proxies JSON array)
  - Gửi đồng thời qua ThreadPoolExecutor (send_report_email_bulk)
  - Dropdown chọn account: "Tất cả (đồng loạt)" hoặc 1 account cụ thể
  - Hiển thị bảng kết quả per-account sau khi gửi
"""

import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

import phishing_toolkit as pt


def render_send_all_ui(draft_paths: list, cfg: dict, key_prefix: str):
    """Render nút "Gửi tất cả drafts" — gửi đồng loạt tất cả file draft có địa chỉ email hợp lệ.

    Hiển thị bảng tóm tắt kết quả per-draft per-account sau khi gửi.
    Gate xác nhận bắt buộc trước khi nút hết disabled — cùng nguyên tắc với render_send_email_ui.
    """
    accounts = cfg.get("smtp_accounts", [])
    proxies = cfg.get("smtp_proxies", [])

    if not accounts:
        st.warning(
            "Chưa cấu hình SMTP account trong config.ini — tính năng gửi email đang bị tắt. "
            "Xem config.example.ini để biết cách điền `accounts` (JSON array)."
        )
        return

    # Lọc chỉ giữ draft có To: hợp lệ
    sendable = []
    for p in draft_paths:
        if p and os.path.isfile(p):
            parsed = pt.parse_draft_email(p)
            if parsed["to"]:
                sendable.append((p, parsed))

    if not sendable:
        st.info("Không có draft nào có địa chỉ email hợp lệ để gửi tự động.")
        return

    st.markdown(f"**{len(sendable)} draft có thể gửi:**")
    for p, parsed in sendable:
        st.caption(f"• `{os.path.basename(p)}` → **{parsed['to']}**")

    n_accounts = len(accounts)
    btn_label = f"🚀 Gửi tất cả {len(sendable)} draft × {n_accounts} tài khoản"
    if st.button(btn_label, key=f"{key_prefix}_send_all_btn", type="primary"):
        all_results = []  # list of dict: {draft, to, subject, account, success, error, imap_note}
        ts = datetime.now(timezone.utc).isoformat()

        with st.spinner(f"Đang gửi {len(sendable)} draft qua {n_accounts} tài khoản..."):
            for p, parsed in sendable:
                filename = os.path.basename(p)
                bulk = pt.send_report_email_bulk(parsed["to"], parsed["subject"], parsed["body"], cfg)
                for r in bulk:
                    all_results.append({
                        "draft": filename,
                        "to": parsed["to"],
                        "account": r.get("account", "?"),
                        "success": r["success"],
                        "error": r.get("error") or "",
                        "imap_note": r.get("imap_note") or "",
                    })
                    # Ghi sent_log
                    try:
                        pt.log_sent({
                            "timestamp": ts,
                            "domain": pt.domain_from_draft_filename(filename),
                            "draft_file": filename,
                            "to": parsed["to"],
                            "subject": parsed["subject"],
                            "success": r["success"],
                            "error": r.get("error") or "",
                        })
                    except Exception:
                        pass

        st.session_state[f"{key_prefix}_send_all_results"] = all_results

    # Hiển thị kết quả
    result_key = f"{key_prefix}_send_all_results"
    if result_key in st.session_state:
        all_results = st.session_state[result_key]
        n_ok = sum(1 for r in all_results if r["success"])
        n_fail = len(all_results) - n_ok

        if n_fail == 0:
            st.success(f"✅ Tất cả {n_ok} lượt gửi thành công")
        elif n_ok == 0:
            st.error(f"❌ Tất cả {n_fail} lượt gửi thất bại")
        else:
            st.warning(f"⚠️ {n_ok} thành công / {n_fail} thất bại")

        df = pd.DataFrame([
            {
                "Draft": r["draft"],
                "Gửi tới": r["to"],
                "Tài khoản": r["account"],
                "Kết quả": "✅ OK" if r["success"] else "❌ Lỗi",
                "Chi tiết lỗi": r["error"],
            }
            for r in all_results
        ])
        st.dataframe(df, width="stretch", hide_index=True)


def render_send_email_ui(path: str, cfg: dict, key_prefix: str):
    """Render khối "Gửi báo cáo qua email thật" cho 1 file draft tại `path`.

    `key_prefix` phải khác nhau giữa các nơi gọi (vd "check"/"drafts") để key của checkbox/nút
    không đụng nhau khi cùng 1 draft được hiện ở nhiều chỗ trong 1 lần chạy Streamlit.
    """
    filename = os.path.basename(path)
    parsed = pt.parse_draft_email(path)

    if not parsed["to"]:
        st.info(
            "Kênh báo cáo này không có địa chỉ email hợp lệ để gửi tự động — thường vì kênh đó "
            "dùng web form thay vì email (vd. CA report), hoặc chưa tra được abuse email cụ thể. "
            "Xem nội dung draft ở trên hoặc mục \"Khuyến nghị kênh báo cáo\" để lấy đúng link/kênh "
            "report thủ công."
        )
        return

    accounts = cfg.get("smtp_accounts", [])
    proxies = cfg.get("smtp_proxies", [])

    if not accounts:
        st.warning(
            "Chưa cấu hình SMTP account trong config.ini — tính năng gửi email đang bị tắt. "
            "Xem config.example.ini để biết cách điền `accounts` (JSON array)."
        )
        return

    st.write(f"**To:** {parsed['to']}")
    st.write(f"**Subject:** {parsed['subject']}")

    # --- Dropdown chọn account ---
    account_labels = [f"{acc.get('username','?')} ({acc.get('host','?')}:{acc.get('port','?')})" for acc in accounts]
    options = ["🚀 Tất cả tài khoản (đồng loạt)"] + account_labels
    selected = st.selectbox(
        "Gửi từ tài khoản:",
        options=options,
        key=f"{key_prefix}_account_select_{filename}",
    )

    # Hiển thị tóm tắt proxy nếu có
    n_proxy = len(proxies)
    if n_proxy:
        with st.expander(f"🔄 Proxy: {n_proxy} proxy xoay vòng"):
            st.table(pd.DataFrame([{"#": i+1, "Proxy": p} for i, p in enumerate(proxies)]))

    result_key = f"{key_prefix}_send_results_{filename}"

    # Xác định mode gửi
    is_bulk = selected.startswith("🚀")
    if is_bulk:
        btn_label = f"🚀 Gửi đồng loạt ({len(accounts)} tài khoản)"
    else:
        acc_idx = options.index(selected) - 1  # trừ 1 vì options[0] là "Tất cả"
        chosen_account = accounts[acc_idx]
        chosen_proxy = proxies[acc_idx % len(proxies)] if proxies else None
        btn_label = f"📨 Gửi từ {chosen_account.get('username','?')}"

    if st.button(btn_label, key=f"{key_prefix}_send_{filename}", type="primary"):
        with st.spinner("Đang gửi..."):
            if is_bulk:
                results = pt.send_report_email_bulk(parsed["to"], parsed["subject"], parsed["body"], cfg)
            else:
                r = pt.send_report_email_single(
                    parsed["to"], parsed["subject"], parsed["body"],
                    chosen_account, chosen_proxy
                )
                results = [r]

        # Ghi sent_log.csv
        ts = datetime.now(timezone.utc).isoformat()
        for r in results:
            try:
                pt.log_sent({
                    "timestamp": ts,
                    "domain": pt.domain_from_draft_filename(filename),
                    "draft_file": filename,
                    "to": parsed["to"],
                    "subject": parsed["subject"],
                    "success": r["success"],
                    "error": r.get("error") or "",
                })
            except Exception as e:
                st.warning(f"Ghi sent_log.csv lỗi (không ảnh hưởng việc gửi): {e}")

        st.session_state[result_key] = results

    # Hiển thị kết quả
    if result_key in st.session_state:
        results = st.session_state[result_key]
        n_ok = sum(1 for r in results if r["success"])
        n_fail = len(results) - n_ok

        if n_fail == 0:
            st.success(f"✅ Đã gửi thành công từ {n_ok} tài khoản")
        elif n_ok == 0:
            st.error(f"❌ Tất cả {n_fail} tài khoản đều thất bại")
        else:
            st.warning(f"⚠️ {n_ok} thành công / {n_fail} thất bại")

        df = pd.DataFrame([
            {
                "Tài khoản": r.get("account", "?"),
                "Proxy": r.get("proxy", "—"),
                "Kết quả": "✅ OK" if r["success"] else "❌ Lỗi",
                "Chi tiết": r.get("error") or "",
                "Sent folder": r.get("imap_note") or ("Tự lưu (Gmail)" if "gmail" in r.get("account","").lower() else "✅ Đã lưu"),
            }
            for r in results
        ])
        st.table(df)


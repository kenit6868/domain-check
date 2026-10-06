"""Native manual cloaking review controls backed by the shared queue/sender."""

import hashlib
import json
import os

import streamlit as st

import lead_triage
import cloaking_review_queue as review_queue
import cloaking_review_sender as review_sender
import phishing_toolkit as pt


def render_review(result, cfg, selected_account, raw_note, backend_results,
                  additional_english, evidence_key, preview_key, *, persist, show_preview):
    target = result["check_url"]
    account_name = str((selected_account or {}).get("username") or "")
    queue_id = review_queue.queue_id_for(review_queue.current_review_day(),
                                         review_queue.canonical_target_url(target))
    item = review_queue.load_item(queue_id)
    if item and item.get("state") == review_queue.SENT:
        st.success("Case này đã gửi đủ delivery trong Cloaking Review.")
        return
    notice = st.session_state.pop(preview_key + "_notice", "")
    if notice:
        st.info(notice)
    cloaking = ((item or {}).get("result") or {}).get("cloaking_result") or lead_triage.review_cloaking_result(result)
    status = review_sender.confirmed_evidence_status(cloaking)
    st.warning(f"Nguy cơ cloaking: {cloaking.get('verdict') or 'INCONCLUSIVE'}. Chọn chế độ tạo draft sau khi xem bằng chứng.")
    if status["ready"]:
        for path in status["attachments"][1:]:
            st.image(path, caption=os.path.basename(path), width=240)
    else:
        st.caption(status["reason"])
    if st.button("Bổ sung ảnh tại Cloaking Review", key=preview_key + "_evidence"):
        try:
            item = lead_triage.ensure_lead_review_case(
                result, cfg, [account_name] if account_name else [
                    value["username"] for value in cfg.get("smtp_accounts") or [] if value.get("username")])
            st.session_state["cloaking_review_active_case"] = item["queue_id"]
            persist()
            st.switch_page("pages/10_Cloaking_Review.py")
        except (OSError, ValueError) as exc:
            st.error(str(exc))
    if st.button("Kiểm tra lại email nhận (Domain Worker)", key=preview_key + "_recipient"):
        lead_triage.refresh_worker_recipients(result)
        persist()
    if result.get("recipients_error"):
        st.warning("Chưa tra được email nhận; kiểm tra lại trước khi tạo draft.")
    left, right = st.columns(2)
    with left:
        confirmed = st.button("Tạo draft cloaking", key=preview_key + "_confirmed",
                              disabled=not account_name or not status["ready"],
                              help="Xác nhận cloaking theo cặp ảnh đang xem; chưa gửi email.")
    with right:
        normal = st.button("Tạo draft không cloaking", key=preview_key + "_normal",
                           disabled=not account_name,
                           help="Loại evidence cloaking, chụp DOM nguồn–đích nếu chưa có; chưa gửi email.")
    evidence = st.session_state.get(evidence_key) or {}
    if confirmed or normal:
        st.session_state.pop(preview_key, None)
        decision = review_sender.CONFIRMED_CLOAKING if confirmed else review_sender.NOT_CLOAKING
        try:
            with st.spinner("Đang tạo draft để duyệt..."):
                preview = lead_triage.prepare_lead_review(
                    result, raw_note, decision=decision, account_name=account_name, cfg=cfg,
                    evidence=evidence, backend_results=backend_results,
                    additional_english=additional_english)
            st.session_state[preview_key] = preview
            if decision == review_sender.NOT_CLOAKING:
                evidence = preview["normal_browser_evidence"]
                st.session_state[evidence_key] = evidence
            persist()
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(str(exc))
            persist()
    preview = st.session_state.get(preview_key)
    if not preview:
        return
    item = review_queue.load_item(preview["queue_id"])
    current = bool(
        preview.get("lead_context") == lead_triage.review_context_fingerprint(
            result, raw_note, backend_results, additional_english, account_name, cfg)
        and review_sender.preparation_is_current(
            preview, item, decision=preview["decision"], account_names=[account_name],
            normal_browser_evidence=evidence if preview["decision"] == review_sender.NOT_CLOAKING else None)
    )
    if not current:
        st.warning("Draft không còn khớp tài khoản, nội dung hoặc evidence; hãy tạo lại.")
        return
    st.caption("Draft cloaking" if preview["decision"] == review_sender.CONFIRMED_CLOAKING else "Draft không cloaking")
    for path in preview["attachments"]:
        if os.path.splitext(path)[1].lower() in {".png", ".jpg", ".jpeg"}:
            st.image(path, caption=os.path.basename(path), width=240)
    account = next(value for value in lead_triage._review_cfg(cfg)["smtp_accounts"]
                   if str(value.get("username") or "").lower() == account_name.lower())
    logo = pt._signature_logo_path(account)
    for delivery in preview["deliveries"]:
        st.markdown(f"**Email nhận:** {delivery['to']}")
        st.markdown(f"**Subject:** {delivery['subject']}")
        english, vietnamese = st.columns(2)
        with english:
            st.markdown("**English — nội dung sẽ gửi**")
            show_preview(delivery["body"], logo)
        with vietnamese:
            st.markdown("**Tiếng Việt — bản đối chiếu**")
            show_preview(delivery["body_vi"], logo, vietnamese=True)
    token = hashlib.sha256(json.dumps(
        [preview["deliveries"], preview["attachment_fingerprints"]], sort_keys=True).encode()).hexdigest()[:16]
    approved = st.checkbox("Tôi đã duyệt draft, email nhận và ảnh; xác nhận gửi theo chế độ đã chọn",
                           key=preview_key + "_approve_" + token)
    if st.button("Gửi report đã duyệt", key=preview_key + "_send", type="primary", disabled=not approved):
        try:
            with st.spinner("Đang gửi report đã duyệt..."):
                outcome = lead_triage.send_lead_review(
                    preview, result=result, raw_note=raw_note, cfg=cfg, account_name=account_name,
                    backend_results=backend_results, additional_english=additional_english, evidence=evidence)
            st.session_state[preview_key + "_notice"] = (
                f"Đã gửi: {outcome.get('sent_ok', 0)} · Đã gửi trước: {outcome.get('already_sent', 0)} "
                f"· Lỗi: {outcome.get('sent_failed', 0)} · {outcome.get('queue_state') or ''}")
            if outcome.get("queue_state") == review_queue.SENT:
                statuses = st.session_state.get("lead_triage_delivery_status") or {}
                statuses[result["target"]] = True
                st.session_state["lead_triage_delivery_status"] = statuses
            persist()
            st.rerun()
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(str(exc))

(() => {
  "use strict";
  const visible = (el) => !!el && !el.disabled && !!el.getClientRects().length;
  const norm = (value) => String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  const setValue = (el, value) => {
    if (!el || !value) return;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, {bubbles:true}));
  };
  const chooseType = async (control, wanted, doc, sleep) => {
    control.dispatchEvent(new MouseEvent("mousedown", {
      bubbles: true, cancelable: true, view: window, button: 0,
    }));
    for (let i = 0; i < 20; i += 1) {
      const option = [...doc.querySelectorAll('[role="option"]')]
        .filter(visible).find((item) =>
          item.getAttribute("data-value") === "1" || norm(item.textContent) === norm(wanted)
        );
      if (option) {
        option.click();
        for (let verify = 0; verify < 10; verify += 1) {
          const hidden = doc.querySelector('input[name="type"]');
          if (hidden?.value === "1" && norm(control.textContent) === norm(wanted)) return true;
          await sleep(100);
        }
        return false;
      }
      await sleep(100);
    }
    return false;
  };
  const adapter = {
    id: "coccoc_safe",
    version: "1.0.1",
    successMessage: "Cốc Cốc Safe confirmed receipt of the report.",
    matches: (loc) => loc.hostname === "safe.coccoc.com",
    detectSuccess: (doc) => /gửi báo cáo thành công|cảm ơn bạn đã báo cáo/i.test(doc.body?.innerText || ""),
    captchaPending: (doc) => {
      const response = doc.querySelector('textarea[name="g-recaptcha-response"]');
      return !!response && !response.value;
    },
    waitUntilReady: async ({document: doc, status, sleep}) => {
      for (let i = 0; i < 80; i += 1) {
        const url = doc.querySelector("#domainsiteurl");
        const type = doc.querySelector("#mui-component-select-type");
        const typeInput = doc.querySelector('input[name="type"]');
        const contact = doc.querySelector('input[name="user_report"]');
        const details = doc.querySelector('textarea[name="note"]');
        if ([url, type, contact, details].every(visible) && typeInput) return {url, type, typeInput, contact, details};
        status(`coccoc_safe@1.0.1; waiting… inputs=${doc.querySelectorAll("input").length}, textareas=${doc.querySelectorAll("textarea").length}`);
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields, {document: doc, sleep}) => {
      const typeReady = await chooseType(fields.type, task.report_type || "Trang web lừa đảo", doc, sleep);
      setValue(fields.url, task.target_url);
      setValue(fields.contact, task.contact_email);
      setValue(fields.details, task.draft);
      const valid = !!task.contact_email && fields.url.value === task.target_url &&
        fields.contact.value === task.contact_email && fields.details.value === task.draft &&
        typeReady && fields.typeInput.value === "1";
      return {
        valid,
        status: valid ? "fields filled" : "fields partially filled",
        message: valid ? "Filled URL, phishing category, contact and report details." : "Form was partially filled; review contact or violation type manually.",
      };
    },
    validate: (fields) => ({
      valid: !!fields.url.value && !!fields.contact.value && !!fields.details.value && fields.typeInput.value === "1",
      message: "URL, contact or description is missing.",
    }),
    submit: () => false,
  };
  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(adapter);
})();

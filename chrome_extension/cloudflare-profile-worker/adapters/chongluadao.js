(() => {
  "use strict";
  const visible = (el) => !!el && !el.disabled && !!el.getClientRects().length;
  const setValue = (el, value) => {
    if (!el || !value) return;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, {bubbles:true}));
  };
  const adapter = {
    id: "chongluadao",
    version: "1.0.0",
    successMessage: "Chống Lừa Đảo confirmed receipt of the report.",
    matches: (loc) => loc.hostname === "chongluadao.vn" && loc.pathname.includes("/report/reportphishing"),
    detectSuccess: (doc) => /gửi báo cáo thành công|report submitted successfully|thank you/i.test(doc.body?.innerText || ""),
    captchaPending: (doc) => {
      const response = doc.querySelector('textarea[name="g-recaptcha-response"]');
      return !!response && !response.value;
    },
    waitUntilReady: async ({document: doc, status, sleep}) => {
      for (let i = 0; i < 80; i += 1) {
        const email = [...doc.querySelectorAll('input[type="email"], input[placeholder="Your Email"]')].find(visible);
        const url = [...doc.querySelectorAll('input[placeholder="Malicious URL"]')].find(visible);
        const type = [...doc.querySelectorAll("select")].find(visible);
        const details = [...doc.querySelectorAll('textarea[placeholder="Further details"]')].find(visible);
        if (email && url && type && details) return {email, url, type, details};
        status(`chongluadao@1.0.0; waiting… inputs=${doc.querySelectorAll("input").length}, textareas=${doc.querySelectorAll("textarea").length}`);
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields) => {
      const wanted = task.report_type || "Phishing";
      const option = [...fields.type.options].find((item) => item.textContent.trim().toLowerCase() === wanted.toLowerCase());
      if (option) {
        fields.type.value = option.value;
        fields.type.dispatchEvent(new Event("change", {bubbles:true}));
      }
      setValue(fields.email, task.contact_email);
      setValue(fields.url, task.target_url);
      setValue(fields.details, task.draft);
      const valid = !!task.contact_email && fields.email.value === task.contact_email &&
        fields.url.value === task.target_url && fields.details.value === task.draft && !!option;
      return {
        valid,
        status: valid ? "fields filled" : "fields partially filled",
        message: valid ? "Filled email, malicious URL, phishing type and report details." : "Form was partially filled; review email or report type manually.",
      };
    },
    validate: (fields) => ({
      valid: !!fields.email.value && !!fields.url.value && !!fields.details.value && !!fields.type.value,
      message: "Email, URL, report type or details are missing.",
    }),
    submit: () => false,
  };
  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(adapter);
})();

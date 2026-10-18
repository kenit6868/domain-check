(() => {
  "use strict";

  const visible = (el) => !!el && !el.disabled && !!el.getClientRects().length;
  const setValue = (el, value) => {
    if (!el || !value) return;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, {bubbles: true}));
  };
  const selectValue = (el, value) => {
    if (!el) return;
    const option = [...el.options].find((item) => item.value.toLowerCase() === value.toLowerCase());
    if (!option) return;
    el.value = option.value;
    el.dispatchEvent(new Event("change", {bubbles: true}));
  };
  const domainFromUrl = (targetUrl) => {
    try {
      return new URL(targetUrl).hostname;
    } catch (_error) {
      return String(targetUrl || "").replace(/^https?:\/\//i, "").split(/[/?#]/, 1)[0];
    }
  };

  const adapter = {
    id: "xyz_registry_abuse",
    version: "1.0.0",
    successMessage: "XYZ.COM LLC confirmed receipt of the abuse ticket.",
    matches: (loc) => loc.hostname === "gen.xyz" &&
      loc.pathname === "/account/submitticket.php" &&
      new URLSearchParams(loc.search).get("deptid") === "6",
    detectSuccess: (doc) => /ticket.*(?:created|submitted)|request.*received|thank you/i
      .test(doc.body?.innerText || ""),
    captchaPending: (doc) => {
      const response = doc.querySelector('textarea[name="g-recaptcha-response"], [name*="captcha" i]');
      return !!response && !response.value;
    },
    waitUntilReady: async ({document: doc, status, sleep}) => {
      for (let i = 0; i < 80; i += 1) {
        const fields = {
          name: doc.querySelector("#name"),
          email: doc.querySelector("#email"),
          type: doc.querySelector("#customfield7"),
          domain: doc.querySelector("#subject"),
          details: doc.querySelector("#message"),
        };
        if (Object.values(fields).every(visible)) return fields;
        status(`xyz_registry_abuse@1.0.0; waiting… inputs=${doc.querySelectorAll("input").length}, textareas=${doc.querySelectorAll("textarea").length}`);
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields) => {
      const domain = domainFromUrl(task.target_url);
      setValue(fields.name, task.contact_name);
      setValue(fields.email, task.contact_email);
      selectValue(fields.type, "Phishing");
      setValue(fields.domain, domain);
      setValue(fields.details, task.draft);
      const valid = !!task.contact_name && !!task.contact_email && !!domain && !!task.draft &&
        fields.name.value === task.contact_name && fields.email.value === task.contact_email &&
        fields.type.value === "Phishing" && fields.domain.value === domain &&
        fields.details.value === task.draft;
      return {
        valid,
        status: valid ? "fields filled" : "fields partially filled",
        message: valid
          ? "Filled reporter, phishing type, abusive domain and ticket details. Attachment and submit remain manual."
          : "XYZ ticket was partially filled; review the highlighted fields manually.",
      };
    },
    validate: (fields) => ({
      valid: !!fields.name.value && !!fields.email.value &&
        fields.type.value === "Phishing" && !!fields.domain.value && !!fields.details.value,
      message: "Reporter, phishing type, domain or ticket details are missing.",
    }),
    submit: () => false,
  };

  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(adapter);
})();

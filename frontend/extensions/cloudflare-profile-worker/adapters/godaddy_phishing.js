(() => {
  "use strict";
  const visible = (el) => !!el && !el.disabled && !!el.getClientRects().length;
  const setValue = (el, value) => {
    if (!el || !value) return;
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    for (const type of ["input", "change", "blur"])
      el.dispatchEvent(new Event(type, {bubbles: true}));
  };
  const readFields = (doc) => ({
    email: doc.querySelector("#email-field"), company: doc.querySelector("#company-field"),
    source: doc.querySelector("#source-field"), details: doc.querySelector("#info-field"),
  });
  const validate = (fields) => ({
    valid: ["email", "company", "source", "details"].every((key) => !!fields[key]?.value.trim()),
    message: "GoDaddy email, brand, reported URL or description is missing.",
  });

  const adapter = {
    id: "godaddy_phishing",
    version: "1.0.1",
    successMessage: "GoDaddy confirmed receipt of the phishing report.",
    matches: (loc) => loc.hostname === "legalportal.godaddy.com" &&
      loc.pathname.startsWith("/abuse/phishing"),
    detectSuccess: (doc) => /report (has been|was) submitted|thank you for your report|confirmation/i
      .test(doc.body?.innerText || ""),
    captchaPending: () => false,
    waitUntilReady: async ({document: doc, status, sleep}) => {
      for (let i = 0; i < 80; i += 1) {
        const fields = readFields(doc);
        if (Object.values(fields).every(visible)) return fields;
        status(`godaddy_phishing@1.0.1; waiting… inputs=${doc.querySelectorAll("input").length}, textareas=${doc.querySelectorAll("textarea").length}`);
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields, {sleep}) => {
      const doc = fields.email.ownerDocument;
      const expected = {email: task.contact_email, company: task.brand_name,
        source: task.target_url, details: task.draft};
      const matches = () => Object.entries(expected).every(([key, value]) =>
        !!value && visible(fields[key]) && fields[key].value === value);
      // Next.js may render inputs before hydration attaches handlers, then reset
      // them on mount. Reacquire replaced nodes and repair only empty fields for
      // a bounded window. Never overwrite a different nonempty operator value.
      let stable = 0;
      for (let i = 0; i < 20; i += 1) {
        Object.assign(fields, readFields(doc));
        for (const [key, value] of Object.entries(expected)) {
          if (visible(fields[key]) && !fields[key].value) setValue(fields[key], value);
        }
        await sleep(250);
        const current = readFields(doc);
        const unchanged = Object.keys(expected).every((key) => fields[key] === current[key]);
        Object.assign(fields, current);
        stable = unchanged && matches() ? stable + 1 : 0;
        if (i >= 7 && stable >= 4) break;
      }
      const valid = matches();
      return {
        valid,
        status: valid ? "fields filled" : "fields partially filled",
        message: valid
          ? "Filled reporter email, impersonated brand, full reported URL and description. Review the form, attest in good faith and submit manually."
          : "GoDaddy form was partially filled; review email, brand, URL or description manually.",
      };
    },
    validate,
    // The operator must personally accept the good-faith attestation and submit.
    submit: () => false,
  };
  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(adapter);
})();

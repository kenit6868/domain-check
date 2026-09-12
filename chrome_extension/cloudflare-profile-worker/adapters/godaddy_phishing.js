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

  const adapter = {
    id: "godaddy_phishing",
    version: "1.0.0",
    successMessage: "GoDaddy confirmed receipt of the phishing report.",
    matches: (loc) => loc.hostname === "legalportal.godaddy.com" &&
      loc.pathname.startsWith("/abuse/phishing"),
    detectSuccess: (doc) => /report (has been|was) submitted|thank you for your report|confirmation/i
      .test(doc.body?.innerText || ""),
    captchaPending: () => false,
    waitUntilReady: async ({document: doc, status, sleep}) => {
      for (let i = 0; i < 80; i += 1) {
        const email = doc.querySelector("#email-field");
        const company = doc.querySelector("#company-field");
        const source = doc.querySelector("#source-field");
        const details = doc.querySelector("#info-field");
        if ([email, company, source, details].every(visible))
          return {email, company, source, details};
        status(`godaddy_phishing@1.0.0; waiting… inputs=${doc.querySelectorAll("input").length}, textareas=${doc.querySelectorAll("textarea").length}`);
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields, {sleep}) => {
      setValue(fields.email, task.contact_email);
      setValue(fields.company, task.brand_name);
      setValue(fields.source, task.target_url);
      setValue(fields.details, task.draft);
      await sleep(150);
      const valid = fields.email.value === task.contact_email &&
        fields.company.value === task.brand_name &&
        fields.source.value === task.target_url &&
        fields.details.value === task.draft;
      return {
        valid,
        status: valid ? "fields filled" : "fields partially filled",
        message: valid
          ? "Filled reporter email, impersonated brand, full reported URL and description. Review the form, attest in good faith and submit manually."
          : "GoDaddy form was partially filled; review email, brand, URL or description manually.",
      };
    },
    validate: (fields) => ({
      valid: !!fields.company.value && !!fields.source.value,
      message: "GoDaddy brand or reported URL is missing.",
    }),
    // The operator must personally accept the good-faith attestation and submit.
    submit: () => false,
  };
  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(adapter);
})();

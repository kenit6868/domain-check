(() => {
  "use strict";
  const visible = (el) => !!el && !el.disabled && !!el.getClientRects().length;
  const setValue = (el, value) => {
    if (!value) return;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, {bubbles: true}));
  };
  const validate = (fields) => ({
    valid: ["name", "email", "company", "phone", "domain", "details"].every((key) => !!fields[key].value.trim()) &&
      fields.type.value === "Phishing or Pharming",
    message: "Review the report and complete required company/phone or other missing fields manually. Send remains manual.",
  });
  const adapter = {
    id: "udr_registry_abuse",
    version: "1.0.0",
    matches: (loc) => loc.protocol === "https:" && loc.hostname === "www.udr.hk.com" &&
      /^\/report-abuse\/?$/.test(loc.pathname),
    detectSuccess: () => false,
    captchaPending: () => false,
    waitUntilReady: async ({document: doc, sleep}) => {
      for (let i = 0; i < 80; i += 1) {
        const form = doc.querySelector("form.contact-form");
        const fields = Object.fromEntries(Object.entries({
          name: "contact_name", email: "contact_email", company: "contact_company",
          phone: "contact_phone", domain: "contact_website", type: "contact_subject", details: "contact_message",
        }).map(([key, id]) => [key, form?.querySelector(`#${id}`)]));
        if (Object.values(fields).every(visible)) return fields;
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields) => {
      let domain = "";
      try { domain = new URL(task.target_url).hostname; } catch (_) { /* Leave invalid targets for review. */ }
      setValue(fields.name, task.contact_name);
      setValue(fields.email, task.contact_email);
      setValue(fields.company, task.company_name);
      setValue(fields.domain, domain);
      setValue(fields.type, "Phishing or Pharming");
      setValue(fields.details, task.draft);
      const result = validate(fields);
      return {...result, status: result.valid ? "fields filled" : "fields partially filled"};
    },
    validate,
    submit: () => false,
  };
  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(adapter);
})();

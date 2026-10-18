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
  const selectValue = (el, value) => {
    if (!el || !value) return;
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    for (const type of ["input", "change", "blur"])
      el.dispatchEvent(new Event(type, {bubbles: true}));
  };
  const reportedDomain = (targetUrl) => {
    try {
      return new URL(targetUrl).hostname;
    } catch (_error) {
      return String(targetUrl || "").replace(/^https?:\/\//i, "").split(/[/?#]/, 1)[0];
    }
  };

  const adapter = {
    id: "registry_co_phishing",
    version: "1.0.0",
    successMessage: ".CO Registry confirmed receipt of the abuse report.",
    matches: (loc) => loc.hostname === "registry.co" &&
      loc.pathname.startsWith("/report-abuse/form"),
    detectSuccess: (doc) => /report received|thank you.*\.co registry.*received your report/i
      .test(doc.body?.innerText || ""),
    captchaPending: (doc) => {
      const response = doc.querySelector('textarea[name="g-recaptcha-response"], [name*="captcha" i]');
      const challenge = doc.querySelector('iframe[src*="recaptcha" i], .g-recaptcha');
      return !!challenge && !(response && response.value);
    },
    waitUntilReady: async ({document: doc, status, sleep}) => {
      for (let i = 0; i < 80; i += 1) {
        const fields = {
          name: doc.querySelector("#ar-name"),
          email: doc.querySelector("#ar-email"),
          type: doc.querySelector("#ar-type"),
          company: doc.querySelector("#ar-brand"),
          domains: doc.querySelector("#ar-domains"),
          source: doc.querySelector("#ar-path"),
          details: doc.querySelector("#ar-description"),
        };
        if (Object.values(fields).every(visible)) return fields;
        status(`registry_co_phishing@1.0.0; waiting… inputs=${doc.querySelectorAll("input").length}, textareas=${doc.querySelectorAll("textarea").length}`);
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields, {sleep}) => {
      setValue(fields.name, task.contact_name);
      setValue(fields.email, task.contact_email);
      selectValue(fields.type, "phishing");
      setValue(fields.company, task.brand_name);
      setValue(fields.domains, reportedDomain(task.target_url));
      setValue(fields.source, task.target_url);
      setValue(fields.details, task.draft);
      await sleep(150);
      const valid = fields.name.value === task.contact_name &&
        fields.email.value === task.contact_email &&
        fields.type.value === "phishing" &&
        fields.company.value === task.brand_name &&
        fields.domains.value === reportedDomain(task.target_url) &&
        fields.source.value === task.target_url &&
        fields.details.value === task.draft;
      return {
        valid,
        status: valid ? "fields filled" : "fields partially filled",
        message: valid
          ? "Filled reporter, phishing type, brand, domain, full abuse path and description. Attach evidence, confirm good faith, complete CAPTCHA and submit manually."
          : ".CO Registry form was partially filled; review reporter, type, brand, domain, URL or description manually.",
      };
    },
    validate: (fields) => {
      const complete = !!fields.name.value && !!fields.email.value &&
        fields.type.value === "phishing" && !!fields.domains.value &&
        !!fields.source.value && !!fields.details.value;
      return {
        valid: complete,
        message: complete ? "" : ".CO Registry required report fields need review.",
      };
    },
    // Evidence upload, good-faith attestation, CAPTCHA and submit stay manual.
    submit: () => false,
  };
  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(adapter);
})();

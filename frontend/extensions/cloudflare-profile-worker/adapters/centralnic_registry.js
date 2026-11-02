(() => {
  "use strict";
  const formId = "a89570f8-bec3-40be-a33b-10c9c21a09fe";
  const findForm = (doc) => {
    const host = doc.querySelector(`hubspot-form[data-form-id="${formId}"]`);
    return (host?.shadowRoot || host)?.querySelector(`form[data-form-id="${formId}"]`) || null;
  };
  const visible = (el) => !!el && !el.disabled && !!el.getClientRects().length;
  const setValue = (el, value) => {
    if (!value) return;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, {bubbles: true}));
  };
  const validate = (fields) => ({
    valid: ["firstName", "lastName", "email", "phone", "details"].every((key) => !!fields[key].value.trim()) &&
      fields.type.value === "Report Abuse",
    message: "Review first/last name, email and report; complete the required phone number and CAPTCHA manually. Submit remains manual.",
  });
  const adapter = {
    id: "centralnic_registry_abuse",
    version: "1.0.0",
    matches: (loc) => loc.protocol === "https:" && loc.hostname === "centralnicregistry.com" && /^\/contact\/?$/.test(loc.pathname),
    detectSuccess: () => false,
    captchaPending: (doc) => {
      const response = findForm(doc)?.querySelector('[name="g-recaptcha-response"]');
      return !!response && !response.value;
    },
    waitUntilReady: async ({document: doc, sleep}) => {
      for (let i = 0; i < 80; i += 1) {
        const form = findForm(doc);
        const fields = Object.fromEntries(Object.entries({
          firstName: "firstname", lastName: "lastname", email: "email", phone: "phone",
          type: "registry___how_can_we_help", details: "registry___notes",
        }).map(([key, name]) => [key, form?.querySelector(`[name="${name}"]`)]));
        if (Object.values(fields).every(visible)) return fields;
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields) => {
      // Profiles store a full name; preserve every word and leave single-word surnames for review.
      const names = String(task.contact_name || "").trim().split(/\s+/);
      setValue(fields.firstName, names[0]);
      setValue(fields.lastName, names.slice(1).join(" "));
      setValue(fields.email, task.contact_email);
      setValue(fields.type, "Report Abuse");
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

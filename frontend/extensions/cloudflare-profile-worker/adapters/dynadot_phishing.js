(() => {
  "use strict";
  const names = {
    name: "full_name_2", email: "email_2", confirm: "email2_2", domains: "domains_2",
    official: "official_website_2", proxy: "proxy_locations_2", url: "domain_path_2", details: "comments_2",
  };
  const visible = (el) => !!el && !el.disabled && !!el.getClientRects().length;
  const readFields = (doc) => {
    const form = [...doc.querySelectorAll('form')].find((el) =>
      visible(el.querySelector('[name="full_name_2"]')));
    return Object.fromEntries(Object.entries(names).map(([key, name]) =>
      [key, form?.querySelector(`[name="${name}"]`)]));
  };
  const setValue = (el, value) => {
    if (!value || el.value) return;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, {bubbles: true}));
  };
  const validate = (fields) => ({
    valid: Object.keys(names).every((key) => visible(fields[key]) && !!fields[key].value.trim()),
    message: "Review the Dynadot phishing form, complete verification and submit manually.",
  });
  const adapter = {
    id: "dynadot_phishing", version: "1.0.0",
    matches: (loc) => loc.protocol === "https:" && loc.hostname === "www.dynadot.com" && /^\/report-abuse\/?$/.test(loc.pathname),
    detectSuccess: () => false,
    captchaPending: (doc) => !doc.querySelector('form [name="full_name_2"]')?.closest('form')
      ?.querySelector('[name="cf-turnstile-response"]')?.value,
    waitUntilReady: async ({document: doc, sleep, status = () => {}}) => {
      let selected = false;
      for (let i = 0; i < 120; i += 1) {
        const fields = readFields(doc);
        if (Object.values(fields).every(visible)) return fields;
        // Only a unique native option labelled exactly Phishing is safe to select.
        // Custom dropdown markup is not assumed; the operator can select it manually.
        if (!selected) {
          const choices = [...doc.querySelectorAll('select')].filter(visible).flatMap((select) =>
            [...select.options].filter((option) => !option.disabled && /^phishing$/i.test(option.textContent.trim()))
              .map((option) => ({select, option})));
          if (choices.length === 1) {
            const {select, option} = choices[0];
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(select, option.value);
            for (const type of ["input", "change"]) select.dispatchEvent(new Event(type, {bubbles: true}));
            selected = true;
          }
        }
        status("Waiting for Dynadot Phishing form; select Phishing manually if needed.");
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields, {sleep}) => {
      let domain = "";
      try { const url = new URL(task.target_url); if (/^https?:$/.test(url.protocol)) domain = url.hostname; } catch (_) {}
      const expected = {name: task.contact_name, email: task.contact_email, confirm: task.contact_email,
        domains: domain, official: "N/A", proxy: "N/A", url: task.target_url, details: task.draft};
      const doc = fields.name.ownerDocument;
      // Vue may replace fields during mount. Reacquire and repair empty fields only.
      for (let i = 0; i < 8; i += 1) {
        Object.assign(fields, readFields(doc));
        for (const [key, value] of Object.entries(expected)) if (visible(fields[key])) setValue(fields[key], value);
        await sleep(250);
      }
      Object.assign(fields, readFields(doc));
      const valid = Object.entries(expected).every(([key, value]) =>
        !!value && visible(fields[key]) && fields[key].value === value);
      return {valid, status: valid ? "fields filled" : "fields partially filled",
        message: valid ? validate(fields).message : "Review missing or conflicting Dynadot fields manually. Submit remains manual."};
    },
    validate,
    submit: () => false,
  };
  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(adapter);
})();

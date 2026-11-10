(() => {
  "use strict";
  // Lightning controls live in nested open shadow roots; generated IDs are unstable.
  const all = (root, selector) => {
    const found = [...root.querySelectorAll(selector)];
    for (const node of root.querySelectorAll("*")) {
      if (node.shadowRoot) found.push(...all(node.shadowRoot, selector));
    }
    return found;
  };
  const visible = (el) => !!el && !el.disabled && !!el.getClientRects().length;
  const unique = (root, selector) => {
    const matches = all(root, selector).filter(visible);
    return matches.length === 1 ? matches[0] : null;
  };
  const names = {type: "abuseType", category: "abuseCategory", domains: "domain", tld: "tld",
    url: "url", observedDate: "observedDate", details: "description", registrar: "notifiedRegistrar",
    notificationDate: "notifiedRegistrarDate", email: "reporterEmail", brand: "phishingDetails"};
  const read = (doc) => {
    const form = unique(doc, "c-abuse-form");
    return Object.fromEntries(Object.entries(names).map(([key, name]) =>
      [key, form ? unique(form.shadowRoot || form, `input[name="${name}"],select[name="${name}"],textarea[name="${name}"]`) : null]));
  };
  const set = (el, value) => {
    if (!visible(el) || !value || el.value) return;
    if (el instanceof HTMLSelectElement &&
        [...el.options].filter((o) => !o.disabled && o.value === value).length !== 1) return;
    if (el.maxLength > 0 && value.length > el.maxLength) return;
    const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype
      : el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, {bubbles: true, composed: true}));
  };
  const today = () => {
    const now = new Date();
    return `${String(now.getMonth() + 1).padStart(2, "0")}/${String(now.getDate()).padStart(2, "0")}/${now.getFullYear()}`;
  };
  const chooseToday = async (input, sleep) => {
    if (!visible(input) || input.value) return;
    // Lightning synthetic shadow is ordinary light DOM in Chrome's isolated world.
    // Follow both physical parents and native shadow hosts, never a page-world API.
    let host = input;
    while (host && host.localName !== "lightning-datepicker") host = host.parentElement || host.getRootNode().host;
    if (!host) return; // Unknown calendar markup stays manual.
    input.click();
    await sleep(100);
    const buttons = all(host.shadowRoot || host, 'button').filter((el) => visible(el) && el.textContent.trim() === "Today");
    if (buttons.length === 1) buttons[0].click();
  };
  const expectations = new WeakMap();
  const message = "Review the report, observation date and registrar notification facts. Next, CAPTCHA, attachments and submission remain manual.";
  const validate = (fields) => {
    const expected = expectations.get(fields);
    return {valid: !!expected && Object.entries(expected).every(([key, value]) =>
      visible(fields[key]) && (key === "registrar" && !value || !!value && fields[key].value === value)), message};
  };
  const adapter = {
    id: "verisign_dns_abuse", version: "1.0.1",
    matches: (loc) => loc.protocol === "https:" && loc.hostname === "verisign.my.site.com" &&
      /^\/(DNSAbuse|ReportAbuse)\/?$/.test(loc.pathname),
    detectSuccess: () => false,
    captchaPending: (doc) => {
      const responses = all(doc, '[name="g-recaptcha-response"]');
      return responses.length === 0 || responses.every((el) => !el.value);
    },
    waitUntilReady: async ({document: doc, sleep, status = () => {}}) => {
      for (let i = 0; i < 120; i += 1) {
        const fields = read(doc);
        set(fields.type, "DNS Abuse");
        set(fields.category, "PHISHING");
        if (Object.values(fields).every(visible)) return fields;
        status("Waiting for Verisign DNS Abuse form. Conflicting selections require manual review.");
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields, {sleep}) => {
      let domain = "", tld = "";
      try {
        const url = new URL(task.target_url);
        const registered = new URL(`https://${task.target_domain || url.hostname}`).hostname;
        if (/^https?:$/.test(url.protocol) && (url.hostname === registered || url.hostname.endsWith(`.${registered}`))) {
          domain = registered;
          tld = registered.split(".").at(-1);
        }
      } catch (_) {}
      const date = today();
      const expected = {type: "DNS Abuse", category: "PHISHING", domains: domain, tld,
        url: task.target_url, observedDate: date, notificationDate: date,
        registrar: task.registrar || "", email: task.contact_email, details: task.draft, brand: task.brand_name || ""};
      const doc = fields.type.ownerDocument;
      for (let i = 0; i < 3; i += 1) {
        Object.assign(fields, read(doc));
        for (const [key, value] of Object.entries(expected)) {
          if (key === "observedDate" || key === "notificationDate") await chooseToday(fields[key], sleep);
          else set(fields[key], value);
        }
        await sleep(150);
      }
      Object.assign(fields, read(doc));
      expectations.set(fields, expected);
      const result = validate(fields);
      return {...result, status: result.valid ? "fields filled" : "fields partially filled"};
    },
    validate,
    submit: () => false,
  };
  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(adapter);
})();

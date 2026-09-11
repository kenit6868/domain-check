(() => {
  "use strict";
  const norm = (v) => String(v || "").replace(/\s+/g, " ").trim().toLowerCase();
  const visible = (el) => !!el && !el.disabled && !!el.getClientRects().length;
  const first = (doc, selectors) => {
    for (const selector of selectors) {
      const found = [...doc.querySelectorAll(selector)].find(visible);
      if (found) return found;
    }
    return null;
  };
  const byText = (doc, text, tags = "input, textarea") => {
    const needle = norm(text);
    const ranked = [...doc.querySelectorAll(tags)].filter(visible).map((element) => {
      let node = element;
      for (let distance = 0; node && distance < 6; distance += 1, node = node.parentElement) {
        if (norm(node.innerText || node.textContent).includes(needle)) return {element, distance};
      }
      return {element, distance: 99};
    }).filter((item) => item.distance < 99).sort((a, b) => a.distance - b.distance);
    return ranked[0]?.element || null;
  };
  const byCaption = (doc, text, tags = "input, textarea") => {
    const needle = norm(text);
    const captions = [...doc.querySelectorAll("label, span, div, p")].filter((node) => {
      const own = norm([...node.childNodes].filter((child) => child.nodeType === Node.TEXT_NODE)
        .map((child) => child.textContent).join(" "));
      return own === needle || own.startsWith(`${needle} `);
    });
    for (const caption of captions) {
      if (caption instanceof HTMLLabelElement && caption.htmlFor) {
        const linked = doc.getElementById(caption.htmlFor);
        if (linked?.matches(tags) && visible(linked)) return linked;
      }
      let container = caption;
      for (let level = 0; container && level < 5; level += 1, container = container.parentElement) {
        const candidates = [...container.querySelectorAll(tags)].filter(visible);
        if (candidates.length === 1) return candidates[0];
      }
    }
    return null;
  };
  const setValue = (el, value) => {
    if (!el || !value) return;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, {bubbles:true}));
  };
  const invalidNames = (form, doc) => [...form.querySelectorAll(":invalid")].map((el) => {
    const label = el.id && doc.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    return norm(label?.textContent) || el.name || el.type || "unknown field";
  }).slice(0, 6);

  const cloudflare = {
    id: "cloudflare",
    version: "1.0.0",
    successMessage: "Cloudflare confirmed receipt of the form.",
    matches: (loc) => loc.hostname === "abuse.cloudflare.com" && loc.pathname.startsWith("/phishing"),
    detectSuccess: (doc) => ["thank you", "successfully submitted", "report has been submitted", "confirmation number"]
      .some((text) => norm(doc.body?.innerText).includes(text)),
    captchaPending: (doc) => {
      const response = doc.querySelector('[name="cf-turnstile-response"], textarea[name*="captcha" i]');
      const challenge = doc.querySelector('iframe[src*="turnstile"], .cf-turnstile, iframe[title*="challenge" i]');
      return !!challenge && !(response && response.value);
    },
    waitUntilReady: async ({document: doc, status, sleep}) => {
      for (let i = 0; i < 80; i += 1) {
        const url = first(doc, ['input[name="urls"]','textarea[name="urls"]','input[name="url"]','textarea[name="url"]','input[type="url"]']) || byText(doc, "URLs");
        const comments = byText(doc, "comments are kept internal", "textarea");
        const evidence = byText(doc, "Logs or other evidence of abuse", "textarea") ||
          first(doc, ['textarea[name*="evidence" i]','textarea[id*="evidence" i]','textarea[name*="logs" i]']) ||
          [...doc.querySelectorAll("textarea")].filter(visible).find((element) => element !== comments);
        if (url && evidence) return {url, evidence};
        status(`cloudflare@1.0.0; waiting… inputs=${doc.querySelectorAll("input").length}, textareas=${doc.querySelectorAll("textarea").length}`);
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields, {document: doc, sleep}) => {
      setValue(fields.url, task.target_url);
      setValue(fields.evidence, task.draft);
      const textInputs = [...doc.querySelectorAll('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="submit"])')].filter(visible);
      const emailInputs = [...doc.querySelectorAll("input")].filter((element) =>
        visible(element) && `${element.type} ${element.name} ${element.id} ${element.autocomplete} ${element.placeholder}`.toLowerCase().includes("email")
      );
      const email = byCaption(doc, "Email address", "input") || first(doc, ['input[name="email"]','input[id="email"]']) || emailInputs[0];
      const position = textInputs.indexOf(email);
      const confirm = byCaption(doc, "Confirm email address", "input") ||
        first(doc, ['input[name*="confirm-email" i]','input[id*="confirm-email" i]','input[name*="confirm_email" i]','input[id*="confirm_email" i]','input[name*="confirmEmail" i]','input[id*="confirmEmail" i]']) ||
        emailInputs.find((element) => element !== email) || (position >= 0 ? textInputs[position + 1] : null);
      setValue(email, task.contact_email);
      setValue(confirm, task.contact_email);
      await sleep(150);
      if (email && email.value !== task.contact_email) setValue(email, task.contact_email);
      if (confirm && confirm.value !== task.contact_email) setValue(confirm, task.contact_email);
      setValue(byCaption(doc, "Company name", "input") || first(doc, ['input[name*="company" i]','input[id*="company" i]']), task.brand_name);
      setValue(byCaption(doc, "Name", "input") || first(doc, ['input[name="name"]','input[name="reporterName"]']), task.contact_name);
      const emailReady = !!email && email.value === task.contact_email;
      const confirmReady = !!confirm && confirm.value === task.contact_email;
      return {
        valid: emailReady && confirmReady,
        status: `fields filled; email fields=${Number(emailReady) + Number(confirmReady)}/2`,
        message: emailReady && confirmReady
          ? "Filled URL, public evidence, email confirmation and company in the current Chrome profile."
          : "Form was partially filled; email confirmation needs manual review.",
      };
    },
    validate: (fields, doc) => {
      const form = fields.url.closest("form") || fields.evidence.closest("form") || doc.querySelector("form");
      if (!form || form.checkValidity()) return {valid:true, message:""};
      form.reportValidity();
      return {valid:false, message:`Required fields need review: ${invalidNames(form, doc).join(", ")}`};
    },
    submit: (_fields, doc) => {
      const button = first(doc, ['button[type="submit"]','input[type="submit"]','button[data-testid*="submit" i]']);
      if (!button) return false;
      button.click();
      return true;
    },
  };
  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(cloudflare);
})();

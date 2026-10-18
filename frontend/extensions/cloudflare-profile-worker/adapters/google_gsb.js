(() => {
  "use strict";
  const visible = (el) => !!el && !el.disabled && !!el.getClientRects().length;
  const norm = (value) => String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  const setValue = (el, value) => {
    if (!el || !value) return;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, {bubbles:true}));
  };
  const choose = async (control, wanted, doc, sleep) => {
    if (!control || !wanted || norm(wanted) === "none") return true;
    control.click();
    for (let i = 0; i < 20; i += 1) {
      const option = [...doc.querySelectorAll('[role="option"], mat-option')]
        .filter(visible).find((item) => norm(item.textContent) === norm(wanted));
      if (option) {
        option.click();
        await sleep(100);
        return true;
      }
      await sleep(100);
    }
    return false;
  };
  const adapter = {
    id: "google_gsb",
    version: "1.0.0",
    successMessage: "Google Safe Browsing confirmed receipt of the report.",
    matches: (loc) => loc.hostname === "safebrowsing.google.com" && loc.pathname.startsWith("/safebrowsing/report_phish"),
    detectSuccess: (doc) => /thank you|report (was|has been) submitted/i.test(doc.body?.innerText || ""),
    captchaPending: () => false,
    waitUntilReady: async ({document: doc, status, sleep}) => {
      for (let i = 0; i < 80; i += 1) {
        const url = [...doc.querySelectorAll('[formcontrolname="url"], input[type="url"], input[name="url"]')].find(visible);
        const details = [...doc.querySelectorAll('[formcontrolname="details"], textarea[name="details"], textarea')].find(visible);
        if (url && details) return {url, details};
        status(`google_gsb@1.0.0; waiting… inputs=${doc.querySelectorAll("input").length}, textareas=${doc.querySelectorAll("textarea").length}`);
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields, {document: doc, sleep}) => {
      setValue(fields.url, task.target_url);
      setValue(fields.details, task.draft);
      const l1Ready = await choose(doc.querySelector('[formcontrolname="l1Taxonomy"]'), task.threat_type, doc, sleep);
      const l3Ready = await choose(doc.querySelector('[formcontrolname="l3Taxonomy"]'), task.threat_category, doc, sleep);
      const valid = fields.url.value === task.target_url && fields.details.value === task.draft && l1Ready && l3Ready;
      return {
        valid,
        status: valid ? "fields filled" : "fields partially filled",
        message: valid ? "Filled URL, report details and available taxonomy fields in the current Chrome profile." : "Google form was partially filled; review the taxonomy fields manually.",
      };
    },
    validate: (fields) => ({valid: !!fields.url.value && !!fields.details.value, message: "URL or report details are missing."}),
    submit: () => false,
  };
  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(adapter);
})();

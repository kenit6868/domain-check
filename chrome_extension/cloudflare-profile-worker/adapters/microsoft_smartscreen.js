(() => {
  "use strict";
  const visible = (el) => !!el && !el.disabled && !!el.getClientRects().length;
  const setValue = (el, value) => {
    if (!el || !value) return;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, {bubbles:true}));
  };
  const norm = (value) => String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  const chooseLanguage = async (wanted, doc, sleep) => {
    if (!wanted) return true;
    const select = [...doc.querySelectorAll("select")].find((item) =>
      visible(item) && /language/i.test(`${item.id} ${item.name} ${item.getAttribute("aria-label") || ""}`)
    );
    if (select) {
      const option = [...select.options].find((item) => norm(item.textContent).includes(norm(wanted)));
      if (!option) return false;
      select.value = option.value;
      select.dispatchEvent(new Event("change", {bubbles:true}));
      return true;
    }
    const button = doc.querySelector("#LanguageListButton");
    if (!visible(button)) return true;
    button.click();
    for (let i = 0; i < 20; i += 1) {
      const option = [...doc.querySelectorAll('[role="option"], [role="menuitem"], li')]
        .filter(visible).find((item) => norm(item.textContent).includes(norm(wanted)));
      if (option) {
        option.click();
        return true;
      }
      await sleep(100);
    }
    return false;
  };
  const adapter = {
    id: "microsoft_smartscreen",
    version: "1.0.0",
    successMessage: "Microsoft confirmed receipt of the unsafe-site report.",
    matches: (loc) => loc.hostname === "www.microsoft.com" && loc.pathname.includes("report-unsafe-site"),
    detectSuccess: (doc) => /thank you|report (was|has been) submitted/i.test(doc.body?.innerText || ""),
    captchaPending: () => false,
    waitUntilReady: async ({document: doc, status, sleep}) => {
      for (let i = 0; i < 80; i += 1) {
        const url = [...doc.querySelectorAll('#WebsiteUrlOrIP, input[name="WebsiteUrlOrIP"], input[type="url"]')].find(visible);
        if (url) return {url};
        status(`microsoft_smartscreen@1.0.0; waiting… inputs=${doc.querySelectorAll("input").length}`);
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields, {document: doc, sleep}) => {
      setValue(fields.url, task.target_url);
      const languageReady = await chooseLanguage(task.language, doc, sleep);
      const valid = fields.url.value === task.target_url && languageReady;
      return {
        valid,
        status: valid ? "URL filled" : "URL not filled",
        message: valid ? "Filled the reported URL and available language field in the current Chrome profile; review the remaining choices manually." : "Microsoft form URL or language field needs manual review.",
      };
    },
    validate: (fields) => ({valid: !!fields.url.value, message: "Reported URL is missing."}),
    submit: () => false,
  };
  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(adapter);
})();

(() => {
  "use strict";
  const visible = (el) => !!el && !el.disabled && !!el.getClientRects().length;
  const setValue = (el, value) => {
    if (!el || !value) return;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, {bubbles:true}));
  };
  const adapter = {
    id: "microsoft_smartscreen",
    version: "1.0.5",
    successMessage: "Microsoft confirmed receipt of the unsafe-site report.",
    matches: (loc) => loc.hostname === "www.microsoft.com" && loc.pathname.includes("report-unsafe-site"),
    detectSuccess: (doc) => /thank you|report (was|has been) submitted/i.test(doc.body?.innerText || ""),
    captchaPending: () => false,
    waitUntilReady: async ({document: doc, status, sleep}) => {
      for (let i = 0; i < 80; i += 1) {
        const url = [...doc.querySelectorAll('#WebsiteUrlOrIP, input[name="WebsiteUrlOrIP"], input[type="url"]')].find(visible);
        if (url) return {url};
        status(`microsoft_smartscreen@1.0.5; waiting… inputs=${doc.querySelectorAll("input").length}`);
        await sleep(250);
      }
      return null;
    },
    fill: async (task, fields) => {
      setValue(fields.url, task.target_url);
      const valid = fields.url.value === task.target_url;
      return {
        valid,
        status: valid ? "URL filled; language left at provider default" : "URL not filled",
        message: valid ? "Filled the reported URL and left the language field unchanged at Microsoft's default; review the remaining choices manually." : "Microsoft form URL needs manual review.",
      };
    },
    validate: (fields) => ({valid: !!fields.url.value, message: "Reported URL is missing."}),
    submit: () => false,
  };
  globalThis.PhishingToolFormAdapters = globalThis.PhishingToolFormAdapters || [];
  globalThis.PhishingToolFormAdapters.push(adapter);
})();

(() => {
  "use strict";
  const hp = new URLSearchParams(location.hash.slice(1));
  const token = hp.get("ptask") || sessionStorage.getItem("pt-cfw-token");
  const port = hp.get("port") || sessionStorage.getItem("pt-cfw-port");
  if (!token || !/^\d{2,5}$/.test(port || "")) return;
  sessionStorage.setItem("pt-cfw-token", token); sessionStorage.setItem("pt-cfw-port", port);
  const base = `http://127.0.0.1:${port}`;
  const badge = document.createElement("div");
  badge.id = "pt-cfw-status";
  Object.assign(badge.style, {position:"fixed",right:"12px",bottom:"12px",zIndex:"2147483647",padding:"8px 12px",borderRadius:"8px",background:"#172554",color:"white",font:"13px sans-serif",boxShadow:"0 2px 10px #0005"});
  badge.textContent = "PhishingTool: connecting…";
  (document.body || document.documentElement).appendChild(badge);
  const status = (text, error = false) => { badge.textContent = `PhishingTool: ${text}`; badge.style.background = error ? "#991b1b" : "#172554"; };
  const api = (path, method = "GET", body = null) => new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({type:"bridge-fetch",url:`${base}${path}`,method,body}, (response) => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      if (!response?.ok) return reject(new Error(response?.error || `Bridge HTTP ${response?.status || 0}`));
      resolve(response.body || {});
    });
  });
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const norm = (v) => String(v || "").replace(/\s+/g, " ").trim().toLowerCase();
  const first = (selectors) => {
    for (const selector of selectors) {
      const el = [...document.querySelectorAll(selector)].find((n) => !n.disabled && n.getClientRects().length);
      if (el) return el;
    }
    return null;
  };
  const byLabel = (text, tags = "input, textarea", exact = false) => {
    const needle = norm(text);
    for (const label of document.querySelectorAll("label")) {
      const labelText = norm(label.textContent);
      if (exact ? labelText !== needle : !labelText.includes(needle)) continue;
      const linked = label.htmlFor ? document.getElementById(label.htmlFor) : label.querySelector(tags);
      if (linked && linked.matches(tags) && !linked.disabled) return linked;
      const nearby = label.parentElement && label.parentElement.querySelector(tags);
      if (nearby && !nearby.disabled) return nearby;
    }
    return null;
  };
  const nearbyText = (element, levels = 5) => {
    let node = element;
    const parts = [];
    for (let i = 0; node && i < levels; i += 1, node = node.parentElement) {
      parts.push(norm(node.innerText || node.textContent));
    }
    return parts.join(" ");
  };
  const byNearbyText = (text, tags = "input, textarea") => {
    const needle = norm(text);
    const ranked = [...document.querySelectorAll(tags)].filter((element) =>
      !element.disabled && element.getClientRects().length
    ).map((element) => {
      let node = element;
      for (let distance = 0; node && distance < 6; distance += 1, node = node.parentElement) {
        if (norm(node.innerText || node.textContent).includes(needle)) return {element, distance};
      }
      return {element, distance: 99};
    }).filter((item) => item.distance < 99).sort((a, b) => a.distance - b.distance);
    return ranked[0]?.element || null;
  };
  const setValue = (el, value) => {
    if (!el || !value) return;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, {bubbles: true}));
  };
  const visibleEmailInputs = () => [...document.querySelectorAll("input")].filter((element) => {
    if (element.disabled || !element.getClientRects().length) return false;
    const identity = `${element.type} ${element.name} ${element.id} ${element.autocomplete} ${element.placeholder}`.toLowerCase();
    return identity.includes("email");
  });
  const report = (state, result) => api(`/result/${token}`, "POST", {state, result}).catch(() => {});
  const success = () => ["thank you", "successfully submitted", "report has been submitted", "confirmation number"]
    .some((x) => norm(document.body?.innerText).includes(x));
  const captchaPending = () => {
    const response = document.querySelector('[name="cf-turnstile-response"], textarea[name*="captcha" i]');
    const challenge = document.querySelector('iframe[src*="turnstile"], .cf-turnstile, iframe[title*="challenge" i]');
    return !!challenge && !(response && response.value);
  };
  const waitFields = async () => {
    for (let i = 0; i < 80; i += 1) {
      const url = byLabel("URLs") || byLabel("URL") ||
        first(['input[name="urls"]','textarea[name="urls"]','input[name="url"]','textarea[name="url"]','input[type="url"]']) ||
        byNearbyText("URLs");
      // Never fall back to a generic textarea: Comments is not public evidence.
      const visibleTextareas = [...document.querySelectorAll("textarea")].filter((element) =>
        !element.disabled && element.getClientRects().length
      );
      const evidence = byLabel("Logs or other evidence of abuse", "textarea") ||
        byNearbyText("Logs or other evidence of abuse", "textarea") ||
        first(['textarea[name*="evidence" i]','textarea[id*="evidence" i]','textarea[name*="logs" i]']) ||
        visibleTextareas.find((element) => !byNearbyText("comments are kept internal", "textarea") ||
          element !== byNearbyText("comments are kept internal", "textarea"));
      if (url && evidence) return {url, evidence};
      status(`waiting for form… inputs=${document.querySelectorAll("input").length}, textareas=${document.querySelectorAll("textarea").length}`);
      await sleep(250);
    }
    return null;
  };
  const invalidNames = (form) => [...form.querySelectorAll(":invalid")].map((el) => {
    const label = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    return norm(label?.textContent) || el.name || el.type || "unknown field";
  }).slice(0, 6);

  (async () => {
    await api("/hello");
    status("connected; loading task…");
    if (success()) return report("SUBMITTED", "Cloudflare confirmed receipt of the form.");
    const task = await api(`/task/${token}`);
    status("task received; waiting for form…");
    let fields = await waitFields();
    const reloadKey = `pt-cfw-reloaded-${token}`;
    if (!fields && !sessionStorage.getItem(reloadKey)) {
      sessionStorage.setItem(reloadKey, "1"); location.reload(); return;
    }
    if (!fields) { status("form fields not found", true); return report("FAILED", "Form remained blank or fields changed after one automatic reload."); }
    sessionStorage.removeItem(reloadKey);
    setValue(fields.url, task.target_url); setValue(fields.evidence, task.draft);
    const emailCandidates = visibleEmailInputs();
    const email = byLabel("Email address", "input", true) ||
      first(['input[name="email"]','input[id="email"]']) || emailCandidates[0];
    const confirmEmail = byLabel("Confirm email address", "input", true) ||
      first(['input[name*="confirm-email" i]','input[id*="confirm-email" i]','input[name*="confirm_email" i]','input[id*="confirm_email" i]','input[name*="confirmEmail" i]','input[id*="confirmEmail" i]']) ||
      emailCandidates.find((element) => element !== email);
    setValue(email, task.contact_email);
    setValue(confirmEmail, task.contact_email);
    await sleep(150);
    // React may re-render controlled inputs after the first event; fill once more
    // only when the displayed value did not stick.
    if (email && email.value !== task.contact_email) setValue(email, task.contact_email);
    if (confirmEmail && confirmEmail.value !== task.contact_email) setValue(confirmEmail, task.contact_email);
    setValue(byLabel("Company name", "input", true) || first(['input[name*="company" i]','input[id*="company" i]']), task.brand_name);
    setValue(byLabel("Name", "input", true) || first(['input[name="name"]','input[name="reporterName"]']), task.contact_name);
    const confirmReady = !!confirmEmail && confirmEmail.value === task.contact_email;
    status(confirmReady ? "fields filled" : "fields filled; confirm email not detected", !confirmReady);
    if (task.mode !== "submit") return report("FILLED", "Filled URL, public evidence, email confirmation and company in the current Chrome profile.");
    for (let i = 0; i < 120 && captchaPending(); i += 1) await sleep(1000);
    if (captchaPending()) return report("NEEDS_MANUAL", "CAPTCHA is not complete; the filled form remains open.");
    const form = fields.url.closest("form") || fields.evidence.closest("form") || document.querySelector("form");
    if (form && !form.checkValidity()) {
      form.reportValidity();
      return report("NEEDS_MANUAL", `Required fields need review: ${invalidNames(form).join(", ")}`);
    }
    const submit = first(['button[type="submit"]','input[type="submit"]','button[data-testid*="submit" i]']);
    if (!submit) return report("NEEDS_MANUAL", "Submit control was not recognized.");
    submit.click();
    for (let i = 0; i < 60; i += 1) { await sleep(500); if (success()) return report("SUBMITTED", "Cloudflare confirmed receipt of the form."); }
    return report("NEEDS_MANUAL", "Submit was clicked, but success could not be verified.");
  })().catch((error) => { status(String(error), true); return report("FAILED", String(error)); });
})();

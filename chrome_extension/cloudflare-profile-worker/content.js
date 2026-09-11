(() => {
  "use strict";
  const hp = new URLSearchParams(location.hash.slice(1));
  const token = hp.get("ptask") || sessionStorage.getItem("pt-cfw-token");
  const port = hp.get("port") || sessionStorage.getItem("pt-cfw-port");
  if (!token || !/^\d{2,5}$/.test(port || "")) return;
  sessionStorage.setItem("pt-cfw-token", token);
  sessionStorage.setItem("pt-cfw-port", port);
  const badge = document.createElement("div");
  badge.id = "pt-cfw-status";
  Object.assign(badge.style, {position:"fixed",right:"12px",bottom:"12px",zIndex:"2147483647",padding:"8px 12px",borderRadius:"8px",background:"#172554",color:"white",font:"13px sans-serif",boxShadow:"0 2px 10px #0005"});
  (document.body || document.documentElement).appendChild(badge);
  const status = (text, error = false) => {
    badge.textContent = `PhishingTool: ${text}`;
    badge.style.background = error ? "#991b1b" : "#172554";
  };
  status("connecting…");
  const base = `http://127.0.0.1:${port}`;
  const api = (path, method = "GET", body = null) => new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({type:"bridge-fetch",url:`${base}${path}`,method,body}, (response) => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      if (!response?.ok) return reject(new Error(response?.error || `Bridge HTTP ${response?.status || 0}`));
      resolve(response.body || {});
    });
  });
  const report = (state, result) => api(`/result/${token}`, "POST", {state, result}).catch(() => {});
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  (async () => {
    await api("/hello");
    const adapters = globalThis.PhishingToolFormAdapters || [];
    const adapter = adapters.find((candidate) => candidate.matches(location, document));
    if (!adapter) {
      status("no adapter for this form", true);
      return report("FAILED", `No form adapter matched ${location.hostname}.`);
    }
    status(`${adapter.id}@${adapter.version}; loading task…`);
    if (adapter.detectSuccess(document)) return report("SUBMITTED", adapter.successMessage);
    const task = await api(`/task/${token}`);
    if (task.provider !== adapter.id) {
      status(`task/provider mismatch: ${task.provider || "missing"}`, true);
      return report("FAILED", `Task provider ${task.provider || "missing"} does not match adapter ${adapter.id}.`);
    }
    status(`${adapter.id}@${adapter.version}; waiting for form…`);
    const fields = await adapter.waitUntilReady({document, status, sleep});
    const reloadKey = `pt-cfw-reloaded-${token}`;
    if (!fields && !sessionStorage.getItem(reloadKey)) {
      sessionStorage.setItem(reloadKey, "1");
      location.reload();
      return;
    }
    if (!fields) {
      status(`${adapter.id}: form fields not found`, true);
      return report("FAILED", `${adapter.id} form remained blank or its fields changed after one automatic reload.`);
    }
    sessionStorage.removeItem(reloadKey);
    const filled = await adapter.fill(task, fields, {document, sleep});
    status(`${adapter.id}@${adapter.version}; ${filled.status}`, !filled.valid);
    if (!filled.valid) return report("NEEDS_MANUAL", filled.message);
    if (task.mode !== "submit") return report("FILLED", filled.message);
    for (let i = 0; i < 120 && adapter.captchaPending(document); i += 1) await sleep(1000);
    if (adapter.captchaPending(document)) return report("NEEDS_MANUAL", "CAPTCHA is not complete; the filled form remains open.");
    const validation = adapter.validate(fields, document);
    if (!validation.valid) return report("NEEDS_MANUAL", validation.message);
    if (!adapter.submit(fields, document)) return report("NEEDS_MANUAL", "Submit control was not recognized.");
    for (let i = 0; i < 60; i += 1) {
      await sleep(500);
      if (adapter.detectSuccess(document)) return report("SUBMITTED", adapter.successMessage);
    }
    return report("NEEDS_MANUAL", "Submit was clicked, but provider success could not be verified.");
  })().catch((error) => {
    status(String(error), true);
    return report("FAILED", String(error));
  });
})();

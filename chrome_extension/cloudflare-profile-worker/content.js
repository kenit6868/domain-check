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
  let activeAdapter = null;
  let activeTask = null;
  let activeFields = null;
  let currentChecklist = [];
  let latestPublicStatus = null;
  const cleanStatusText = (value) => String(value || "")
    .replace(/([#?&](?:ptask|port|token|key|captcha)\s*=)[^&\s#]+/gi, "$1[REDACTED]")
    .replace(/\s+/g, " ").trim().slice(0, 300);
  const fieldLabels = {
    url: "Reported URL", source: "Reported URL", details: "Evidence / details",
    evidence: "Evidence / details", email: "Email", confirm: "Confirm email",
    company: "Company / brand", name: "Contact name", contact: "Contact email",
    type: "Report type", typeInput: "Report type",
  };
  const fieldHasValue = (field) => {
    if (!field) return false;
    if (field.type === "checkbox" || field.type === "radio") return !!field.checked;
    return String(field.value ?? field.textContent ?? "").trim().length > 0;
  };
  const buildChecklist = () => {
    const seen = new Set();
    const items = [];
    for (const [key, field] of Object.entries(activeFields || {})) {
      const label = fieldLabels[key] || key.replace(/([A-Z])/g, " $1");
      if (seen.has(label)) continue;
      seen.add(label);
      items.push({label, ok: fieldHasValue(field), manual: false});
    }
    if (activeAdapter && activeTask) {
      const captchaPending = !!activeAdapter.captchaPending(document);
      items.push({label: "CAPTCHA", ok: !captchaPending, manual: captchaPending});
      items.push({
        label: "Submit", ok: !!activeAdapter.detectSuccess(document),
        manual: activeTask.mode !== "submit" || captchaPending,
      });
    }
    return items.slice(0, 12);
  };
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "assistant-read-status") {
      sendResponse({ok: true, status: latestPublicStatus});
      return false;
    }
    if (message?.type !== "assistant-command") return false;
    (async () => {
      if (!activeAdapter || !activeTask) throw new Error("No active form task on this tab.");
      activeFields = await activeAdapter.waitUntilReady({document, status, sleep});
      if (!activeFields) throw new Error("Form fields are not available.");
      if (message.action === "refill") {
        const filled = await activeAdapter.fill(activeTask, activeFields, {document, sleep});
        currentChecklist = buildChecklist();
        status(`${activeAdapter.id}@${activeAdapter.version}; ${filled.status}`, !filled.valid,
          filled.valid ? "FILLED" : "NEEDS_MANUAL");
      } else if (message.action === "recheck") {
        const validation = activeAdapter.validate(activeFields, document);
        currentChecklist = buildChecklist();
        status(validation.valid ? "Form fields checked." : validation.message,
          !validation.valid, validation.valid ? "FILLED" : "NEEDS_MANUAL");
      } else {
        throw new Error("Unsupported popup action.");
      }
      sendResponse({ok: true, status: latestPublicStatus});
    })().catch((error) => sendResponse({ok: false, error: cleanStatusText(error)}));
    return true;
  });
  const status = (text, error = false, state = "WORKING") => {
    badge.textContent = `PhishingTool: ${text}`;
    badge.style.background = error ? "#991b1b" : "#172554";
    latestPublicStatus = {
      state, message: cleanStatusText(text),
      adapterId: activeAdapter?.id || "", adapterVersion: activeAdapter?.version || "",
      hostname: location.hostname, checklist: currentChecklist, updatedAt: Date.now(),
    };
    chrome.runtime.sendMessage({type: "assistant-status", ...latestPublicStatus}).catch(() => {});
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
  const report = (state, result) => {
    status(result, state === "FAILED", state);
    return api(`/result/${token}`, "POST", {state, result}).catch(() => {});
  };
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  (async () => {
    await api("/hello");
    const adapters = globalThis.PhishingToolFormAdapters || [];
    const adapter = adapters.find((candidate) => candidate.matches(location, document));
    if (!adapter) {
      status("no adapter for this form", true);
      return report("FAILED", `No form adapter matched ${location.hostname}.`);
    }
    activeAdapter = adapter;
    status(`${adapter.id}@${adapter.version}; loading task…`);
    if (adapter.detectSuccess(document)) return report("SUBMITTED", adapter.successMessage);
    const task = await api(`/task/${token}`);
    if (task.provider !== adapter.id) {
      status(`task/provider mismatch: ${task.provider || "missing"}`, true);
      return report("FAILED", `Task provider ${task.provider || "missing"} does not match adapter ${adapter.id}.`);
    }
    activeTask = task;
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
    activeFields = fields;
    sessionStorage.removeItem(reloadKey);
    const filled = await adapter.fill(task, fields, {document, sleep});
    currentChecklist = buildChecklist();
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

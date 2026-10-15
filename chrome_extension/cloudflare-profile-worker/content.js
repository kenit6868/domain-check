(() => {
  "use strict";
  let activeAdapter = null;
  let activeTask = null;
  let activeFields = null;
  let currentChecklist = [];
  let latestPublicStatus = null;
  let activeStatusPublisher = () => Promise.resolve();
  const cleanStatusText = (value) => String(value || "")
    .replace(/([#?&](?:ptask|port|token|key|captcha)\s*=)[^&\s#]+/gi, "$1[REDACTED]")
    .replace(/\s+/g, " ").trim().slice(0, 300);
  const fieldLabels = {
    url: "Reported URL", source: "Reported URL", details: "Evidence / details",
    evidence: "Evidence / details", email: "Email", confirm: "Confirm email",
    company: "Company / brand", name: "Contact name", contact: "Contact email",
    domains: "Involved domain", type: "Report type", typeInput: "Report type",
  };
  const status = (text, error = false, state = "WORKING") => {
    latestPublicStatus = {
      state, message: cleanStatusText(text), adapterId: activeAdapter?.id || "",
      adapterVersion: activeAdapter?.version || "", hostname: location.hostname,
      checklist: currentChecklist, updatedAt: Date.now(),
    };
    chrome.runtime.sendMessage({type: "assistant-status", ...latestPublicStatus}).catch(() => {});
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
      items.push({label: "Submit", ok: false, manual: true});
    }
    return items.slice(0, 12);
  };
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const publishHttpsStatus = (state, result) => chrome.runtime.sendMessage({
    type: "form-task-status", state, result: cleanStatusText(result),
    adapterId: activeAdapter?.id || "", adapterVersion: activeAdapter?.version || "",
  }).catch(() => {});
  const runAssistantCommand = async (action) => {
    if (!activeAdapter || !activeTask) throw new Error("No active form task on this tab.");
    activeFields = await activeAdapter.waitUntilReady({document, status, sleep});
    if (!activeFields) throw new Error("Form fields are not available.");
    if (action === "refill") {
      const filled = await activeAdapter.fill(activeTask, activeFields, {document, sleep});
      currentChecklist = buildChecklist();
      const state = filled.valid ? "FILLED" : "NEEDS_MANUAL";
      status(`${activeAdapter.id}@${activeAdapter.version}; ${filled.status}`, !filled.valid, state);
      void activeStatusPublisher(state, filled.message);
    } else if (action === "recheck") {
      const validation = activeAdapter.validate(activeFields, document);
      currentChecklist = buildChecklist();
      const state = validation.valid ? "FILLED" : "NEEDS_MANUAL";
      status(validation.valid ? "Form fields checked." : validation.message, !validation.valid, state);
      void activeStatusPublisher(state, validation.message);
    } else throw new Error("Unsupported assistant action.");
    return latestPublicStatus;
  };
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "assistant-read-status") {
      sendResponse({ok: true, status: latestPublicStatus});
      return false;
    }
    if (message?.type !== "assistant-command") return false;
    runAssistantCommand(message.action)
      .then((nextStatus) => sendResponse({ok: true, status: nextStatus}))
      .catch((error) => sendResponse({ok: false, error: cleanStatusText(error)}));
    return true;
  });

  const legacyChannel = () => {
    const params = new URLSearchParams(location.hash.slice(1));
    const token = params.get("ptask") || sessionStorage.getItem("pt-cfw-token");
    const port = params.get("port") || sessionStorage.getItem("pt-cfw-port");
    if (!token || !/^\d{2,5}$/.test(port || "")) return null;
    sessionStorage.setItem("pt-cfw-token", token);
    sessionStorage.setItem("pt-cfw-port", port);
    const base = `http://127.0.0.1:${port}`;
    const api = (path, method = "GET", body = null) => new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({type: "bridge-fetch", url: `${base}${path}`, method, body}, (response) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (!response?.ok) return reject(new Error(response?.error || `Bridge HTTP ${response?.status || 0}`));
        resolve(response.body || {});
      });
    });
    return {
      load: async () => { await api("/hello"); return api(`/task/${token}`); },
      report: (state, result) => api(`/result/${token}`, "POST", {state, result}).catch(() => {}),
      legacy: true,
    };
  };
  const httpsChannel = () => ({
    load: async () => {
      const response = await chrome.runtime.sendMessage({type: "form-task-for-tab"});
      return response?.ok ? response.task : null;
    },
    report: (state, result) => publishHttpsStatus(state, result),
    legacy: false,
  });

  (async () => {
    const channel = legacyChannel() || httpsChannel();
    const task = await channel.load();
    if (!task) return;
    activeStatusPublisher = channel.report;
    const adapters = globalThis.PhishingToolFormAdapters || [];
    const adapter = adapters.find((candidate) => candidate.matches(location, document));
    if (!adapter) {
      status("no adapter for this form", true, "FAILED");
      return channel.report("FAILED", `No form adapter matched ${location.hostname}.`);
    }
    activeAdapter = adapter;
    if (task.provider !== adapter.id) {
      status("task/provider mismatch", true, "FAILED");
      return channel.report("FAILED", "Task provider does not match the current form adapter.");
    }
    if (!channel.legacy && task.mode !== "fill_only") {
      status("HTTPS Quick Report task is not fill-only", true, "FAILED");
      return channel.report("FAILED", "Task policy rejected a non-fill-only action.");
    }
    activeTask = task;
    status(`${adapter.id}@${adapter.version}; waiting for form…`);
    void channel.report("WORKING", "Extension is waiting for supported form fields.");
    const fields = await adapter.waitUntilReady({document, status, sleep});
    if (!fields) {
      status(`${adapter.id}: form fields not found`, true, "FAILED");
      return channel.report("FAILED", `${adapter.id} form fields were not found.`);
    }
    activeFields = fields;
    const filled = await adapter.fill(task, fields, {document, sleep});
    currentChecklist = buildChecklist();
    const state = filled.valid ? "FILLED" : "NEEDS_MANUAL";
    status(`${adapter.id}@${adapter.version}; ${filled.status}`, !filled.valid, state);
    await channel.report(state, filled.message);
    if (!channel.legacy || task.mode !== "submit") return;
    // Legacy submit tasks remain compatible. HTTPS Quick Report tasks never reach this branch.
    for (let i = 0; i < 120 && adapter.captchaPending(document); i += 1) await sleep(1000);
    if (adapter.captchaPending(document)) return channel.report("NEEDS_MANUAL", "CAPTCHA is not complete; the filled form remains open.");
    const validation = adapter.validate(fields, document);
    if (!validation.valid) return channel.report("NEEDS_MANUAL", validation.message);
    if (!adapter.submit(fields, document)) return channel.report("NEEDS_MANUAL", "Submit control was not recognized.");
    for (let i = 0; i < 60; i += 1) {
      await sleep(500);
      if (adapter.detectSuccess(document)) return channel.report("SUBMITTED", adapter.successMessage);
    }
    return channel.report("NEEDS_MANUAL", "Submit was clicked, but provider success could not be verified.");
  })().catch((error) => {
    status(cleanStatusText(error), true, "FAILED");
    void activeStatusPublisher("FAILED", cleanStatusText(error));
  });
})();

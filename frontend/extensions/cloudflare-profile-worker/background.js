"use strict";
const tabStatuses = new Map();
const providerOrigins = {
  google_gsb: "https://safebrowsing.google.com",
  microsoft_smartscreen: "https://www.microsoft.com",
  cloudflare: "https://abuse.cloudflare.com",
  chongluadao: "https://chongluadao.vn",
  coccoc_safe: "https://safe.coccoc.com",
  godaddy_phishing: "https://legalportal.godaddy.com",
  registry_co_phishing: "https://registry.co",
  xyz_registry_abuse: "https://gen.xyz",
};
const cleanText = (value, limit = 300) => String(value || "")
  .replace(/([#?&](?:ptask|port|token|key|captcha)\s*=)[^&\s#]+/gi, "$1[REDACTED]")
  .replace(/\s+/g, " ").trim().slice(0, limit);
const badgeFor = (state, message) => {
  if (state === "WORKING") return {text: "…", color: "#2563eb", title: "PhishingTool đang xử lý form"};
  if (state === "FILLED") return {text: "✓", color: "#16a34a", title: "PhishingTool đã điền form"};
  if (state === "SUBMITTED") return {text: "✓", color: "#15803d", title: "PhishingTool đã ghi nhận submit"};
  if (state === "FAILED") return {text: "×", color: "#dc2626", title: "PhishingTool gặp lỗi"};
  if (state === "NEEDS_MANUAL" && /captcha|turnstile/i.test(message))
    return {text: "C", color: "#d97706", title: "PhishingTool đang chờ CAPTCHA thủ công"};
  if (state === "NEEDS_MANUAL")
    return {text: "!", color: "#d97706", title: "PhishingTool cần thao tác thủ công"};
  return {text: "", color: "#475569", title: "PhishingTool Web Form Assistant"};
};
const updateBadge = (tabId, state, message) => {
  const badge = badgeFor(state, message);
  void Promise.allSettled([
    chrome.action.setBadgeText({tabId, text: badge.text}),
    chrome.action.setBadgeBackgroundColor({tabId, color: badge.color}),
    chrome.action.setTitle({tabId, title: badge.title}),
  ]);
};
const getConnection = async () => {
  const stored = await chrome.storage.local.get(["domaincheckOrigin", "deviceId"]);
  return {origin: String(stored.domaincheckOrigin || ""), deviceId: String(stored.deviceId || "")};
};
const senderOrigin = (sender) => {
  try { return new URL(sender.url || "").origin; } catch (_) { return ""; }
};
const apiPost = async (origin, path, body) => {
  const response = await fetch(`${origin}${path}`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body), cache: "no-store", credentials: "omit",
  });
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(cleanText(payload.message || `API HTTP ${response.status}`));
  return payload;
};
const taskStorageKey = (tabId) => `formTask:${tabId}`;
chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  (async () => {
    const connection = await getConnection();
    if (!connection.origin || senderOrigin(sender) !== connection.origin) throw new Error("origin_not_allowed");
    if (!connection.deviceId) throw new Error("device_not_initialized");
    if (message?.type === "domaincheck-ping") {
      return {ok: true, deviceId: connection.deviceId, extensionVersion: chrome.runtime.getManifest().version};
    }
    if (message?.type !== "domaincheck-open-form") throw new Error("unsupported_message");
    const token = String(message.capabilityToken || "");
    if (token.length < 40 || token.length > 200) throw new Error("invalid_capability");
    const task = await apiPost(connection.origin, "/api/v1/form-tasks/exchange", {
      capability_token: token, device_id: connection.deviceId,
    });
    if (task.mode !== "fill_only" || !providerOrigins[task.provider]) throw new Error("invalid_task_policy");
    const formUrl = new URL(task.form_url);
    if (formUrl.origin !== providerOrigins[task.provider]) throw new Error("provider_origin_mismatch");
    const tab = await chrome.tabs.create({url: formUrl.href, active: true});
    if (tab.id == null) throw new Error("tab_not_created");
    await chrome.storage.session.set({[taskStorageKey(tab.id)]: {
      id: task.id, statusToken: task.status_token, apiOrigin: connection.origin,
      provider: task.provider, formUrl: formUrl.href, target_url: task.target_url,
      draft: task.draft || "", mode: "fill_only", threat_type: task.threat_type || "",
      threat_category: task.threat_category || "", expiresAt: task.expires_at,
      contact_email: task.contact_email || "", contact_name: task.contact_name || "",
      brand_name: task.brand_name || "", report_type: task.report_type || "",
    }});
    return {ok: true, taskId: task.id, tabId: tab.id, provider: task.provider};
  })().then(sendResponse).catch((error) => sendResponse({ok: false, error: cleanText(error?.message || error)}));
  return true;
});
chrome.tabs.onRemoved.addListener((tabId) => {
  tabStatuses.delete(tabId);
  void chrome.storage.session.remove(taskStorageKey(tabId));
});
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "loading") {
    tabStatuses.delete(tabId);
    updateBadge(tabId, "IDLE", "");
  }
});
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message) return false;
  if (message.type === "form-task-for-tab" && sender.tab?.id != null) {
    (async () => {
      const key = taskStorageKey(sender.tab.id);
      const stored = await chrome.storage.session.get([key]);
      const task = stored[key];
      if (!task) return {ok: false, error: "task_not_found"};
      const current = new URL(sender.tab.url || "about:blank");
      if (current.origin !== providerOrigins[task.provider]) return {ok: false, error: "provider_origin_mismatch"};
      return {ok: true, task: {
        provider: task.provider, target_url: task.target_url, draft: task.draft,
        mode: "fill_only", threat_type: task.threat_type, threat_category: task.threat_category,
        contact_email: task.contact_email, contact_name: task.contact_name,
        brand_name: task.brand_name, report_type: task.report_type,
      }};
    })().then(sendResponse).catch((error) => sendResponse({ok: false, error: cleanText(error)}));
    return true;
  }
  if (message.type === "form-task-status" && sender.tab?.id != null) {
    (async () => {
      const key = taskStorageKey(sender.tab.id);
      const stored = await chrome.storage.session.get([key]);
      const task = stored[key];
      if (!task) throw new Error("task_not_found");
      const allowed = new Set(["WORKING", "FILLED", "NEEDS_MANUAL", "FAILED"]);
      const state = allowed.has(message.state) ? message.state : "FAILED";
      const payload = await apiPost(task.apiOrigin, `/api/v1/form-tasks/${task.id}/status`, {
        status_token: task.statusToken, state, result: cleanText(message.result),
        adapter_id: cleanText(message.adapterId, 60), adapter_version: cleanText(message.adapterVersion, 30),
      });
      if (["FILLED", "NEEDS_MANUAL", "FAILED"].includes(state)) {
        const next = {...task, statusToken: task.statusToken};
        await chrome.storage.session.set({[key]: next});
      }
      return {ok: true, state: payload.state};
    })().then(sendResponse).catch((error) => sendResponse({ok: false, error: cleanText(error)}));
    return true;
  }
  if (message.type === "assistant-status" && sender.tab?.id != null) {
    let hostname = "";
    try { hostname = sender.tab.url ? new URL(sender.tab.url).hostname : ""; } catch (_) {}
    const state = cleanText(message.state, 40) || "IDLE";
    const statusMessage = cleanText(message.message);
    tabStatuses.set(sender.tab.id, {
      state,
      message: statusMessage,
      adapterId: cleanText(message.adapterId, 60),
      adapterVersion: cleanText(message.adapterVersion, 30),
      hostname: cleanText(hostname, 120),
      checklist: Array.isArray(message.checklist) ? message.checklist.slice(0, 12).map((item) => ({
        label: cleanText(item?.label, 50), ok: !!item?.ok, manual: !!item?.manual,
      })) : [],
      updatedAt: Date.now(),
    });
    updateBadge(sender.tab.id, state, statusMessage);
    sendResponse({ok: true});
    return false;
  }
  if (message.type === "assistant-get-status") {
    sendResponse({
      ok: true,
      extensionVersion: chrome.runtime.getManifest().version,
      status: tabStatuses.get(Number(message.tabId)) || null,
    });
    return false;
  }
  return false;
});

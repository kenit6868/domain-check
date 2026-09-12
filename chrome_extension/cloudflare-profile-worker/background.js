"use strict";
const tabStatuses = new Map();
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
chrome.tabs.onRemoved.addListener((tabId) => tabStatuses.delete(tabId));
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "loading") {
    tabStatuses.delete(tabId);
    updateBadge(tabId, "IDLE", "");
  }
});
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message) return false;
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
  if (message.type !== "bridge-fetch") return false;
  let url;
  try { url = new URL(message.url); } catch (_) { sendResponse({ok:false,error:"invalid_url"}); return false; }
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !/^\/(hello|task\/|result\/)/.test(url.pathname)) {
    sendResponse({ok:false,error:"blocked_url"}); return false;
  }
  fetch(url.href, {
    method: message.method === "POST" ? "POST" : "GET",
    headers: message.method === "POST" ? {"Content-Type":"application/json"} : {},
    body: message.method === "POST" ? JSON.stringify(message.body || {}) : undefined,
    cache: "no-store"
  }).then(async (response) => {
    let body = {}; try { body = await response.json(); } catch (_) {}
    sendResponse({ok:response.ok,status:response.status,body});
  }).catch((error) => sendResponse({ok:false,error:String(error)}));
  return true;
});

"use strict";
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "bridge-fetch") return false;
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

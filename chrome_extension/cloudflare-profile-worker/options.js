"use strict";
const originInput = document.getElementById("origin");
const status = document.getElementById("status");
const device = document.getElementById("device");
const normalizeOrigin = (value) => {
  const parsed = new URL(String(value || "").trim());
  const local = ["localhost", "127.0.0.1"].includes(parsed.hostname);
  if (parsed.protocol !== "https:" && !(local && parsed.protocol === "http:")) throw new Error("Production phải dùng HTTPS; HTTP chỉ cho localhost.");
  if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) throw new Error("Chỉ nhập origin, không nhập path/query/credential.");
  return parsed.origin;
};
const getDeviceId = async () => {
  const stored = await chrome.storage.local.get(["deviceId"]);
  if (stored.deviceId) return stored.deviceId;
  const value = crypto.randomUUID();
  await chrome.storage.local.set({deviceId: value});
  return value;
};
(async () => {
  const stored = await chrome.storage.local.get(["domaincheckOrigin"]);
  originInput.value = stored.domaincheckOrigin || "";
  device.textContent = await getDeviceId();
})().catch(() => {});
document.getElementById("save").addEventListener("click", async () => {
  try {
    const origin = normalizeOrigin(originInput.value);
    const parsed = new URL(origin);
    const permissionOrigin = `${parsed.protocol}//${parsed.hostname}/*`;
    const granted = await chrome.permissions.request({origins: [permissionOrigin]});
    if (!granted) throw new Error("Quyền truy cập origin chưa được cấp.");
    await chrome.storage.local.set({domaincheckOrigin: origin});
    status.className = "ok";
    status.textContent = `Đã khóa kết nối vào ${origin}`;
  } catch (error) {
    status.className = "error";
    status.textContent = String(error).replace(/^Error:\s*/, "").slice(0, 220);
  }
});

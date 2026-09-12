"use strict";
const text = (id, value) => { document.getElementById(id).textContent = value || "—"; };
const labels = {
  WORKING: "Đang xử lý", FILLED: "Đã điền", SUBMITTED: "Đã ghi nhận submit",
  NEEDS_MANUAL: "Cần thao tác thủ công", FAILED: "Lỗi", IDLE: "Chưa có task",
};
let activeTabId = null;
let currentStatus = null;
let extensionVersion = "—";
const renderChecklist = (items) => {
  const list = document.getElementById("checklist");
  list.replaceChildren();
  const safeItems = Array.isArray(items) && items.length
    ? items : [{label: "Chưa có dữ liệu field", ok: false, manual: true}];
  for (const item of safeItems) {
    const row = document.createElement("li");
    row.className = item.ok ? "ok" : "pending";
    row.textContent = `${item.label}${item.manual && !item.ok ? " — thủ công" : ""}`;
    list.appendChild(row);
  }
};
const renderStatus = (status) => {
  currentStatus = status || null;
  const dot = document.getElementById("dot");
  for (const id of ("refill recheck copy").split(" ")) document.getElementById(id).disabled = !status;
  if (!status) {
    text("connection", "Chưa có task trên tab này");
    text("state", labels.IDLE);
    dot.className = "error";
    renderChecklist([]);
    return;
  }
  const ageSeconds = Math.max(0, Math.round((Date.now() - status.updatedAt) / 1000));
  text("connection", ageSeconds <= 90 ? "Đang kết nối với form" : "Trạng thái cũ");
  dot.className = ageSeconds <= 90 ? "ok" : "error";
  text("host", status.hostname);
  text("adapter", status.adapterId ? `${status.adapterId} v${status.adapterVersion || "—"}` : "Đang nhận diện");
  text("state", labels[status.state] || status.state);
  text("message", status.message || "Không có chi tiết.");
  renderChecklist(status.checklist);
};
const runCommand = async (action) => {
  text("action-result", action === "refill" ? "Đang điền lại…" : "Đang kiểm tra…");
  try {
    const response = await chrome.tabs.sendMessage(activeTabId, {type: "assistant-command", action});
    if (!response?.ok) throw new Error(response?.error || "Không thực hiện được lệnh.");
    renderStatus(response.status);
    text("action-result", action === "refill" ? "Đã chạy điền lại." : "Đã kiểm tra lại form.");
  } catch (error) {
    text("action-result", String(error).replace(/\s+/g, " ").slice(0, 180));
  }
};
(async () => {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  activeTabId = tab?.id ?? null;
  text("host", (() => { try { return new URL(tab?.url || "").hostname; } catch (_) { return "—"; } })());
  const response = await chrome.runtime.sendMessage({type: "assistant-get-status", tabId: tab?.id});
  extensionVersion = response?.extensionVersion || "—";
  text("version", `Extension v${extensionVersion}`);
  let status = response?.status;
  if (tab?.id != null) {
    try {
      const direct = await chrome.tabs.sendMessage(tab.id, {type: "assistant-read-status"});
      status = direct?.status || status;
    } catch (_) {}
  }
  renderStatus(status);
})().catch((error) => {
  text("connection", "Không đọc được trạng thái");
  text("message", String(error));
  document.getElementById("dot").className = "error";
});
document.getElementById("refill").addEventListener("click", () => runCommand("refill"));
document.getElementById("recheck").addEventListener("click", () => runCommand("recheck"));
document.getElementById("copy").addEventListener("click", async () => {
  if (!currentStatus) return;
  const checklist = (currentStatus.checklist || [])
    .map((item) => `${item.ok ? "OK" : "PENDING"}: ${item.label}${item.manual ? " (manual)" : ""}`)
    .join("\n");
  const diagnostic = [
    `Extension: ${extensionVersion}`,
    `Host: ${currentStatus.hostname || "—"}`,
    `Adapter: ${currentStatus.adapterId || "—"} ${currentStatus.adapterVersion || ""}`.trim(),
    `State: ${currentStatus.state || "—"}`,
    `Message: ${currentStatus.message || "—"}`,
    checklist,
  ].filter(Boolean).join("\n");
  try {
    await navigator.clipboard.writeText(diagnostic);
    text("action-result", "Đã sao chép chẩn đoán an toàn.");
  } catch (_) {
    text("action-result", "Chrome không cho phép sao chép.");
  }
});

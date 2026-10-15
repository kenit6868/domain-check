export interface ExtensionConnection {
  deviceId: string
  extensionVersion: string
}

interface ExtensionReply {
  ok?: boolean
  error?: string
  deviceId?: string
  extensionVersion?: string
  taskId?: string
  tabId?: number
}

interface ChromeRuntime {
  lastError?: { message?: string }
  sendMessage: (
    extensionId: string,
    message: Record<string, unknown>,
    callback: (response: ExtensionReply | undefined) => void,
  ) => void
}

declare global {
  interface Window {
    chrome?: { runtime?: ChromeRuntime }
  }
}

export function getExtensionId(): string {
  return String(import.meta.env.VITE_FORM_ASSISTANT_EXTENSION_ID ?? '').trim()
}

function connectionError(code?: unknown): string {
  code = (typeof code === 'string' ? code : '').replace(/^Error:\s*/, '').trim()
  if (code === 'origin_not_allowed') return `Extension chưa cho phép trang ${window.location.origin}. Mở Options của Web Form Assistant, lưu đúng Application origin rồi kiểm tra lại.`
  if (code === 'device_not_initialized') return 'Extension chưa khởi tạo thiết bị. Mở Options của Web Form Assistant và lưu Application origin.'
  return 'Không kết nối được extension. Kiểm tra ID, bật extension và Reload bản v3 trong đúng Chrome profile, sau đó tải lại trang web.'
}

function sendMessage(message: Record<string, unknown>): Promise<ExtensionReply> {
  return new Promise((resolve, reject) => {
    const extensionId = getExtensionId()
    if (!extensionId) { reject(new Error('Frontend chưa có VITE_FORM_ASSISTANT_EXTENSION_ID. Điền frontend/.env rồi khởi động lại frontend.')); return; }
    if (!/^[a-p]{32}$/.test(extensionId)) { reject(new Error('ID extension không hợp lệ. Cần đúng 32 chữ cái a–p từ chrome://extensions.')); return; }
    const runtime = window.chrome?.runtime
    if (typeof runtime?.sendMessage !== 'function') { reject(new Error('Trang chưa có API kết nối extension. Dùng Chrome đã nạp Web Form Assistant v3, Reload extension rồi tải lại trang. Kiểm tra manifest cho phép origin hiện tại.')); return; }
    const timeout = window.setTimeout(() => { reject(new Error('Extension không phản hồi.')) }, 2_500)
    try { runtime.sendMessage(extensionId, message, (response) => {
      window.clearTimeout(timeout)
      const runtimeError = window.chrome?.runtime?.lastError?.message
      if (runtimeError) {
        const message = /receiving end does not exist|could not establish connection/i.test(runtimeError)
          ? 'Không tìm thấy extension nhận message theo ID frontend. Đối chiếu ID tại chrome://extensions, bật extension trong đúng profile rồi Reload extension.'
          : /message port closed|message channel closed/i.test(runtimeError)
            ? 'Extension đã đóng kết nối trước khi phản hồi. Reload extension và kiểm tra lỗi Service worker trong chrome://extensions.'
            : connectionError()
        reject(new Error(message))
      }
      else if (!response?.ok) reject(new Error(connectionError(response?.error)))
      else resolve(response)
    }) } catch {
      window.clearTimeout(timeout)
      reject(new Error(connectionError()))
    }
  })
}

export async function pingExtension(): Promise<ExtensionConnection> {
  const response = await sendMessage({ type: 'domaincheck-ping' })
  if (!response.deviceId || !response.extensionVersion) throw new Error('Extension trả về handshake không hợp lệ.')
  return { deviceId: response.deviceId, extensionVersion: response.extensionVersion }
}

export async function openExtensionForm(capabilityToken: string): Promise<{ taskId: string; tabId: number }> {
  const response = await sendMessage({ type: 'domaincheck-open-form', capabilityToken })
  if (!response.taskId || response.tabId === undefined) throw new Error('Extension không xác nhận tab form.')
  return { taskId: response.taskId, tabId: response.tabId }
}

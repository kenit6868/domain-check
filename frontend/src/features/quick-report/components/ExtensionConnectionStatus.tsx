import { Button } from '../../../components/shared/Button'
import { Cable, Download, RefreshCw } from 'lucide-react'

import type { ReturnTypeOfUseExtension } from './types'
import { getExtensionId } from '../extension/extensionBridge'
import { bundledExtensionVersion, compareExtensionVersions } from '../extension/extensionCompatibility'

export function ExtensionConnectionStatus({ extension }: { extension: ReturnTypeOfUseExtension }) {
  const connected = extension.state === 'connected'
  const bundledVersion = bundledExtensionVersion
  const comparison = compareExtensionVersions(bundledVersion, extension.connection?.extensionVersion ?? '')
  const updateAvailable = connected && (comparison === null || comparison > 0)
  return (
    <div className={`mt-2 flex flex-wrap items-center justify-between gap-3 rounded-xl border px-3 py-2 text-xs ${connected && !updateAvailable ? 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200' : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200'}`}>
      <div className="min-w-0 flex-1"><span className="flex items-center gap-2" role="status"><Cable size={17} className="shrink-0" />{connected ? `Web Form Assistant v${extension.connection?.extensionVersion ?? ''} đã kết nối` : extension.state === 'checking' ? 'Đang kiểm tra Web Form Assistant…' : `Chưa kết nối extension. ${extension.message}`}</span>
      {!connected && <p className="mt-2 break-all text-xs">ID frontend: <code>{getExtensionId() || 'Chưa cấu hình'}</code> · Application origin: <code>{window.location.origin}</code></p>}</div>
      <div className="flex flex-wrap items-center gap-2"><a className="status-primary" href={`${import.meta.env.BASE_URL}downloads/web-form-assistant.zip?v=${bundledVersion}`} download="web-form-assistant.zip"><Download size={14} aria-hidden="true" />{updateAvailable ? 'Tải bản cập nhật' : 'Tải extension'}{bundledVersion ? ` v${bundledVersion}` : ''}</a><Button type="button" loading={extension.state === 'checking'} className="secondary-button inline-flex items-center gap-2" onClick={() => { void extension.refresh() }}><RefreshCw size={14} />Kiểm tra lại</Button></div>
      {updateAvailable && <p className="w-full" role="alert">Có bản v{bundledVersion}. Bản đang cài chỉ tự điền được các form tương thích; các form cần bản mới vẫn có thể mở thủ công. Cập nhật rồi nhấn Kiểm tra lại.</p>}
      <details className="w-full border-t border-current/10 pt-2"><summary className="cursor-pointer">{updateAvailable ? 'Có phiên bản mới · Hướng dẫn cập nhật' : 'Hướng dẫn cài đặt / cập nhật'}</summary><p className="mt-2 leading-6">Giải nén ZIP vào một thư mục cố định. Mở chrome://extensions, bật Developer mode và chọn Load unpacked tới thư mục có manifest.json. Khi cập nhật, thay nội dung trong đúng thư mục đã cài rồi nhấn Reload trên extension. Mở Options, lưu Application origin của web này, sau đó tải lại trang và nhấn Kiểm tra lại. Nếu ID extension thay đổi, liên hệ quản trị viên để cập nhật ID kết nối của web.</p></details>
    </div>
  )
}

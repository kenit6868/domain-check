import { Button } from '../../../components/shared/Button'
import { Cable, RefreshCw } from 'lucide-react'

import type { ReturnTypeOfUseExtension } from './types'
import { getExtensionId } from '../extension/extensionBridge'

export function ExtensionConnectionStatus({ extension }: { extension: ReturnTypeOfUseExtension }) {
  const connected = extension.state === 'connected'
  return (
    <div className={`mt-2 flex flex-wrap items-center justify-between gap-3 rounded-xl border px-3 py-2 text-xs ${connected ? 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200' : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200'}`}>
      <div className="min-w-0 flex-1"><span className="flex items-center gap-2" role="status"><Cable size={17} className="shrink-0" />{connected ? `Web Form Assistant v${extension.connection?.extensionVersion ?? ''} đã kết nối` : extension.state === 'checking' ? 'Đang kiểm tra Web Form Assistant…' : `Chưa kết nối extension. ${extension.message}`}</span>
      {!connected && <p className="mt-2 break-all text-xs">ID frontend: <code>{getExtensionId() || 'Chưa cấu hình'}</code> · Application origin: <code>{window.location.origin}</code></p>}</div>
      <Button type="button" loading={extension.state === 'checking'} className="secondary-button inline-flex items-center gap-2" onClick={() => { void extension.refresh() }}><RefreshCw size={14} />Kiểm tra lại</Button>
    </div>
  )
}


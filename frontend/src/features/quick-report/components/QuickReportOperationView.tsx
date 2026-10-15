import { Button } from '../../../components/shared/Button'
import { Clock3, RotateCcw } from 'lucide-react'
import { useState } from 'react'

import type { QuickReportCheck, QuickReportOperation } from '../schemas/quickReportSchema'
import { QuickReportResultCard } from './QuickReportResultCard'

import type { ReturnTypeOfUseExtension } from './types'

const ACTIVE_STATES = new Set(['queued', 'running', 'stopping'])

function identity(check: QuickReportCheck) {
  const url = new URL(check.requested_url)
  return `${url.hostname}${url.pathname}${url.search}`
}

export function QuickReportOperationView({ operation, refreshing, onReset, canOpenForm, extension, daily }: { operation: QuickReportOperation; refreshing: boolean; onReset: () => void; canOpenForm: boolean; extension: ReturnTypeOfUseExtension; daily?: { business_day: string; checks: QuickReportCheck[] } }) {
  const [hideCached, setHideCached] = useState(true)
  const [hidePrevious, setHidePrevious] = useState(true)
  const current = operation.checks.filter(() => !daily || daily.business_day === operation.business_day)
  const rows = new Map((daily?.checks ?? []).map((check) => [identity(check), check]))
  current.forEach((check) => {
    const saved = rows.get(identity(check))
    // Daily DB rows are authoritative if another tab has produced a newer check.
    if (!saved || (saved.id === check.id && (!['succeeded', 'failed'].includes(saved.status) || ['succeeded', 'failed'].includes(check.status)))) rows.set(identity(check), check)
  })
  // Worker order is the operation input order, not the daily first-seen order.
  const currentKeys = new Set(current.map(identity))
  const checks = daily ? [
    ...current.map((check) => rows.get(identity(check)) ?? check),
    ...[...rows.values()].filter((check) => !currentKeys.has(identity(check))),
  ] : current
  const currentIds = new Set(current.map((check) => check.id))
  const isOld = (check: QuickReportCheck) => check.cache_provenance.reused === true || Boolean(daily && !currentIds.has(check.id))
  const cached = checks.filter(isOld).length
  const fresh = checks.filter((check) => !isOld(check) && check.cache_provenance.source === 'network').length
  const unresolved = checks.length - cached - fresh
  const hidden = daily ? hidePrevious : hideCached
  const complete = operation.checks.filter((check) => ['succeeded', 'failed'].includes(check.status)).length
  const active = ACTIVE_STATES.has(operation.state)
  return (
    <section className="mt-2 space-y-2" aria-live="polite">
      <div className="data-panel flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2"><Clock3 size={18} className={active ? 'animate-pulse text-indigo-600' : 'text-emerald-600'} /><h2 className="font-semibold">{daily ? `Kết quả hôm nay · ${daily.business_day}` : `Operation ${operation.id.slice(0, 8)}`}</h2></div>
          {operation.checks.length > 0 && <p className="mt-1 text-xs text-slate-500">Lượt gần nhất: {complete}/{operation.checks.length} URL · ngày {operation.business_day} · {operation.stage}{refreshing ? ' · đang đồng bộ' : ''}</p>}
          <p className="mt-1 text-xs text-slate-500">{cached} {daily ? 'kết quả cũ' : 'kết quả cache'} · {fresh} check mới{unresolved > 0 ? ` · ${String(unresolved)} chưa có kết quả` : ''}{daily ? ` · ${String(checks.length)} URL trong ngày` : ''}</p>
        </div>
        {!active && !daily && <Button type="button" className="secondary-button inline-flex items-center gap-2" onClick={onReset}><RotateCcw size={15} />Danh sách mới</Button>}
      </div>
      {operation.state === 'queued' && <p className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100" role="status">Lượt kiểm tra đang xếp hàng, chưa được worker nhận. Nếu chờ lâu, cần kiểm tra tiến trình Quick Report worker trên máy chủ. Trang chỉ tự cập nhật trạng thái, không chạy check lại.</p>}
      {active && <progress aria-label="Tiến độ kiểm tra URL" className="h-2 w-full accent-indigo-600" max={Math.max(operation.checks.length, 1)} value={complete} />}
      {operation.duplicates.length > 0 && <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800" role="status">Đã bỏ {operation.duplicates.length} URL trùng trong request: {operation.duplicates.join(', ')}</div>}
      {cached > 0 && <label className="flex items-center gap-2 text-xs text-slate-500"><input type="checkbox" checked={hidden} onChange={(event) => { if (daily) setHidePrevious(event.target.checked); else setHideCached(event.target.checked) }} />Ẩn kết quả đã check trong ngày ({cached})</label>}
      {hidden && cached === checks.length && cached > 0 && <p className="data-panel p-3 text-sm text-slate-500" role="status">Kết quả cũ đã được ẩn. Nhập URL mới để tiếp tục; muốn kiểm tra lại URL cũ, xoá cache hôm nay rồi nhập lại.</p>}
      {daily && checks.length === 0 && <p className="p-3 text-xs text-slate-500">Chưa có kết quả trong ngày. Nhập URL ở trên để bắt đầu.</p>}
      <div className="space-y-2">{checks.map((check, index) => hidden && isOld(check) ? null : <QuickReportResultCard key={check.id} check={check} index={index} canOpenForm={canOpenForm} extension={extension} />)}</div>

    </section>
  )
}





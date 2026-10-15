import { AlertTriangle, Cloud, Database, Server } from 'lucide-react'
import { CopyButton } from '../../../components/shared/CopyButton'
import type { QuickReportCheck } from '../schemas/quickReportSchema'
import { QuickReportProviderActions } from './QuickReportProviderActions'
import type { ReturnTypeOfUseExtension } from './types'

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

export function QuickReportResultCard({ check, index, canOpenForm, extension }: { check: QuickReportCheck; index: number; canOpenForm: boolean; extension: ReturnTypeOfUseExtension }) {
  const cdns = Array.isArray(check.result.cdn_detected) ? check.result.cdn_detected.filter((item): item is string => typeof item === 'string') : []
  const infrastructure = [...new Set([...(check.result.cloudflare === true ? ['Cloudflare'] : []), ...cdns])]
  const recipients = Array.isArray(check.result.report_recipients) ? check.result.report_recipients.map(record).filter((item): item is Record<string, unknown> => item !== null) : []
  const pending = !['succeeded', 'failed'].includes(check.status)
  return <article className="data-panel overflow-hidden">
    <header className="flex flex-wrap items-center justify-between gap-3 px-3 py-2">
      <div className="flex min-w-0 items-center gap-3"><span className="text-xs text-slate-500">#{index + 1}</span><h2 className="break-all font-mono text-sm font-semibold">{check.requested_url}</h2></div>
      <div className="flex items-center gap-2">{check.cache_provenance.reused === true && <span title="Dùng kết quả đã check trong ngày, không kiểm tra mạng lại" className="inline-flex items-center gap-1 text-xs text-sky-600 dark:text-sky-300"><Database size={14} />Cache</span>}<Status status={check.status} /><CopyButton value={check.requested_url} label="Copy URL" /></div>
    </header>
    {pending ? <p className="border-t border-slate-100 px-3 py-2 text-sm text-slate-500 dark:border-slate-800" role="status">{check.status === 'not_queued' ? 'Bản ghi cũ chưa có job xử lý. Nhập lại URL ở trên và bấm Kiểm tra để tạo lượt mới.' : check.status === 'running' ? 'Worker đang kiểm tra URL này.' : 'URL đang chờ worker nhận xử lý.'}</p>
      : check.status === 'failed' ? <div className="flex gap-3 px-5 pb-4 text-sm text-rose-600"><AlertTriangle size={18} /><p>Không hoàn tất được lượt kiểm tra URL này.</p></div>
      : <>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-slate-100 px-3 py-2 dark:border-slate-800">
          <div className="flex flex-wrap content-start gap-2" aria-label="Hạ tầng">{infrastructure.length ? infrastructure.map((name) => <span key={name} className="inline-flex items-center gap-2 rounded-lg bg-slate-100 px-2 py-1 text-xs font-medium dark:bg-slate-800">{name.toLowerCase().includes('cloudflare') ? <Cloud size={16} className="text-orange-500" /> : <Server size={16} className="text-indigo-500" />}{name}</span>) : <span className="text-xs text-slate-500">Chưa xác định hạ tầng</span>}</div>
          <dl className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs">{(['registrar', 'registry'] as const).map((channel) => {
            const emails = recipients.filter((item) => item.channel === channel && typeof item.email === 'string').map((item) => String(item.email))
            const hasForm = check.report_forms?.some((form) => form.channel === channel)
            return <div className="flex flex-wrap gap-x-2 gap-y-1" key={channel}><dt className="font-medium text-slate-500">{channel === 'registrar' ? 'Registrar' : 'Registry'}:</dt><dd className="break-all">{emails.length ? emails.join(', ') : hasForm ? 'Liên hệ qua form' : 'Chưa tìm thấy email'}</dd></div>
          })}</dl>
        </div>
        <QuickReportProviderActions check={check} canOpenForm={canOpenForm} extension={extension} />
      </>}
  </article>
}

function Status({ status }: { status: string }) {
  const color = status === 'succeeded' ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300' : status === 'failed' ? 'bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300' : 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300'
  return <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${color}`}>{status === 'succeeded' ? 'Hoàn tất' : status === 'failed' ? 'Lỗi' : status === 'running' ? 'Đang chạy' : status === 'not_queued' ? 'Chưa có job' : 'Đang chờ'}</span>
}




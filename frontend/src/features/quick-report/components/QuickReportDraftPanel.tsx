import { AppSelect, AppSelectItem } from '../../../components/shared/AppSelect'
import { useState } from 'react'
import { CopyButton } from '../../../components/shared/CopyButton'

export type ReportDraft = { channel: string; label: string; form_url: string; draft: string; note: string; autofill_provider: string | null }
export function QuickReportDraftPanel({ drafts, loading, error, wide = false }: { drafts: ReportDraft[]; loading: boolean; error?: string; wide?: boolean }) {
  const [selected, setSelected] = useState('')
  const active = drafts.find((item) => `${item.channel}:${item.label}` === selected) ?? drafts[0]
  if (!loading && !error && !active) return null
  return <div className="mt-2 border-t border-slate-200 pt-2 dark:border-slate-800">
    <div className="mb-1 flex min-h-6 flex-wrap items-center justify-between gap-2">
      <span className="text-xs font-medium text-slate-500">Nội dung đối chiếu</span>
      {drafts.length > 1 && <AppSelect aria-label="Chọn nội dung báo cáo" className="max-w-full rounded-lg border border-slate-200 bg-transparent px-2 py-1 text-xs dark:border-slate-700" value={active ? `${active.channel}:${active.label}` : ''} onValueChange={(event) => { setSelected(event) }}>
        {drafts.map((item) => <AppSelectItem key={`${item.channel}:${item.label}`} value={`${item.channel}:${item.label}`}>{item.channel === 'registrar' ? 'Registrar · ' : item.channel === 'registry' ? 'Registry · ' : ''}{item.label}</AppSelectItem>)}
      </AppSelect>}
    </div>
    {loading ? <p className="text-xs text-slate-500" role="status">Đang tải nội dung…</p> : error ? <p className="text-xs text-rose-500" role="alert">{error}</p> : active ? <>
      <pre className={`h-28 ${wide ? 'md:h-16' : ''} overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-2 font-sans text-xs leading-5 dark:bg-slate-950/50`}>{active.draft || 'Form này chỉ nhận URL và lựa chọn phân loại; không có phần nội dung văn bản tự điền.'}</pre>
      {active.draft && <div className="mt-2"><CopyButton value={active.draft} label="Copy nội dung đối chiếu" /></div>}
      {active.note === 'netcraft' && <p className="mt-1 text-xs text-slate-500">Bấm Gửi API để gửi báo cáo Netcraft.</p>}
    </> : null}
  </div>
}


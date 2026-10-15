import { Button } from '../../../components/shared/Button'
import { Filter, Globe2, Play } from 'lucide-react'
import { useMemo, useState } from 'react'

import { parseQuickReportTargets, type ParsedTargets } from '../utils/parseTargets'

interface QuickReportInputProps {
  canCheck: boolean
  busy: boolean
  onCheck: (targets: string[]) => void
}

export function QuickReportInput({ canCheck, busy, onCheck }: QuickReportInputProps) {
  const [raw, setRaw] = useState('')
  const [prepared, setPrepared] = useState<ParsedTargets | null>(null)
  const parsed = useMemo(() => parseQuickReportTargets(raw), [raw])
  const tooMany = (prepared?.valid.length ?? 0) > 100
  return (
    <section className="data-panel target-panel mt-2 p-3">
      <div className="target-panel-heading"><span className="feature-icon"><Globe2 size={20} /></span><div><h2 className="text-base font-semibold">Danh sách mục tiêu</h2><p className="mt-1 text-xs text-slate-500">Mỗi dòng một URL · Tối đa 100 URL mỗi lần kiểm tra</p></div></div>
      <label className="field">
        URL cần kiểm tra
        <textarea
          className="target-textarea"
          rows={3}
          value={raw}
          placeholder={'https://example.test/login\nsecond.example.test/path'}
          onChange={(event) => { setRaw(event.target.value); setPrepared(null) }}
        />
      </label>
      <div className="target-actions mt-2 flex flex-wrap items-center justify-end gap-2">
        <Button type="button" className="secondary-button inline-flex items-center gap-2" disabled={!raw.trim() || busy} onClick={() => { setPrepared(parsed) }}>
          <Filter size={16} /> Lọc danh sách
        </Button>
        <Button
          type="button"
          className="primary-button inline-flex items-center gap-2"
          loading={busy}
          disabled={!canCheck || busy || !prepared?.valid.length || tooMany}
          onClick={() => { if (prepared) onCheck(prepared.valid) }}
        >
          <Play size={16} /> {busy ? 'Đang tạo operation…' : 'Kiểm tra'}
        </Button>
        {!canCheck && <span className="text-sm text-amber-700 dark:text-amber-300">Bạn chỉ có quyền xem, chưa có quyền chạy check.</span>}
      </div>
      {prepared && (
        <div className="mt-4 grid gap-3 sm:grid-cols-3" aria-live="polite">
          <Summary label="Hợp lệ" value={prepared.valid.length} tone="good" />
          <Summary label="Không hợp lệ" value={prepared.invalid.length} tone={prepared.invalid.length ? 'bad' : 'neutral'} />
          <Summary label="Giới hạn" value={100} tone={tooMany ? 'bad' : 'neutral'} />
          {prepared.invalid.length > 0 && <p className="sm:col-span-3 break-words text-sm text-rose-700 dark:text-rose-300">Bỏ qua: {prepared.invalid.join(', ')}</p>}
          {tooMany && <p className="sm:col-span-3 text-sm text-rose-700">Mỗi operation nhận tối đa 100 URL.</p>}
        </div>
      )}
    </section>
  )
}

function Summary({ label, value, tone }: { label: string; value: number; tone: 'good' | 'bad' | 'neutral' }) {
  const color = tone === 'good' ? 'text-emerald-700' : tone === 'bad' ? 'text-rose-700' : 'text-slate-600'
  return <div className="rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-800"><span className="block text-xs text-slate-500">{label}</span><strong className={color}>{value}</strong></div>
}

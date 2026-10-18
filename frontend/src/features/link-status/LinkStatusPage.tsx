import { CopyButton } from '../../components/shared/CopyButton'
import { AppSelect, AppSelectItem } from '../../components/shared/AppSelect'
import { useEffect, useRef, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, CircleHelp, LoaderCircle, ShieldAlert } from 'lucide-react'
import { z } from 'zod'
import { Button } from '../../components/shared/Button'
import { httpClient, readCookie } from '../../lib/http/client'
import type { Me } from '../auth/types'

const resultSchema = z.object({
  url: z.string(), group: z.string().default(''), domain: z.string(), status: z.string(), domain_status: z.string(), website_status: z.string(),
  http_code: z.number().nullable(), final_url: z.string(), provider: z.string(), reason: z.string(), checked_at: z.string(),
  redirects: z.array(z.object({ code: z.number(), url: z.string(), destination: z.string() })),
  registration: z.object({ state: z.string(), source: z.string(), statuses: z.array(z.string()), hold_flags: z.array(z.string()) }),
  dns: z.object({ state: z.string(), records: z.record(z.string(), z.array(z.string())) }), conflict: z.boolean().optional(),
})
const schema = z.object({ batch: z.object({ id: z.string(), phase: z.string(), urls: z.array(z.string()),
  results: z.array(resultSchema), processed: z.number(), total: z.number(), invalid_count: z.number(), duplicate_count: z.number(), started_at: z.string() }).nullable() })
const eventSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('start'), batch: schema.shape.batch.unwrap() }),
  z.object({ type: z.literal('result'), index: z.number(), result: resultSchema, processed: z.number() }),
  z.object({ type: z.literal('complete'), processed: z.number() }),
  z.object({ type: z.literal('heartbeat') }),
])
const labels: Record<string, string> = {
  REGISTRATION_NOT_FOUND: 'RDAP không tìm thấy', GEO_RESTRICTED: 'Hạn chế vùng truy cập',
  DOMAIN_HOLD: 'Domain hold', DNS_OK: 'DNS phân giải được', DNS_NXDOMAIN: 'DNS NXDOMAIN', DNS_ERROR: 'Lỗi DNS',
  CF_PHISHING_BLOCKED: 'CF chặn phishing', CF_MALWARE_BLOCKED: 'CF chặn malware', CF_CHALLENGE: 'CF Challenge',
  ACCESS_BLOCKED: 'Chặn truy cập', RATE_LIMITED: 'Giới hạn truy cập', LEGAL_RESTRICTION: 'Hạn chế pháp lý',
  SERVER_ERROR: 'Lỗi máy chủ', CONNECTION_ERROR: 'Lỗi kết nối', TIMEOUT: 'Hết thời gian', TLS_ERROR: 'Lỗi TLS',
  HTTP_OK: 'HTTP OK', URL_NOT_FOUND: 'URL không tồn tại', URL_GONE: 'URL đã gỡ', REDIRECT: 'Chuyển hướng',
  REDIRECT_ERROR: 'Lỗi chuyển hướng', UNKNOWN: 'Chưa xác định',
}
function Status({ value }: { value: string }) {
  const good = ['DNS_OK', 'HTTP_OK'].includes(value)
  const danger = ['DOMAIN_HOLD', 'CF_PHISHING_BLOCKED', 'CF_MALWARE_BLOCKED'].includes(value)
  const Icon = good ? CheckCircle2 : danger ? ShieldAlert : value === 'UNKNOWN' ? CircleHelp : AlertTriangle
  return <span className={`inline-flex whitespace-nowrap items-center gap-1 rounded-full px-2 py-1 text-xs font-medium ${good ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200' : danger ? 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200' : 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200'}`}><Icon size={13} aria-hidden="true" />{labels[value] ?? value}</span>
}

export function LinkStatusPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const [text, setText] = useState('')
  const [domainFilter, setDomainFilter] = useState('')
  const [websiteFilter, setWebsiteFilter] = useState('')
  const [batch, setBatch] = useState<z.infer<typeof schema>['batch']>(null)
  const [busy, setBusy] = useState(false)
  const [checkError, setCheckError] = useState('')
  const active = useRef<AbortController | null>(null)
  useEffect(() => () => { active.current?.abort() }, [])
  async function run(input: string) {
    active.current?.abort()
    const controller = new AbortController()
    active.current = controller
    setBusy(true); setCheckError(''); setBatch(null); setDomainFilter(''); setWebsiteFilter('')
    const observed: z.infer<typeof resultSchema>[] = []
    const state = { completed: false }
    try {
      const response = await fetch(httpClient.getUri({ url: '/link-status/check' }), {
        method: 'POST', credentials: 'include', signal: controller.signal,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': readCookie('dc_csrf') },
        body: JSON.stringify({ text: input }),
      })
      if (!response.ok || !response.body) throw new Error('Link check failed')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      function event(line: string) {
        const value = eventSchema.parse(JSON.parse(line))
        if (active.current !== controller) return
        if (value.type === 'start') setBatch(value.batch)
        if (value.type === 'result') {
          observed[value.index] = value.result
          const results = observed.filter((row) => Boolean(row))
          setBatch((old) => old ? { ...old, processed: value.processed, results } : old)
        }
        if (value.type === 'complete') {
          state.completed = true
          setBatch((old) => old ? { ...old, phase: 'COMPLETED', processed: value.processed } : old)
        }
      }
      try {
        while (!controller.signal.aborted) {
          const { value, done } = await reader.read()
          buffer += decoder.decode(value, { stream: !done })
          let index: number
          while ((index = buffer.indexOf('\n')) >= 0) {
            const line = buffer.slice(0, index).trim(); buffer = buffer.slice(index + 1)
            if (line) event(line)
          }
          if (done) break
        }
        if (buffer.trim()) event(buffer)
        if (!state.completed) throw new Error('Link check interrupted')
      } finally { await reader.cancel(); reader.releaseLock() }
    } catch {
      if (!controller.signal.aborted && active.current === controller) {
        setCheckError('Kiểm tra bị gián đoạn hoặc không thực hiện được. Vui lòng nhấn kiểm tra lại.')
        setBatch((old) => old ? { ...old, phase: 'INTERRUPTED' } : old)
      }
    } finally { if (active.current === controller) setBusy(false) }
  }
  const running = busy
  const rows = batch?.results ?? []
  const counts = [
    { label: 'Lỗi DNS', count: rows.filter((row) => row.domain_status === 'DNS_ERROR').length, icon: AlertTriangle, color: 'text-amber-700 dark:text-amber-300' },
    { label: 'Domain Hold', count: rows.filter((row) => row.domain_status === 'DOMAIN_HOLD').length, icon: ShieldAlert, color: 'text-red-700 dark:text-red-300' },
    { label: 'CF Phishing', count: rows.filter((row) => row.website_status === 'CF_PHISHING_BLOCKED').length, icon: ShieldAlert, color: 'text-red-700 dark:text-red-300' },
  ]
  const filtered = rows.filter((row) => (!domainFilter || row.domain_status === domainFilter) && (!websiteFilter || row.website_status === websiteFilter))
  const options = (field: 'domain_status' | 'website_status') => [...new Set(rows.map((row) => row[field]))].sort().map((status) =>
    <AppSelectItem key={status} value={status}>{labels[status] ?? status} ({rows.filter((row) => row[field] === status).length})</AppSelectItem>)
  if (!me.permissions.includes('link_status.view')) return <p>Bạn chưa có quyền xem Check Link Status.</p>
  return <div className="space-y-4">
    <div><h1 className="text-xl font-semibold">Check Link Status</h1><p className="mt-1 text-sm text-slate-500">Kiểm tra tên miền, DNS và website từ máy backend. Mỗi trạng thái có dữ liệu đối chiếu riêng.</p></div>
    <section className="data-panel space-y-3 p-4">
      <div className="field"><label className="block text-sm font-medium" htmlFor="link-status-input">Danh sách domain/URL</label>
      <textarea id="link-status-input" className="target-textarea min-h-40 w-full" value={text} onChange={(event) => { setText(event.target.value); }} disabled={running} placeholder={'Nhóm thương hiệu\nhttps://example.com/path\nexample.org'} /></div>
      <div className="flex flex-wrap items-center gap-3"><Button loading={running} disabled={!text.trim() || running || !me.permissions.includes('link_status.check')} onClick={() => { void run(text); }}>Kiểm tra trạng thái</Button>
        {batch && !running && <Button disabled={!me.permissions.includes('link_status.check')} onClick={() => { const input = batch.urls.join('\n'); setText(input); void run(input) }}>Kiểm tra lại danh sách</Button>}
        <span className="text-xs text-slate-500">RDAP/WHOIS + DNS + HTTP · Kết quả tạm thời, mỗi lần kiểm tra lấy dữ liệu mới</span></div>
      {checkError && <p role="alert" className="text-sm text-red-600">{checkError}</p>}
    </section>
    {batch && <section className="data-panel space-y-4 p-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3" role="group" aria-label="Tổng hợp trạng thái">
        {counts.map(({ label, count, icon: Icon, color }) => <div key={label} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50"><span className={`rounded-lg bg-white p-2 dark:bg-slate-800 ${color}`}><Icon size={19} aria-hidden="true" /></span><div><p className="text-xs text-slate-500 dark:text-slate-400">{label}</p><p className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{count}</p></div></div>)}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="flex items-center gap-2 text-sm font-medium">{running && <LoaderCircle className="animate-spin" size={16} />}{batch.phase === 'CHECKING' ? 'Đang kiểm tra' : batch.phase === 'INTERRUPTED' ? 'Kiểm tra bị gián đoạn — chạy lại để lấy kết quả mới' : 'Đã kiểm tra'} {batch.processed}/{batch.total}</span><span className="text-xs text-slate-500">Bắt đầu: {new Date(batch.started_at).toLocaleString()}</span></div>
      <progress aria-label="Tiến độ kiểm tra link" className="h-2 w-full accent-blue-600" value={batch.processed} max={batch.total} />
      {(batch.invalid_count > 0 || batch.duplicate_count > 0) && <p className="text-xs text-slate-500">Bỏ qua {batch.invalid_count} mục không hợp lệ và {batch.duplicate_count} URL trùng.</p>}
      <div className="flex flex-wrap items-end gap-3">
        <label className="field text-sm">Trạng thái tên miền<AppSelect aria-label="Lọc trạng thái tên miền" className="mt-1 block" value={domainFilter} onValueChange={(e) => { setDomainFilter(e); }}><AppSelectItem value="">Tất cả ({rows.length})</AppSelectItem>{options('domain_status')}</AppSelect></label>
        <label className="field text-sm">Trạng thái website<AppSelect aria-label="Lọc trạng thái website" className="mt-1 block" value={websiteFilter} onValueChange={(e) => { setWebsiteFilter(e); }}><AppSelectItem value="">Tất cả ({rows.length})</AppSelectItem>{options('website_status')}</AppSelect></label>
        <Button onClick={() => { setDomainFilter(''); setWebsiteFilter('') }}>Xóa bộ lọc</Button>
        <span className="py-2 text-xs text-slate-500">Hiện {filtered.length}/{rows.length} kết quả</span>
      </div>
      <div className="max-h-[65vh] overflow-auto"><table className="min-w-[1000px] w-full text-left text-sm"><thead className="sticky top-0 bg-slate-100 text-slate-800 dark:bg-slate-900 dark:text-slate-200"><tr>{['URL', 'Tên miền', 'Website', 'HTTP', 'Chuyển hướng', 'Chi tiết'].map((name) => <th className="p-3" key={name}>{name}</th>)}</tr></thead><tbody>{filtered.map((row) => <tr key={row.url} className="border-b border-slate-200 dark:border-slate-800">
        <td className="min-w-56 max-w-96 break-all p-3">{row.group && <strong className="mb-1 block">{row.group}</strong>}<span className="inline-flex max-w-full items-start gap-2"><span className="min-w-0 break-all"><a href={row.url} target="_blank" rel="noopener noreferrer" className="text-blue-700 underline underline-offset-2 hover:text-blue-900 dark:text-blue-400 dark:hover:text-blue-300">{row.url}</a></span><span className="shrink-0"><CopyButton value={row.url} iconOnly /></span></span><p className="mt-1 text-xs text-slate-500">{new Date(row.checked_at).toLocaleString()}</p></td>
        <td className="p-3"><Status value={row.domain_status} /></td><td className="p-3"><Status value={row.website_status} />{row.provider && <p className="mt-1 text-xs">{row.provider}</p>}</td><td className="p-3">{row.http_code ?? '—'}</td>
        <td className="min-w-52 max-w-80 break-all p-3">{row.redirects.length > 0 ? <div className="space-y-2">{row.redirects.map((hop, index) => <div key={index}><span className="mr-2 inline-block rounded bg-slate-100 px-2 py-0.5 text-xs font-medium dark:bg-slate-800">{hop.code}</span><span className="inline-flex max-w-full items-start gap-2"><span className="min-w-0 break-all"><a href={hop.destination} target="_blank" rel="noopener noreferrer" className="text-blue-700 underline underline-offset-2 hover:text-blue-900 dark:text-blue-400 dark:hover:text-blue-300">{hop.destination}</a></span><span className="shrink-0"><CopyButton value={hop.destination} iconOnly /></span></span></div>)}</div> : '—'}</td>
        <td className="min-w-44 p-3"><details><summary className="cursor-pointer">Xem dữ liệu đối chiếu</summary><div className="max-w-md space-y-2 break-all py-2 text-xs"><p>Tên miền đăng ký: {row.domain || 'Chưa xác định'}</p><p>{row.registration.source || 'RDAP/WHOIS'}: {row.registration.state} · {row.registration.statuses.join(', ') || 'Không có trạng thái xác nhận'}</p><p>DNS: {row.dns.state}</p>{Object.entries(row.dns.records).map(([kind, records]) => <p key={kind}>{kind}: {records.join(', ') || 'Không có bản ghi'}</p>)}<p>Kết luận: {row.reason}</p>{row.conflict && <p className="text-amber-700">Domain hold nhưng DNS vẫn phân giải; cần kiểm tra lại sau thời gian cache.</p>}<p>URL cuối: {row.final_url || 'Chưa truy cập được'}</p>{row.redirects.map((hop, index) => <p key={index}>{hop.code}: {hop.url} → {hop.destination}</p>)}<p>Góc kiểm tra: máy backend</p></div></details></td>
      </tr>)}</tbody></table>{!filtered.length && <p className="p-6 text-center text-sm text-slate-500">{rows.length ? 'Không có kết quả phù hợp bộ lọc.' : 'Đang chờ kết quả kiểm tra.'}</p>}</div>
      <p className="text-xs text-slate-500">404/410 là trạng thái của URL. Challenge, lỗi DNS hoặc timeout chưa xác nhận tên miền đã bị thu hồi hay takedown thành công.</p>
    </section>}
  </div>
}

import { ForbiddenPage } from '../../components/shared/StatusPage'
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useOutletContext } from 'react-router-dom'
import { z } from 'zod'
import { CheckCircle2, LoaderCircle, SearchCheck } from 'lucide-react'
import { Button } from '../../components/shared/Button'
import { request } from '../../lib/http/client'
import type { Me } from '../auth/types'

const rowSchema = z.object({ id: z.string(), url: z.string(), state: z.string(), attempts: z.number(), error: z.string().nullable(), report_id: z.string().nullable(), draft: z.string(), excluded: z.boolean().default(false) })
const schema = z.object({ batch: z.object({ id: z.string(), day: z.string(), phase: z.string(), urls: z.array(z.string()), invalid: z.array(z.string()), duplicates: z.array(z.string()), processed: z.number(), current_id: z.string().nullable(), error: z.string().nullable(), delay_seconds: z.number(), next_run_at: z.string().nullish(), account_id: z.string().nullish() }).nullable(), records: z.array(rowSchema), submit_enabled: z.boolean().optional() })
const labels: Record<string, string> = { READY: 'Sẵn sàng', CHECKING: 'Đang kiểm tra', CHECKED: 'Đã kiểm tra', CHECK_FAILED: 'Kiểm tra lỗi', QUEUED: 'Chờ gửi', SEND_QUEUED: 'Chờ runner', SENDING: 'Đang gửi', SUBMITTING: 'Đang gửi', SUBMITTED: 'Đã gửi', ALREADY_SUBMITTED: 'Đã gửi gần đây', NOT_CLOUDFLARE: 'Không dùng Cloudflare', UNKNOWN: 'Chưa rõ — cần đối soát', WAITING_FOR_SESSION: 'Chờ Cookie mới', PAUSED: 'Đã tạm dừng', CANCELLED: 'Đã ngừng', COMPLETED: 'Hoàn tất xử lý', INTERRUPTED: 'Kiểm tra bị gián đoạn', FAILED: 'Gửi lỗi' }
const errors: Record<string, string> = { cookie_rejected: 'Cookie bị từ chối hoặc hết hạn. Nhập Cookie mới để tiếp tục.', provider_rate_limit_or_challenge: 'Cloudflare yêu cầu chờ hoặc xác minh. Chỉ tiếp tục khi đã xử lý.', submission_unconfirmed: 'Chưa xác định được kết quả gửi. Đối soát trước khi xử lý tiếp; URL này không được tự gửi lại.', runner_interrupted: 'Runner bị gián đoạn. Nhập Cookie mới để tiếp tục phần chưa gửi.', runner_unavailable: 'Chưa kết nối được runner Cloudflare.', runner_failed: 'Runner gặp lỗi. Các request đang gửi được giữ là chưa rõ kết quả.', profile_changed_recheck: 'Profile đã đổi; kiểm tra lại trước khi gửi.', permission_revoked: 'Quyền gửi đã bị thu hồi.', day_changed: 'Đã sang ngày mới; cần kiểm tra lại URL.', precheck_failed: 'Kiểm tra Cloudflare thất bại.', provider_rejected: 'Cloudflare từ chối report.', interrupted_after_intent: 'Gửi bị gián đoạn, cần đối soát.' }
const active = ['CHECKING', 'SEND_QUEUED', 'SENDING', 'PAUSED', 'WAITING_FOR_SESSION']

function Status({ value }: { value: string }) {
  const success = ['SUBMITTED', 'ALREADY_SUBMITTED', 'COMPLETED'].includes(value)
  const failure = ['FAILED', 'CHECK_FAILED', 'CANCELLED', 'INTERRUPTED'].includes(value)
  const running = ['CHECKING', 'SENDING', 'SUBMITTING', 'SEND_QUEUED'].includes(value)
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium ${success ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200' : failure ? 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200' : running ? 'bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200' : 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200'}`}><span aria-hidden="true">{success ? '✓' : failure ? '!' : running ? '↻' : '•'}</span>{labels[value] ?? value}</span>
}

function PrecheckProgress({ pending, batch }: { pending: boolean; batch: z.infer<typeof schema>['batch'] | undefined }) {
  const preparing = pending && batch?.phase !== 'CHECKING'
  const total = preparing ? 0 : batch?.urls.length ?? 0
  const processed = Math.min(total, Math.max(0, batch?.processed ?? 0))
  const complete = !pending && batch?.phase === 'CHECKED'
  const percent = total ? Math.round(processed * 100 / total) : 0
  const Icon = complete ? CheckCircle2 : preparing ? SearchCheck : LoaderCircle
  return <section aria-label="Tiến độ kiểm tra Cloudflare" className="data-panel space-y-3 border-t-2 border-t-cyan-500 p-4">
    <div className="flex items-center gap-3"><span className={`flex size-9 shrink-0 items-center justify-center rounded-xl ${complete ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300' : 'bg-cyan-100 text-cyan-700 dark:bg-cyan-950 dark:text-cyan-300'}`}><Icon aria-hidden="true" size={18} className={!complete && !preparing ? 'motion-safe:animate-spin' : ''} /></span><div className="min-w-0 flex-1"><p role="status" className="text-sm font-semibold">{complete ? 'Đã hoàn tất kiểm tra Cloudflare' : preparing ? 'Đang chuẩn bị kiểm tra…' : 'Đang kiểm tra Cloudflare'}</p><p className="text-xs text-slate-500">{preparing ? 'Đang chuẩn hóa danh sách và kết nối worker.' : `Đã kiểm tra ${String(processed)}/${String(total)} URL`}</p></div>{!preparing && <span className={`text-lg font-semibold tabular-nums ${complete ? 'text-emerald-600' : 'text-cyan-600'}`}>{percent}%</span>}</div>
    <progress aria-label="Số URL đã kiểm tra" max={total || 1} value={preparing ? undefined : processed} className="sr-only" />
    <div aria-hidden="true" className="h-2.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800"><div style={{ width: preparing ? '100%' : `${String(percent)}%` }} className={`h-full rounded-full transition-[width] duration-500 motion-reduce:transition-none ${complete ? 'bg-emerald-500' : 'bg-gradient-to-r from-cyan-500 to-blue-500'} ${preparing ? 'motion-safe:animate-pulse' : ''}`} /></div>
    {!preparing && !complete && batch?.current_id && <p className="break-all text-xs text-slate-500">Đang xử lý: <span className="font-medium text-slate-700 dark:text-slate-200">{batch.current_id}</span></p>}
    {!complete && <p className="text-xs text-slate-500">Tiến độ cập nhật theo từng URL hoàn tất; URL đang kiểm tra có thể cần thêm thời gian.</p>}
  </section>
}

export function CloudflareWorkerPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const client = useQueryClient()
  const key = ['cloudflare-worker', me.id]
  const can = (action: string) => me.permissions.includes(`cloudflare_worker.${action}`)
  const query = useQuery({ queryKey: key, enabled: can('view'), queryFn: async () => schema.parse(await request({ url: '/cloudflare-worker' })), refetchInterval: 2000 })
  const [input, setInput] = useState('')
  const [cookie, setCookie] = useState('')
  const [visible, setVisible] = useState(false)
  const [showExcluded, setShowExcluded] = useState(false)
  const [delay, setDelay] = useState(30)
  const [confirmed, setConfirmed] = useState(false)
  const [clock, setClock] = useState(() => Date.now())
  const hydrated = useRef('')
  const batch = query.data?.batch
  useEffect(() => {
    if (batch && hydrated.current !== batch.id) {
      hydrated.current = batch.id; setInput(batch.urls.join('\n')); setDelay(batch.delay_seconds); setConfirmed(false); setCookie('')
    }
  }, [batch])
  useEffect(() => { const timer = window.setInterval(() => { setClock(Date.now()) }, 1000); return () => { window.clearInterval(timer) } }, [])
  const mutation = useMutation({ mutationFn: async (action: string) => {
    const url = action === 'precheck' ? '/cloudflare-worker/precheck' : action === 'clear' ? '/cloudflare-worker/cache' : `/cloudflare-worker/${batch?.id ?? ''}/${action}`
    const data = action === 'precheck' ? { urls: input.split(/\r?\n/).map((line) => line.trim()).filter(Boolean) } : ['start', 'resume', 'retry'].includes(action) ? { cookie, delay_seconds: delay, confirmed } : undefined
    return request({ url, method: action === 'clear' ? 'DELETE' : 'POST', ...(data ? { data } : {}) })
  }, onSuccess: (result, action) => {
    setConfirmed(false)
    if (action === 'precheck') {
      const parsed = schema.safeParse(result)
      if (parsed.success) client.setQueryData(key, (previous: z.infer<typeof schema> | undefined) => ({ ...previous, ...parsed.data,
        ...(previous?.submit_enabled !== undefined ? { submit_enabled: previous.submit_enabled } : {}) }))
    }
    if (action === 'clear') { setInput(''); hydrated.current = '' }
  },
  onSettled: () => { setCookie(''); void client.invalidateQueries({ queryKey: key }) } })
  if (!can('view')) return <ForbiddenPage message="Bạn không có quyền xem Cloudflare Worker." />
  const phase = batch?.phase ?? ''
  const busy = mutation.isPending || active.includes(phase)
  const sending = ['SENDING', 'SEND_QUEUED'].includes(phase)
  const allRows = query.data?.records ?? []
  const rows = allRows.filter((row) => !row.excluded)
  const excluded = allRows.filter((row) => row.excluded)
  const sent = rows.filter((row) => ['SUBMITTED', 'ALREADY_SUBMITTED'].includes(row.state)).length
  const unknown = rows.filter((row) => row.state === 'UNKNOWN').length
  const failed = rows.filter((row) => row.state === 'FAILED').length
  const ready = rows.filter((row) => ['READY', 'QUEUED'].includes(row.state)).length
  const enabled = query.data?.submit_enabled === true
  const sendReady = enabled && confirmed && !mutation.isPending && !query.isError && Number.isInteger(delay) && delay >= 0 && delay <= 300
  const remaining = batch?.next_run_at ? Math.max(0, Math.ceil((Date.parse(batch.next_run_at) - clock) / 1000)) : 0
  const requestingPrecheck = mutation.isPending && mutation.variables === 'precheck'
  return <section className="space-y-3">
    <header><h1 className="text-xl font-semibold">Cloudflare Worker</h1><p className="text-xs text-slate-500">Kiểm tra URL dùng Cloudflare và gửi tuần tự qua Dashboard bằng Cookie bạn cung cấp.</p></header>
    <section className="data-panel space-y-3 p-3"><label className="field">Danh sách domain/URL<textarea rows={6} disabled={busy || query.isPending || query.isError} value={input} onChange={(event) => { setInput(event.target.value); setConfirmed(false) }} placeholder="Mỗi dòng một domain hoặc URL" /></label>
      <div className="flex flex-wrap gap-2"><Button className="primary-button" disabled={busy || !input.trim() || !can('precheck') || query.isPending || query.isError} loading={mutation.isPending && mutation.variables === 'precheck'} onClick={() => { mutation.mutate('precheck') }}>Kiểm tra Cloudflare</Button>{phase === 'CHECKING' && <Button disabled={!can('stop') || mutation.isPending} onClick={() => { mutation.mutate('cancel') }}>Ngừng kiểm tra</Button>}<Button disabled={busy || !batch || !can('clear_cache')} onClick={() => { mutation.mutate('clear') }}>Xóa cache kiểm tra hôm nay</Button></div>
    </section>
    {(requestingPrecheck || phase === 'CHECKING' || phase === 'CHECKED') && <PrecheckProgress pending={requestingPrecheck} batch={batch} />}
    {(query.error || mutation.error) && <p role="alert" className="error-notice">{(mutation.error ?? query.error)?.message}</p>}
    {query.isError && <Button onClick={() => { void query.refetch() }}>Tải lại trạng thái</Button>}
    {query.isPending && <p role="status">Đang tải Cloudflare Worker…</p>}
    {batch && <>
      {phase !== 'CHECKING' && <div className="data-panel flex flex-wrap items-center gap-3 p-3 text-xs"><Status value={phase} /><span>Ngày {batch.day}</span><span>Kiểm tra {batch.processed}/{batch.urls.length}</span><span>Đã gửi {sent}/{rows.length} · Lỗi {failed} · Chưa rõ {unknown}</span>{remaining > 0 && sending && <span role="timer">URL tiếp theo sau {remaining}s</span>}{batch.current_id && <span className="min-w-0 break-all">Đang xử lý: {rows.find((row) => row.id === batch.current_id)?.url ?? batch.current_id}</span>}</div>}
      {batch.error && <p role="alert" className="error-notice">{errors[batch.error] ?? 'Batch cần kiểm tra trước khi tiếp tục.'}</p>}
      {batch.invalid.length > 0 && <p className="text-xs text-amber-700">Bỏ qua URL không hợp lệ: {batch.invalid.join(', ')}</p>}
      {batch.duplicates.length > 0 && <p className="text-xs text-slate-500">Đã bỏ {batch.duplicates.length} URL trùng trong nội dung nhập.</p>}
      {unknown > 0 && <p className="text-xs text-amber-700">URL chưa rõ kết quả được khóa gửi lại. Hãy đối soát trên Cloudflare.</p>}
      {phase !== 'CHECKING' && <section className="data-panel space-y-3 p-3" aria-label="Cấu hình gửi Cloudflare">
        <p className="text-xs text-slate-500">Account ID lấy từ {me.permissions.includes('mail_accounts.view_own') ? <Link to="/settings/mail" className="underline">Cấu hình mail → Cloudflare Dashboard</Link> : 'Cấu hình mail → Cloudflare Dashboard'}, hoặc Cookie curr-account nếu chưa cấu hình. Cookie chỉ giữ trong RAM của runner; restart cần nhập lại.</p>
        {batch.account_id ? <p className="break-all text-xs text-slate-500">Account ID đã chốt cho batch: <span className="font-mono text-slate-700 dark:text-slate-200">{batch.account_id}</span></p> : <p className="text-xs text-slate-500">Chưa lưu Account ID vẫn có thể gửi nếu Cookie chứa curr-account hợp lệ. Nếu cả hai nguồn đều thiếu, hệ thống chặn gửi.</p>}
        {!enabled && <p className="text-xs text-amber-700">Gửi Cloudflare đang tắt trên backend. Bạn vẫn có thể kiểm tra và xem nội dung report.</p>}
        <label className="field">Cookie Cloudflare<input type={visible ? 'text' : 'password'} autoComplete="off" spellCheck={false} value={cookie} disabled={sending || mutation.isPending} onChange={(event) => { setCookie(event.target.value); setConfirmed(false) }} /></label>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={visible} onChange={(event) => { setVisible(event.target.checked) }} />Hiện Cookie</label>
        <label className="field">Thời gian chờ trước URL tiếp theo (giây)<input type="number" min={0} max={300} disabled={sending || mutation.isPending} value={delay} onChange={(event) => { setDelay(Number(event.target.value)); setConfirmed(false) }} /></label>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={confirmed} disabled={sending || mutation.isPending} onChange={(event) => { setConfirmed(event.target.checked) }} />Tôi đã kiểm tra nội dung report và xác nhận gửi.</label>
        <div className="flex flex-wrap gap-2"><Button className="primary-button" disabled={!sendReady || !can('start') || !cookie.trim() || !ready || !['CHECKED', 'COMPLETED'].includes(phase)} onClick={() => { mutation.mutate('start') }}>Bắt đầu gửi</Button><Button disabled={!sendReady || !can('retry') || !cookie.trim() || !failed || !['CHECKED', 'COMPLETED'].includes(phase)} onClick={() => { mutation.mutate('retry') }}>Thử lại URL lỗi</Button><Button disabled={!sending || !can('stop') || mutation.isPending} onClick={() => { mutation.mutate('pause') }}>Tạm dừng</Button><Button disabled={!sendReady || !can('resume') || !['PAUSED', 'WAITING_FOR_SESSION'].includes(phase) || (phase === 'WAITING_FOR_SESSION' && !cookie.trim())} onClick={() => { mutation.mutate('resume') }}>Tiếp tục</Button><Button className="danger-button" disabled={!active.includes(phase) || !can('stop') || mutation.isPending} onClick={() => { mutation.mutate('cancel') }}>Ngừng hẳn</Button></div>
      </section>}
      {rows.length > 0 && <ResultTable rows={rows} />}
      {excluded.length > 0 && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={showExcluded} onChange={(event) => { setShowExcluded(event.target.checked) }} />Hiện URL bị loại ({excluded.length})</label>}
      {showExcluded && excluded.length > 0 && <ResultTable rows={excluded} />}
    </>}
  </section>
}

function ResultTable({ rows }: { rows: z.infer<typeof rowSchema>[] }) {
  return <div className="data-panel max-h-96 overflow-auto"><table className="data-table"><thead className="sticky top-0 bg-white dark:bg-slate-950"><tr><th>URL / nội dung report</th><th>Trạng thái</th><th>Lần thử</th><th>Mã report / kết quả</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td className="max-w-lg break-all">{row.url}{row.draft && <details className="mt-1"><summary className="cursor-pointer text-xs text-cyan-700">Xem nội dung report</summary><pre className="mt-2 whitespace-pre-wrap break-words text-xs">{row.draft}</pre></details>}</td><td><Status value={row.state} /></td><td>{row.attempts}</td><td className="max-w-xs break-words text-xs">{row.report_id ?? (row.error ? errors[row.error] ?? 'Cần kiểm tra kết quả.' : '—')}</td></tr>)}</tbody></table></div>
}

import { useEffect, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getSendJob, type WorkerItem, type WorkerOperation } from './api'

const tones = {
  blue: 'border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200',
  green: 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200',
  violet: 'border-violet-200 bg-violet-50 text-violet-800 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-200',
  amber: 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200',
  slate: 'border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200',
  red: 'border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-200',
}
export function WorkerMetric({ label, value, tone = 'blue', compact = false }: { label: string; value: number | string; tone?: keyof typeof tones; compact?: boolean }) {
  if (compact) return <div className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-xs ${tones[tone]}`} aria-label={`${label}: ${String(value)}`}>
    <span>{({ 'Email đã gửi': 'Đã gửi', 'Email lỗi': 'Lỗi', 'Chưa rõ kết quả': 'Chưa rõ', 'Chưa gửi / đang gửi': 'Chờ/gửi' } as Record<string, string>)[label] ?? label}</span><strong className="text-sm tabular-nums">{value}</strong>
  </div>
  return <div className={`min-w-0 rounded-xl border p-4 ${tones[tone]}`} aria-label={`${label}: ${String(value)}`}>
    <p className="text-sm font-medium">{label}</p>
    <p className="mt-2 break-words text-3xl font-bold tabular-nums sm:text-4xl">{value}</p>
  </div>
}

export function PrecheckProgress({ operation, ownerId, sendJobId = null, accounts = [], controls }: { operation: WorkerOperation; ownerId: string; sendJobId?: string | null; accounts?: { id: string; label: string; sender_email: string }[]; controls?: ReactNode }) {
  const send = useQuery({ queryKey: ['domain-worker-send', ownerId, sendJobId ?? ''], enabled: Boolean(sendJobId),
    queryFn: ({ signal }) => getSendJob(sendJobId ?? '', signal),
    refetchInterval: (query) => query.state.data && ['queued', 'running', 'stopping'].includes(query.state.data.state) ? 2000 : false })
  const done = operation.checks.filter((item) => ['succeeded', 'failed', 'unknown'].includes(item.status)).length
  const ready = operation.checks.filter((item) => item.status === 'succeeded' && item.disposition === 'ready' && item.recipients.length > 0 && item.evidence_set_id)
  const checking = operation.checks.filter((item) => item.status === 'running')
  const sentReady = ready.filter((item) => operation.sender_account_ids.length > 0 && operation.sender_account_ids.every((accountId) => deliveryStates(item, accountId, send.data).every((state) => state === 'sent'))).length
  const running = ['queued', 'running', 'stopping'].includes(operation.state)
  return <div className="space-y-4 p-4">
    {!sendJobId && <><div className="grid gap-3 sm:grid-cols-3">
      <WorkerMetric label="Đã kiểm tra" value={`${String(done)}/${String(operation.checks.length)}`} />
      <WorkerMetric label="Đủ điều kiện" value={ready.length} tone="green" />
      <WorkerMetric label="Đang kiểm tra" value={checking.length} tone="violet" />
    </div>
    <progress aria-label="Tiến độ precheck" className="h-3 w-full accent-cyan-600" value={done} max={operation.checks.length || 1} />
    {running && <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-950" role="status">
      <p className="text-xs font-semibold uppercase tracking-wide text-blue-700 dark:text-blue-300">Domain đang kiểm tra</p>
      {checking.length ? checking.map((item) => <p key={item.id} className="mt-2 break-all text-lg font-semibold">{item.requested_url}</p>) : <p className="mt-2 text-sm">{operation.state === 'stopping' ? 'Đang chờ worker dừng tại checkpoint…' : 'Đang chờ worker xử lý URL tiếp theo…'}</p>}
    </div>}
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
      <WorkerMetric label="Cloaking" value={operation.checks.filter((item) => item.disposition === 'cloaking_review').length} tone="violet" />
      <WorkerMetric label="Thiếu bằng chứng" value={operation.checks.filter((item) => item.disposition === 'evidence_review').length} tone="amber" />
      <WorkerMetric label="Không email" value={operation.checks.filter((item) => item.disposition === 'excluded_no_email').length + (operation.input_summary?.no_email_count ?? 0)} tone="slate" />
      <WorkerMetric label="Đã gửi hôm nay" value={operation.checks.filter((item) => item.disposition === 'excluded_already_sent').length + (operation.input_summary?.sent_count ?? 0) + sentReady} tone="green" />
      <WorkerMetric label="Lỗi / chưa rõ" value={operation.checks.filter((item) => ['failed', 'unknown'].includes(item.status)).length} tone="red" />
    </div>
    </>}
    {sendJobId && <details className="rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-900"><summary>Kết quả precheck: {done}/{operation.checks.length} URL · {ready.length} đủ điều kiện</summary>
      <p className="mt-2">Cloaking: {operation.checks.filter((item) => item.disposition === 'cloaking_review').length} · Thiếu bằng chứng: {operation.checks.filter((item) => item.disposition === 'evidence_review').length} · Không email: {operation.checks.filter((item) => item.disposition === 'excluded_no_email').length + (operation.input_summary?.no_email_count ?? 0)} · Đã gửi trước lượt này: {operation.checks.filter((item) => item.disposition === 'excluded_already_sent').length + (operation.input_summary?.sent_count ?? 0)} · Lỗi / chưa rõ: {operation.checks.filter((item) => ['failed', 'unknown'].includes(item.status)).length}</p>
    </details>}
    {controls}
    <section aria-label="Domain đủ điều kiện gửi" className="overflow-hidden rounded-xl border border-emerald-200 dark:border-emerald-800">
      <header className="bg-emerald-50 p-4 dark:bg-emerald-950"><h3 className="font-semibold">{sendJobId ? 'Theo dõi domain' : 'Danh sách sẵn sàng gửi'} ({ready.length})</h3>
        <p className="text-xs tabular-nums">Domain đạt điều kiện được thêm vào bảng sau mỗi lần cập nhật.{running ? ' Danh sách tiếp tục cập nhật đến khi precheck kết thúc.' : ' Kết quả gửi cập nhật tại đây theo từng tài khoản.'}</p></header>
      {send.isError && <p role="alert" className="error-notice m-3">Không cập nhật được trạng thái gửi. Đang hiển thị dữ liệu gần nhất.</p>}
      {ready.length ? <div className="max-h-96 overflow-auto"><table className="data-table"><thead><tr><th>#</th><th>Domain / URL</th><th>Email nhận</th><th>Trạng thái domain</th><th>Tài khoản gửi</th></tr></thead>
        <tbody>{ready.map((item, index) => {
          const sentItem = send.data?.items.find((entry) => entry.requested_url === item.requested_url)
          const groups = operation.sender_account_ids.map((accountId) => {
            const account = accounts.find((entry) => entry.id === accountId)
            const outcomes = deliveryStates(item, accountId, send.data)
            return { accountId, account, outcomes }
          })
          const overall = pendingStatus(deliveryStatus(groups.flatMap((group) => group.outcomes), Boolean(sendJobId), sentItem?.state === 'running'), send.data)
          return <tr key={item.id}><td>{index + 1}</td><td className="min-w-48 break-all font-medium">{item.requested_url}</td>
            <td>{item.recipients.map((recipient) => <p key={`${recipient.channel}:${recipient.email}`} className="break-all">{recipient.email}</p>)}</td>
            <td><DeliveryBadge state={overall} /></td>
            <td className="min-w-56 space-y-2">{groups.map((group) => <div key={group.accountId} className="border-b border-slate-100 py-1.5 last:border-0 dark:border-slate-800" aria-label={`Kết quả tài khoản ${group.account?.sender_email ?? group.accountId}`}>
              <p className="break-all text-xs font-medium">{group.account?.sender_email ?? `Tài khoản ${group.accountId}`}</p>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
              <DeliveryBadge state={pendingStatus(deliveryStatus(group.outcomes, Boolean(sendJobId), false), send.data)} />
              <p className="text-xs tabular-nums">Đã gửi {group.outcomes.filter((state) => state === 'sent').length}/{group.outcomes.length}{group.outcomes.includes('failed') && <> · Lỗi {group.outcomes.filter((state) => state === 'failed').length}</>}{group.outcomes.includes('unknown') && <> · Chưa rõ {group.outcomes.filter((state) => state === 'unknown').length}</>}</p>
              </div>

            </div>)}</td></tr>
        })}</tbody></table></div>
        : <p className="p-4 text-sm text-slate-500">{running ? 'Chưa có domain đủ điều kiện. Hệ thống đang tiếp tục kiểm tra.' : 'Không có domain đủ điều kiện gửi email.'}</p>}
    </section>
  </div>
}

function deliveryStates(item: WorkerItem, accountId: string, job?: Awaited<ReturnType<typeof getSendJob>>) {
  const sentItem = job?.items.find((entry) => entry.requested_url === item.requested_url)
  return item.recipients.map((recipient) => sentItem?.deliveries.find((delivery) => delivery.sender_account_id === accountId && delivery.channel === recipient.channel && delivery.recipient.toLowerCase() === recipient.email.toLowerCase())?.state ?? 'pending')
}
function deliveryStatus(states: string[], started: boolean, running: boolean) {
  if (states.length && states.every((state) => state === 'sent')) return 'sent'
  if (states.includes('unknown')) return 'unknown'
  if (states.includes('failed')) return states.includes('sent') ? 'partial' : 'failed'
  if (states.includes('sending') || running) return 'sending'
  if (states.includes('sent')) return 'partial'
  return started ? 'pending' : 'ready'
}
function pendingStatus(state: string, job?: Awaited<ReturnType<typeof getSendJob>>) {
  if (state !== 'pending') return state
  if (job?.control_mode === 'cancel') return 'cancelled'
  if (job?.state === 'stopped') return 'paused'
  return state
}
function DeliveryBadge({ state }: { state: string }) {
  const labels: Record<string, string> = { sent: 'Đã gửi thành công', unknown: 'Chưa rõ kết quả', failed: 'Gửi lỗi', partial: 'Gửi một phần', sending: 'Đang gửi', pending: 'Chờ gửi', ready: 'Sẵn sàng gửi', cancelled: 'Đã ngừng — chưa gửi', paused: 'Tạm dừng — chưa gửi' }
  return <span className={`inline-block rounded-full px-2 py-1 text-xs font-semibold ${state === 'sent' ? tones.green : ['failed', 'unknown', 'partial'].includes(state) ? tones.amber : tones.blue}`}>{labels[state] ?? state}</span>
}

export function BatchCountdown({ deadline, compact = false }: { deadline: string; compact?: boolean }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => { setNow(Date.now()) }, 1000)
    return () => { window.clearInterval(timer) }
  }, [])
  const seconds = Math.max(0, Math.ceil((new Date(deadline).getTime() - now) / 1000))
  const clock = [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map((value) => String(value).padStart(2, '0')).join(':')
  if (compact) return <div className="inline-flex flex-wrap items-center gap-1.5 rounded-md bg-violet-50 px-2 py-1 text-xs text-violet-900 dark:bg-violet-950 dark:text-violet-200" title={`Dự kiến lúc ${new Date(deadline).toLocaleTimeString('vi-VN')}`}>
    <span>Batch tiếp theo</span><span role="timer" aria-label="Đếm ngược batch tiếp theo" className="font-semibold tabular-nums">{clock}</span>
    {seconds === 0 && <span>Đã đến giờ. Đang chờ worker nhận batch tiếp theo…</span>}
  </div>
  return <div className="rounded-xl border border-violet-200 bg-violet-50 p-4 text-violet-900 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-200">
    <p className="text-sm font-medium">Batch tiếp theo sau</p>
    <p role="timer" aria-label="Đếm ngược batch tiếp theo" className="mt-2 text-4xl font-bold tabular-nums">{clock}</p>
    <p className="mt-2 text-xs">{seconds > 0 ? `Dự kiến lúc ${new Date(deadline).toLocaleTimeString('vi-VN')}` : 'Đã đến giờ. Đang chờ worker nhận batch tiếp theo…'}</p>
  </div>
}

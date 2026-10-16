import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '../../components/shared/Button'
import type { Me } from '../auth/types'
import { controlSendJob, getSendJob, getSendSummary, getWorkerWorkspace, startSendBatch } from './api'
import { BatchCountdown, WorkerMetric } from './WorkerProgress'

const active = new Set(['queued', 'running', 'stopping'])
const labels: Record<string, string> = { queued: 'Chờ worker', running: 'Đang gửi', stopping: 'Đang dừng',
  stopped: 'Đã dừng', completed: 'Hoàn tất gửi', partial: 'Còn delivery cần xử lý', pending: 'Chưa gửi',
  sending: 'Đang gửi', sent: 'Đã gửi', failed: 'Gửi lỗi', unknown: 'Chưa rõ kết quả — không tự gửi lại', succeeded: 'Đã gửi' }

export function SendBatchPanel({ sourceId, me, readOnly = false, sendJobId = null }: { sourceId: string; me: Me; readOnly?: boolean; sendJobId?: string | null }) {
  const client = useQueryClient()

  const [batchSize, setBatchSize] = useState(5)
  const [minutes, setMinutes] = useState(5)
  const [confirmed, setConfirmed] = useState(false)
  const [startedId, setStartedId] = useState('')
  const summaryKey = ['domain-worker-send-summary', me.id, sourceId]
  const summary = useQuery({ queryKey: summaryKey, enabled: !readOnly, queryFn: ({ signal }) => getSendSummary(sourceId, signal), refetchInterval: 5000 })
  const id = sendJobId || startedId || summary.data?.send_job_id || ''
  const jobKey = ['domain-worker-send', me.id, id]
  const job = useQuery({ queryKey: jobKey, enabled: Boolean(id), queryFn: ({ signal }) => getSendJob(id, signal),
    refetchInterval: (query) => query.state.data && active.has(query.state.data.state) ? 2000 : false })
  const start = useMutation({ mutationFn: () => startSendBatch(sourceId, batchSize, minutes * 60, confirmed),
    onMutate: () => { client.setQueryData(['domain-worker-workspace', me.id], (old: Record<string, unknown> | undefined) => old ? { ...old, locked: true, phase: 'sending', current_id: sourceId } : old) },
    onSuccess: (data) => { setStartedId(data.id); setConfirmed(false)
      client.setQueryData<Awaited<ReturnType<typeof getWorkerWorkspace>>>(['domain-worker-workspace', me.id], (old) => old ? {
        ...old, locked: true, phase: 'sending', current_id: sourceId, send_job_id: data.id,
        history: old.history.map((entry) => entry.id === sourceId ? { ...entry, send_job_id: data.id, send_state: data.state } : entry) } : old)
      client.setQueryData(['domain-worker-send', me.id, data.id], data); void client.invalidateQueries({ queryKey: summaryKey }) },
    onSettled: () => { void client.invalidateQueries({ queryKey: ['domain-worker-workspace', me.id] }) } })
  const control = useMutation({ mutationFn: (action: 'pause' | 'cancel' | 'resume' | 'retry') => controlSendJob(id, action),
    onMutate: (action) => { if (['resume', 'retry'].includes(action)) client.setQueryData(['domain-worker-workspace', me.id], (old: Record<string, unknown> | undefined) => old ? { ...old, locked: true, phase: 'sending', current_id: sourceId } : old) },
    onSuccess: (data) => { client.setQueryData(jobKey, data); void client.invalidateQueries({ queryKey: summaryKey }) },
    onSettled: () => { void client.invalidateQueries({ queryKey: ['domain-worker-workspace', me.id] }) } })
  const busy = start.isPending || control.isPending || Boolean(job.data && active.has(job.data.state))
  const sending = job.data && active.has(job.data.state) ? job.data : null
  const valid = Number.isInteger(batchSize) && batchSize >= 1 && batchSize <= 100 && Number.isInteger(minutes) && minutes >= 0 && minutes <= 1440
  const error = start.error ?? control.error ?? summary.error ?? job.error
  const deliveries = [...new Map(job.data?.items.flatMap((item) => item.deliveries).map((delivery) => [delivery.id, delivery]) ?? []).values()]
  const totalItems = job.data?.items.length ?? 0
  const doneItems = job.data?.items.filter((item) => !['pending', 'running'].includes(item.state)).length ?? 0
  const totalBatches = job.data ? Math.ceil(totalItems / job.data.batch_size) : 0
  const waitingBetweenBatches = Boolean(job.data?.state === 'queued' && job.data.next_run_at && new Date(job.data.next_run_at).getTime() > job.dataUpdatedAt)
  const currentBatch = job.data ? Math.min(totalBatches, Math.ceil(doneItems / job.data.batch_size) + (active.has(job.data.state) && !waitingBetweenBatches && doneItems % job.data.batch_size === 0 ? 1 : 0)) : 0
  const cancelled = job.data?.control_mode === 'cancel'
  const paused = job.data?.state === 'stopped' && !cancelled
  const stopTitle = cancelled ? job.data?.state === 'stopping' ? 'Đang ngừng hẳn job' : 'Job đã ngừng hẳn' : paused ? 'Job đang tạm dừng' : job.data?.state === 'stopping' ? 'Đang tạm dừng job' : null
  return <section className="data-panel space-y-2 border-t-2 border-t-blue-500 p-3" aria-label="Cấu hình gửi worker">
    <h2 className="text-sm font-semibold">{id ? 'Theo dõi gửi email' : 'Cấu hình gửi worker'}</h2>
    {!readOnly && summary.isPending && <p role="status">Đang lọc delivery đã gửi hôm nay…</p>}
    {error && <p className="error-notice" role="alert">{error.message}</p>}
    {summary.data && !id && summary.data.pending_deliveries === 0 && <p role="status" className="rounded-lg bg-amber-50 p-4 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">Không có domain còn đủ điều kiện gửi email.{summary.data.blocked_deliveries > 0 ? ' Có mục chưa rõ kết quả cần đối soát.' : ' Các email đủ điều kiện đã được gửi.'}</p>}
    {summary.data && !id && summary.data.pending_deliveries > 0 && <>
      <p className="text-xs">{summary.data.ready_domains} domain còn cần gửi · {summary.data.pending_deliveries} delivery còn thiếu · {summary.data.already_sent} đã gửi hôm nay · {summary.data.blocked_deliveries} chưa rõ/đang gửi.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="field">Số domain mỗi batch<input type="number" min={1} max={100} value={sending?.batch_size ?? batchSize} disabled={busy} onChange={(event) => { setBatchSize(Number(event.target.value)); setConfirmed(false) }} /></label>
        <label className="field">Nghỉ giữa hai batch (phút)<input type="number" min={0} max={1440} value={sending ? sending.interval_seconds / 60 : minutes} disabled={busy} onChange={(event) => { setMinutes(Number(event.target.value)); setConfirmed(false) }} /></label>
      </div>
      <label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => { setConfirmed(event.target.checked) }} />Tôi xác nhận gửi báo cáo bằng các tài khoản đã chọn cho lượt precheck này.</label>
      <Button className="primary-button" loading={start.isPending} disabled={busy || !valid || !confirmed || !summary.data.pending_deliveries || !me.permissions.includes('domain_worker.start')}
        onClick={() => { start.mutate() }}>Bắt đầu gửi batch</Button>
    </>}
    {job.data && <section className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700" aria-label="Tiến trình gửi batch">
      <div role="status" className={`flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border p-3 ${job.data.state === 'completed' ? 'border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200' : active.has(job.data.state) ? 'border-blue-200 bg-blue-50 text-blue-900 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200' : 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200'}`}>
        <h3 className="text-sm font-semibold">{stopTitle ?? (job.data.state === 'completed' ? 'Job đã hoàn thành' : active.has(job.data.state) ? `${waitingBetweenBatches ? 'Đang nghỉ sau' : job.data.state === 'queued' ? 'Đang chờ chạy' : 'Đang chạy'} batch ${String(currentBatch)}/${String(totalBatches)}` : labels[job.data.state] ?? job.data.state)}</h3>
        <p className="text-xs tabular-nums">{doneItems}/{totalItems} domain đã xử lý</p>
      <div className="flex flex-wrap items-center gap-2">
        <WorkerMetric compact label="Email đã gửi" value={deliveries.filter((delivery) => delivery.state === 'sent').length} tone="green" />
        <WorkerMetric compact label="Email lỗi" value={deliveries.filter((delivery) => delivery.state === 'failed').length} tone="red" />
        <WorkerMetric compact label="Chưa rõ kết quả" value={deliveries.filter((delivery) => delivery.state === 'unknown').length} tone="amber" />
        <WorkerMetric compact label="Chưa gửi / đang gửi" value={deliveries.filter((delivery) => ['pending', 'sending'].includes(delivery.state)).length} />
      </div>
      {job.data.state === 'queued' && job.data.next_run_at && <BatchCountdown key={job.data.next_run_at} deadline={job.data.next_run_at} compact />}
        <progress aria-label="Tiến độ gửi email" className="h-1.5 w-24 accent-blue-600" max={100} value={totalItems ? doneItems / totalItems * 100 : 0} />
        {job.data.state === 'completed' && <p className="basis-full text-xs">Đã xử lý xong toàn bộ danh sách. Bạn có thể kiểm tra danh sách mới.</p>}
        {job.data.state === 'stopping' && <p className="basis-full text-xs">Đang hoàn tất domain hiện tại và lưu kết quả; không nhận domain tiếp theo.</p>}
        {paused && <p className="basis-full text-xs">Kết quả đã được giữ lại. Bấm Tiếp tục job để xử lý phần còn lại, hoặc Ngừng hẳn job để kết thúc lượt này.</p>}
        {cancelled && job.data.state === 'stopped' && <p className="basis-full text-xs">Lượt này đã kết thúc, không thể tiếp tục hoặc retry job này. Email đã gửi được giữ nguyên; các mục chưa gửi vẫn lưu trong lịch sử.</p>}
        {['partial', 'failed'].includes(job.data.state) && <p className="basis-full text-xs">Lượt chạy đã kết thúc nhưng còn mục lỗi hoặc chưa rõ kết quả. Kiểm tra bên dưới và thử lại các mục lỗi.</p>}
      </div>

      {active.has(job.data.state) && <div className="flex flex-wrap items-center gap-x-2 rounded-lg bg-blue-50 px-3 py-2 dark:bg-blue-950"><p className="text-xs font-semibold uppercase tracking-wide text-blue-600 dark:text-blue-300">Domain đang gửi</p>
        {job.data.items.some((item) => item.state === 'running') ? job.data.items.filter((item) => item.state === 'running').map((item) => <p key={item.id} className="break-all text-xs font-medium">{item.requested_url}</p>) : <p className="basis-full text-xs">{job.data.next_run_at ? 'Đang chờ lượt batch tiếp theo.' : 'Đang chờ worker nhận domain tiếp theo…'}</p>}
      </div>}

      <p className="text-xs text-slate-500">Đã gửi nghĩa là máy chủ mail đã chấp nhận thư; chưa xác minh thư tới hộp thư người nhận.</p>
      {deliveries.some((delivery) => delivery.state === 'unknown') && <p className="text-xs">Mục chưa rõ không được tự gửi lại. Cần đối soát với log máy chủ mail trước khi quyết định tiếp.</p>}
      {deliveries.some((delivery) => delivery.state === 'sent' && ['failed', 'unknown', 'configuration_missing', 'endpoint_denied'].includes(delivery.sent_copy_status)) && <p className="error-notice" role="alert">Email đã gửi nhưng chưa lưu được vào Đã gửi. Cần kiểm tra kết nối mail; không gửi lại email.</p>}
      <p className="text-xs">{job.data.business_day} · {job.data.batch_size} domain/batch · nghỉ {job.data.interval_seconds / 60} phút</p>

      {!readOnly && <div className="flex flex-wrap gap-2">
        {['queued', 'running'].includes(job.data.state) && !cancelled && me.permissions.includes('domain_worker.stop') && <Button disabled={control.isPending} onClick={() => { control.mutate('pause') }}>Tạm dừng job</Button>}
        {!cancelled && !['completed'].includes(job.data.state) && me.permissions.includes('domain_worker.stop') && <Button className="text-rose-700 dark:text-rose-300" disabled={control.isPending} onClick={() => { control.mutate('cancel') }}>Ngừng hẳn job</Button>}
        {!cancelled && ['stopped', 'partial', 'failed'].includes(job.data.state) && job.data.items.some((item) => item.state === 'pending') && me.permissions.includes('domain_worker.start') && <Button className="primary-button" disabled={busy} onClick={() => { control.mutate('resume') }}>Tiếp tục job</Button>}
        {!cancelled && !active.has(job.data.state) && job.data.items.some((item) => ['failed', 'unknown'].includes(item.state) && item.deliveries.some((row) => ['failed', 'pending'].includes(row.state))) && me.permissions.includes('domain_worker.retry') && <Button disabled={busy} onClick={() => { control.mutate('retry') }}>Thử lại các mục lỗi</Button>}
      </div>}

    </section>}
  </section>
}

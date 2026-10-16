import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '../../components/shared/Button'
import type { Me } from '../auth/types'
import { controlSendJob, getSendJob, getSendSummary, startSendBatch } from './api'

const active = new Set(['queued', 'running', 'stopping'])
const labels: Record<string, string> = { queued: 'Chờ worker', running: 'Đang gửi', stopping: 'Đang dừng',
  stopped: 'Đã dừng', completed: 'Hoàn tất gửi', partial: 'Còn delivery cần xử lý', pending: 'Chưa gửi',
  sending: 'Đang gửi', sent: 'Đã gửi', failed: 'Gửi lỗi', unknown: 'Chưa rõ kết quả — không tự gửi lại', succeeded: 'Đã gửi' }

export function SendBatchPanel({ sourceId, me }: { sourceId: string; me: Me }) {
  const client = useQueryClient()
  const [batchSize, setBatchSize] = useState(5)
  const [minutes, setMinutes] = useState(5)
  const [confirmed, setConfirmed] = useState(false)
  const [startedId, setStartedId] = useState('')
  const summaryKey = ['domain-worker-send-summary', me.id, sourceId]
  const summary = useQuery({ queryKey: summaryKey, queryFn: ({ signal }) => getSendSummary(sourceId, signal), refetchInterval: 5000 })
  const id = startedId || summary.data?.send_job_id || ''
  const jobKey = ['domain-worker-send', me.id, id]
  const job = useQuery({ queryKey: jobKey, enabled: Boolean(id), queryFn: ({ signal }) => getSendJob(id, signal),
    refetchInterval: (query) => query.state.data && active.has(query.state.data.state) ? 2000 : false })
  const start = useMutation({ mutationFn: () => startSendBatch(sourceId, batchSize, minutes * 60, confirmed),
    onSuccess: (data) => { setStartedId(data.id); setConfirmed(false)
      client.setQueryData(['domain-worker-send', me.id, data.id], data); void client.invalidateQueries({ queryKey: summaryKey }) } })
  const control = useMutation({ mutationFn: (action: 'stop' | 'resume' | 'retry') => controlSendJob(id, action),
    onSuccess: (data) => { client.setQueryData(jobKey, data); void client.invalidateQueries({ queryKey: summaryKey }) } })
  const busy = start.isPending || control.isPending || Boolean(job.data && active.has(job.data.state))
  const sending = job.data && active.has(job.data.state) ? job.data : null
  const valid = Number.isInteger(batchSize) && batchSize >= 1 && batchSize <= 100 && Number.isInteger(minutes) && minutes >= 0 && minutes <= 1440
  const error = start.error ?? control.error ?? summary.error ?? job.error
  const deliveries = [...new Map(job.data?.items.flatMap((item) => item.deliveries).map((delivery) => [delivery.id, delivery]) ?? []).values()]
  return <section className="data-panel space-y-3 p-3" aria-label="Cấu hình gửi worker">
    <h2 className="text-sm font-semibold">Cấu hình gửi worker</h2>
    {summary.isPending && <p role="status">Đang lọc delivery đã gửi hôm nay…</p>}
    {error && <p className="error-notice" role="alert">{error.message}</p>}
    {summary.data && <>
      <p className="text-xs">{summary.data.ready_domains} domain còn cần gửi · {summary.data.pending_deliveries} delivery còn thiếu · {summary.data.already_sent} đã gửi hôm nay · {summary.data.blocked_deliveries} chưa rõ/đang gửi.</p>
      <details className="text-xs"><summary>Danh sách domain đủ điều kiện gửi ({summary.data.ready_domains})</summary>
        <ol className="list-inside list-decimal space-y-1 pt-2">{summary.data.checks.map((check) => <li className="break-all" key={check.check_id}>{check.requested_url} · {check.pending_deliveries} delivery</li>)}</ol>
      </details>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="field">Số domain mỗi batch<input type="number" min={1} max={100} value={sending?.batch_size ?? batchSize} disabled={busy} onChange={(event) => { setBatchSize(Number(event.target.value)); setConfirmed(false) }} /></label>
        <label className="field">Nghỉ giữa hai batch (phút)<input type="number" min={0} max={1440} value={sending ? sending.interval_seconds / 60 : minutes} disabled={busy} onChange={(event) => { setMinutes(Number(event.target.value)); setConfirmed(false) }} /></label>
      </div>
      <label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => { setConfirmed(event.target.checked) }} />Tôi xác nhận gửi báo cáo bằng các tài khoản đã chọn cho lượt precheck này.</label>
      <Button className="primary-button" loading={start.isPending} disabled={busy || !valid || !confirmed || !summary.data.pending_deliveries || !me.permissions.includes('domain_worker.start')}
        onClick={() => { start.mutate() }}>Bắt đầu gửi batch</Button>
    </>}
    {job.data && <section className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700" aria-label="Tiến trình gửi batch">
      <h3 className="text-sm font-semibold">{labels[job.data.state] ?? job.data.state}</h3>
      <p className="text-xs" role="status">Đã gửi: {deliveries.filter((delivery) => delivery.state === 'sent').length} · Lỗi: {deliveries.filter((delivery) => delivery.state === 'failed').length} · Chưa rõ: {deliveries.filter((delivery) => delivery.state === 'unknown').length} · Chưa gửi/đang gửi: {deliveries.filter((delivery) => ['pending', 'sending'].includes(delivery.state)).length}</p>
      {deliveries.some((delivery) => delivery.state === 'sent' && ['failed', 'unknown', 'configuration_missing', 'endpoint_denied'].includes(delivery.sent_copy_status)) && <p className="error-notice" role="alert">Email đã gửi nhưng chưa lưu được vào Đã gửi. Cần kiểm tra kết nối mail; không gửi lại email.</p>}
      <p className="text-xs">{job.data.business_day} · {job.data.batch_size} domain/batch · nghỉ {job.data.interval_seconds / 60} phút</p>
      {job.data.state === 'queued' && job.data.next_run_at && <p className="text-xs">Batch tiếp theo từ {new Date(job.data.next_run_at).toLocaleString('vi-VN')}</p>}
      <div className="flex flex-wrap gap-2">
        {active.has(job.data.state) && me.permissions.includes('domain_worker.stop') && <Button disabled={control.isPending} onClick={() => { control.mutate('stop') }}>Dừng gửi</Button>}
        {job.data.state === 'stopped' && job.data.items.some((item) => item.state === 'pending') && me.permissions.includes('domain_worker.start') && <Button disabled={busy} onClick={() => { control.mutate('resume') }}>Tiếp tục gửi</Button>}
        {!active.has(job.data.state) && job.data.items.some((item) => item.state === 'failed') && me.permissions.includes('domain_worker.retry') && <Button disabled={busy} onClick={() => { control.mutate('retry') }}>Thử lại delivery lỗi</Button>}
      </div>
      <div className="overflow-x-auto"><table className="data-table"><thead><tr><th>Domain/URL</th><th>Tiến trình</th><th>Email nhận</th></tr></thead><tbody>
        {job.data.items.map((item) => <tr key={item.id}><td className="max-w-80 break-all">{item.requested_url}</td><td>{labels[item.state] ?? item.state}</td>
          <td>{item.deliveries.map((delivery) => <p key={delivery.id}>{delivery.recipient} · {labels[delivery.state] ?? delivery.state}</p>)}</td></tr>)}
      </tbody></table></div>
    </section>}
  </section>
}

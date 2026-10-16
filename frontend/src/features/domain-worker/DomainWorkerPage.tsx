import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useOutletContext } from 'react-router-dom'
import { Button } from '../../components/shared/Button'
import type { Me } from '../auth/types'
import { controlPrecheck, createPrecheck, getWorkerAccounts, getPrecheck, getReadiness, type WorkerItem } from './api'
import { SendBatchPanel } from './SendBatchPanel'

const active = new Set(['queued', 'running', 'stopping'])
const captureErrors: Record<string, string> = {
  browser_runtime_missing: 'Backend chưa cài Playwright; cần cập nhật môi trường worker.',
  browser_executable_missing: 'Worker chưa có Chromium; cần cài trình duyệt cho Playwright.',
  browser_timeout: 'Chụp ảnh quá thời gian chờ. Có thể precheck lại URL này.',
  browser_navigation_failed: 'Trình duyệt không tải được trang để chụp ảnh.',
  egress_request_denied: 'Một request của trang bị chặn bởi chính sách mạng.',
  terminal_page: 'Trang cảnh báo hoặc lỗi trình duyệt không dùng làm bằng chứng nội dung.',
  invalid_capture_artifacts: 'Ảnh hoặc manifest chụp được không hợp lệ.',
}
const states: Record<string, string> = { queued: 'Chờ worker', pending: 'Chờ xử lý', running: 'Đang kiểm tra',
  stopping: 'Đang dừng', stopped: 'Đã dừng', completed: 'Hoàn tất precheck', partial: 'Hoàn tất một phần',
  failed: 'Lỗi', unknown: 'Kết quả chưa rõ', ready: 'Đủ evidence', excluded_no_email: 'Không có email hợp lệ',
  cloaking_review: 'Cần duyệt cloaking', evidence_review: 'Cần Browser Evidence', precheck_error: 'Lỗi precheck' }
export function DomainWorkerPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const client = useQueryClient()
  const key = `domaincheck:v1:u:${me.id}:domain-worker:operation`
  const [id, setId] = useState(() => sessionStorage.getItem(key) ?? '')
  const [input, setInput] = useState('')
  const accountKey = `${key}:accounts`
  const [accountSelection, setAccountSelection] = useState<string[] | null>(() => {
    try {
      const saved: unknown = JSON.parse(sessionStorage.getItem(accountKey) ?? 'null')
      return Array.isArray(saved) && saved.every((value: unknown) => typeof value === 'string') ? saved : null
    } catch { return null }
  })
  const canView = me.permissions.includes('domain_worker.view')
  const queryKey = ['domain-worker', me.id, id]
  const accounts = useQuery({ queryKey: ['domain-worker', me.id, 'accounts'], enabled: canView,
    queryFn: ({ signal }) => getWorkerAccounts(signal) })
  const selectedAccounts = (accountSelection ?? accounts.data?.map((account) => account.id) ?? [])
    .filter((accountId) => accounts.data?.some((account) => account.id === accountId))
  const chooseAccount = (accountId: string, checked: boolean) => {
    const next = checked ? [...selectedAccounts, accountId] : selectedAccounts.filter((entry) => entry !== accountId)
    setAccountSelection(next); sessionStorage.setItem(accountKey, JSON.stringify(next))
  }
  const readiness = useQuery({ queryKey: ['domain-worker', me.id, 'readiness'], enabled: canView,
    queryFn: ({ signal }) => getReadiness(signal) })
  const create = useMutation({ mutationFn: () => createPrecheck(input.split(/\r?\n/).map((v) => v.trim()).filter(Boolean), selectedAccounts),
    onSuccess: (data) => { setId(data.id); sessionStorage.setItem(key, data.id)
      client.setQueryData(['domain-worker', me.id, data.id], data) } })
  const operation = useQuery({ queryKey, enabled: canView && Boolean(id), queryFn: ({ signal }) => getPrecheck(id, signal),
    select: (data) => data.is_current_day ? data : { ...data, checks: [] },
    refetchInterval: (query) => query.state.data && active.has(query.state.data.state) ? 2000 : 30000 })
  const control = useMutation({ mutationFn: (action: 'stop' | 'resume' | 'retry') => controlPrecheck(id, action,
    operation.data?.checks.filter((item) => item.status === 'failed').map((item) => item.item_id)),
    onSuccess: (data) => { client.setQueryData(queryKey, data) } })
  const busy = create.isPending || control.isPending || Boolean(operation.data?.is_current_day && active.has(operation.data.state))
  if (!canView) return <p className="error-notice" role="alert">Bạn không có quyền xem Domain Worker.</p>
  return <section className="space-y-3">
    <header><h1 className="text-xl font-semibold">Domain Worker</h1><p className="mt-1 text-xs text-slate-500">Chọn tài khoản → precheck → cấu hình batch → gửi email. Delivery đã gửi được bỏ qua trong ngày.</p></header>
    <form className="data-panel space-y-2 p-3" onSubmit={(event) => { event.preventDefault(); if (!busy && readiness.data?.ready && selectedAccounts.length && accounts.isSuccess && me.permissions.includes('domain_worker.precheck')) create.mutate() }}>
      <fieldset disabled={busy || !me.permissions.includes('domain_worker.precheck')} className="space-y-2">
        <legend className="text-xs font-semibold">Tài khoản email dùng để lọc và gửi</legend>
        {accounts.data?.map((account) => <label className="flex items-center gap-2 text-xs" key={account.id}>
          <input type="checkbox" checked={selectedAccounts.includes(account.id)} onChange={(event) => { chooseAccount(account.id, event.target.checked) }} />
          {account.label} · {account.sender_email}</label>)}
        {accounts.isPending && <p role="status">Đang tải tài khoản…</p>}
        {accounts.isError && <p role="alert" className="error-notice">Không tải được tài khoản. <Button onClick={() => { void accounts.refetch() }}>Thử lại</Button></p>}
        {accounts.isSuccess && !accounts.data.length && <p className="text-xs">Cần thêm tài khoản đang bật tại <Link to="/settings/mail" className="underline">cấu hình mail</Link>.</p>}
      </fieldset>
      <label className="field">Danh sách URL<textarea rows={4} value={input} disabled={busy || !me.permissions.includes('domain_worker.precheck')}
        placeholder="Mỗi dòng một full URL, tối đa 100 URL" onChange={(event) => { setInput(event.target.value) }} /></label>
      <div className="flex flex-wrap items-center gap-2"><Button className="primary-button" type="submit" loading={create.isPending}
        disabled={busy || !input.trim() || !selectedAccounts.length || selectedAccounts.length > 100 || !accounts.isSuccess || !readiness.data?.ready || !me.permissions.includes('domain_worker.precheck')}>Precheck danh sách</Button>
        <span className="text-xs text-slate-500">URL trùng được gộp; giữ nguyên path/query và thứ tự nhập.</span></div>
      {readiness.isPending && <p role="status" className="text-xs">Đang kiểm tra hồ sơ tổ chức…</p>}
      {readiness.isError && <p className="error-notice" role="alert">Không tải được hồ sơ. <Button onClick={() => { void readiness.refetch() }}>Thử lại</Button></p>}
      {readiness.data && !readiness.data.ready && <p className="text-xs text-amber-700 dark:text-amber-300">Cần hoàn thiện thương hiệu, tên và email liên hệ tại <Link className="underline" to="/settings/mail">cấu hình mail</Link> trước khi precheck.</p>}
    </form>
    {(create.error || control.error) && <p role="alert" className="error-notice">{(create.error ?? control.error)?.message}</p>}
    {operation.isError && <p role="alert" className="error-notice">Không tải được lượt precheck. <Button onClick={() => { void operation.refetch() }}>Thử lại</Button></p>}
    {id && operation.isPending && <p role="status">Đang tải lượt precheck…</p>}
    <div className="flex flex-wrap gap-4 text-xs">
      {me.permissions.includes('cloaking_review.view') && <Link className="underline" to="/cloaking-review">Cloaking Review ({operation.data?.checks.filter((item) => item.disposition === 'cloaking_review').length ?? 0})</Link>}
      {me.permissions.includes('domain_evidence_review.view') && <Link className="underline" to="/domain-evidence-review">Domain Evidence Review ({operation.data?.checks.filter((item) => item.disposition === 'evidence_review').length ?? 0})</Link>}
    </div>
    {operation.data && !operation.data.is_current_day && <p className="data-panel p-3 text-sm">Đã sang ngày mới. Nhập danh sách và precheck lại; kết quả hôm qua không dùng cho lượt hôm nay.</p>}
    {operation.data?.is_current_day && <section className="data-panel overflow-hidden" aria-label="Tiến trình precheck">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 p-3 dark:border-slate-700">
        <div><h2 className="text-sm font-semibold">{states[operation.data.state] ?? operation.data.state}</h2>
          <p className="mt-1 text-xs text-slate-500">{operation.data.business_day} · {operation.data.checks.filter((item) => ['succeeded', 'failed', 'unknown'].includes(item.status)).length}/{operation.data.checks.length} URL</p></div>
        <div className="flex gap-2">
          {active.has(operation.data.state) && me.permissions.includes('domain_worker.stop') && <Button disabled={control.isPending} onClick={() => { control.mutate('stop') }}>Dừng precheck</Button>}
          {['stopped', 'partial', 'failed'].includes(operation.data.state) && operation.data.checks.some((item) => item.status === 'pending') && me.permissions.includes('domain_worker.precheck') && <Button disabled={busy} onClick={() => { control.mutate('resume') }}>Tiếp tục precheck</Button>}
          {!active.has(operation.data.state) && operation.data.checks.some((item) => item.status === 'failed') && me.permissions.includes('domain_worker.retry') && <Button disabled={busy} onClick={() => { control.mutate('retry') }}>Thử lại URL lỗi</Button>}
        </div>
      </div>
      {operation.data.state === 'queued' && <p className="px-3 pt-2 text-xs text-slate-500">Đang chờ process worker; tải lại trang không chạy kiểm tra mới.</p>}
      <p className="px-3 pt-2 text-xs text-slate-500">Tài khoản của lượt này: {operation.data.sender_account_ids.map((accountId) => accounts.data?.find((account) => account.id === accountId)?.sender_email ?? accountId).join(', ') || 'Lượt cũ chưa chọn tài khoản; cần precheck lại.'}</p>
      <p className="p-3 text-xs">Đủ điều kiện: {operation.data.checks.filter((item) => item.disposition === 'ready').length} · Cloaking: {operation.data.checks.filter((item) => item.disposition === 'cloaking_review').length} · Thiếu bằng chứng: {operation.data.checks.filter((item) => item.disposition === 'evidence_review').length} · Không email: {operation.data.checks.filter((item) => item.disposition === 'excluded_no_email').length} · Đã gửi hôm nay: {operation.data.checks.filter((item) => item.disposition === 'excluded_already_sent').length}</p>
      {!active.has(operation.data.state) && !me.permissions.includes('domain_worker.start') && <div className="overflow-x-auto"><table className="data-table"><thead><tr><th>#</th><th>URL</th><th>Email nhận</th><th>Cloaking HTTP</th><th>Phân loại</th></tr></thead>
        <tbody>{operation.data.checks.filter((item) => item.disposition === 'ready' || ['pending', 'running', 'failed', 'unknown'].includes(item.status))
          .map((item, index) => <WorkerRow key={item.id} item={item} index={index} />)}</tbody></table></div>}
      <p className="p-3 text-xs text-slate-500">Sẵn sàng: {operation.data.checks.filter((item) => item.disposition === 'ready' && item.recipients.length > 0 && item.evidence_set_id).length} URL có email và đủ bằng chứng.</p>
      {operation.data.checks.some((item) => item.disposition === 'excluded_no_email') && <details className="p-3 text-xs"><summary>URL bị loại vì không có email hợp lệ</summary>
        {operation.data.checks.filter((item) => item.disposition === 'excluded_no_email').map((item) => <p key={item.id} className="break-all">{item.requested_url}</p>)}</details>}
    </section>}
    {operation.data?.is_current_day && !active.has(operation.data.state) && operation.data.sender_account_ids.length > 0 && me.permissions.includes('domain_worker.start') && <SendBatchPanel key={operation.data.id} sourceId={operation.data.id} me={me} />}
  </section>
}
function WorkerRow({ item, index }: { item: WorkerItem; index: number }) {
  return <tr><td>{index + 1}</td><td className="max-w-80 break-all font-medium">{item.requested_url}</td>
    <td>{item.recipients.length ? item.recipients.map((row) => <span key={`${row.channel}:${row.email}`}>{row.channel}: {row.email}</span>) : '—'}</td>
    <td>{item.cloaking_verdict ?? '—'}</td>
    <td><span className={`status ${item.disposition === 'ready' ? 'status-active' : 'status-pending'}`}>{states[item.disposition] ?? item.disposition}</span>
      {item.evidence_reason === 'capture_disabled' && <span>Capture đang tắt</span>}
      {item.evidence_reason === 'capture_failed' && <span>Capture thất bại</span>}
      {item.evidence_failure_code && captureErrors[item.evidence_failure_code] && <span>{captureErrors[item.evidence_failure_code]}</span>}
      {item.status === 'unknown' && <span>Worker gián đoạn; không tự chạy lại.</span>}</td></tr>
}

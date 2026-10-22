import { ForbiddenPage } from '../../components/shared/StatusPage'
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useOutletContext, useSearchParams } from 'react-router-dom'
import { Button } from '../../components/shared/Button'
import type { Me } from '../auth/types'
import { clearWorkerCache, closePrecheck, controlPrecheck, createPrecheck, getWorkerAccounts, getWorkerWorkspace, getPrecheck, getReadiness, getSendSummary, type WorkerOperation } from './api'
import { SendBatchPanel } from './SendBatchPanel'
import { PrecheckProgress } from './WorkerProgress'

const active = new Set(['queued', 'running', 'stopping'])
const states: Record<string, string> = { queued: 'Chờ worker', pending: 'Chờ xử lý', running: 'Đang kiểm tra',
  stopping: 'Đang dừng', stopped: 'Đã dừng', completed: 'Hoàn tất precheck', partial: 'Hoàn tất một phần',
  failed: 'Lỗi', unknown: 'Kết quả chưa rõ', ready: 'Đủ evidence', excluded_no_email: 'Không có email hợp lệ',
  cloaking_review: 'Cần duyệt cloaking', evidence_review: 'Cần Browser Evidence', precheck_error: 'Lỗi precheck' }
const sendStates: Record<string, string> = { queued: 'Chờ gửi', running: 'Đang gửi', stopping: 'Đang dừng gửi',
  stopped: 'Đã dừng gửi', completed: 'Hoàn tất gửi', partial: 'Gửi một phần', failed: 'Gửi lỗi' }
export function DomainWorkerPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const client = useQueryClient()
  const key = `domaincheck:v1:u:${me.id}:domain-worker:operation`
  const [search, setSearch] = useSearchParams()
  const [historySelection, setHistorySelection] = useState<{ id: string; day: string | undefined } | null>(null)
  const [input, setInput] = useState('')
  const accountKey = `${key}:accounts`
  const [accountSelection, setAccountSelection] = useState<string[] | null>(() => {
    try {
      const saved: unknown = JSON.parse(sessionStorage.getItem(accountKey) ?? 'null')
      return Array.isArray(saved) && saved.every((value: unknown) => typeof value === 'string') ? saved : null
    } catch { return null }
  })
  const canView = me.permissions.includes('domain_worker.view')
  const workspaceKey = ['domain-worker-workspace', me.id]
  const workspace = useQuery({ queryKey: workspaceKey, enabled: canView,
    queryFn: ({ signal }) => getWorkerWorkspace(signal), refetchInterval: 2000 })
  const day = workspace.data?.business_day
  const requestedId = search.get('batch') || sessionStorage.getItem(key) || ''
  const selectedHistory = historySelection?.id === requestedId && historySelection.day === day
  const savedIsToday = !day || workspace.data?.history.some((entry) => entry.id === requestedId && entry.business_day === day)
  const id = workspace.data ? ((workspace.data.locked ? workspace.data.current_id : (selectedHistory || savedIsToday ? requestedId : null))
    || workspace.data.current_id || '') : ''
  useEffect(() => {
    if (id) sessionStorage.setItem(key, id)
    else if (workspace.isSuccess) sessionStorage.removeItem(key)
    if (workspace.isSuccess && requestedId && !selectedHistory && !savedIsToday && search.has('batch')) {
      const next = new URLSearchParams(search); next.delete('batch'); setSearch(next, { replace: true })
    }
  }, [id, key, workspace.isSuccess, requestedId, selectedHistory, savedIsToday, search, setSearch])
  const [submitted, setSubmission] = useState<{ day: string | undefined; noJob: boolean; summary: WorkerOperation['input_summary'] } | null>(null)
  const submission = submitted?.day === day ? submitted : null
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
    onSuccess: (data) => {
      setSubmission({ day, noJob: Boolean(data.no_job), summary: data.input_summary })
      if (data.id) { setHistorySelection({ id: data.id, day }); setSearch({ batch: data.id }); sessionStorage.setItem(key, data.id)
        client.setQueryData(['domain-worker', me.id, data.id], data) }
      void client.invalidateQueries({ queryKey: workspaceKey })
    }, onError: () => { void client.invalidateQueries({ queryKey: workspaceKey }) } })
  const operation = useQuery({ queryKey, enabled: canView && Boolean(id), queryFn: ({ signal }) => getPrecheck(id, signal),
    select: (data) => ({ ...data, is_current_day: data.is_current_day && (!day || data.business_day === day) }),
    refetchInterval: (query) => query.state.data && active.has(query.state.data.state) ? 2000 : 30000 })
  const control = useMutation({ mutationFn: (action: 'stop' | 'resume' | 'retry') => controlPrecheck(id, action,
    operation.data?.checks.filter((item) => item.status === 'failed').map((item) => item.item_id)),
    onSuccess: (data) => { client.setQueryData(queryKey, data); void client.invalidateQueries({ queryKey: workspaceKey }) } })
  const close = useMutation({ mutationFn: () => closePrecheck(id), onSuccess: (data) => {
    client.setQueryData(queryKey, data); void client.invalidateQueries({ queryKey: workspaceKey }) } })
  const clearCache = useMutation({ mutationFn: clearWorkerCache, onSuccess: () => {
    setSubmission(null)
    void client.invalidateQueries({ queryKey: workspaceKey })
  } })
  const processing = Boolean(workspace.data?.locked) || Boolean(operation.data?.is_current_day && active.has(operation.data.state))
  const busy = clearCache.isPending || create.isPending || control.isPending || close.isPending || processing || !workspace.isSuccess
  const filtered = submission?.noJob ? submission.summary : operation.data?.input_summary
  const sendSummary = useQuery({ queryKey: ['domain-worker-send-summary', me.id, id], enabled: false,
    queryFn: ({ signal }) => getSendSummary(id, signal) })
  const sendJobId = workspace.data?.history.find((entry) => entry.id === id)?.send_job_id ?? sendSummary.data?.send_job_id ?? (workspace.data?.current_id === id ? workspace.data.send_job_id : null)
  const sendPanel = operation.data && !active.has(operation.data.state) && ((operation.data.is_current_day && me.permissions.includes('domain_worker.start') && operation.data.checks.some((item) => item.disposition === 'ready' && item.recipients.length > 0 && item.evidence_set_id)) || workspace.data?.history.some((entry) => entry.id === id && entry.send_job_id)) && operation.data.sender_account_ids.length > 0 && <SendBatchPanel key={operation.data.id} sourceId={operation.data.id} me={me}
      readOnly={!operation.data.is_current_day} sendJobId={workspace.data?.history.find((entry) => entry.id === id)?.send_job_id ?? null} />
  if (!canView) return <ForbiddenPage message="Bạn không có quyền xem Domain Worker." />
  return <section className="space-y-3">
    <header className="rounded-xl border border-cyan-200 bg-gradient-to-r from-cyan-50 to-blue-50 p-4 dark:border-cyan-900 dark:from-cyan-950 dark:to-slate-900"><h1 className="text-xl font-semibold">Domain Worker</h1><p className="mt-1 text-xs text-slate-500">Chọn tài khoản → precheck → cấu hình batch → gửi email. Delivery đã gửi được bỏ qua trong ngày.</p></header>
    {processing && <p role="status" className="data-panel p-3 text-sm">{workspace.data?.phase === 'paused' ? 'Job đang tạm dừng. Tiếp tục job hoặc ngừng hẳn trước khi kiểm tra danh sách mới.' : 'Đang xử lý batch hiện tại. Vui lòng đợi hoàn thành trước khi kiểm tra danh sách mới.'}</p>}
    {workspace.data?.phase === 'awaiting_send' && <p className="text-xs">Precheck đã xong. Xác nhận gửi bên dưới hoặc chọn Kết thúc lượt này để nhập danh sách khác.</p>}
    {workspace.isPending && <p role="status">Đang khôi phục batch từ máy chủ…</p>}
    {workspace.isError && <p className="error-notice" role="alert">Không tải được trạng thái batch. <Button onClick={() => { void workspace.refetch() }}>Thử lại</Button></p>}
    <form className="data-panel space-y-2 border-t-4 border-t-cyan-500 p-4" onSubmit={(event) => { event.preventDefault(); if (!busy && readiness.data?.ready && selectedAccounts.length && accounts.isSuccess && me.permissions.includes('domain_worker.precheck')) create.mutate() }}>
      <fieldset disabled={busy || !me.permissions.includes('domain_worker.precheck')} className="space-y-2">
        <legend className="text-xs font-semibold">Tài khoản email dùng để lọc và gửi</legend>
        {accounts.data?.map((account) => <label className="flex items-center gap-2 text-xs" key={account.id}>
          <input type="checkbox" checked={(processing && operation.data ? operation.data.sender_account_ids : selectedAccounts).includes(account.id)} onChange={(event) => { chooseAccount(account.id, event.target.checked) }} />
          {account.label} · {account.sender_email}</label>)}
        {accounts.isPending && <p role="status">Đang tải tài khoản…</p>}
        {accounts.isError && <p role="alert" className="error-notice">Không tải được tài khoản. <Button onClick={() => { void accounts.refetch() }}>Thử lại</Button></p>}
        {accounts.isSuccess && !accounts.data.length && <p className="text-xs">Cần thêm tài khoản đang bật tại <Link to="/settings/mail" className="underline">cấu hình mail</Link>.</p>}
      </fieldset>
      <label className="field">Danh sách URL<textarea rows={4} value={input} disabled={busy || !me.permissions.includes('domain_worker.precheck')}
        placeholder="Mỗi dòng một full URL, tối đa 100 URL" onChange={(event) => { setInput(event.target.value) }} /></label>
      <div className="flex flex-wrap items-center gap-2"><Button className="primary-button" type="submit" loading={create.isPending}
        disabled={busy || !input.trim() || !selectedAccounts.length || selectedAccounts.length > 100 || !accounts.isSuccess || !readiness.data?.ready || !me.permissions.includes('domain_worker.precheck')}>Precheck danh sách</Button>
        {me.permissions.includes('domain_worker.clear_cache') && <Button type="button" disabled={busy} loading={clearCache.isPending}
          onClick={() => { clearCache.mutate() }}>Xóa cache không có email</Button>}
        <span className="text-xs text-slate-500">URL trùng được gộp; giữ nguyên path/query và thứ tự nhập. Xóa cache để kiểm tra lại domain đã bị bỏ qua vì không có email hôm nay.</span></div>
      {readiness.isPending && <p role="status" className="text-xs">Đang kiểm tra hồ sơ tổ chức…</p>}
      {readiness.isError && <p className="error-notice" role="alert">Không tải được hồ sơ. <Button onClick={() => { void readiness.refetch() }}>Thử lại</Button></p>}
      {readiness.data && !readiness.data.ready && <p className="text-xs text-amber-700 dark:text-amber-300">Cần hoàn thiện thương hiệu, tên và email liên hệ tại <Link className="underline" to="/settings/mail">cấu hình mail</Link> trước khi precheck.</p>}
    </form>
    {clearCache.isSuccess && <p role="status" className="data-panel p-3 text-sm">Đã xóa cache {clearCache.data.cleared_count} kết quả không có email trong ngày. Bạn có thể precheck lại; lịch sử và trạng thái gửi được giữ nguyên.</p>}
    {clearCache.error && <p role="alert" className="error-notice">{clearCache.error.message}</p>}
    {(create.error || control.error || close.error) && <p role="alert" className="error-notice">{(create.error ?? control.error ?? close.error)?.message}</p>}
    {submission?.noJob && <p role="status" className="data-panel p-3 text-sm">{submission.summary?.processing_count ? 'Không tạo job mới: còn mục chưa rõ kết quả gửi, cần đối soát trước khi tiếp tục.' : submission.summary?.no_email_count ? 'Không có domain mới cần precheck. Các domain đã kiểm tra hôm nay không có email hoặc đã gửi thành công. Không tạo job mới.' : 'Tất cả domain đã gửi thành công trong ngày. Không tạo job mới.'}</p>}
    {filtered && <section className="data-panel p-3 text-xs" aria-label="Kết quả lọc danh sách">
      <p>Đầu vào: {filtered.input_count} · Còn precheck: {filtered.queued_count} · Trùng: {filtered.duplicate_count} · Đã gửi thành công: {filtered.sent_count} · Không có email (đã kiểm tra): {filtered.no_email_count} · Chưa rõ/đang xử lý: {filtered.processing_count}</p>
      {filtered.excluded.length > 0 && <details><summary>Xem mục bị loại ({filtered.excluded.length})</summary>
        {filtered.excluded.map((entry, index) => <p className="break-all" key={`${String(index)}:${entry.requested_url}`}>{entry.requested_url} · {entry.reason === 'duplicate' ? 'Trùng trong danh sách' : entry.reason === 'already_sent' ? 'Đã gửi thành công' : entry.reason === 'no_email' ? 'Không có email — đã kiểm tra hôm nay' : 'Kết quả gửi chưa rõ, cần đối soát'}</p>)}</details>}
    </section>}
    {operation.isError && <p role="alert" className="error-notice">Không tải được lượt precheck. <Button onClick={() => { void operation.refetch() }}>Thử lại</Button></p>}
    {id && operation.isPending && <p role="status">Đang tải lượt precheck…</p>}
    <div className="flex flex-wrap gap-4 text-xs">
      {me.permissions.includes('cloaking_review.view') && <Link className="underline" to="/cloaking-review">Cloaking Review ({operation.data?.is_current_day ? operation.data.checks.filter((item) => item.disposition === 'cloaking_review').length : 0})</Link>}
      {me.permissions.includes('domain_evidence_review.view') && <Link className="underline" to="/domain-evidence-review">Domain Evidence Review ({operation.data?.is_current_day ? operation.data.checks.filter((item) => item.disposition === 'evidence_review').length : 0})</Link>}
    </div>
    {operation.data && !operation.data.is_current_day && <p className="data-panel p-3 text-sm">Lịch sử ngày trước: chỉ xem kết quả, không dùng để gửi hôm nay.</p>}
    {operation.data && <section className="data-panel overflow-hidden" aria-label="Tiến trình precheck">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 p-3 dark:border-slate-700">
        <div><h2 className="text-xl font-semibold">{sendJobId ? 'Theo dõi lượt xử lý' : states[operation.data.state] ?? operation.data.state}</h2>
          <p className="mt-1 text-xs text-slate-500">{operation.data.business_day} · {operation.data.checks.filter((item) => ['succeeded', 'failed', 'unknown'].includes(item.status)).length}/{operation.data.checks.length} URL</p></div>
        <div className="flex gap-2">
          {active.has(operation.data.state) && me.permissions.includes('domain_worker.stop') && <Button disabled={control.isPending} onClick={() => { control.mutate('stop') }}>Dừng precheck</Button>}
          {operation.data.is_current_day && ['stopped', 'partial', 'failed'].includes(operation.data.state) && operation.data.checks.some((item) => item.status === 'pending') && me.permissions.includes('domain_worker.precheck') && <Button disabled={control.isPending || workspace.data?.phase === 'sending'} onClick={() => { control.mutate('resume') }}>Tiếp tục precheck</Button>}
          {operation.data.is_current_day && !active.has(operation.data.state) && operation.data.checks.some((item) => item.status === 'failed') && me.permissions.includes('domain_worker.retry') && <Button disabled={control.isPending || workspace.data?.phase === 'sending'} onClick={() => { control.mutate('retry') }}>Thử lại URL lỗi</Button>}
          {workspace.data?.phase === 'awaiting_send' && me.permissions.includes('domain_worker.stop') && <Button disabled={close.isPending} onClick={() => { close.mutate() }}>Kết thúc lượt này</Button>}
        </div>
      </div>
      {operation.data.state === 'queued' && <p className="px-3 pt-2 text-xs text-slate-500">Đang chờ process worker; tải lại trang không chạy kiểm tra mới.</p>}
      <p className="mx-4 mt-4 break-all rounded-lg bg-slate-50 p-3 text-sm font-medium dark:bg-slate-900">Tài khoản của lượt này: {operation.data.sender_account_ids.map((accountId) => accounts.data?.find((account) => account.id === accountId)?.sender_email ?? accountId).join(', ') || 'Lượt cũ chưa chọn tài khoản; cần precheck lại.'}</p>
      <PrecheckProgress operation={operation.data} ownerId={me.id} accounts={accounts.data ?? []}
        sendJobId={sendJobId} controls={sendPanel} />

    </section>}

    {workspace.data && workspace.data.history.length > 0 && <details className="data-panel p-3 text-xs"><summary>Lịch sử batch (50 lượt gần nhất)</summary>
      <div className="space-y-2 pt-2">{workspace.data.history.map((entry) => <div key={entry.id} className="flex flex-wrap items-center gap-2">
        <span>{new Date(entry.created_at).toLocaleString('vi-VN')} · {entry.send_state ? sendStates[entry.send_state] ?? entry.send_state : states[entry.state] ?? entry.state} · {entry.id.slice(0, 8)}</span>
        <Button disabled={processing || create.isPending || id === entry.id} onClick={() => { setSubmission(null); setHistorySelection({ id: entry.id, day }); setSearch({ batch: entry.id }); sessionStorage.setItem(key, entry.id) }}>Xem kết quả</Button>
      </div>)}</div></details>}
  </section>
}

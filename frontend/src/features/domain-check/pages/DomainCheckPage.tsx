import { InvestigationDetails } from '../components/InvestigationDetails'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'

import { Button } from '../../../components/shared/Button'
import { CopyButton } from '../../../components/shared/CopyButton'
import type { Me } from '../../auth/types'
import { createDomainCheck, getDomainCheck, getReportGuidance,
  type DomainCheckOperation, type ReportGuide } from '../api/domainCheckApi'

const activeStates = new Set(['pending', 'queued', 'running', 'stopping'])
const guideGroups = [
  { channel: 'browser', title: 'Cảnh báo trình duyệt', hint: 'Google, Microsoft, Chống Lừa Đảo, Cốc Cốc, Netcraft' },
  { channel: 'cdn', title: 'CDN', hint: 'Form theo hạ tầng đã phát hiện' },
  { channel: 'registrar', title: 'Registrar', hint: 'Web form và mẫu email nếu có địa chỉ nhận' },
  { channel: 'registry', title: 'Registry', hint: 'Kênh leo thang khi phù hợp' },
] as const
function operationKey(userId: string) { return `domaincheck:v1:u:${userId}:domain-check:active-operation` }

export function DomainCheckPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const queryClient = useQueryClient()
  const canView = me.permissions.includes('check_domain.view')
  const [target, setTarget] = useState('')
  const [submitVt, setSubmitVt] = useState(false)
  const [operationId, setOperationId] = useState(() => sessionStorage.getItem(operationKey(me.id)) ?? '')
  const create = useMutation({ mutationFn: () => createDomainCheck(target.trim(), submitVt), onSuccess: (operation) => {
    sessionStorage.setItem(operationKey(me.id), operation.id)
    setOperationId(operation.id)
    queryClient.setQueryData(['domain-check', me.id, operation.id], operation)
  } })
  const operation = useQuery({ queryKey: ['domain-check', me.id, operationId], enabled: canView && Boolean(operationId),
    queryFn: ({ signal }) => getDomainCheck(operationId, signal),
    refetchInterval: (query) => query.state.data && activeStates.has(query.state.data.state) ? 2000 : false })
  const current = operation.data ?? (create.data?.id === operationId ? create.data : undefined)
  const busy = create.isPending || Boolean(current && activeStates.has(current.state))
  if (!canView) return <div className="error-notice" role="alert">Bạn không có quyền xem Check Domain.</div>
  return <section className="space-y-3">
    <header><h1 className="text-xl font-semibold">Check Domain</h1><p className="mt-1 text-xs text-slate-500">Kiểm tra một URL, xem thông số và mẫu báo cáo theo từng kênh.</p></header>
    <form className="data-panel space-y-2 p-3" onSubmit={(event) => { event.preventDefault(); if (target.trim() && !busy) create.mutate() }}>
      <div className="flex flex-wrap items-end gap-2"><label className="field flex-1 basis-64" htmlFor="domain-target">Domain hoặc URL cần kiểm tra
        <input id="domain-target" className="w-full" type="text" value={target}
          onChange={(event) => { setTarget(event.target.value) }} placeholder="vd: example.com/login"
          autoComplete="off" required disabled={busy || !me.permissions.includes('check_domain.check')} />
      </label>
      <Button className="primary-button" type="submit" loading={create.isPending}
        disabled={busy || !target.trim() || !me.permissions.includes('check_domain.check')}>Kiểm tra</Button></div>
      {me.permissions.includes('check_domain.submit_vt') && <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={submitVt} onChange={(event) => { setSubmitVt(event.target.checked) }} disabled={busy} />
        Submit lên VirusTotal nếu chưa có dữ liệu
      </label>}
      {!me.permissions.includes('check_domain.check') && <p className="text-sm text-slate-500">Bạn không có quyền thực hiện kiểm tra.</p>}
    </form>
    {create.isError && <p className="error-notice" role="alert">{create.error.message}</p>}
    {operation.isError && <div className="error-notice" role="alert">Không tải được kết quả kiểm tra. <Button onClick={() => { void operation.refetch() }}>Thử lại</Button></div>}
    {operationId && operation.isPending && <p role="status">Đang tải lần kiểm tra gần nhất…</p>}
    {current && <DomainCheckResult operation={current} me={me} />}
  </section>
}

function DomainCheckResult({ operation, me }: { operation: DomainCheckOperation; me: Me }) {
  const check = operation.check
  const complete = check.status === 'succeeded'
  const guidance = useQuery({ queryKey: ['domain-check', me.id, check.id, 'report-guidance'], enabled: complete,
    queryFn: ({ signal }) => getReportGuidance(check.id, signal) })
  return <div className="space-y-3">
    <section className="data-panel p-3" aria-label="Kết quả Check Domain">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="break-all text-sm font-semibold">{check.requested_url}</h2>
        <p className="mt-0.5 text-xs text-slate-500">{check.domain}</p></div><span className={`status ${complete ? 'status-active' : activeStates.has(check.status) ? 'status-pending' : 'status-disabled'}`}>{statusLabel(check.status)}</span></div>
      {activeStates.has(operation.state) && <p className="mt-4 text-sm" role="status">Đang xử lý lần kiểm tra này. Kết quả sẽ tự cập nhật.</p>}
      {check.status === 'failed' && <p className="mt-4 error-notice" role="alert">Kiểm tra thất bại. Bạn có thể thực hiện lượt kiểm tra mới.</p>}
      {check.status === 'unknown' && <p className="mt-4 error-notice" role="alert">Không xác định được kết quả sau khi worker gián đoạn. Không tự kiểm tra lại URL.</p>}
      {complete && <><div className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-4">
        <Metric label="VirusTotal malicious" value={field(check.result.virustotal, 'malicious')} />
        <Metric label="VirusTotal suspicious" value={field(check.result.virustotal, 'suspicious')} />
        <Metric label="Cloudflare" value={check.result.cloudflare === true ? 'Có' : check.result.cloudflare === false ? 'Không' : 'Chưa có dữ liệu'} />
        <Metric label="Google Safe Browsing" value={field(check.result.safebrowsing, 'flagged') === 'true' ? 'Có cảnh báo' : field(check.result.safebrowsing, 'flagged') === 'false' ? 'Không có cảnh báo' : 'Chưa có dữ liệu'} />
      </div><InvestigationDetails result={check.result} /></>}
    </section>
    {complete && <section className="space-y-3" aria-label="Mẫu báo cáo">
      <div><h2 className="text-base font-semibold">Mẫu báo cáo và form</h2>
        <p className="mt-1 text-xs text-slate-500">Nội dung để tham khảo và copy; kiểm tra lại trước khi tự gửi. Ảnh chụp là tùy chọn.</p></div>
      {guidance.isPending && <p role="status">Đang chuẩn bị mẫu báo cáo…</p>}
      {guidance.isError && <div className="error-notice" role="alert">Không tải được mẫu báo cáo. <Button onClick={() => { void guidance.refetch() }}>Thử lại</Button></div>}
      {guidance.data && <div className="grid gap-2 lg:grid-cols-3">{guideGroups.map((group) => <GuideGroup key={group.channel} title={group.title} hint={group.hint}
        browser={group.channel === 'browser'} guides={guidance.data.filter((guide) => guide.channel === group.channel)} />)}</div>}
    </section>}
  </div>
}

function GuideGroup({ title, hint, guides, browser }: { title: string; hint: string; guides: ReportGuide[]; browser: boolean }) {
  return <section className={`data-panel min-w-0 p-3 ${browser ? 'lg:col-span-3' : ''}`} aria-label={title}>
    <div className="mb-2 flex flex-wrap items-baseline gap-2"><h3 className="text-sm font-semibold">{title}</h3>
      <span className="text-xs text-slate-500">{hint}</span><span className="ml-auto text-xs text-slate-500">{guides.length}</span></div>
    {guides.length ? <div className={`grid gap-2 ${browser ? 'sm:grid-cols-2 xl:grid-cols-5' : ''}`}>{guides.map((guide) =>
      <GuideCard key={`${guide.channel}:${guide.label}`} guide={guide} />)}</div>
      : <p className="rounded-lg bg-slate-50 p-3 text-xs text-slate-500 dark:bg-slate-800">Chưa có kênh phù hợp từ kết quả kiểm tra.</p>}
  </section>
}

function GuideCard({ guide }: { guide: ReportGuide }) {
  return <article className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-xs font-semibold">{guide.label}</h4>
      {guide.form_url && <a className="text-xs font-medium text-indigo-600 underline dark:text-indigo-300" href={guide.form_url} target="_blank" rel="noopener noreferrer">Mở form</a>}</div>
    {guide.note && <p className="mt-1 break-all text-xs text-slate-500">{guide.note}</p>}
    {guide.draft && <details className="mt-2"><summary className="cursor-pointer text-xs font-medium text-indigo-600 dark:text-indigo-300">Xem nội dung mẫu</summary>
      <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-3 text-[11px] leading-4 dark:bg-slate-800">{guide.draft}</pre>
      <div className="mt-2"><CopyButton value={guide.draft} label="Copy nội dung" /></div></details>}
  </article>
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 font-semibold">{value}</p></div> }
function field(value: unknown, key: string) { return value && typeof value === 'object' && key in value ? String((value as Record<string, unknown>)[key]) : 'N/A' }
function statusLabel(status: string) { return ({ pending: 'Đang chờ', queued: 'Đang chờ', running: 'Đang kiểm tra', succeeded: 'Đã kiểm tra', completed: 'Hoàn tất', failed: 'Thất bại', unknown: 'Chưa rõ kết quả' } as Record<string, string>)[status] ?? status }

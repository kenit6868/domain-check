import { AppSelect, AppSelectItem } from '../../../components/shared/AppSelect'
import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowUpRight, CloudCog, FileSearch, Images, Layers3, Link2, Mail, RefreshCw, Reply, ScanEye, ScrollText, SearchCheck, UserCog, Users } from 'lucide-react'
import { useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import type { Me } from '../../auth/types'
import { StatusBadge } from '../../../components/shared/StatusBadge'
import { Button } from '../../../components/shared/Button'
import { QueryError } from '../../../components/shared/QueryError'
import { getDashboard } from '../api/dashboardApi'

const tools = [
  { key: 'check_domain', permission: 'check_domain.view', to: '/domain-check', label: 'Check Domain', icon: SearchCheck },
  { key: 'quick_report', permission: 'quick_report.view', to: '/quick-report', label: 'Quick Report', icon: FileSearch },
  { key: 'domain_worker', permission: 'domain_worker.view', to: '/domain-worker', label: 'Domain Worker', icon: Layers3 },
  { key: 'cloaking_review', permission: 'cloaking_review.view', to: '/cloaking-review', label: 'Cloaking Review', icon: ScanEye },
  { key: 'domain_evidence_review', permission: 'domain_evidence_review.view', to: '/domain-evidence-review', label: 'Evidence Review', icon: Images },
  { key: 'cloudflare_worker', permission: 'cloudflare_worker.view', to: '/cloudflare-worker', label: 'Cloudflare Worker', icon: CloudCog },
  { key: 'link_status', permission: 'link_status.view', to: '/link-status', label: 'Check Link Status', icon: Link2 },
  { key: 'provider_replies', permission: 'provider_replies.view', to: '/provider-replies', label: 'Phản hồi NCC', icon: Reply },
  { key: 'mail_accounts', permission: 'mail_accounts.view_own', to: '/settings/mail', label: 'Cấu hình mail', icon: Mail },
  { key: 'audit_logs', permission: 'audit_logs.view', to: '/audit-log', label: 'Audit Log', icon: ScrollText },
  { key: 'users', permission: 'users.view', to: '/admin/users', label: 'Người dùng', icon: Users },
  { key: 'roles', permission: 'roles.view', to: '/admin/roles', label: 'Role & quyền', icon: UserCog },
]
const labels: Record<string, string> = { checked_urls: 'URL đã kiểm tra', sent_emails: 'Email gửi thành công', cloudflare_submitted: 'Cloudflare tiếp nhận', failed_deliveries: 'Delivery lỗi', unknown_deliveries: 'Delivery chưa rõ kết quả' }
const trendKeys = ['checked_urls', 'sent_emails', 'cloudflare_submitted'] as const
const colors = ['bg-sky-500', 'bg-emerald-500', 'bg-violet-500']
const label = (key: string) => tools.find((tool) => tool.key === key)?.label ?? key
const number = (value: number) => value.toLocaleString('vi-VN')

export function DashboardPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const canView = me.permissions.includes('dashboard.view')
  const canAll = me.permissions.includes('dashboard.view_all')
  const [selectedScope, setScope] = useState<'own' | 'all'>(() => canAll ? 'all' : 'own')
  const scope = canAll ? selectedScope : 'own'
  const [days, setDays] = useState(7)
  const [selectedFeature, setFeature] = useState('')
  const [selectedUser, setUser] = useState('')
  const [offset, setOffset] = useState(0)
  const visibleTools = tools.filter((tool) => me.permissions.includes(tool.permission))
  const feature = scope === 'all' || visibleTools.some((tool) => tool.key === selectedFeature) ? selectedFeature : ''
  const user = scope === 'all' ? selectedUser : ''
  const report = useQuery({
    queryKey: ['dashboard', me.id, [...me.permissions].sort().join('|'), scope, days, feature, user, offset],
    queryFn: ({ signal }) => getDashboard(scope, days, feature, user, offset, signal),
    enabled: canView, staleTime: 30_000, refetchInterval: 30_000,
  })
  const data = canView ? report.data : undefined
  const series = trendKeys.filter((key) => data && key in data.metrics)
  const maximum = Math.max(1, ...(data?.trend.flatMap((point) => series.map((key) => point[key] ?? 0)) ?? []))
  const formatTime = (value: string) => new Date(value).toLocaleString('vi-VN', { timeZone: data?.timezone ?? me.timezone })
  return <section className="space-y-4">
    <div className="page-heading flex flex-wrap items-center justify-between gap-3"><div><p className="eyebrow">Dashboard</p><h1 className="page-title">Chào {me.display_name}</h1><p className="page-description">{scope === 'all' && canView ? 'Tổng quan vận hành toàn hệ thống' : 'Công việc và kết quả của bạn'} · {me.timezone}</p></div><StatusBadge value="active" /></div>
    {canView && <>
      <div className="data-panel flex flex-wrap items-end gap-3 p-3">
        {canAll && <label className="field">Phạm vi<AppSelect value={scope} onValueChange={(event) => { setScope(event as 'own' | 'all'); setUser(''); setOffset(0) }}><AppSelectItem value="all">Toàn hệ thống</AppSelectItem><AppSelectItem value="own">Cá nhân</AppSelectItem></AppSelect></label>}
        <label className="field">Khoảng thời gian<AppSelect value={days} onValueChange={(event) => { setDays(Number(event)); setOffset(0) }}><AppSelectItem value={1}>Hôm nay</AppSelectItem><AppSelectItem value={7}>7 ngày</AppSelectItem><AppSelectItem value={30}>30 ngày</AppSelectItem></AppSelect></label>
        <label className="field">Workflow<AppSelect value={feature} onValueChange={(event) => { setFeature(event); setOffset(0) }}><AppSelectItem value="">Tất cả được phép xem</AppSelectItem>{tools.filter((tool) => ['check_domain', 'quick_report', 'domain_worker', 'cloaking_review', 'domain_evidence_review', 'cloudflare_worker', 'provider_replies'].includes(tool.key) && (scope === 'all' || me.permissions.includes(tool.permission))).map((tool) => <AppSelectItem key={tool.key} value={tool.key}>{tool.label}</AppSelectItem>)}</AppSelect></label>
        {scope === 'all' && user && <Button className="secondary-button" onClick={() => { setUser(''); setOffset(0) }}>Bỏ lọc người dùng</Button>}
        <Button className="secondary-button inline-flex items-center gap-2" disabled={report.isFetching} onClick={() => { void report.refetch() }}><RefreshCw size={14} />Làm mới</Button>
        {data && <span className="text-xs text-slate-500" role="status">Cập nhật {formatTime(data.updated_at)}</span>}
      </div>
      {report.isPending && <div className="data-panel p-4" role="status">Đang tổng hợp dữ liệu…</div>}
      {report.isError && <QueryError message={report.error.message} onRetry={() => { void report.refetch() }} />}
      {data && <>
        <div className="dashboard-metrics-grid">{Object.entries(data.metrics).map(([key, value]) => <article className="data-panel p-4" key={key}><p className="text-xs text-slate-500">{labels[key] ?? key}</p><p className="mt-2 text-3xl font-semibold tracking-tight">{number(value)}</p><p className="mt-2 text-[11px] text-slate-500">{key.endsWith('_deliveries') ? 'Tồn đọng hiện tại · mọi ngày' : `${data.start} — ${data.end}`}</p></article>)}{scope === 'all' && <article className="data-panel p-4"><p className="text-xs text-slate-500">Tài khoản hệ thống</p><p className="mt-2 text-3xl font-semibold">{number(data.user_total ?? 0)}</p><p className="mt-2 text-xs text-slate-500">{number(data.pending_users ?? 0)} chờ kích hoạt</p></article>}</div>
        {Object.keys(data.metrics).length === 0 && <p className="data-panel p-4 text-sm text-slate-500">Chưa có nguồn thống kê được cấp quyền. Bạn vẫn có thể mở các công cụ bên dưới.</p>}
        {series.length > 0 && <article className="data-panel p-4"><h2 className="font-semibold">Xu hướng hoạt động</h2><p className="mt-1 text-xs text-slate-500">URL duy nhất mỗi ngày; email tính theo delivery thành công, không theo lần retry.</p><div className="mt-3 flex flex-wrap gap-4 text-xs">{series.map((key) => <span className="flex items-center gap-2" key={key}><i className={`size-2 rounded-full ${colors[trendKeys.indexOf(key)] ?? ''}`} />{labels[key]}</span>)}</div><div className="dashboard-chart mt-4 flex items-end gap-1 border-b border-slate-200 dark:border-slate-700" role="img" aria-label="Biểu đồ hoạt động theo ngày">{data.trend.map((point) => <div key={point.date} className="flex h-full min-w-0 flex-1 items-end justify-center gap-px">{series.map((key) => <div key={key} className={`w-full max-w-6 rounded-t-sm ${colors[trendKeys.indexOf(key)] ?? ''}`} style={{ height: `${String(Math.max(point[key] ? 2 : 0, (point[key] ?? 0) / maximum * 100))}%` }} title={`${point.date} · ${labels[key] ?? key}: ${String(point[key] ?? 0)}`} />)}</div>)}</div><div className="mt-2 flex justify-between text-[10px] text-slate-500"><span>{data.start}</span><span>{data.end}</span></div><details className="mt-3 text-xs"><summary className="cursor-pointer text-slate-500">Xem số liệu theo ngày</summary><div className="mt-2 overflow-x-auto"><table className="data-table w-full"><thead><tr><th>Ngày</th>{series.map((key) => <th key={key}>{labels[key]}</th>)}</tr></thead><tbody>{data.trend.map((point) => <tr key={point.date}><td>{point.date}</td>{series.map((key) => <td key={key}>{point[key] ?? 0}</td>)}</tr>)}</tbody></table></div></details></article>}
        <div className="grid gap-4 xl:grid-cols-2">
          {data.pending.length > 0 && <article className="data-panel p-4"><h2 className="font-semibold">Cần xử lý hôm nay</h2><p className="mt-1 text-xs text-slate-500">Ngày {data.end} · trạng thái đã lưu, không tự chạy đối soát.</p><div className="mt-3 space-y-2">{data.pending.map((item) => <div key={item.feature} className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700"><span className="text-sm">{scope === 'own' ? <Link className="hover:underline" to={item.href}>{label(item.feature)}</Link> : label(item.feature)}</span><span className="role-chip">{number(item.count)}</span></div>)}{data.pending.length === 0 && <p className="text-sm text-slate-500">Không có hàng đợi được phép xem.</p>}</div>{data.features.includes('provider_replies') && <p className="mt-3 text-xs text-slate-500">Phản hồi NCC: {data.last_mail_sync ? `đồng bộ gần nhất ${formatTime(data.last_mail_sync)}` : 'chưa có lần đồng bộ được ghi nhận'}. Dashboard không tự đọc mailbox.</p>}</article>}
          {data.features.some((item) => ['domain_worker', 'cloudflare_worker'].includes(item)) && <article className="data-panel p-4"><h2 className="font-semibold">Tiến trình worker</h2><p className="mt-1 text-xs text-slate-500">Batch đang chạy hoặc tạm dừng · tối đa 20 mỗi workflow</p><div className="mt-3 space-y-3">{data.workers.map((worker) => <div key={worker.id} className="rounded-lg border border-slate-200 p-3 dark:border-slate-700"><div className="flex flex-wrap justify-between gap-2 text-xs"><span className="font-medium">{label(worker.feature)} · {worker.owner_name}</span><StatusBadge value={worker.stale ? 'stale' : worker.state} /></div><progress className="mt-2 h-1.5 w-full accent-sky-500" max={Math.max(1, worker.total)} value={worker.completed} aria-label={`${label(worker.feature)} ${String(worker.completed)}/${String(worker.total)}`} /><div className="mt-1 flex justify-between text-xs text-slate-500"><span>{worker.completed}/{worker.total}</span>{worker.href && <Link to={worker.href} className="text-sky-600 hover:underline">Mở công việc</Link>}</div></div>)}{data.workers.length === 0 && <p className="text-sm text-slate-500">Không có batch đang hoạt động.</p>}</div></article>}
        </div>
        {scope === 'all' && <article className="data-panel p-4"><div className="flex flex-wrap justify-between gap-2"><h2 className="font-semibold">Hoạt động theo người dùng</h2><span className="text-xs text-slate-500">Lỗi/UNKNOWN tính tồn hiện tại; không mở dữ liệu riêng của người khác.</span></div><div className="mt-3 overflow-x-auto"><table className="data-table w-full"><thead><tr><th>Người dùng</th><th>Trạng thái</th>{'checked_urls' in data.metrics && <th>URL</th>}{'sent_emails' in data.metrics && <><th>Email đã gửi</th><th>Lỗi / UNKNOWN</th></>}<th>Lọc thống kê</th></tr></thead><tbody>{data.users.map((person) => <tr key={person.id}><td>{person.name}</td><td><StatusBadge value={person.status} /></td>{'checked_urls' in data.metrics && <td>{person.checked_urls ?? 0}</td>}{'sent_emails' in data.metrics && <><td>{person.sent_emails ?? 0}</td><td>{person.attention ?? 0}</td></>}<td><Button className="secondary-button" onClick={() => { setUser(person.id); setOffset(0) }}>Xem thống kê</Button></td></tr>)}</tbody></table></div><div className="mt-3 flex justify-end gap-2"><Button className="secondary-button" disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - 50)) }}>Trước</Button><Button className="secondary-button" disabled={Boolean(user) || data.users.length < 50} onClick={() => { setOffset(offset + 50) }}>Tiếp</Button></div></article>}
        {data.events.length > 0 && <article className="data-panel p-4"><div className="flex justify-between"><h2 className="font-semibold">Hoạt động gần đây</h2><Link className="text-xs text-sky-600 hover:underline" to="/audit-log">Mở Audit Log</Link></div><ul className="mt-3 divide-y divide-slate-200 dark:divide-slate-700">{data.events.map((event) => <li key={event.id} className="flex flex-wrap justify-between gap-2 py-2 text-xs"><span className="inline-flex items-center gap-2"><Activity size={13} />{event.action} <StatusBadge value={event.outcome} /></span><time dateTime={event.at} className="text-slate-500">{formatTime(event.at)}</time></li>)}</ul></article>}
        <p className="text-xs text-slate-500">Số liệu từ dữ liệu web đã lưu. URL duy nhất trong kỳ có thể khác tổng URL từng ngày. Gửi thành công hoặc Cloudflare tiếp nhận không đồng nghĩa đã takedown. Check Link Status không lưu lịch sử nên không được tính.</p>
      </>}
    </>}
    {!canView && <p className="data-panel p-4 text-sm text-slate-500">Bạn chưa được cấp quyền xem thống kê Dashboard. Các công cụ được phép sử dụng vẫn hiển thị bên dưới.</p>}
    <div><h2 className="mb-3 font-semibold">Truy cập nhanh</h2><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{visibleTools.map((tool) => <Link key={tool.to} to={tool.to} className="data-panel group flex items-center justify-between gap-3 p-3 transition-colors hover:border-sky-400"><span className="flex items-center gap-3"><span className="workspace-icon"><tool.icon size={18} /></span><span className="text-sm font-medium">{tool.label}</span></span><ArrowUpRight size={15} className="shrink-0 text-slate-400 group-hover:text-sky-500" /></Link>)}</div>{visibleTools.length === 0 && <p className="mt-3 text-sm text-slate-500">Chưa có chức năng được cấp quyền. Vui lòng liên hệ quản trị viên.</p>}</div>
  </section>
}

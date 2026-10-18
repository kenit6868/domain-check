import { StatusBadge } from '../../components/shared/StatusBadge'
import { ForbiddenPage } from '../../components/shared/StatusPage'
import { AppSelect, AppSelectItem } from '../../components/shared/AppSelect'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { z } from 'zod'
import { Button } from '../../components/shared/Button'
import { request } from '../../lib/http/client'
import type { Me } from '../auth/types'

const eventSchema = z.object({ id: z.string(), occurred_at: z.string(),
  actor_user_id: z.string().nullable(), owner_user_id: z.string().nullable(), actor_name: z.string().nullable(),
  owner_name: z.string().nullable(), actor_type: z.string(), action: z.string(), resource_type: z.string(),
  resource_id: z.string().nullable(), outcome: z.string(), request_id: z.string(), metadata: z.record(z.string(), z.unknown()) })
const pageSchema = z.object({ items: z.array(eventSchema), next_cursor: z.string().nullable() })
const tabs = [{ id: 'auth', label: 'Đăng nhập & phiên' }, { id: 'activity', label: 'Hoạt động' }, { id: 'admin', label: 'Quản trị & cấu hình' }]
const outcomes: Record<string, string> = { success: 'Thành công', accepted: 'Đã tiếp nhận yêu cầu',
  denied: 'Bị từ chối', failed: 'Thất bại', sent: 'SMTP đã chấp nhận', unknown: 'Chưa rõ kết quả',
  pending: 'Chờ xử lý', interrupted: 'Bị gián đoạn', completed: 'Hoàn tất', partial: 'Hoàn tất một phần', stopped: 'Đã dừng' }
const actions: Record<string, string> = { 'auth.login_success': 'Đăng nhập thành công',
  'auth.login_failed': 'Đăng nhập thất bại', 'auth.logout': 'Đăng xuất', 'auth.session_expired': 'Phiên hết hạn',
  'auth.session_revoked': 'Phiên bị thu hồi', 'security.access_denied': 'Truy cập bị từ chối',
  'delivery.intent': 'Ghi nhận ý định gửi email', 'delivery.outcome': 'Kết quả gửi email',
  'link_status.started': 'Bắt đầu kiểm tra link', 'link_status.completed': 'Hoàn tất kiểm tra link',
  'link_status.interrupted': 'Kiểm tra link bị gián đoạn' }
const features: Record<string, string> = { quick_report: 'Quick Report', check_domain: 'Check Domain',
  domain_worker: 'Domain Worker', cloaking_review: 'Cloaking Review', domain_evidence_review: 'Domain Evidence Review',
  cloudflare_worker: 'Cloudflare Worker', provider_replies: 'Phản hồi NCC', delivery: 'Gửi email',
  link_status: 'Check Link Status', users: 'Người dùng', roles: 'Role & quyền',
  mail_accounts: 'Tài khoản mail', mail_profile: 'Hồ sơ mail', auth: 'Đăng nhập', security: 'Bảo mật',
  jobs: 'Worker job', previews: 'Preview', cache: 'Cache' }
function dateValue(offset: number, timezone: string) { const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date()); const value = (type: string) => parts.find((part) => part.type === type)?.value ?? ''; const day = new Date(`${value('year')}-${value('month')}-${value('day')}T12:00:00Z`); day.setUTCDate(day.getUTCDate() + offset); return day.toISOString().slice(0, 10) }

export function AuditLogPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const canView = me.permissions.includes('audit_logs.view')
  const canAll = me.permissions.includes('audit_logs.view_all')
  const [category, setCategory] = useState('auth')
  const [scope, setScope] = useState('own')
  const [draft, setDraft] = useState(() => ({ start: dateValue(-6, me.timezone), end: dateValue(0, me.timezone), user: '', feature: '', action: '', outcome: '', request_id: '' }))
  const [filters, setFilters] = useState(draft)
  const [cursors, setCursors] = useState<(string | null)[]>([null])
  const [selected, setSelected] = useState<string | null>(null)
  const effectiveScope = canAll ? scope : 'own'
  const query = useQuery({ queryKey: ['audit-log', me.id, category, effectiveScope, filters, cursors.at(-1)], enabled: canView,
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams({ category, scope: effectiveScope, limit: '50' })
      Object.entries(filters).forEach(([key, value]) => { if (value && (key !== 'user' || effectiveScope === 'all')) params.set(key, value) })
      const cursor = cursors.at(-1); if (cursor) params.set('cursor', cursor)
        return pageSchema.parse(await request({ url: `/audit-logs?${params}`, signal }))
    } })
  const detail = useQuery({ queryKey: ['audit-log-detail', me.id, effectiveScope, selected], enabled: canView && Boolean(selected),
    queryFn: async ({ signal }) => eventSchema.parse(await request({ url: `/audit-logs/${selected ?? ''}?scope=${effectiveScope}`, signal })) })
  function resetPage() { setCursors([null]); setSelected(null) }
  if (!canView) return <ForbiddenPage message="Bạn không có quyền xem Audit Log." />
  const invalid = !draft.start || !draft.end || draft.start > draft.end || (Date.parse(draft.end) - Date.parse(draft.start)) / 86400000 > 366
  return <section className="space-y-3">
    <header><h1 className="text-xl font-semibold">Audit Log</h1><p className="mt-1 text-xs text-slate-500">Tra cứu lịch sử thao tác và kết quả. Thời gian hiển thị theo {me.timezone}; khoảng ngày lọc theo múi giờ tài khoản.</p></header>
    <div role="tablist" aria-label="Nhóm nhật ký" className="flex flex-wrap gap-2">{tabs.map((tab) => <button key={tab.id} role="tab" aria-selected={category === tab.id} className={`rounded-lg border px-3 py-2 text-sm ${category === tab.id ? 'border-blue-500 bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-200' : 'border-slate-300 dark:border-slate-700'}`} onClick={() => { setCategory(tab.id); resetPage() }}>{tab.label}</button>)}</div>
    <form className="data-panel flex flex-wrap items-end gap-3 p-3" onSubmit={(event) => { event.preventDefault(); if (!invalid) { setFilters(draft); resetPage() } }}>
      {canAll && <label className="field text-xs">Phạm vi<AppSelect value={effectiveScope} onValueChange={(event) => { setScope(event); resetPage() }}><AppSelectItem value="own">Cá nhân</AppSelectItem><AppSelectItem value="all">Toàn hệ thống</AppSelectItem></AppSelect></label>}
      <label className="field text-xs">Từ ngày<input type="date" value={draft.start} onChange={(e) => { setDraft({ ...draft, start: e.target.value }) }} /></label>
      <label className="field text-xs">Đến ngày<input type="date" value={draft.end} onChange={(e) => { setDraft({ ...draft, end: e.target.value }) }} /></label>
      {effectiveScope === 'all' && <label className="field text-xs">Người dùng (tên hoặc ID)<input maxLength={100} value={draft.user} onChange={(e) => { setDraft({ ...draft, user: e.target.value }) }} /></label>}
      <label className="field text-xs">Chức năng<AppSelect value={draft.feature} onValueChange={(e) => { setDraft({ ...draft, feature: e }) }}><AppSelectItem value="">Tất cả</AppSelectItem>{Object.entries(features).map(([code, label]) => <AppSelectItem key={code} value={code}>{label}</AppSelectItem>)}</AppSelect></label>
      <label className="field text-xs">Kết quả<AppSelect value={draft.outcome} onValueChange={(e) => { setDraft({ ...draft, outcome: e }) }}><AppSelectItem value="">Tất cả</AppSelectItem>{Object.entries(outcomes).map(([code, label]) => <AppSelectItem key={code} value={code}>{label}</AppSelectItem>)}</AppSelect></label>
      <label className="field text-xs">Mã hành động<input maxLength={160} value={draft.action} onChange={(e) => { setDraft({ ...draft, action: e.target.value }) }} placeholder="auth.login_failed" /></label>
      <label className="field text-xs">Request ID<input maxLength={128} value={draft.request_id} onChange={(e) => { setDraft({ ...draft, request_id: e.target.value }) }} /></label>
      <Button type="submit" disabled={invalid || query.isFetching}>Áp dụng bộ lọc</Button><Button type="button" disabled={query.isFetching} onClick={() => { void query.refetch() }}>Tải lại</Button>
      {invalid && <p role="alert" className="text-xs text-red-600">Chọn khoảng ngày hợp lệ, tối đa 367 ngày.</p>}
    </form>
    {query.isError && <p role="alert" className="error-notice">Không tải được nhật ký. Kiểm tra quyền và kết nối rồi tải lại.</p>}
    {query.isPending ? <p role="status">Đang tải nhật ký…</p> : query.data && <div role="tabpanel" aria-label={tabs.find((tab) => tab.id === category)?.label} className="data-panel overflow-hidden">
      <div className="max-h-[60vh] overflow-auto"><table className="data-table min-w-[900px]"><thead className="sticky top-0 bg-white dark:bg-slate-950"><tr><th>Thời gian</th><th>Người thao tác</th><th>Chức năng / hành động</th><th>Kết quả</th><th>Request ID</th><th>Chi tiết</th></tr></thead><tbody>{query.data.items.map((row) => <tr key={row.id}><td className="whitespace-nowrap">{new Date(row.occurred_at).toLocaleString('vi-VN', { timeZone: me.timezone })}</td><td>{row.actor_name ?? 'Chưa xác định danh tính'}{row.owner_name && row.owner_user_id !== row.actor_user_id && <p className="text-xs text-slate-500">Chủ dữ liệu: {row.owner_name}</p>}</td><td><p>{features[row.action.split('.')[0] ?? ''] ?? row.resource_type}</p><p className="text-xs text-slate-500">{actions[row.action] ?? row.action}</p></td><td><StatusBadge value={row.outcome} label={outcomes[row.outcome] ?? row.outcome} /></td><td className="max-w-44 break-all font-mono text-xs">{row.request_id}</td><td><Button onClick={() => { setSelected(row.id) }}>Xem chi tiết</Button></td></tr>)}</tbody></table>{query.data.items.length === 0 && <p className="p-6 text-center text-sm text-slate-500">Không có nhật ký phù hợp.</p>}</div>
      <div className="flex items-center justify-between gap-2 p-3"><Button disabled={cursors.length <= 1 || query.isFetching} onClick={() => { setCursors(cursors.slice(0, -1)); setSelected(null) }}>Trang trước</Button><span className="text-xs">Trang {cursors.length} · {query.data.items.length} sự kiện</span><Button disabled={!query.data.next_cursor || query.isFetching} onClick={() => { setCursors([...cursors, query.data.next_cursor]); setSelected(null) }}>Trang tiếp</Button></div>
    </div>}
    {selected && <section className="data-panel space-y-2 p-3" aria-label="Chi tiết sự kiện"><div className="flex items-center justify-between"><h2 className="font-semibold">Chi tiết sự kiện</h2><Button onClick={() => { setSelected(null) }}>Đóng chi tiết</Button></div>{detail.isPending ? <p role="status">Đang tải chi tiết…</p> : detail.isError ? <p role="alert">Không tải được chi tiết hoặc bạn không còn quyền xem.</p> : <><p className="break-all text-xs">{detail.data.action} · {detail.data.resource_type} · {detail.data.resource_id ?? 'Không có đối tượng xác định'}</p><p className="break-all text-xs">Người thao tác: {detail.data.actor_name ?? 'Chưa xác định'} · {detail.data.actor_user_id ?? '—'}</p><p className="break-all text-xs">Chủ dữ liệu: {detail.data.owner_name ?? '—'} · {detail.data.owner_user_id ?? '—'}</p><p className="break-all text-xs">Request ID: {detail.data.request_id}</p><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(detail.data.metadata, null, 2)}</pre></>}</section>}
    <p className="text-xs text-slate-500">“Đã tiếp nhận yêu cầu” chỉ là kết quả HTTP; xem sự kiện kết quả gửi để đối chiếu SMTP/provider. Phiên hết hạn được ghi khi phiên tiếp tục gọi API. Nhật ký không có chức năng sửa hoặc xóa.</p>
  </section>
}

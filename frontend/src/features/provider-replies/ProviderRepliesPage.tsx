import { ForbiddenPage } from '../../components/shared/StatusPage'
import { AppSelect, AppSelectItem } from '../../components/shared/AppSelect'
import { CheckCircle2, CircleHelp, Clock3, LoaderCircle, XCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import { Button } from '../../components/shared/Button'
import { request } from '../../lib/http/client'
import type { Me } from '../auth/types'
import { accountSchema, detailSchema, getReply, listSchema, progressSchema, makePreview, sendReply, type ReplyDetail, type ReplyList, type ReplyFields, type ReplyPreview } from './api'

const states: Record<string, string> = { pending: 'Chưa trả lời', sent: 'Đã trả lời', replied_in_sent: 'Đã có phản hồi trong Đã gửi', failed: 'Gửi lỗi', sending: 'Đang gửi', unknown: 'Chưa rõ — cần đối soát' }

const statusStyles: Record<string, { icon: typeof CircleHelp; color: string } | undefined> = {
  pending: { icon: Clock3, color: 'bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-200' },
  sent: { icon: CheckCircle2, color: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-200' },
  replied_in_sent: { icon: CheckCircle2, color: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-200' },
  failed: { icon: XCircle, color: 'bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-200' },
  sending: { icon: LoaderCircle, color: 'bg-sky-100 text-sky-800 dark:bg-sky-950/50 dark:text-sky-200' },
  unknown: { icon: CircleHelp, color: 'bg-violet-100 text-violet-800 dark:bg-violet-950/50 dark:text-violet-200' },
}

function ReplyStatus({ state }: { state: string }) {
  const { icon: Icon, color } = statusStyles[state] ?? { icon: CircleHelp, color: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200' }
  return <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium ${color}`}><Icon aria-hidden="true" className={`h-3.5 w-3.5 shrink-0${state === 'sending' ? ' motion-safe:animate-spin' : ''}`} /><span>{states[state] ?? state}</span></span>
}

export function ProviderRepliesPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const client = useQueryClient()
  const [search, setSearch] = useSearchParams()
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: me.timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
  const [from, setFrom] = useState(search.get('from') ?? today)
  const [to, setTo] = useState(search.get('to') ?? today)
  const [unread, setUnread] = useState(false)
  const [limit, setLimit] = useState(100)
  const [actionOnly, setActionOnly] = useState(true)
  const [provider, setProvider] = useState('cloudflare')
  const [busy, setBusy] = useState(false)
  const canView = me.permissions.includes('provider_replies.view')
  const accounts = useQuery({ queryKey: ['provider-accounts', me.id], enabled: canView,
    queryFn: async ({ signal }) => accountSchema.parse(await request({ url: '/provider-replies/accounts', signal })) })
  const account = search.get('account') ?? accounts.data?.find((row) => row.enabled && row.imap_configured)?.id ?? accounts.data?.[0]?.id ?? ''
  const selected = search.get('case') ?? ''
  const detailPanel = useRef<HTMLDivElement>(null)
  const listPanel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!selected) return
    detailPanel.current?.scrollIntoView({ block: 'start' })
    detailPanel.current?.focus({ preventScroll: true })
  }, [selected])
  const invalidDates = !from || !to || from > to || (new Date(to).getTime() - new Date(from).getTime()) > 31 * 86400000
  const key = ['provider-replies', me.id, account, from, to]
  const inbox = useQuery({ queryKey: key, enabled: canView && Boolean(account) && !invalidDates,
    queryFn: async ({ signal }) => listSchema.parse(await request({ url: `/provider-replies?account_id=${account}&date_from=${from}&date_to=${to}`, signal })), refetchInterval: 10000 })
  const progressKey = ['provider-sync-progress', me.id, account]
  const progress = useQuery({ queryKey: progressKey, enabled: canView && Boolean(account),
    queryFn: async ({ signal }) => progressSchema.parse(await request({ url: `/provider-replies/sync-progress?account_id=${account}`, signal })),
    refetchInterval: (query) => query.state.data?.progress?.status === 'running' ? 1000 : 5000 })
  const sync = useMutation({ mutationFn: (syncId: string) => request({ url: '/provider-replies/sync', method: 'POST', timeout: 300000,
    data: { account_id: account, date_from: from, date_to: to, unread_only: unread, limit, sync_id: syncId } }),
    onSettled: () => { void client.invalidateQueries({ queryKey: progressKey }); void client.invalidateQueries({ queryKey: ['provider-replies', me.id] }) } })
  const receivedProgress = progress.data?.progress
  const live = sync.isPending && receivedProgress?.sync_id !== sync.variables ? undefined : receivedProgress
  const syncing = sync.isPending || live?.status === 'running'
  const locked = busy || syncing
  const providers = [...new Map((inbox.data?.items ?? []).map((row) => [row.provider, row.provider_label])).entries()]
  const items = inbox.data?.items.filter((row) => (provider === 'all' || row.provider === provider) && (!actionOnly || (row.needs_reply))) ?? []
  if (!canView) return <ForbiddenPage message="Bạn không có quyền xem phản hồi NCC." />
  return <section className="space-y-3">
    <header><h1 className="text-xl font-semibold">Phản hồi NCC</h1><p className="mt-1 text-xs text-slate-500">Đồng bộ theo tài khoản → đọc yêu cầu → bổ sung bằng chứng → xem draft → trả lời đúng thread.</p></header>
    <div className="data-panel grid gap-3 border-t-2 border-t-cyan-500 p-3 md:grid-cols-4">
      <label className="field">Tài khoản<AppSelect value={account} disabled={locked || !accounts.data?.length} onValueChange={(event) => { setSearch({ account: event, from, to }); sync.reset() }}><AppSelectItem value="" disabled>Chọn tài khoản</AppSelectItem>{accounts.data?.map((row) => <AppSelectItem key={row.id} value={row.id}>{row.sender_email}{!row.enabled ? ' (đã tắt)' : !row.imap_configured ? ' (chưa cấu hình IMAP)' : ''}</AppSelectItem>)}</AppSelect></label>
      <label className="field">Từ ngày<input type="date" value={from} disabled={locked} onChange={(event) => { setFrom(event.target.value); setSearch({ account, from: event.target.value, to }) }} /></label>
      <label className="field">Đến ngày<input type="date" value={to} disabled={locked} onChange={(event) => { setTo(event.target.value); setSearch({ account, from, to: event.target.value }) }} /></label>
      <label className="field">Giới hạn mỗi thư mục<input type="number" min={1} max={500} value={limit} disabled={locked} onChange={(event) => { setLimit(Number(event.target.value)) }} /></label>
      <div className="flex flex-wrap items-center gap-3 md:col-span-4"><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={unread} disabled={locked} onChange={(event) => { setUnread(event.target.checked) }} />Chỉ thư chưa đọc khi đồng bộ</label>
        <Button className="primary-button" loading={syncing} disabled={locked || !account || invalidDates || !Number.isInteger(limit) || limit < 1 || limit > 500 || !me.permissions.includes('provider_replies.sync')} onClick={() => { sync.mutate(crypto.randomUUID()); void progress.refetch() }}>Đồng bộ phản hồi</Button>
        <Button disabled={locked} onClick={() => { void inbox.refetch() }}>Làm mới danh sách</Button><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={actionOnly} onChange={(event) => { setActionOnly(event.target.checked) }} />Chỉ cần phản hồi</label>
      </div>
      <label className="field md:col-span-2">Lọc NCC<AppSelect value={provider} disabled={locked} onValueChange={(event) => { setProvider(event) }}><AppSelectItem value="cloudflare">Cloudflare — có thể phản hồi</AppSelectItem><AppSelectItem value="all">Tất cả NCC</AppSelectItem>{providers.filter(([id]) => id !== 'cloudflare').map(([id, label]) => <AppSelectItem key={id} value={id}>{label} — chỉ xem, chưa hỗ trợ phản hồi</AppSelectItem>)}</AppSelect></label>
      <p className="self-end text-xs text-slate-500 md:col-span-2">Hiện hỗ trợ phản hồi Cloudflare. Đang hiện {items.length}/{inbox.data?.items.length ?? 0} thư theo bộ lọc.</p>
      {(syncing || live) && <section aria-label="Tiến độ đồng bộ" className="rounded-lg bg-cyan-50 p-3 text-xs dark:bg-cyan-950/30 md:col-span-4">
        <p role="status" className="font-semibold">{syncing ? `Đang đồng bộ · ${live?.folder || (live?.stage === 'saving' ? 'Lưu và đối chiếu kết quả' : 'Kết nối máy chủ mail')}` : live?.status === 'completed' ? `Đồng bộ đã hoàn tất${live.folders.some((folder) => folder.error_code || folder.limited) ? ' · có thư mục lỗi hoặc giới hạn số thư' : ''}` : live?.status === 'interrupted' ? 'Đồng bộ bị gián đoạn — có thể đồng bộ lại' : 'Đồng bộ thất bại — kiểm tra cấu hình và thử lại'}</p>
        <div className="mt-2 flex items-center gap-3"><progress aria-label="Tiến độ thư mục hiện tại" className="h-2 min-w-0 flex-1 accent-cyan-600" max={syncing ? live?.total || 1 : 1} value={syncing ? live?.total ? live.processed : undefined : 1} /><span>{syncing ? live?.total ? `${String(live.processed)}/${String(live.total)} thư` : 'Đang xác định số thư…' : live?.status === 'completed' ? `Đã nhập ${String(live.imported ?? 0)} thư mới` : ''}</span></div>
        <p className="mt-1 text-slate-500">{!syncing ? live?.status === 'completed' ? 'Đã kết thúc lượt đồng bộ. Xem kết quả từng thư mục bên dưới.' : 'Có thể bấm đồng bộ lại sau khi kiểm tra kết nối.' : live?.stage === 'saving' ? 'Đang lưu và đối chiếu kết quả…' : live?.folder_index ? `Thư mục ${String(live.folder_index)}/${String(live.folder_total)}` : 'Chờ máy chủ phản hồi; chưa thể ước tính thời gian hoàn tất.'}</p>
        <div className="mt-2 flex flex-wrap gap-3">{live?.folders.map((folder) => <span key={folder.mailbox}>{folder.folder}: {folder.processed ?? folder.fetched}/{folder.total ?? folder.fetched} · {folder.error_code ? 'Chưa đọc hết / lỗi' : folder.limited ? 'Đã chạm giới hạn số thư' : 'Đã kiểm tra'}</span>)}</div>
        {progress.isError && <p role="alert">Không tải được tiến độ. Lượt đồng bộ có thể vẫn đang chạy.</p>}
      </section>}
      {invalidDates && <p role="alert" className="error-notice md:col-span-4">Chọn khoảng ngày hợp lệ, tối đa 32 ngày.</p>}
      {sync.isPending && <p role="status" className="text-xs md:col-span-4">Đang đọc Inbox và các thư mục liên quan. Thao tác này không đánh dấu đã đọc hoặc gửi email.</p>}
      {inbox.data?.last_sync && <div className="space-y-1 text-xs md:col-span-4"><p className="text-slate-500">Lần đồng bộ: {new Date(inbox.data.last_sync.synced_at).toLocaleString('vi-VN')}{inbox.data.last_sync.date_from ? ` · ${inbox.data.last_sync.date_from} → ${inbox.data.last_sync.date_to ?? ''}` : ''}</p><div className="flex flex-wrap gap-3">{inbox.data.last_sync.folders.map((folder) => <span key={folder.mailbox} className={folder.error_code ? 'text-amber-700 dark:text-amber-300' : ''}>{folder.folder}: {folder.matched}/{folder.fetched}{folder.error_code ? ' · chưa đọc được' : ''}{folder.skipped_large ? ` · bỏ ${String(folder.skipped_large)} thư quá lớn` : ''}</span>)}</div></div>}
    </div>
    {(accounts.error || inbox.error || sync.error) && <p role="alert" className="error-notice">{(accounts.error ?? inbox.error ?? sync.error)?.message}</p>}
    {accounts.isSuccess && !accounts.data.length && <p className="data-panel p-3 text-sm">Thêm tài khoản mail và cấu hình IMAP tại Cài đặt mail trước khi đồng bộ.</p>}
    {inbox.isPending && account && <p role="status">Đang tải danh sách đã lưu…</p>}
    {inbox.isSuccess && !items.length && <p className="data-panel p-3 text-sm">Không có phản hồi trong phạm vi đang chọn. Bấm Đồng bộ phản hồi để đọc từ hệ thống mail.</p>}
    {Boolean(items.length) && <div ref={listPanel} tabIndex={-1} aria-label="Danh sách phản hồi" className="data-panel max-h-80 scroll-mt-20 overflow-auto"><table className="data-table"><thead className="sticky top-0 z-10 bg-white dark:bg-slate-950"><tr><th>NCC / yêu cầu</th><th>Nội dung / thư mục</th><th>Liên kết báo cáo</th><th>Trạng thái</th><th /></tr></thead><tbody>{items.map((row) => <tr key={row.id} className={selected === row.id ? 'bg-cyan-50 dark:bg-cyan-950/30' : ''}>
      <td><strong>{row.provider_label}</strong><p className="text-xs text-slate-500">{row.request_label}</p></td><td className="max-w-96 break-words">{row.subject}<p className="text-xs text-slate-500">{row.location.mailbox} · {row.seen ? 'Đã đọc' : 'Chưa đọc'}</p></td><td>{(row.matched || row.linked_delivery_id) ? 'Đã khớp' : 'Chưa nối lịch sử'}</td><td><ReplyStatus state={row.state} /></td><td><Button disabled={locked} onClick={() => { setSearch({ account, from, to, case: row.id }) }}>Mở thư</Button></td>
    </tr>)}</tbody></table></div>}
    {selected && <div ref={detailPanel} tabIndex={-1} role="region" aria-label="Chi tiết case đang mở" className="scroll-mt-20 space-y-2">
      <div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold">Xử lý phản hồi</h2><Button disabled={locked} onClick={() => { listPanel.current?.scrollIntoView({ block: 'start' }); listPanel.current?.focus({ preventScroll: true }) }}>Về danh sách ({items.length})</Button></div>
      <MessageDetail key={`${account}:${selected}`} id={selected} accountId={account} me={me} onBusy={setBusy} />
    </div>}
  </section>
}

function MessageDetail({ id, accountId, me, onBusy }: { id: string; accountId: string; me: Me; onBusy: (busy: boolean) => void }) {
  const client = useQueryClient()
  const key = ['provider-reply', me.id, id]
  const detail = useQuery({ queryKey: key, queryFn: ({ signal }) => getReply(id, signal), refetchInterval: 5000 })
  const update = (data: ReplyDetail) => { client.setQueryData(key, data); client.setQueriesData<ReplyList>({ queryKey: ['provider-replies', me.id] }, (previous) => previous ? { ...previous, items: previous.items.map((row) => row.id === data.id ? { ...row, state: data.state, seen: data.seen } : row) } : previous); void client.invalidateQueries({ queryKey: ['provider-replies', me.id] }) }
  if (detail.isPending) return <p role="status">Đang tải email gốc…</p>
  if (detail.isError) return <p role="alert" className="error-notice">{detail.error.message}</p>
  if (detail.data.account_id !== accountId) return <p role="alert" className="error-notice">Thư này thuộc tài khoản khác. Chọn lại đúng tài khoản gửi trước khi mở thư.</p>
  return <ReplyEditor id={id} me={me} data={detail.data} checkedAt={detail.dataUpdatedAt} onBusy={onBusy} update={update} refresh={async () => { const data = await getReply(id); update(data) }} />
}

function ReplyEditor({ id, me, data, checkedAt, onBusy, update, refresh }: { id: string; me: Me; data: ReplyDetail; checkedAt: number; onBusy: (busy: boolean) => void; update: (data: ReplyDetail) => void; refresh: () => Promise<void> }) {
  const [fields, setFields] = useState<ReplyFields>({ ...data.context, subject: '', body: '', legal_approved: false })
  const [files, setFiles] = useState<File[]>([])
  const [prepared, setPrepared] = useState<ReplyPreview | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [dirty, setDirty] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const edit = (field: keyof ReplyFields, value: string | boolean) => { setFields((current) => ({ ...current, [field]: value })); setConfirmed(false); setDirty(true) }
  const exact = prepared ?? data.preview
  const eligible = data.reply_supported && !data.reply_block_reason && ['pending', 'failed'].includes(data.state)
  const validFiles = files.length <= 3 && files.every((file) => ['image/png', 'image/jpeg'].includes(file.type) && file.size > 0 && file.size <= 10 * 1024 * 1024)
  const current = !dirty && Boolean(exact && exact.fingerprint === data.preview?.fingerprint && (!data.preview.expires_at || new Date(data.preview.expires_at).getTime() > checkedAt))
  const preview = useMutation({ mutationFn: () => makePreview(id, fields, files), onMutate: () => { onBusy(true) }, onSettled: () => { onBusy(false) }, onSuccess: async (result) => {
    setPrepared(result); setFiles([]); if (input.current) input.current.value = ''; setDirty(false); setConfirmed(false); await refresh()
  } })
  const capture = useMutation({ mutationFn: async () => detailSchema.parse(await request({ url: `/provider-replies/${id}/capture`, method: 'POST', data: { reported_url: fields.reported_url, show_browser: false }, timeout: 210000 })), onMutate: () => { onBusy(true) }, onSettled: () => { onBusy(false) }, onSuccess: (result) => { update(result); setPrepared(null); setDirty(true); setConfirmed(false) } })
  const seen = useMutation({ mutationFn: () => request({ url: `/provider-replies/${id}/seen`, method: 'POST' }), onSuccess: refresh })
  const send = useMutation({ mutationFn: async () => {
    if (!exact || !current || !confirmed) throw new Error('Hãy tạo và xác nhận preview hiện tại.')
    return sendReply(id, exact)
  }, onMutate: () => { onBusy(true) }, onSettled: () => { onBusy(false) }, onSuccess: (result) => { update(result); setConfirmed(false) }, onError: refresh })
  const busy = preview.isPending || capture.isPending || send.isPending || data.state === 'sending'
  return <section className="data-panel space-y-3 border-t-2 border-t-indigo-500 p-3" aria-label="Phản hồi đang chọn">
    <header className="flex flex-wrap items-center justify-between gap-2"><h2 className="break-words text-sm font-semibold">{data.mail.subject}</h2><span className="status status-pending">{states[data.state] ?? data.state}</span></header>
    <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs"><span>From: {data.mail.sender}</span><span>Reply-To: {data.mail.reply_to || 'Không có'}</span><span>Ticket: {data.mail.ticket || 'Không có'}</span><span>Thư mục: {data.mail.source_mailbox}</span></div>
    <details className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900"><summary className="cursor-pointer text-xs font-semibold">Email gốc — {data.mail.request_label}</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words text-xs">{data.mail.body}</pre></details>
    <details className="text-xs"><summary className="cursor-pointer">Tóm tắt tiếng Việt để đối chiếu — không gửi</summary><pre className="mt-2 whitespace-pre-wrap break-words">{data.reading_aid}</pre></details>
    {!data.seen && me.permissions.includes('provider_replies.mark_seen') && <Button disabled={busy} loading={seen.isPending} onClick={() => { seen.mutate() }}>Đánh dấu đã đọc</Button>}
    {!data.matched && data.reply_supported && <p className="text-xs text-slate-500">Chưa nối được lịch sử báo cáo. Bạn vẫn có thể xử lý yêu cầu Cloudflare trên thư gốc của tài khoản này; kiểm tra URL và bằng chứng trước khi gửi.</p>}
    {!data.reply_supported && <p className="text-xs text-amber-700 dark:text-amber-300">NCC này chưa hỗ trợ phản hồi trên web. Hiện chỉ hỗ trợ Cloudflare; các NCC khác chỉ xem.</p>}
    {data.reply_block_reason === 'missing_message_id' && <p role="alert" className="error-notice">Thư gốc thiếu Message-ID hợp lệ nên chưa thể trả lời đúng thread. Đồng bộ lại thư hoặc kiểm tra trên hệ thống mail.</p>}
    {!data.needs_reply && <p className="text-xs text-slate-500">Thư này không nằm trong nhóm cần phản hồi của luồng hiện tại. Bạn vẫn có thể đọc email gốc.</p>}
    {data.mail.channel !== 'email' && <p className="text-xs text-amber-700 dark:text-amber-300">NCC yêu cầu portal hoặc không cho reply. Không gửi SMTP cho thư này.</p>}
    {data.mail.channel !== 'email' && <details className="text-xs"><summary className="cursor-pointer">Mẫu tham khảo để copy thủ công — chưa phải nội dung đã duyệt</summary><p className="mt-2 font-semibold">{data.suggestion.subject}</p><pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words">{data.suggestion.body}</pre></details>}
    <fieldset disabled={busy || !eligible} className="grid gap-3 md:grid-cols-2">
      <label className="field">Reported URL<input value={fields.reported_url} onChange={(event) => { edit('reported_url', event.target.value) }} /></label>
      <label className="field">Website chính thức<input value={fields.official_url} onChange={(event) => { edit('official_url', event.target.value) }} /></label>
      <label className="field md:col-span-2">Thông tin đã xác minh bổ sung (tiếng Anh)<textarea rows={3} value={fields.evidence} onChange={(event) => { edit('evidence', event.target.value) }} /></label>
      <label className="field md:col-span-2">Ảnh bằng chứng (1–3 PNG/JPEG, tối đa 10 MB/ảnh)<input ref={input} type="file" multiple accept="image/png,image/jpeg" onChange={(event) => { setFiles(Array.from(event.target.files ?? [])); setDirty(true); setConfirmed(false) }} /></label>
    </fieldset>
    <div className="flex flex-wrap gap-2">{files.map((file, index) => <Thumbnail key={`${String(index)}:${file.name}`} file={file} index={index} />)}{data.images.map((image, index) => <a key={image.id} href={`/api/v1/provider-replies/${id}/images/${image.id}`} target="_blank" rel="noreferrer"><img className="h-28 max-w-48 rounded object-contain" alt={`Ảnh đã lưu ${String(index+1)}`} src={`/api/v1/provider-replies/${id}/images/${image.id}`} /></a>)}</div>
    {data.images.length > 0 && <p className="break-words text-xs text-emerald-700 dark:text-emerald-300">Đã lưu {data.images.length} ảnh bằng chứng.{data.capture_summary?.resolved_destination ? ` Control: ${data.capture_summary.label} · URL đích: ${data.capture_summary.resolved_destination}` : ''} Tạo lại draft để dùng bộ ảnh này.</p>}
    {!validFiles && <p role="alert" className="error-notice">Chọn tối đa 3 ảnh PNG/JPEG hợp lệ, mỗi ảnh tối đa 10 MB.</p>}
    {eligible && <div className="space-y-1"><Button className="primary-button" loading={capture.isPending} disabled={busy || !fields.reported_url || !me.permissions.includes('provider_replies.preview') || !me.permissions.includes('check_domain.capture') || data.capture_enabled === false} onClick={() => { capture.mutate() }}>Chụp URL nguồn và đích từ DOM</Button><p className="text-xs text-slate-500">{!me.permissions.includes('check_domain.capture') || !me.permissions.includes('provider_replies.preview') ? 'Tài khoản cần quyền chụp bằng chứng và tạo draft phản hồi.' : data.capture_enabled === false ? 'Browser capture đang tắt trên backend. Có thể upload ảnh; yêu cầu Cloudflare cần DOM phải bật capture.' : !fields.reported_url ? 'Nhập URL vi phạm để chụp bằng chứng.' : 'Dùng Browser Evidence chung: chụp trang nguồn, mở đích từ DOM và tạo ảnh/manifest cho draft.'}</p></div>}
    {capture.isPending && <p role="status" className="text-xs">Đang chụp ảnh nguồn và đích từ DOM. Giới hạn 180 giây; kết thúc sẽ hiện ảnh hoặc lỗi cụ thể.</p>}
    {data.mail.risk === 'approval_required' && <label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={fields.legal_approved} disabled={busy || !eligible} onChange={(event) => { edit('legal_approved', event.target.checked) }} />Người có thẩm quyền đã duyệt nội dung pháp lý/định danh.</label>}
    <details className="text-xs"><summary className="cursor-pointer">Sửa subject hoặc nội dung phản hồi (tiếng Anh)</summary><div className="mt-2 space-y-2"><label className="field">Subject tùy chỉnh<input disabled={busy || !eligible} value={fields.subject} placeholder="Để trống để dùng mẫu cũ" onChange={(event) => { edit('subject', event.target.value) }} /></label><label className="field">Nội dung tùy chỉnh<textarea rows={8} disabled={busy || !eligible} value={fields.body} placeholder="Để trống để tạo theo yêu cầu NCC và dữ liệu trên" onChange={(event) => { edit('body', event.target.value) }} /></label></div></details>
    <Button disabled={busy || !eligible || !validFiles || !me.permissions.includes('provider_replies.preview')} loading={preview.isPending} onClick={() => { preview.mutate() }}>Tạo / cập nhật draft phản hồi</Button>
    {(preview.error || capture.error || seen.error || send.error) && <p role="alert" className="error-notice">{(preview.error ?? capture.error ?? seen.error ?? send.error)?.message}</p>}
    {exact && current && <section className="space-y-2" aria-label="Draft phản hồi sẽ gửi"><h3 className="text-sm font-semibold">Draft phản hồi sẽ gửi</h3>{exact.deliveries.map((item) => <div key={item.recipient} className="rounded-lg border border-slate-200 p-3 dark:border-slate-700"><p className="break-all text-xs">From: {data.sender_email} → To: {item.recipient} · In-Reply-To: {item.in_reply_to}</p><p className="my-2 text-xs font-semibold">{item.subject}</p><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">{item.text_body}</pre></div>)}{prepared?.warnings?.map((warning) => <p key={warning} className="text-xs text-amber-700 dark:text-amber-300">{warning}</p>)}</section>}
    {dirty && <p className="text-xs text-slate-500">Dữ liệu đã thay đổi. Tạo lại draft để kiểm tra trước khi gửi.</p>}
    <label className="flex items-start gap-2 text-xs"><input type="checkbox" disabled={busy || !current || !eligible} checked={confirmed} onChange={(event) => { setConfirmed(event.target.checked) }} />Tôi đã đọc email gốc, kiểm tra tài khoản/người nhận và xác nhận nội dung phản hồi đang hiển thị.</label>
    <Button className="primary-button" disabled={busy || !eligible || !current || !confirmed || (data.mail.risk === 'approval_required' && !fields.legal_approved) || !me.permissions.includes('provider_replies.send')} loading={send.isPending} onClick={() => { send.mutate() }}>{data.state === 'failed' ? 'Thử lại phản hồi bị lỗi' : 'Gửi phản hồi đúng thread'}</Button>
    {data.state === 'sent' && <p role="status" className="text-sm text-emerald-700 dark:text-emerald-300">Đã gửi phản hồi. Hệ thống chặn gửi lại thư này.</p>}
    {data.state === 'replied_in_sent' && <p role="status" className="text-sm text-emerald-700 dark:text-emerald-300">Đã tìm thấy phản hồi khớp trong Đã gửi. Khóa gửi lại; chưa khẳng định thư đã tới người nhận.</p>}
    {data.state === 'unknown' && <p role="alert" className="error-notice">Chưa rõ kết quả sau khi gửi. Cần đối soát với hệ thống mail; không tự gửi lại.</p>}
    {data.sent_copy_status && ['failed', 'unknown', 'endpoint_denied', 'configuration_missing'].includes(data.sent_copy_status) && <p className="text-xs text-amber-700 dark:text-amber-300">SMTP đã chấp nhận nhưng bản sao Đã gửi chưa được xác nhận. Không gửi lại SMTP.</p>}
    <p className="text-xs text-slate-500">Số lần thử: {data.attempt_count}{data.error_code ? ` · ${data.error_code}` : ''}. “Đã gửi” xác nhận máy chủ mail chấp nhận, chưa khẳng định người nhận đã nhận.</p>
  </section>
}

function Thumbnail({ file, index }: { file: File; index: number }) {
  const image = useRef<HTMLImageElement>(null)
  useEffect(() => { const url = URL.createObjectURL(file); if (image.current) image.current.src = url; return () => { URL.revokeObjectURL(url) } }, [file])
  return <img ref={image} className="h-28 max-w-48 rounded object-contain" alt={`Ảnh đã chọn ${String(index+1)}`} />
}

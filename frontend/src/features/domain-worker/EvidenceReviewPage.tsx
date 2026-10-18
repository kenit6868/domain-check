import { ForbiddenPage } from '../../components/shared/StatusPage'
import { AppSelect, AppSelectItem } from '../../components/shared/AppSelect'
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useOutletContext, useSearchParams } from 'react-router-dom'
import { z } from 'zod'
import { Button } from '../../components/shared/Button'
import { request } from '../../lib/http/client'
import type { Me } from '../auth/types'
import { captureCloakingReview, getEvidenceReview, previewEvidenceReview, sendEvidenceReview, type EvidencePreview } from './evidenceReviewApi'

const labels: Record<string, string> = { pending: 'Chờ bổ sung ảnh', ready: 'Chờ duyệt gửi', sending: 'Đang gửi',
  partial: 'Gửi một phần / cần đối soát', failed: 'Có mục lỗi — có thể thử lại', sent: 'Đã gửi đủ tài khoản' }
const deliveryLabels: Record<string, string> = { pending: 'Chưa gửi', sending: 'Đang gửi', sent: 'Đã gửi', failed: 'Gửi lỗi', unknown: 'Chưa rõ — không tự gửi lại' }
const queueSchema = z.array(z.object({ id: z.uuid(), requested_url: z.string(), state: z.string(),
  sender_account_ids: z.array(z.uuid()), recipients: z.array(z.object({ channel: z.string(), email: z.string() })),
  evidence_reason: z.string().nullable(), has_evidence: z.boolean().default(false) }))

export function EvidenceReviewPage({ kind = 'evidence' }: { kind?: 'evidence' | 'cloaking' }) {
  const permission = kind === 'cloaking' ? 'cloaking_review' : 'domain_evidence_review'
  const { me } = useOutletContext<{ me: Me }>()
  const [search, setSearch] = useSearchParams()
  const [notice, setNotice] = useState('')
  const [warning, setWarning] = useState(false)
  const [mutating, setMutating] = useState(false)
  const selected = search.get('case') ?? ''
  const queue = useQuery({ queryKey: ['worker-review', me.id, kind], enabled: me.permissions.includes(`${permission}.view`),
    queryFn: async ({ signal }) => queueSchema.parse(await request({ url: `/domain-worker/reviews/${kind}`, signal, timeout: 30000 })), refetchInterval: 5000 })
  const busy = mutating || Boolean(queue.data?.some((entry) => entry.id === selected && entry.state === 'sending'))
  if (!me.permissions.includes(`${permission}.view`)) return <ForbiddenPage message="Bạn không có quyền xem trang này." />
  return <section className="space-y-3">
    <header className="flex flex-wrap items-center justify-between gap-2"><div><h1 className="text-xl font-semibold">{kind === 'cloaking' ? 'Cloaking Review' : 'Domain Evidence Review'}</h1>
      <p className="mt-1 text-xs text-slate-500">{kind === 'cloaking' ? 'Chọn case → đối chiếu bằng chứng → kết luận → xem draft → gửi trực tiếp.' : 'Chọn case → bổ sung ảnh → xem đúng draft → gửi trực tiếp. Không tạo job gửi mới.'}</p></div>
      <div className="flex gap-2"><Link className="text-xs underline" to="/domain-worker">Về Domain Worker</Link><Button onClick={() => { void queue.refetch() }}>Làm mới</Button></div></header>
    {notice && <p role="status" className={`rounded-lg p-3 text-sm ${warning ? 'bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200' : 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200'}`}>{notice}</p>}
    {queue.isPending && <p role="status">Đang tải danh sách…</p>}
    {queue.isError && <p role="alert" className="error-notice">{queue.error.message}</p>}
    {queue.isSuccess && !queue.data.length && <p className="data-panel p-3 text-sm">Hôm nay không có domain đang chờ bổ sung ảnh hoặc gửi tiếp.</p>}
    {Boolean(queue.data?.length) && <div className="data-panel overflow-auto"><table className="data-table"><thead><tr><th>Domain / URL</th><th>Email nhận</th><th>Tài khoản</th><th>Trạng thái</th><th /></tr></thead>
      <tbody>{queue.data?.map((row) => <tr key={row.id} className={selected === row.id ? 'bg-cyan-50 dark:bg-cyan-950' : ''}>
        <td className="max-w-80 break-all font-medium">{row.requested_url}</td><td>{row.recipients.map((recipient) => <p key={`${recipient.channel}:${recipient.email}`}>{recipient.email}</p>)}</td>
        <td>{row.sender_account_ids.length}</td><td>{labels[row.state] ?? row.state}</td>
        <td><Button disabled={busy} onClick={() => { setSearch({ case: row.id }); setNotice('') }}>Mở case</Button></td></tr>)}</tbody></table></div>}
    {selected ? <CaseEditor key={`${kind}:${selected}`} kind={kind} id={selected} me={me} onBusy={setMutating} onComplete={(copyWarning, skipped) => { setWarning(copyWarning); setNotice(skipped ? 'Đã bỏ qua case. Không gửi email.' : copyWarning ? 'Đã gửi đủ email nhưng có thư chưa lưu được vào Đã gửi. Kiểm tra kết nối mail; không gửi lại email.' : 'Đã gửi đủ mọi tài khoản/người nhận. Case đã được loại khỏi danh sách chờ.'); setSearch((current) => current.get('case') === selected ? {} : current) }} />
      : Boolean(queue.data?.length) && <p className="text-sm text-slate-500">Chọn Mở case để xem ảnh, tạo draft và gửi.</p>}
  </section>
}

function CaseEditor({ id, me, onComplete, onBusy, kind }: { kind: 'evidence' | 'cloaking'; id: string; me: Me; onComplete: (copyWarning: boolean, skipped?: boolean) => void; onBusy: (busy: boolean) => void }) {
  const client = useQueryClient()
  const permission = kind === 'cloaking' ? 'cloaking_review' : 'domain_evidence_review'
  const key = ['evidence-review-detail', me.id, kind, id]
  const detail = useQuery({ queryKey: key, queryFn: ({ signal }) => getEvidenceReview(id, signal, kind), refetchInterval: 3000 })
  const [files, setFiles] = useState<File[]>([])
  const [chosen, setChosen] = useState<string[] | null>(null)
  const [prepared, setPrepared] = useState<EvidencePreview | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [decisionChoice, setDecisionChoice] = useState<string | null>(null)
  const [difference, setDifference] = useState(false)
  const [captureChoice, setUseCapture] = useState<boolean | null>(null)
  const decision = decisionChoice ?? detail.data?.decision ?? 'confirmed_cloaking'
  const useCapture = captureChoice ?? (kind === 'cloaking' && detail.data?.capture_decision === decision && !detail.data.images.length)
  const fileInput = useRef<HTMLInputElement>(null)
  const accounts = chosen ?? (detail.data?.preview?.deliveries.map((delivery) => delivery.sender_account_id)
    .filter((value, index, values) => values.indexOf(value) === index)
    ?? detail.data?.accounts.filter((account) => account.enabled && detail.data.sender_account_ids.includes(account.id)).map((account) => account.id) ?? [])
  const exact = prepared ?? detail.data?.preview ?? null
  const current = Boolean(!useCapture && (kind !== 'cloaking' || (decision === detail.data?.decision && (decision !== 'confirmed_cloaking' || difference))) && exact && exact.fingerprint === detail.data?.preview?.fingerprint && files.length === 0 && accounts.length > 0 && accounts.length === new Set(exact.deliveries.map((delivery) => delivery.sender_account_id)).size
    && exact.deliveries.every((delivery) => accounts.includes(delivery.sender_account_id)) && new Date(exact.expires_at).getTime() > detail.dataUpdatedAt)
  const validFiles = files.length <= (kind === 'cloaking' && decision === 'confirmed_cloaking' ? 2 : 3) && (kind !== 'cloaking' || decision !== 'confirmed_cloaking' || files.length === 0 || files.length === 2) && files.every((file) => ['image/png', 'image/jpeg'].includes(file.type) && file.size > 0 && file.size <= 10 * 1024 * 1024)
  const invalidate = () => {
    void client.invalidateQueries({ queryKey: ['worker-review', me.id] })
    void client.invalidateQueries({ queryKey: ['domain-worker', me.id] })
    void client.invalidateQueries({ queryKey: ['domain-worker-workspace', me.id] })
    void client.invalidateQueries({ queryKey: key })
  }
  const capture = useMutation({ mutationFn: () => captureCloakingReview(id, decision), onMutate: () => { onBusy(true) }, onSettled: () => { onBusy(false) }, onSuccess: (data) => {
    client.setQueryData(key, data); setUseCapture(true); setFiles([]); setPrepared(null); setConfirmed(false); setDifference(false); invalidate()
  } })
  const preview = useMutation({ mutationFn: () => previewEvidenceReview(id, accounts, files, kind, decision, difference, useCapture), onMutate: () => { onBusy(true) }, onSettled: () => { onBusy(false) }, onSuccess: (data) => {
    setPrepared(data.preview); setUseCapture(false); setFiles([]); setConfirmed(false); if (fileInput.current) fileInput.current.value = ''; invalidate()
  } })
  const skip = useMutation({ mutationFn: () => request({ url: `/domain-worker/reviews/cloaking/${id}/skip`, method: 'POST' }),
    onMutate: () => { onBusy(true) }, onSettled: () => { onBusy(false) }, onSuccess: () => { invalidate(); onComplete(false, true) } })
  const send = useMutation({ onMutate: () => { onBusy(true) }, onSettled: () => { onBusy(false) }, mutationFn: () => {
    if (!exact || !current || !confirmed) throw new Error('Hãy xem và xác nhận preview hiện tại trước khi gửi.')
    return sendEvidenceReview(id, exact, kind)
  }, onSuccess: (data) => { client.setQueryData(key, data); invalidate(); setConfirmed(false); if (data.state === 'sent') onComplete(data.deliveries.some((delivery) => ['failed', 'unknown', 'configuration_missing', 'endpoint_denied'].includes(delivery.sent_copy_status ?? ''))) }, onError: invalidate })
  const busy = preview.isPending || send.isPending || skip.isPending || capture.isPending || detail.data?.state === 'sending'
  if (detail.isPending) return <p role="status">Đang tải case…</p>
  if (detail.isError) return <p role="alert" className="error-notice">{detail.error.message} <Button onClick={() => { void detail.refetch() }}>Thử lại</Button></p>
  const data = detail.data
  const hasEligible = data.deliveries.length === 0 || data.deliveries.some((delivery) => accounts.includes(delivery.sender_account_id) && ['pending', 'failed'].includes(delivery.state))
  return <section className="data-panel space-y-3 border-t-2 border-t-cyan-500 p-3" aria-label="Case đang chọn">
    <header className="flex flex-wrap justify-between gap-2"><h2 className="break-all text-sm font-semibold">{data.requested_url}</h2><span className="status status-pending">{labels[data.state] ?? data.state}</span></header>
    <p className="text-xs text-slate-500">{data.recipients.map((row) => row.email).join(', ')} · {data.source_job_ids.length} job nguồn</p>
    {kind === 'cloaking' && <div className="space-y-2 rounded-lg bg-amber-50 p-3 dark:bg-amber-950/30">
      <p className="text-xs font-semibold">Kết quả precheck: {data.cloaking?.verdict ?? 'INCONCLUSIVE'} · Điểm: {data.cloaking?.score ?? 0}/100</p>
      {Boolean(Object.keys(data.cloaking?.profiles ?? {}).length) && <details className="text-xs"><summary className="cursor-pointer font-medium">Quan sát theo profile</summary><div className="mt-2 overflow-x-auto"><table className="data-table"><thead><tr><th>Profile</th><th>HTTP</th><th>URL cuối / tiêu đề</th></tr></thead><tbody>{Object.entries(data.cloaking?.profiles ?? {}).map(([name, profile]) => <tr key={name}><td>{profile.label ?? name}</td><td>{profile.status_code ?? 'Chưa rõ'}</td><td className="max-w-96 break-all">{profile.final_url}<p className="text-xs text-slate-500">{profile.title}</p></td></tr>)}</tbody></table></div></details>}
      <label className="field">Kết luận duyệt<AppSelect disabled={busy || data.state === 'sent' || data.deliveries.some((delivery) => ['sent', 'sending', 'unknown'].includes(delivery.state))} value={decision} onValueChange={(event) => { setDecisionChoice(event); setFiles([]); setUseCapture(false); setDifference(false); setPrepared(null); setConfirmed(false); if (fileInput.current) fileInput.current.value = '' }}>
        <AppSelectItem value="confirmed_cloaking">Xác nhận cloaking — hai ảnh đối chiếu</AppSelectItem><AppSelectItem value="not_cloaking">Không cloaking — bằng chứng report thường riêng</AppSelectItem>
      </AppSelect></label>
      {decision === 'confirmed_cloaking' ? <label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={difference} disabled={busy} onChange={(event) => { setDifference(event.target.checked); setConfirmed(false) }} />Tôi xác nhận hai ảnh của đúng URL này thể hiện nội dung khác nhau giữa các profile.</label>
        : <p className="text-xs">Bổ sung 1–3 ảnh report thường. Draft và tệp gửi sẽ không chứa bằng chứng hoặc kết luận cloaking.</p>}
      {!data.deliveries.some((delivery) => ['sent', 'sending', 'unknown'].includes(delivery.state)) && me.permissions.includes('cloaking_review.skip') && <Button disabled={busy} loading={skip.isPending} onClick={() => { skip.mutate() }}>Bỏ qua case này</Button>}
      {skip.error && <p role="alert" className="error-notice">{skip.error.message}</p>}
      {data.capture_pending && !data.capture_reason && !data.capture_images?.length && <p role="status" className="text-xs">Precheck đang chụp cặp ảnh đối chiếu tự động. Ảnh sẽ hiển thị khi hoàn tất.</p>}
      {data.capture_reason === 'capture_failed' && !data.capture_images?.length && <p className="text-xs text-amber-700 dark:text-amber-300">Precheck chưa chụp được cặp ảnh hợp lệ. Chụp lại hoặc bổ sung hai ảnh đối chiếu.</p>}
      {data.capture_reason === 'capture_disabled' && !data.capture_images?.length && <p className="text-xs">Chụp tự động chưa bật trên worker. Có thể bổ sung ảnh thủ công.</p>}
      {me.permissions.includes('check_domain.capture') && <Button disabled={busy || data.state === 'sent' || Boolean(data.capture_pending && !data.capture_reason && !data.capture_images?.length)} loading={capture.isPending} onClick={() => { capture.mutate() }}>{data.capture_images?.length ? 'Chụp lại bằng chứng' : 'Chụp bằng chứng tự động'}</Button>}
      {capture.isPending && <p role="status" className="text-xs">Đang chụp trong browser cô lập; không click hoặc nhập dữ liệu. Vui lòng đợi…</p>}
      {capture.error && <p role="alert" className="error-notice">{capture.error.message}</p>}
      {data.capture_decision === decision && Boolean(data.capture_images?.length) && <div className="space-y-2"><p className="text-xs">Ảnh tự động — kiểm tra ảnh trước khi xác nhận.</p><div className="flex flex-wrap gap-2">{data.capture_images?.map((image, index) => <a key={image.id} href={`/api/v1/domain-worker/reviews/cloaking/${id}/images/${image.id}`} target="_blank" rel="noreferrer" title="Mở ảnh đầy đủ"><img className="h-28 max-w-48 rounded object-contain" alt={`Ảnh capture ${String(index + 1)}`} src={`/api/v1/domain-worker/reviews/cloaking/${id}/images/${image.id}`} /><span className="text-xs">{data.capture_profile_names?.[index]}</span></a>)}</div>
        {data.capture_comparison_only && <p className="text-xs text-amber-700 dark:text-amber-300">Đã chụp hai profile để đối chiếu, nhưng browser chưa quan sát được khác biệt nội dung rõ ràng. Dấu hiệu script chỉ là nghi vấn; chỉ xác nhận cloaking nếu bạn thấy bằng chứng khác biệt thực tế.</p>}
        <label className="flex items-start gap-2 text-xs"><input type="checkbox" disabled={busy} checked={useCapture} onChange={(event) => { setUseCapture(event.target.checked); setFiles([]); setPrepared(null); setConfirmed(false) }} />Dùng bộ ảnh tự động này cho draft mới</label></div>}
    </div>}
    {data.evidence_reason && <p className="text-xs text-amber-700 dark:text-amber-300">Capture tự động chưa tạo được bằng chứng hợp lệ. Hãy bổ sung ảnh của đúng URL này.</p>}
    {data.images.length > 0 && (kind !== 'cloaking' || decision === data.decision) && <div><p className="mb-2 text-xs">Ảnh đã lưu — có thể dùng lại khi thử lại phần lỗi.</p><div className="flex flex-wrap gap-2">{data.images.map((image, index) => <a key={image.id} href={`/api/v1/domain-worker/reviews/${kind}/${id}/images/${image.id}`} target="_blank" rel="noreferrer" title="Mở ảnh đầy đủ"><img className="h-28 max-w-48 rounded object-contain" alt={`Ảnh đã lưu ${String(index + 1)}`} src={`/api/v1/domain-worker/reviews/${kind}/${id}/images/${image.id}`} /></a>)}</div></div>}
    {me.permissions.includes(`${permission}.upload`) && <label className="field">Ảnh bằng chứng ({kind === 'cloaking' && decision === 'confirmed_cloaking' ? 'đúng 2' : '1–3'} PNG/JPEG, tối đa 10 MB/ảnh)<input ref={fileInput} type="file" multiple accept="image/png,image/jpeg" disabled={busy || data.state === 'sent'} onChange={(event) => {
      setFiles(Array.from(event.target.files ?? [])); setUseCapture(false); setPrepared(null); setConfirmed(false); preview.reset(); send.reset()
    }} /></label>}
    <div className="flex flex-wrap gap-2">{files.filter((file) => ['image/png', 'image/jpeg'].includes(file.type)).slice(0, 3).map((file, index) => <Thumbnail key={`${String(index)}:${file.name}`} file={file} index={index} />)}</div>
    {!validFiles && <p role="alert" className="error-notice">Chọn {kind === 'cloaking' && decision === 'confirmed_cloaking' ? 'đúng 2' : 'tối đa 3'} ảnh PNG/JPEG, không rỗng, mỗi ảnh tối đa 10 MB. Toàn bộ lựa chọn phải hợp lệ trước khi tạo preview.</p>}
    {!data.sender_account_ids.length && <p className="text-xs text-amber-700 dark:text-amber-300">Case cũ chưa lưu tài khoản gửi. Chọn tài khoản bên dưới; lựa chọn sẽ được lưu khi tạo draft, không cần precheck lại.</p>}
    <fieldset disabled={busy || data.state === 'sent'} className="flex flex-wrap gap-3"><legend className="mb-1 text-xs font-semibold">Tài khoản gửi</legend>
      {data.accounts.map((account) => <label key={account.id} className="flex items-center gap-1.5 text-xs"><input type="checkbox" disabled={!account.enabled} checked={accounts.includes(account.id)} onChange={(event) => {
        setChosen(event.target.checked ? [...accounts, account.id] : accounts.filter((value) => value !== account.id)); setPrepared(null); setConfirmed(false)
      }} />{account.sender_email}{!account.enabled && ' (đã tắt)'}</label>)}</fieldset>
    <Button disabled={busy || !validFiles || (kind === 'cloaking' && (!me.permissions.includes('cloaking_review.approve') || (decision === 'confirmed_cloaking' && !difference) || (decision !== data.decision && !files.length && !useCapture))) || !accounts.length || (!files.length && !data.images.length && !useCapture) || data.state === 'sent' || !me.permissions.includes(`${permission}.preview`)}
      loading={preview.isPending} onClick={() => { preview.mutate() }}>Tạo / cập nhật draft để xem</Button>
    {(preview.error || send.error) && <p role="alert" className="error-notice">{(preview.error ?? send.error)?.message}</p>}
    {data.deliveries.length > 0 && <div className="overflow-auto"><table className="data-table"><thead><tr><th>Tài khoản gửi</th><th>Email nhận</th><th>Kênh</th><th>Trạng thái</th></tr></thead>
      <tbody>{data.deliveries.map((delivery) => <tr key={`${delivery.sender_account_id}:${delivery.channel}:${delivery.recipient}`}><td>{data.accounts.find((account) => account.id === delivery.sender_account_id)?.sender_email ?? delivery.sender_account_id}</td>
        <td>{delivery.recipient}</td><td>{delivery.channel}</td><td>{deliveryLabels[delivery.state] ?? delivery.state}{delivery.error_code && <p className="text-xs">{delivery.error_code === 'smtp_rejected' ? 'Máy chủ từ chối thư hoặc đăng nhập' : delivery.error_code === 'smtp_outcome_uncertain' ? 'Mất kết nối khi gửi; cần đối soát' : 'Cần kiểm tra kết nối mail hoặc bằng chứng'}</p>}</td></tr>)}</tbody></table></div>}
    {data.deliveries.some((delivery) => delivery.state === 'sent' && ['failed', 'unknown', 'configuration_missing', 'endpoint_denied'].includes(delivery.sent_copy_status ?? '')) && <p role="alert" className="error-notice">Email đã gửi nhưng chưa lưu được vào Đã gửi. Kiểm tra kết nối mail; không gửi lại email.</p>}
    {data.deliveries.some((delivery) => ['unknown', 'sending'].includes(delivery.state)) && <p className="text-xs text-amber-700 dark:text-amber-300">Mục chưa rõ không tự gửi lại. Cần đối soát với máy chủ mail; retry chỉ xử lý mục lỗi hoặc chưa gửi.</p>}
    {data.errors?.length ? <p role="alert" className="error-notice">Một số mục chưa gửi được. Kiểm tra tài khoản/bằng chứng và tạo lại preview nếu cần; kết quả đã gửi được giữ nguyên.</p> : null}
    {exact && current && <section className="space-y-2" aria-label="Nội dung draft sẽ gửi"><h3 className="text-sm font-semibold">Nội dung draft sẽ gửi</h3>
      <p className="text-xs text-slate-500">Đây là đúng nội dung sẽ gửi. Ảnh, hồ sơ hoặc tài khoản thay đổi sẽ bị chặn trước SMTP.</p>
      {exact.deliveries.map((delivery, index) => <details key={`${delivery.sender_account_id}:${delivery.channel}:${delivery.recipient}`} open={index === 0} className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
        <summary className="break-all text-xs font-semibold">{data.accounts.find((account) => account.id === delivery.sender_account_id)?.sender_email ?? delivery.sender_account_id} → {delivery.recipient} · {delivery.channel}</summary>
        <p className="my-2 break-all text-xs">Subject: {delivery.subject}</p><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">{delivery.text_body}</pre></details>)}
    </section>}
    {!current && <p className="text-xs text-slate-500">Tạo preview mới sau khi chọn ảnh/tài khoản và đọc nội dung trước khi gửi.</p>}
    <label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={confirmed} disabled={busy || !current} onChange={(event) => { setConfirmed(event.target.checked) }} />Tôi đã đọc draft, kiểm tra người nhận và xác nhận gửi nội dung đang hiển thị.</label>
    <Button className="primary-button" loading={send.isPending} disabled={busy || !current || !confirmed || !hasEligible || data.state === 'sent' || !me.permissions.includes(`${permission}.send`) || (kind === 'cloaking' && !me.permissions.includes('cloaking_review.approve'))}
      onClick={() => { send.mutate() }}>{data.deliveries.some((delivery) => delivery.state === 'sent') ? 'Gửi tiếp các mục còn thiếu' : 'Gửi mail domain này'}</Button>
    {send.isPending && <p role="status" className="text-xs">Đang gửi trực tiếp và lưu kết quả từng email. Không bấm gửi lại ở tab khác.</p>}
    <p className="text-xs text-slate-500">Đã gửi là máy chủ mail đã chấp nhận thư; chưa xác minh hộp thư người nhận.</p>
  </section>
}

function Thumbnail({ file, index }: { file: File; index: number }) {
  const image = useRef<HTMLImageElement>(null)
  useEffect(() => { const url = URL.createObjectURL(file); if (image.current) image.current.src = url; return () => { URL.revokeObjectURL(url) } }, [file])
  return <img ref={image} alt={`Ảnh đã chọn ${String(index + 1)}`} className="h-28 max-w-48 rounded object-contain" />
}

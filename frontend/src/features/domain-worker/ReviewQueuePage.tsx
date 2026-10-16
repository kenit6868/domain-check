import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useOutletContext, useSearchParams } from 'react-router-dom'
import { z } from 'zod'
import { Button } from '../../components/shared/Button'
import { request } from '../../lib/http/client'
import type { Me } from '../auth/types'

const cases = z.array(z.object({ id: z.uuid(), check_id: z.uuid(), requested_url: z.string(), state: z.string(),
  sender_account_ids: z.array(z.uuid()), recipients: z.array(z.object({ channel: z.string(), email: z.string() })),
  cloaking_verdict: z.string().nullable(), evidence_reason: z.string().nullable(),
}))

export function ReviewQueuePage({ kind }: { kind: 'cloaking' | 'evidence' }) {
  const { me } = useOutletContext<{ me: Me }>()
  const [search, setSearch] = useSearchParams()
  const client = useQueryClient()
  const permission = kind === 'cloaking' ? 'cloaking_review' : 'domain_evidence_review'
  const title = kind === 'cloaking' ? 'Cloaking Review' : 'Domain Evidence Review'
  const selectedId = search.get('case') ?? ''
  const queue = useQuery({ queryKey: ['worker-review', me.id, kind], enabled: me.permissions.includes(`${permission}.view`),
    queryFn: async ({ signal }) => cases.parse(await request({ url: `/domain-worker/reviews/${kind}`, signal })), refetchInterval: 5000 })
  const [selection, setSelection] = useState<{ caseId: string; files: File[] }>({ caseId: '', files: [] })
  const files = useMemo(() => selection.caseId === selectedId ? selection.files : [], [selection, selectedId])
  const setFiles = (value: File[]) => { setSelection({ caseId: selectedId, files: value }) }
  const selected = queue.data?.find((row) => row.id === selectedId)
  const upload = useMutation({ mutationFn: async () => {
    const data = new FormData(); files.forEach((file) => { data.append('files', file) })
    return request({ url: `/domain-worker/reviews/evidence/${selectedId}/images`, method: 'POST', data })
  }, onSuccess: () => {
    setFiles([]); void client.invalidateQueries({ queryKey: ['worker-review', me.id] })
    void client.invalidateQueries({ queryKey: ['domain-worker', me.id] })
  } })
  if (!me.permissions.includes(`${permission}.view`)) return <p role="alert">Bạn không có quyền xem trang này.</p>
  return <section className="space-y-3">
    <header><h1 className="text-xl font-semibold">{title}</h1><p className="mt-1 text-xs text-slate-500">Danh sách cần xử lý của ngày hiện tại, giữ tài khoản đã chọn từ precheck.</p></header>
    <Link className="text-xs underline" to="/domain-worker">Về Domain Worker</Link>
    {queue.isPending && <p role="status">Đang tải danh sách…</p>}
    {queue.isError && <p role="alert" className="error-notice">{queue.error.message}</p>}
    {queue.isSuccess && !queue.data.length && <p className="data-panel p-3 text-sm">Không có case cần xử lý hôm nay.</p>}
    {Boolean(queue.data?.length) && <div className="data-panel overflow-x-auto"><table className="data-table">
      <thead><tr><th>URL</th><th>Tài khoản</th><th>Email nhận</th><th>Trạng thái</th><th /></tr></thead>
      <tbody>{queue.data?.map((row) => <tr key={row.id} className={row.id === selectedId ? 'bg-indigo-50 dark:bg-indigo-950' : ''}>
        <td className="max-w-80 break-all">{row.requested_url}</td><td>{row.sender_account_ids.length}</td>
        <td>{row.recipients.map((recipient) => <span key={recipient.email}>{recipient.channel}: {recipient.email}</span>)}</td>
        <td>{kind === 'cloaking' ? row.cloaking_verdict : 'Thiếu Browser Evidence'}</td>
        <td><Button onClick={() => { setSearch({ case: row.id }); setFiles([]); upload.reset() }}>Chọn case</Button></td>
      </tr>)}</tbody></table></div>}
    {selected && <section className="data-panel space-y-2 p-3" aria-label="Case đang chọn">
      <h2 className="break-all text-sm font-semibold">{selected.requested_url}</h2>
      {kind === 'cloaking' ? <p className="text-sm">Case đã được tách khỏi batch thường. Cần xác minh cặp ảnh và duyệt cloaking trước khi gửi; web chưa có thao tác phê duyệt/gửi case này.</p>
        : <>
          <p className="text-xs text-slate-500">Chọn 1–3 ảnh PNG/JPEG của đúng URL này. Evidence hợp lệ sẽ đưa domain trở lại danh sách đủ điều kiện của các lượt nguồn.</p>
          {!selected.sender_account_ids.length && <p className="text-xs">Case cũ chưa có tài khoản nguồn. Chọn tài khoản và precheck lại URL tại Domain Worker.</p>}
          {selected.sender_account_ids.length > 0 && me.permissions.includes('domain_evidence_review.upload') && <>
            <label className="field">Ảnh bằng chứng<input type="file" accept="image/png,image/jpeg" multiple disabled={upload.isPending}
              onChange={(event) => { setFiles(Array.from(event.target.files ?? [])); upload.reset() }} /></label>
            <div className="flex flex-wrap gap-2">{files.map((file, index) => <FileThumbnail key={`${selectedId}:${String(index)}:${file.name}`} file={file} index={index} />)}</div>
            <Button className="primary-button" disabled={!files.length || files.length > 3 || files.some((file) => !['image/png', 'image/jpeg'].includes(file.type))} loading={upload.isPending}
              onClick={() => { upload.mutate() }}>Dùng bằng chứng cho case này</Button>
          </>}
        </>}
      {upload.isError && <p className="error-notice" role="alert">{upload.error.message}</p>}
    </section>}
    {upload.isSuccess && <p role="status" className="text-sm">Đã bổ sung bằng chứng. Domain đã đủ điều kiện trong lượt nguồn; chưa gửi email.</p>}
  </section>
}

function FileThumbnail({ file, index }: { file: File; index: number }) {
  const image = useRef<HTMLImageElement>(null)
  useEffect(() => {
    const url = URL.createObjectURL(file)
    if (image.current) image.current.src = url
    return () => { URL.revokeObjectURL(url) }
  }, [file])
  return <img ref={image} alt={`Ảnh đã chọn ${String(index + 1)}`} className="h-28 max-w-48 rounded object-contain" />
}

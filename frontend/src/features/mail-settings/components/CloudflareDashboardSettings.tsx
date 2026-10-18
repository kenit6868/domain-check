import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Button } from '../../../components/shared/Button'
import { request } from '../../../lib/http/client'

export function CloudflareDashboardSettings({ disabled }: { disabled: boolean }) {
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['mail', 'cloudflare-dashboard'], queryFn: () => request<{ account_id: string }>({ url: '/settings/cloudflare-dashboard' }) })
  const [edited, setEdited] = useState('')
  const [dirty, setDirty] = useState(false)
  const value = dirty ? edited : query.data?.account_id ?? ''
  const save = useMutation({ mutationFn: () => request<{ account_id: string }>({ url: '/settings/cloudflare-dashboard', method: 'PUT', data: { account_id: value.trim() } }),
    onSuccess: (data) => { client.setQueryData(['mail', 'cloudflare-dashboard'], data); setEdited(data.account_id); setDirty(false) } })
  const invalid = Boolean(value.trim() && !/^[a-f\d]{32}$/i.test(value.trim()))
  return <section className="data-panel mt-3 space-y-3 p-4" aria-label="Cloudflare Dashboard">
    <h2 className="font-semibold">Cloudflare Dashboard</h2>
    <p className="text-xs text-slate-500">Cấu hình chung của bạn, dùng cho Cloudflare Worker. Cookie được nhập riêng tại Worker; không lưu trong cấu hình mail.</p>
    <label className="field">Cloudflare Account ID<input autoComplete="off" spellCheck={false} maxLength={32} value={value} disabled={disabled || query.isPending || query.isError || save.isPending} onChange={(event) => { setEdited(event.target.value); setDirty(true); save.reset() }} /></label>
    <p className="text-xs text-slate-500">32 ký tự hex. Để trống sẽ lấy từ Cookie curr-account; nếu hai giá trị khác nhau, hệ thống chặn gửi.</p>
    {invalid && <p role="alert" className="error-notice">Account ID phải gồm đúng 32 ký tự hex.</p>}
    {(query.error || save.error) && <p role="alert" className="error-notice">{(query.error ?? save.error)?.message}</p>}
    {query.isError && <Button onClick={() => { void query.refetch() }}>Tải lại cấu hình Cloudflare</Button>}
    {save.isSuccess && <p role="status" className="text-xs text-emerald-700">Đã lưu Cloudflare Account ID.</p>}
    {!disabled && <Button className="primary-button" disabled={invalid || !dirty || query.isPending || query.isError} loading={save.isPending} onClick={() => { save.mutate() }}>Lưu cấu hình Cloudflare</Button>}
  </section>
}

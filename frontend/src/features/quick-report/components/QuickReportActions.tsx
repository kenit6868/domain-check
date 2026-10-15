import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Send } from 'lucide-react'
import { toast } from '../../../lib/notifications'
import { useOutletContext } from 'react-router-dom'
import { Button } from '../../../components/shared/Button'
import { QueryError } from '../../../components/shared/QueryError'
import type { Me } from '../../auth/types'
import { getNetcraft, previewNetcraft, submitNetcraft } from '../api/actionApi'

const labels = { PREVIEW: 'Chưa gửi', SENDING: 'Đang gửi', SUBMITTED: 'Netcraft đã nhận báo cáo', FAILED: 'Nhà cung cấp từ chối', UNKNOWN: 'Chưa xác định kết quả — không tự gửi lại' }

export function NetcraftAction({ checkId, compact = false }: { checkId: string; compact?: boolean }) {
  const { me } = useOutletContext<{ me: Me }>()
  const allowed = me.permissions.includes('quick_report.submit_netcraft')
  const client = useQueryClient()
  const key = ['quick-report', me.id, 'netcraft', checkId]
  const status = useQuery({ queryKey: key, queryFn: ({ signal }) => getNetcraft(checkId, signal), enabled: allowed,
    refetchInterval: (query) => query.state.data?.state === 'SENDING' ? 1_500 : false })
  const send = useMutation({ mutationFn: async () => {
    const preview = await previewNetcraft(checkId)
    client.setQueryData(key, preview)
    if (['SUBMITTED', 'UNKNOWN', 'SENDING'].includes(preview.state)) return preview
    if (!preview.submit_enabled) throw new Error('Gửi API Netcraft chưa được bật trên máy chủ.')
    return submitNetcraft(preview)
  }, onSuccess: (result) => {
    client.setQueryData(key, result)
    if (result.state === 'SUBMITTED') toast.success('Netcraft đã nhận báo cáo.')
    else if (result.state === 'FAILED') toast.error('Netcraft từ chối báo cáo. Kiểm tra trạng thái trước khi thử lại.')
    else if (result.state === 'SENDING') toast.info('Báo cáo đang được xử lý.')
    else toast.warning('Chưa xác định kết quả gửi Netcraft. Không gửi lại ngay.')
  }, onSettled: () => { void client.invalidateQueries({ queryKey: key }) } })
  if (!allowed) return null
  const value = status.data
  const locked = value && ['SUBMITTED', 'UNKNOWN', 'SENDING'].includes(value.state)
  const error = send.error ?? status.error
  return <div className={compact ? 'contents' : 'border-t border-slate-200 p-3 dark:border-slate-800'}>
    <div className="flex flex-wrap items-center gap-2"><Button className="primary-button report-action inline-flex items-center gap-2" aria-label={value?.state === 'FAILED' ? 'Thử gửi lại Netcraft' : 'Gửi API Netcraft'} loading={send.isPending} disabled={Boolean(locked) || send.isPending || status.isPending || status.isError || value?.submit_enabled === false} onClick={() => { send.mutate() }}>
      <Send size={14} /><span>Netcraft · {value?.state === 'FAILED' ? 'Thử gửi lại' : 'Gửi API'}</span>
    </Button>{value && value.state !== 'PREVIEW' && <span className="text-xs text-slate-500" role="status">{labels[value.state]}</span>}
    {value?.submit_enabled === false && <span className="text-xs text-slate-500">API chưa bật</span>}</div>
    {error && <QueryError message={error.message} onRetry={() => { void status.refetch() }} />}
  </div>
}

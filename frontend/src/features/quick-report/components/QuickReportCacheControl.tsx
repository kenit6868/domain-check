import { useMutation } from '@tanstack/react-query'
import { DatabaseZap } from 'lucide-react'
import { z } from 'zod'
import { Button } from '../../../components/shared/Button'
import { request } from '../../../lib/http/client'

export function QuickReportCacheControl({ busy, onCleared }: { busy: boolean; onCleared: () => void }) {
  const clear = useMutation({
    meta: { pendingMessage: 'Đang xoá cache Quick Report…', successMessage: 'Đã xoá cache hôm nay. Lần kiểm tra tiếp theo sẽ lấy dữ liệu mới.' },
    mutationFn: async () => z.object({ feature: z.literal('quick-report'), business_day: z.string(), deleted_count: z.number().int().nonnegative() }).parse(
      await request<unknown>({ url: '/cache/clear', method: 'POST', data: { feature: 'quick-report' } })),
    onSuccess: () => { onCleared() },
  })
  return <div className="my-2 flex flex-wrap items-center gap-2 text-xs">
    <Button className="secondary-button inline-flex items-center gap-2" disabled={busy || clear.isPending} loading={clear.isPending} title={busy ? 'Đợi lượt kiểm tra kết thúc trước khi xoá cache' : 'Chỉ xoá cache Quick Report của bạn trong hôm nay; giữ lịch sử và trạng thái gửi'} onClick={() => { clear.mutate() }}><DatabaseZap size={14} />Xoá cache hôm nay</Button>
    {clear.data && <span className="text-emerald-700 dark:text-emerald-300" role="status">Đã xoá {clear.data.deleted_count} mục cache ngày {clear.data.business_day} và dọn danh sách đang xem. Nhập lại URL để kiểm tra mới.</span>}
    {clear.error && <span className="text-rose-600 dark:text-rose-300" role="alert">{clear.error.message}</span>}
  </div>
}

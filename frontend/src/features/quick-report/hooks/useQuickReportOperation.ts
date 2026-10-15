import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { toast } from '../../../lib/notifications'

import { queryKeys } from '../../../lib/query/keys'
import { createQuickReport, getQuickReport } from '../api/quickReportApi'

const ACTIVE_STATES = new Set(['queued', 'running', 'stopping'])

function storageKey(userId: string) {
  return `domaincheck:v1:u:${userId}:quick-report:active-operation`
}

export function useQuickReportOperation(userId: string) {
  const queryClient = useQueryClient()
  const [operationId, setOperationId] = useState<string | null>(() => sessionStorage.getItem(storageKey(userId)))
  const operation = useQuery({
    queryKey: queryKeys.quickReport(userId, operationId ?? 'none'),
    queryFn: ({ signal }) => getQuickReport(operationId ?? '', signal),
    enabled: operationId !== null,
    refetchInterval: (query) => ACTIVE_STATES.has(query.state.data?.state ?? '') ? 2_000 : false,
    retry: 1,
  })
  const create = useMutation({
    meta: { pendingMessage: 'Đang tạo lượt kiểm tra…', successMessage: 'Đã tạo lượt kiểm tra. Đang chờ worker xử lý.' },
    mutationFn: createQuickReport,
    onSuccess: (created) => {
      sessionStorage.setItem(storageKey(userId), created.id)
      setOperationId(created.id)
      queryClient.setQueryData(queryKeys.quickReport(userId, created.id), created)
    },
  })
  const previous = useRef<{ id: string; state: string } | null>(null)
  const current = operation.data ?? create.data
  useEffect(() => {
    if (!current) return
    if (previous.current?.id === current.id && ACTIVE_STATES.has(previous.current.state) && !ACTIVE_STATES.has(current.state)) {
      const failed = current.checks.filter((check) => check.status === 'failed').length
      const id = `quick-report-complete-${current.id}`
      if (failed || current.state === 'failed') toast.warning('Lượt kiểm tra đã kết thúc, có URL chưa xử lý thành công. Xem kết quả chi tiết.', { id })
      else toast.success('Lượt kiểm tra đã hoàn tất. Bạn có thể xem kết quả và chọn thao tác báo cáo.', { id })
    }
    previous.current = { id: current.id, state: current.state }
  }, [current])
  const reset = () => {
    sessionStorage.removeItem(storageKey(userId))
    setOperationId(null)
    create.reset()
  }
  return { operationId, operation, create, reset }
}

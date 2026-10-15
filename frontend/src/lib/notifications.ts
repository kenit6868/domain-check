import { toast } from 'sonner'
import { ApiError } from './http/errors'

export { toast }

export function notifyError(error: unknown, id?: string | number) {
  const status = error instanceof ApiError ? error.status : undefined
  const message = status === 401 ? 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.'
    : status === 403 ? 'Bạn không có quyền thực hiện thao tác này.'
    : status === 409 ? 'Thao tác chưa thể thực hiện. Kiểm tra thông báo tại biểu mẫu.'
    : status === 0 ? 'Không thể kết nối máy chủ. Kiểm tra kết nối và thử lại.'
    : 'Thao tác chưa hoàn tất. Kiểm tra thông tin và thử lại.'
  // Keep contact data, drafts, credentials and raw server responses out of toast.
  return toast.error(message, { id: id ?? `action-error-${String(status ?? 'unknown')}`, duration: 6_000 })
}

declare module '@tanstack/react-query' {
  interface Register {
    mutationMeta: { successMessage?: string; pendingMessage?: string; silentError?: boolean }
  }
}

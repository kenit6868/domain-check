import axios from 'axios'

export interface ApiErrorPayload {
  code?: string
  message?: string
  detail?: string
  field_errors?: Record<string, string[]>
  request_id?: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly fieldErrors: Record<string, string[]>
  readonly requestId: string | null

  constructor(message: string, status = 0, payload: ApiErrorPayload = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = payload.code ?? 'request_failed'
    this.fieldErrors = payload.field_errors ?? {}
    this.requestId = payload.request_id ?? null
  }
}

export function normalizeApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  if (!axios.isAxiosError<ApiErrorPayload>(error)) {
    return new ApiError(error instanceof Error ? error.message : 'Yêu cầu không thể hoàn tất.')
  }
  const payload = error.response?.data ?? {}
  const message = payload.message ?? payload.detail ?? (
    error.code === 'ERR_CANCELED'
      ? 'Yêu cầu đã bị hủy.'
      : error.response
        ? 'Yêu cầu không thể hoàn tất.'
        : 'Không thể kết nối backend.'
  )
  return new ApiError(message, error.response?.status ?? 0, payload)
}

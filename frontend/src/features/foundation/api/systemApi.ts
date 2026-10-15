import { request } from '../../../lib/http/client'

export interface LiveResponse {
  status: string
  service: string
}

export interface TelegramAuthStatus {
  configured: boolean
  issuer: string
  redirect_uri: string
}

export const getLiveness = (signal?: AbortSignal) =>
  request<LiveResponse>({ url: '/health/live', ...(signal ? { signal } : {}) })

export const getTelegramAuthStatus = (signal?: AbortSignal) =>
  request<TelegramAuthStatus>({ url: '/auth/telegram/status', ...(signal ? { signal } : {}) })

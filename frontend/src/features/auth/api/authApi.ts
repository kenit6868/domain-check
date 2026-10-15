import { request } from '../../../lib/http/client'
import type { Me } from '../types'

export const getCurrentUser = (signal?: AbortSignal) =>
  request<Me>({ url: '/me', ...(signal ? { signal } : {}) })

export const logoutCurrentUser = () =>
  request<undefined>({ url: '/auth/logout', method: 'POST' })

import axios, { type AxiosRequestConfig } from 'axios'

import { normalizeApiError } from './errors'

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

function readCookie(name: string): string {
  const prefix = `${name}=`
  const entry = document.cookie.split('; ').find((item) => item.startsWith(prefix))
  return entry ? decodeURIComponent(entry.slice(prefix.length)) : ''
}

export const httpClient = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
  headers: { Accept: 'application/json' },
  timeout: 30_000,
})

httpClient.interceptors.request.use((config) => {
  const method = (config.method ?? 'get').toUpperCase()
  if (!SAFE_METHODS.has(method)) {
    config.headers.set('X-CSRF-Token', readCookie('dc_csrf'))
  }
  return config
})

httpClient.interceptors.response.use(
  (response) => response,
  (error: unknown) => Promise.reject(normalizeApiError(error)),
)

export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const response = await httpClient.request<T>(config)
  return response.data
}

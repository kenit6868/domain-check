import { AxiosError, type AxiosAdapter, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'

import { httpClient } from '../src/lib/http/client'

type Handler = (config: InternalAxiosRequestConfig) => unknown

export function installApiMock(handler: Handler): () => void {
  const previous = httpClient.defaults.adapter
  const adapter: AxiosAdapter = async (config) => {
    const data = await handler(config)
    return { data, status: 200, statusText: 'OK', headers: {}, config } satisfies AxiosResponse
  }
  httpClient.defaults.adapter = adapter
  return () => {
    if (previous === undefined) delete httpClient.defaults.adapter
    else httpClient.defaults.adapter = previous
  }
}

export function installNetworkFailure(): () => void {
  const previous = httpClient.defaults.adapter
  httpClient.defaults.adapter = (config) => Promise.reject(new AxiosError('offline', 'ERR_NETWORK', config))
  return () => {
    if (previous === undefined) delete httpClient.defaults.adapter
    else httpClient.defaults.adapter = previous
  }
}

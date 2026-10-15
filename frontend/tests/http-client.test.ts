import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, describe, expect, it } from 'vitest'

import { createQuickReport } from '../src/features/quick-report/api/quickReportApi'
import { httpClient } from '../src/lib/http/client'
import { installApiMock } from './httpMock'

const operation = {
  id: '22222222-2222-4222-8222-222222222222', state: 'queued', stage: 'queued',
  business_day: '2026-10-15', checks: [], duplicates: [],
}

describe('Axios transport', () => {
  afterEach(() => { document.cookie = 'dc_csrf=; Max-Age=0; path=/' })

  it('uses same-origin credentials and adds CSRF only through the shared client', async () => {
    document.cookie = 'dc_csrf=test-token; path=/'
    let captured: unknown = null
    const restore = installApiMock((config) => { captured = config; return operation })
    await createQuickReport(['https://example.test/login'])
    const config = captured as InternalAxiosRequestConfig
    expect(config.withCredentials).toBe(true)
    expect(config.headers.get('X-CSRF-Token')).toBe('test-token')
    expect(config.data).toBe(JSON.stringify({ targets: ['https://example.test/login'] }))
    restore()
  })

  it('normalizes backend errors into the frontend error contract', async () => {
    const previous = httpClient.defaults.adapter
    httpClient.defaults.adapter = (config) => Promise.reject(new AxiosError(
      'forbidden', 'ERR_BAD_REQUEST', config, undefined,
      { data: { code: 'permission_denied', message: 'Không đủ quyền', request_id: 'req-1' }, status: 403, statusText: 'Forbidden', headers: {}, config },
    ))
    await expect(createQuickReport(['https://example.test'])).rejects.toMatchObject({
      status: 403, code: 'permission_denied', message: 'Không đủ quyền', requestId: 'req-1',
    })
    if (previous === undefined) delete httpClient.defaults.adapter
    else httpClient.defaults.adapter = previous
  })
})

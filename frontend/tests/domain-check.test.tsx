import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppRouter } from '../src/app/router/AppRouter'
import { createQueryClient } from '../src/lib/query/client'
import { installApiMock } from './httpMock'

const me = { id: '11111111-1111-4111-8111-111111111111', display_name: 'Operator',
  telegram_username: 'operator', status: 'active', timezone: 'Asia/Bangkok',
  permissions: ['check_domain.view', 'check_domain.check'] }
const checkId = '33333333-3333-4333-8333-333333333333'
const operationId = '22222222-2222-4222-8222-222222222222'
function operation(state = 'completed') { return { id: operationId, state, stage: state,
  business_day: '2026-10-16', check: { id: checkId, requested_url: 'https://example.com/login', domain: 'example.com',
    status: state === 'completed' ? 'succeeded' : 'running',
    result: state === 'completed' ? { virustotal: { malicious: 2, suspicious: 1 }, whois: { registrar: 'Fixture Registrar' } } : {},
    cache_provenance: {} } } }
const guidance = [
  { channel: 'browser', label: 'Google', form_url: 'https://safebrowsing.google.com/safebrowsing/report_phish/',
    draft: 'Please review https://example.com/login for suspected phishing.', note: '' },
  { channel: 'registrar', label: 'Fixture Registrar', form_url: 'https://registrar.example.com/abuse',
    draft: 'Please review the reported URL.', note: '' },
  { channel: 'registrar', label: 'Fixture Registrar · Email', form_url: null,
    draft: 'To: abuse@registrar.example\nSubject: Suspected phishing report', note: 'Email nhận: abuse@registrar.example' },
]
function renderPage() { return render(<QueryClientProvider client={createQueryClient()}>
  <MemoryRouter initialEntries={['/domain-check']}><AppRouter /></MemoryRouter></QueryClientProvider>) }

describe('Check Domain guidance workflow', () => {
  afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks() })

  it('submits exactly one target and shows report forms without evidence', async () => {
    const calls: unknown[] = []
    const restore = installApiMock((config) => {
      if (config.url === '/me') return me
      if (config.url === '/domain-checks/checks' && config.method === 'post') { calls.push(config.data); return operation('running') }
      if (config.url === `/domain-checks/checks/${operationId}`) return operation()
      if (config.url === `/domain-checks/checks/${checkId}/report-guidance`) return guidance
      throw new Error(`Unexpected request: ${config.url ?? ''}`)
    })
    renderPage()
    await userEvent.type(await screen.findByLabelText('Domain hoặc URL cần kiểm tra'), 'https://example.com/login')
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra' }))
    await screen.findByText('Mẫu báo cáo và form', {}, { timeout: 5000 })
    expect(JSON.parse(String(calls[0]))).toEqual({ target: 'https://example.com/login', submit_vt: false })
    expect(await screen.findByText(/Please review https:\/\/example.com\/login/)).toBeInTheDocument()
    expect(await screen.findAllByRole('link', { name: 'Mở form' })).toHaveLength(2)
    expect(screen.getByRole('region', { name: 'Cảnh báo trình duyệt' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'CDN' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Registrar' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Registry' })).toBeInTheDocument()
    expect(screen.getByText('Email nhận: abuse@registrar.example')).toBeInTheDocument()
    expect(screen.getByText('Thông số kiểm tra')).toBeInTheDocument()
    expect(screen.queryByText('WHOIS', { selector: 'summary' })).not.toBeInTheDocument()
    expect(screen.queryByText('Browser Evidence')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /chụp|upload|gửi/i })).not.toBeInTheDocument()
    expect(sessionStorage.getItem(`domaincheck:v1:u:${me.id}:domain-check:active-operation`)).toBe(operationId)
    restore()
  })

  it('reloads the owned operation with GET and does not check again', async () => {
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:domain-check:active-operation`, operationId)
    let posts = 0
    const restore = installApiMock((config) => {
      if (config.url === '/me') return me
      if (config.method === 'post') posts += 1
      if (config.url === `/domain-checks/checks/${operationId}`) return operation()
      if (config.url === `/domain-checks/checks/${checkId}/report-guidance`) return guidance
      throw new Error(`Unexpected request: ${config.url ?? ''}`)
    })
    renderPage()
    await screen.findByText('Mẫu báo cáo và form')
    expect(posts).toBe(0)
    restore()
  })

  it('hides checking for view-only accounts while keeping the report examples', async () => {
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:domain-check:active-operation`, operationId)
    const restore = installApiMock((config) => {
      if (config.url === '/me') return { ...me, permissions: ['check_domain.view'] }
      if (config.url === `/domain-checks/checks/${operationId}`) return operation()
      if (config.url === `/domain-checks/checks/${checkId}/report-guidance`) return guidance
      throw new Error(`Unexpected request: ${config.url ?? ''}`)
    })
    renderPage()
    await screen.findByText('Mẫu báo cáo và form')
    expect(screen.getByRole('button', { name: 'Kiểm tra' })).toBeDisabled()
    expect(await screen.findAllByRole('link', { name: 'Mở form' })).toHaveLength(2)
    restore()
  })
})

import { Theme } from '@radix-ui/themes'
import { chooseSelect } from './selectHelper'
import { QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it } from 'vitest'
import { AppRouter } from '../src/app/router/AppRouter'
import { createQueryClient } from '../src/lib/query/client'
import { queryKeys } from '../src/lib/query/keys'
import { installApiMock } from './httpMock'

const me = { id: 'owner', display_name: 'Dashboard Reader', telegram_username: 'reader', status: 'active', timezone: 'Asia/Bangkok', roles: ['Reviewer'], permissions: ['dashboard.view', 'domain_worker.view'] }
const fixture = { scope: 'own', days: 7, timezone: me.timezone, start: '2026-10-12', end: '2026-10-18', updated_at: '2026-10-18T06:00:00Z',
  features: ['domain_worker'], metrics: { checked_urls: 12, sent_emails: 4, failed_deliveries: 1, unknown_deliveries: 0 },
  trend: [{ date: '2026-10-18', checked_urls: 12, sent_emails: 4 }], pending: [], workers: [], users: [], user_total: null, pending_users: null, users_offset: 0, last_mail_sync: null, events: [] }

function mount(permissions = me.permissions) {
  const client = createQueryClient()
  client.setQueryData(queryKeys.me, { ...me, permissions })
  render(<QueryClientProvider client={client}><Theme><MemoryRouter initialEntries={['/']}><AppRouter /></MemoryRouter></Theme></QueryClientProvider>)
  return client
}
afterEach(cleanup)

it('shows only authorized personal statistics and tools', async () => {
  const calls: string[] = []
  const restore = installApiMock((config) => { calls.push(config.url ?? ''); return fixture })
  const client = mount()
  try {
    expect(await screen.findByText('Xu hướng hoạt động')).toBeVisible()
    expect(screen.queryByLabelText('Phạm vi')).not.toBeInTheDocument()
    expect(screen.queryByText('Cloudflare tiếp nhận')).not.toBeInTheDocument()
    expect(screen.queryByText('Cần xử lý hôm nay')).not.toBeInTheDocument()
    expect(calls[0]).toContain('scope=own')
    expect(within(screen.getByRole('main')).queryByRole('link', { name: 'Cloudflare Worker' })).not.toBeInTheDocument()
    await chooseSelect(screen.getByLabelText('Khoảng thời gian'), '30')
    await waitFor(() => { expect(calls.some((url) => url.includes('days=30'))).toBe(true) })
  } finally { cleanup(); client.clear(); restore() }
})

it('defaults authorized admins to system scope and supports user filters', async () => {
  const calls: string[] = []
  const restore = installApiMock((config) => { calls.push(config.url ?? ''); return { ...fixture, scope: 'all', user_total: 2, pending_users: 1, users: [{ id: 'other', name: 'Other Operator', status: 'active', checked_urls: 7, sent_emails: 2, attention: 0 }], workers: [{ id: 'job', owner_name: 'Other Operator', feature: 'domain_worker', state: 'running', total: 10, completed: 3, stale: false, href: null }], events: [{ id: 'event', action: 'domain_worker.request_start', outcome: 'accepted', at: fixture.updated_at }] } })
  const client = mount(['dashboard.view', 'dashboard.view_all'])
  try {
    expect(await screen.findByText('Other Operator', { selector: 'td' })).toBeVisible()
    expect(screen.getByText('Đang hoạt động', { selector: 'td .status' })).toHaveClass('status-badge', 'status-active')
    expect(screen.getByText('Đang chạy')).toHaveClass('status-badge', 'status-info')
    expect(screen.getByText('Đã nhận yêu cầu')).toHaveClass('status-badge', 'status-info')
    expect(screen.getByLabelText('Phạm vi')).toHaveTextContent('Toàn hệ thống')
    expect(calls[0]).toContain('scope=all')
    await userEvent.click(screen.getByRole('button', { name: 'Xem thống kê' }))
    await waitFor(() => { expect(calls.some((url) => url.includes('user_id=other'))).toBe(true) })
    await chooseSelect(screen.getByLabelText('Phạm vi'), 'own')
    await waitFor(() => { expect(calls.at(-1)).toContain('scope=own'); expect(calls.at(-1)).not.toContain('user_id') })
  } finally { cleanup(); client.clear(); restore() }
})

it('removes cached system statistics immediately after dashboard access is removed', async () => {
  const calls: string[] = []
  const restore = installApiMock((config) => { calls.push(config.url ?? ''); return { ...fixture, scope: 'all', user_total: 54321 } })
  const client = mount(['dashboard.view', 'dashboard.view_all', 'domain_worker.view'])
  try {
    await screen.findByText('Tài khoản hệ thống')
    const before = calls.length
    act(() => { client.setQueryData(queryKeys.me, { ...me, permissions: ['domain_worker.view'] }) })
    expect(await screen.findByText(/Bạn chưa được cấp quyền xem thống kê Dashboard/)).toBeVisible()
    expect(screen.queryByText('Tài khoản hệ thống')).not.toBeInTheDocument()
    expect(screen.queryByText('Xu hướng hoạt động')).not.toBeInTheDocument()
    expect(calls).toHaveLength(before)
  } finally { cleanup(); client.clear(); restore() }
})

it('keeps useful tool shortcuts without fetching unauthorized statistics', async () => {
  const calls: string[] = []
  const restore = installApiMock((config) => { calls.push(config.url ?? ''); throw new Error('Unexpected protected request') })
  const client = mount(['mail_accounts.view_own'])
  try {
    expect(await screen.findByRole('heading', { name: 'Truy cập nhanh' })).toBeVisible()
    expect(within(screen.getByRole('main')).getByRole('link', { name: 'Cấu hình mail' })).toBeVisible()
    expect(calls).toEqual([])
  } finally { cleanup(); client.clear(); restore() }
})

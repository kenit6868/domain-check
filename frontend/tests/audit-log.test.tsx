import { Theme } from '@radix-ui/themes'
import { chooseSelect } from './selectHelper'
import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { AppRouter } from '../src/app/router/AppRouter'
import { createQueryClient } from '../src/lib/query/client'
import { installApiMock } from './httpMock'

const event = { id: '11111111-1111-4111-8111-111111111111', occurred_at: new Date().toISOString(),
  actor_user_id: 'owner', owner_user_id: 'owner', actor_name: 'Audit Operator', owner_name: 'Audit Operator',
  actor_type: 'user', action: 'auth.login_success', resource_type: 'session', resource_id: 'session-id',
  outcome: 'success', request_id: 'request-fixture', metadata: { provider: 'telegram' } }

function mount(permissions: string[]) {
  const calls: string[] = []
  const restore = installApiMock((config) => {
    const url = config.url ?? ''; calls.push(url)
    if (url === '/me') return { id: 'owner', display_name: 'Reader', status: 'active', timezone: 'Asia/Bangkok', telegram_username: null, permissions }
    if (url.startsWith('/audit-logs/')) return event
    if (url.startsWith('/audit-logs?')) {
      const params = new URL(url, 'http://test').searchParams
      return { items: [{ ...event, id: params.has('cursor') ? 'second-page' : event.id }], next_cursor: params.has('cursor') ? null : 'next-page' }
    }
    throw new Error('Unexpected request')
  })
  render(<QueryClientProvider client={createQueryClient()}><Theme><MemoryRouter initialEntries={['/audit-log']}><AppRouter /></MemoryRouter></Theme></QueryClientProvider>)
  return { calls, restore }
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('shows personal log with three tabs and no global scope control', async () => {
  const { calls, restore } = mount(['audit_logs.view'])
  try {
    expect(await screen.findByText('Audit Operator', { selector: 'td' })).toBeVisible()
    const badge = screen.getByText('Thành công', { selector: 'td span' })
    expect(badge).toHaveClass('status', 'status-badge', 'status-active')
    expect(badge).toHaveAttribute('title', 'success')
    expect(screen.getAllByRole('tab')).toHaveLength(3)
    expect(screen.queryByLabelText('Phạm vi')).not.toBeInTheDocument()
    expect(calls.filter((url) => url.startsWith('/audit-logs?')).every((url) => url.includes('scope=own'))).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: 'Xem chi tiết' }))
    expect(await screen.findByRole('region', { name: 'Chi tiết sự kiện' })).toHaveTextContent('telegram')
    expect(screen.queryByRole('button', { name: /xóa|sửa|export/i })).not.toBeInTheDocument()
  } finally { restore() }
})

it('applies global filters and resets pagination on tab changes', async () => {
  const { calls, restore } = mount(['audit_logs.view', 'audit_logs.view_all'])
  try {
    await screen.findByText('Audit Operator', { selector: 'td' })
    await chooseSelect(screen.getByLabelText('Phạm vi'), 'all')
    await userEvent.type(screen.getByLabelText('Người dùng (tên hoặc ID)'), 'Other user')
    await userEvent.type(screen.getByLabelText('Request ID'), 'trace-one')
    await userEvent.click(screen.getByRole('button', { name: 'Áp dụng bộ lọc' }))
    await waitFor(() => { expect(calls.some((url) => url.includes('user=Other+user') && url.includes('request_id=trace-one') && url.includes('scope=all'))).toBe(true) })
    await userEvent.click(screen.getByRole('button', { name: 'Trang tiếp' }))
    await waitFor(() => { expect(calls.some((url) => url.includes('cursor=next-page'))).toBe(true) })
    await userEvent.click(screen.getByRole('tab', { name: 'Quản trị & cấu hình' }))
    await waitFor(() => { expect(calls.at(-1)).toContain('category=admin') })
    expect(calls.at(-1)).not.toContain('cursor=')
  } finally { restore() }
})

it('does not fetch audit data without view permission even with view_all', async () => {
  const { calls, restore } = mount(['audit_logs.view_all'])
  try {
    expect(await screen.findByRole('alert')).toHaveTextContent('Bạn không có quyền xem Audit Log.')
    expect(calls.some((url) => url.startsWith('/audit-logs'))).toBe(false)
  } finally { restore() }
})

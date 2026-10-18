import { Theme } from '@radix-ui/themes'
import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { AppRouter } from '../src/app/router/AppRouter'
import { createQueryClient } from '../src/lib/query/client'
import { installApiMock } from './httpMock'

const me = { id: 'permission-audit', display_name: 'Reader', telegram_username: null,
  status: 'active', timezone: 'Asia/Bangkok', permissions: [] as string[] }

function mount(path: string) {
  return render(<QueryClientProvider client={createQueryClient()}><Theme><MemoryRouter initialEntries={[path]}><AppRouter /></MemoryRouter></Theme></QueryClientProvider>)
}

afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks() })

it.each(['/audit-log', '/quick-report', '/domain-check', '/domain-worker', '/cloaking-review',
  '/domain-evidence-review', '/cloudflare-worker', '/link-status', '/provider-replies',
  '/settings/mail', '/admin/users', '/admin/roles'])('denies direct navigation to %s without fetching protected data', async (path) => {
  const requests: string[] = []
  const restore = installApiMock((config) => {
    requests.push(config.url ?? '')
    if (config.url === '/me') return me
    throw new Error('Unexpected protected request')
  })
  try {
    mount(path)
    expect(await screen.findByText(/Bạn.*quyền/)).toBeVisible()
    expect(screen.getAllByRole('navigation').every((nav) => nav.querySelectorAll('a').length === 1)).toBe(true)
    expect(requests.every((url) => url === '/me')).toBe(true)
  } finally { restore() }
})

it('does not fetch role choices or offer assignment without roles.view', async () => {
  const requests: string[] = []
  const restore = installApiMock((config) => {
    requests.push(config.url ?? '')
    if (config.url === '/me') return { ...me, permissions: ['users.view', 'users.assign_roles'] }
    if (config.url?.startsWith('/users?')) return { items: [{ id: 'u2', display_name: 'Pending user', telegram_username: null,
      identity_provider: 'telegram', status: 'pending', timezone: 'Asia/Bangkok', last_login_at: null, roles: [] }], total: 1, offset: 0, limit: 25 }
    throw new Error('Unexpected protected request')
  })
  try {
    mount('/admin/users')
    expect(await screen.findByText('Pending user')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Gán role' })).not.toBeInTheDocument()
    expect(requests).not.toContain('/roles')
  } finally { restore() }
})

it('refuses form submission with view-only Check Domain permission', async () => {
  const requests: string[] = []
  const restore = installApiMock((config) => {
    requests.push(config.url ?? '')
    if (config.url === '/me') return { ...me, permissions: ['check_domain.view'] }
    throw new Error('Unexpected protected request')
  })
  try {
    mount('/domain-check')
    const input = await screen.findByLabelText('Domain hoặc URL cần kiểm tra')
    expect(input).toBeDisabled()
    fireEvent.change(input, { target: { value: 'https://example.com/' } })
    fireEvent.submit(input.closest('form') as HTMLFormElement)
    expect(requests.every((url) => url === '/me')).toBe(true)
  } finally { restore() }
})

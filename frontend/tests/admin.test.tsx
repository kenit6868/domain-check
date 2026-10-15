import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppRouter } from '../src/app/router/AppRouter'
import { installApiMock } from './httpMock'

function renderAdmin(path = '/admin/users') {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><AppRouter /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('admin pages', () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  it('creates one SMTP account without a label, IMAP, proxy or organization profile', async () => {
    let submitted: Record<string, unknown> | null = null
    const account = { id: 'fixture-mail', label: 'sender@example.test', smtp_host: 'mail.example.test', smtp_port: 465,
      smtp_username: 'sender@example.test', smtp_transport: 'implicit_tls', enabled: true, is_default: false,
      imap: { host: '', port: 993, username: '', mailbox: 'INBOX', sent_mailbox: 'Sent', junk_mailbox: 'Junk', cloudflare_mailbox: '2-Cloudflare', evidence_mailbox: '' },
      proxy: { scheme: null, host: '', port: null }, signature: { contact_name: '', contact_email: '', signature_role: '' },
      has_smtp_password: true, has_imap_password: false, has_proxy_credentials: false, version: 1 }
    const restore = installApiMock((config) => {
      if (config.url === '/me') return { id: 'u1', display_name: 'Owner', status: 'active', timezone: 'Asia/Bangkok', telegram_username: null, permissions: ['mail_accounts.view_own', 'mail_accounts.manage_own'] }
      if (config.url === '/mail-accounts' && config.method === 'post') {
        submitted = JSON.parse(String(config.data)) as Record<string, unknown>
        return account
      }
      if (config.url === '/mail-accounts') return submitted ? [account] : []
      if (config.url === '/settings/profile') return { brand_name: '', contact_name: '', contact_email: '', company_name: '', business_registration_no: '', signature_role: '', logo_artifact_id: null, version: 1 }
      throw new Error('Unexpected request')
    })
    renderAdmin('/settings/mail')
    await userEvent.click(await screen.findByRole('button', { name: 'Thêm tài khoản' }))
    await userEvent.click(screen.getByRole('button', { name: 'Lưu tài khoản' }))
    expect(await screen.findByText('Vui lòng nhập SMTP username (email đăng nhập).')).toBeInTheDocument()
    expect(submitted).toBeNull()
    await userEvent.type(screen.getByLabelText('SMTP username'), 'sender@example.test')
    await userEvent.type(screen.getByLabelText('SMTP host'), 'mail.example.test')
    await userEvent.type(screen.getByLabelText('SMTP password'), 'fixture-password')
    await userEvent.clear(screen.getByLabelText('SMTP port'))
    await userEvent.type(screen.getByLabelText('SMTP port'), '465')
    expect(screen.getByLabelText('Transport')).toHaveValue('implicit_tls')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu tài khoản' }))
    expect(await screen.findByText('sender@example.test', { selector: 'h2' })).toBeInTheDocument()
    expect(submitted).toMatchObject({ label: 'sender@example.test', smtp_username: 'sender@example.test', smtp_port: 465, smtp_transport: 'implicit_tls', imap: { host: '' } })
    restore()
  })

  it('shows only actions granted by effective permissions', async () => {
    const restore = installApiMock((config) => {
      const url = config.url ?? ''
      const body = url.endsWith('/me')
        ? { id: 'u1', display_name: 'Read only', telegram_username: 'reader', status: 'active', timezone: 'Asia/Bangkok', permissions: ['users.view'] }
        : url.includes('/users')
          ? { items: [{ id: 'u2', display_name: 'Pending user', telegram_username: null, identity_provider: 'telegram', status: 'pending', timezone: 'Asia/Bangkok', last_login_at: null, roles: [] }], total: 1, offset: 0, limit: 100 }
          : []
      return body
    })
    renderAdmin()
    expect(await screen.findByText('Pending user')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Kích hoạt' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Gán role' })).not.toBeInTheDocument()
    expect(screen.queryByText('Role & quyền')).not.toBeInTheDocument()
    restore()
  })

  it('renders pending users without exposing business navigation', async () => {
    const restore = installApiMock(() => ({
      id: 'u1', display_name: 'Waiting', telegram_username: null, status: 'pending',
      timezone: 'Asia/Bangkok', permissions: [],
    }))
    renderAdmin('/pending')
    expect(await screen.findByText('Tài khoản đang chờ duyệt')).toBeInTheDocument()
    expect(screen.queryByText('Người dùng')).not.toBeInTheDocument()
    restore()
  })

  it('allows editing user permissions while keeping the full admin role locked', async () => {
    const restore = installApiMock((config) => {
      const url = config.url ?? ''
      if (url.endsWith('/me')) return {
        id: 'u1', display_name: 'Administrator', telegram_username: 'admin', status: 'active',
        timezone: 'Asia/Bangkok', permissions: ['roles.view', 'roles.manage'],
      }
      if (url.endsWith('/permissions')) return [
        { code: 'quick_report.view', description: 'View Quick Report', safe_for_default_user: true },
      ]
      if (url.endsWith('/roles')) return [
        { id: 'r-admin', key: 'admin', name: 'Administrator', description: 'Full application permissions', system: true, permissions: ['quick_report.view'], version: 1 },
        { id: 'r-user', key: 'user', name: 'User', description: 'Configured by an administrator', system: true, permissions: [], version: 1 },
      ]
      return []
    })
    renderAdmin('/admin/roles')
    const editButtons = await screen.findAllByRole('button', { name: 'Sửa quyền' })
    const [adminEdit, userEdit] = editButtons
    expect(adminEdit).toBeDisabled()
    expect(userEdit).toBeEnabled()
    await userEvent.click(userEdit as HTMLElement)
    expect(await screen.findByText(/Mọi tài khoản được kích hoạt/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /quick_report\.view/ })).not.toBeChecked()
    restore()
  })

  it('renders owned mail metadata and only secret presence flags', async () => {
    const restore = installApiMock((config) => {
      const url = config.url ?? ''
      let body: unknown
      if (url.endsWith('/me')) {
        body = { id: 'u1', display_name: 'Mailer', telegram_username: null, status: 'active', timezone: 'Asia/Bangkok', permissions: ['mail_accounts.view_own', 'mail_accounts.manage_own'] }
      } else if (url.includes('/mail-accounts')) {
        body = [{ id: 'm1', label: 'Abuse sender', smtp_host: 'mail.example.test', smtp_port: 465, smtp_username: 'sender@example.test', smtp_transport: 'implicit_tls', enabled: true, is_default: true, imap: { host: 'mail.example.test', port: 993, username: 'sender@example.test', mailbox: 'INBOX', sent_mailbox: 'Sent', junk_mailbox: 'Junk', cloudflare_mailbox: '2-Cloudflare', evidence_mailbox: '' }, proxy: { scheme: null, host: '', port: null }, signature: { contact_name: '', contact_email: '', signature_role: '' }, has_smtp_password: true, has_imap_password: false, has_proxy_credentials: false, version: 1 }]
      } else {
        body = { brand_name: '', contact_name: '', contact_email: '', company_name: '', business_registration_no: '', signature_role: '', logo_artifact_id: null, version: 1 }
      }
      return body
    })
    renderAdmin('/settings/mail')
    expect(await screen.findByText('Abuse sender')).toBeInTheDocument()
    expect(screen.getByText('SMTP password: đã lưu')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('********')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Thêm tài khoản' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Kiểm tra SMTP' })).not.toBeInTheDocument()
    restore()
  })

  it('runs an explicit mail connection test without sending credentials in the request', async () => {
    const requests: Array<{ url: string; method?: string; data?: unknown }> = []
    const restore = installApiMock((config) => {
      const url = config.url ?? ''
      requests.push({ url, ...(config.method ? { method: config.method } : {}), data: config.data as unknown })
      let body: unknown
      if (url.endsWith('/me')) {
        body = { id: 'u1', display_name: 'Tester', telegram_username: null, status: 'active', timezone: 'Asia/Bangkok', permissions: ['mail_accounts.view_own', 'mail_accounts.test_own'] }
      } else if (url.endsWith('/mail-accounts/m1/test-smtp')) {
        body = { status: 'success', protocol: 'smtp', transport: 'starttls', code: 'authenticated', message: 'Connection and authentication succeeded', elapsed_ms: 12 }
      } else if (url.includes('/mail-accounts')) {
        body = [{ id: 'm1', label: 'Test sender', smtp_host: 'mail.example.test', smtp_port: 587, smtp_username: 'sender@example.test', smtp_transport: 'starttls', enabled: true, is_default: true, imap: { host: '', port: 993, username: '', mailbox: 'INBOX', sent_mailbox: 'Sent', junk_mailbox: 'Junk', cloudflare_mailbox: '2-Cloudflare', evidence_mailbox: '' }, proxy: { scheme: null, host: '', port: null }, signature: { contact_name: '', contact_email: '', signature_role: '' }, has_smtp_password: true, has_imap_password: false, has_proxy_credentials: false, version: 1 }]
      } else {
        body = { brand_name: '', contact_name: '', contact_email: '', company_name: '', business_registration_no: '', signature_role: '', logo_artifact_id: null, version: 1 }
      }
      return body
    })
    renderAdmin('/settings/mail')
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra SMTP' }))
    expect(await screen.findByText(/SMTP: Connection and authentication succeeded/)).toBeInTheDocument()
    const action = requests.find((request) => request.url.endsWith('/mail-accounts/m1/test-smtp'))
    expect(action?.method?.toUpperCase()).toBe('POST')
    expect(action?.data).toBeUndefined()
    restore()
  })
})

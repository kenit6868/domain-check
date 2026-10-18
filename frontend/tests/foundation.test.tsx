import { Theme } from '@radix-ui/themes'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRouter } from '../src/app/router/AppRouter'
import { ApiError } from '../src/lib/http/errors'
import { installApiMock, installNetworkFailure } from './httpMock'

function Location() { return <span data-testid="location">{useLocation().pathname}</span> }
function renderApp(path = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><Theme><MemoryRouter initialEntries={[path]}><AppRouter /><Location /></MemoryRouter></Theme></QueryClientProvider>)
}
const me = { id: '11111111-1111-4111-8111-111111111111', display_name: 'Operator', telegram_username: 'operator', status: 'active', timezone: 'Asia/Bangkok', permissions: ['quick_report.view'] }
function anonymousMock(configured = true) {
  return installApiMock((config) => {
    if (config.url === '/me') throw new ApiError('Not authenticated', 401)
    if (config.url === '/auth/telegram/status') return { configured, issuer: 'https://oauth.telegram.org', redirect_uri: 'https://app.test/api/v1/auth/telegram/callback' }
    if (config.url === '/health/live') return { status: 'ok', service: 'domaincheck-api' }
    throw new Error('Unexpected request')
  })
}
describe('login routing and dashboard', () => {
  it('shows a missing page with a Dashboard recovery link for authenticated users', async () => {
    const restore = installApiMock(() => me)
    renderApp('/missing-page')
    expect(await screen.findByRole('heading', { name: 'Trang này không tồn tại' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Về Dashboard/ })).toHaveAttribute('href', '/')
    expect(screen.getByTestId('location')).toHaveTextContent('/missing-page')
    restore()
  })
  it('shows a permission recovery screen without exposing the restricted page', async () => {
    const restore = installApiMock(() => me)
    renderApp('/admin/roles')
    expect(await screen.findByRole('heading', { name: 'Truy cập bị giới hạn' })).toBeInTheDocument()
    expect(screen.getByText('403 · TRUY CẬP HẠN CHẾ')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Về Dashboard/ })).toHaveAttribute('href', '/')
    restore()
  })
  afterEach(() => { cleanup(); vi.restoreAllMocks() })
  it.each(['/', '/quick-report', '/settings/mail', '/admin/users', '/admin/roles', '/unknown'])('redirects anonymous %s to the dedicated login', async (path) => {
    const restore = anonymousMock()
    renderApp(path)
    const login = await screen.findByRole('link', { name: 'Đăng nhập bằng Telegram' })
    expect(login).toHaveAttribute('href', '/api/v1/auth/telegram/start?return_path=/')
    expect(screen.getByTestId('location')).toHaveTextContent('/login')
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
    restore()
  })
  it('disables Telegram when configuration is missing', async () => {
    const restore = anonymousMock(false)
    renderApp('/login')
    expect(await screen.findByRole('alert')).toHaveTextContent('Đăng nhập Telegram chưa được cấu hình')
    expect(screen.queryByRole('link', { name: 'Đăng nhập bằng Telegram' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Telegram chưa sẵn sàng' })).toBeDisabled()
    restore()
  })
  it('shows a protected dashboard at root without a login button', async () => {
    const restore = installApiMock((config) => { if (config.url === '/me') return me; throw new Error('Unexpected request') })
    renderApp()
    expect(await screen.findByRole('heading', { name: 'Chào Operator' })).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/)
    expect(screen.queryByRole('link', { name: 'Đăng nhập bằng Telegram' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Người dùng' })).not.toBeInTheDocument()
    restore()
  })
  it('redirects an authenticated visitor from login to dashboard', async () => {
    const restore = installApiMock(() => me)
    renderApp('/login')
    expect(await screen.findByRole('heading', { name: 'Chào Operator' })).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/)
    restore()
  })
  it('keeps pending users gated without a redirect loop', async () => {
    const restore = installApiMock(() => ({ ...me, status: 'pending', permissions: [] }))
    renderApp('/login')
    expect(await screen.findByRole('heading', { name: 'Tài khoản đang chờ duyệt' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Chào Operator' })).not.toBeInTheDocument()
    restore()
  })
  it('shows session errors without treating an outage as anonymous', async () => {
    const restore = installNetworkFailure()
    renderApp('/quick-report')
    expect(await screen.findByRole('heading', { name: 'Chưa thể tải phiên đăng nhập' })).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/quick-report')
    restore()
  })
  it('shows a safe callback error on login', async () => {
    const restore = anonymousMock()
    renderApp('/login?login_error=telegram_denied')
    expect(await screen.findByRole('alert')).toHaveTextContent('Bạn đã huỷ đăng nhập Telegram')
    restore()
  })
})

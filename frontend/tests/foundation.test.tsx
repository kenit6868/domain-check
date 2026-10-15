import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppRouter } from '../src/app/router/AppRouter'
import { installApiMock, installNetworkFailure } from './httpMock'

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AppRouter />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('foundation shell', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('shows the real API status without exposing removed menus', async () => {
    const restore = installApiMock((config) => config.url === '/auth/telegram/status'
      ? { configured: true, issuer: 'https://oauth.telegram.org', redirect_uri: 'https://app.test/api/v1/auth/telegram/callback' }
      : { status: 'ok', service: 'domaincheck-api' })
    renderApp()

    expect(await screen.findByText('Operational')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Đăng nhập bằng Telegram' })).toHaveAttribute('href', '/api/v1/auth/telegram/start?return_path=/app')
    expect(screen.queryByText('Thống kê email')).not.toBeInTheDocument()
    expect(screen.queryByText('Thống kê tổng quát')).not.toBeInTheDocument()
    expect(screen.queryByText('Case Log')).not.toBeInTheDocument()
    expect(screen.queryByText('Report Drafts')).not.toBeInTheDocument()
    restore()
  })

  it('explains missing Telegram configuration instead of starting a broken flow', async () => {
    const restore = installApiMock((config) => config.url === '/auth/telegram/status'
      ? { configured: false, issuer: 'https://oauth.telegram.org', redirect_uri: 'http://localhost:8000/api/v1/auth/telegram/callback' }
      : { status: 'ok', service: 'domaincheck-api' })
    renderApp()

    expect(await screen.findByRole('alert')).toHaveTextContent('Backend chưa có Telegram Client ID/Secret')
    expect(screen.queryByRole('link', { name: 'Đăng nhập bằng Telegram' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Telegram chưa sẵn sàng' })).toBeDisabled()
    restore()
  })

  it('fails closed when the API is unavailable', async () => {
    const restore = installNetworkFailure()
    renderApp()

    expect(
      await screen.findByRole('alert', {}, { timeout: 3_000 }),
    ).toHaveTextContent('Không có thao tác điều tra')
    restore()
  })
})


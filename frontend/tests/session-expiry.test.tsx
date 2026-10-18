import { Theme } from '@radix-ui/themes'
import { focusManager, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it } from 'vitest'
import { AppRouter } from '../src/app/router/AppRouter'
import { ApiError } from '../src/lib/http/errors'
import { createQueryClient } from '../src/lib/query/client'
import { queryKeys } from '../src/lib/query/keys'
import { installApiMock } from './httpMock'
import { useCurrentUser } from '../src/features/auth/hooks/useCurrentUser'

afterEach(cleanup)

it('shows the Telegram handle and all actual roles beneath the header display name', async () => {
  const client = createQueryClient()
  client.setQueryData(queryKeys.me, { id: 'owner', display_name: 'Header User', telegram_username: '@operator', status: 'active', timezone: 'Asia/Bangkok', permissions: ['roles.view'], roles: ['Administrator', 'Reviewer'] })
  const restore = installApiMock(() => [])
  try {
    render(<QueryClientProvider client={client}><Theme><MemoryRouter initialEntries={['/admin/roles']}><AppRouter /></MemoryRouter></Theme></QueryClientProvider>)
    const header = await screen.findByRole('banner')
    expect(header).toHaveTextContent('Header User')
    expect(header).toHaveTextContent('@operator')
    expect(header).not.toHaveTextContent('@@operator')
    expect(screen.getByLabelText('Role')).toHaveTextContent('AdministratorReviewer')
    expect(screen.getByTitle('Administrator')).toHaveClass('rounded-full')
  } finally { cleanup(); client.clear(); restore() }
})

it('refreshes permissions on tab focus while keeping the same signed-in user', async () => {
  const client = createQueryClient()
  let permissions = ['link_status.view', 'link_status.check']
  const restore = installApiMock(() => ({ id: 'owner', display_name: 'User', status: 'active', timezone: 'Asia/Bangkok', telegram_username: null, permissions }))
  try {
    const { result } = renderHook(useCurrentUser, { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> })
    await waitFor(() => { expect(result.current.data?.permissions).toContain('link_status.check') })
    permissions = ['link_status.view']
    client.getQueryCache().find({ queryKey: queryKeys.me })?.setState({ dataUpdatedAt: Date.now() - 60_000 })
    act(() => { focusManager.setFocused(false); focusManager.setFocused(true) })
    await waitFor(() => { expect(result.current.data?.permissions).toEqual(['link_status.view']) })
    expect(result.current.data?.id).toBe('owner')
    expect(result.current.isSuccess).toBe(true)
  } finally { cleanup(); client.clear(); restore(); focusManager.setFocused(undefined) }
})

it('redirects a cached authenticated session after a protected query returns 401', async () => {
  const client = createQueryClient()
  client.setQueryData(queryKeys.me, { id: 'owner', display_name: 'Admin', status: 'active', timezone: 'Asia/Bangkok', permissions: [], telegram_username: null })
  const restore = installApiMock((config) => {
    if (config.url?.includes('health')) return { status: 'ok' }
    if (config.url?.includes('telegram')) return { configured: true }
    throw new ApiError('Session authorization is stale', 401)
  })
  try {
    await expect(client.query({ queryKey: ['admin', 'roles'], queryFn: () => Promise.reject(new ApiError('Session authorization is stale', 401)) })).rejects.toThrow()
    render(<QueryClientProvider client={client}><Theme><MemoryRouter initialEntries={['/admin/roles']}><AppRouter /></MemoryRouter></Theme></QueryClientProvider>)
    expect(await screen.findByText(/Vui lòng đăng nhập lại để sử dụng quyền hiện tại/)).toBeVisible()
    expect(screen.queryByText('Session authorization is stale')).not.toBeInTheDocument()
  } finally { cleanup(); client.clear(); restore() }
})

it('expires the cached session on a mutation 401 but preserves it on permission denied', async () => {
  const client = createQueryClient()
  client.setQueryData(queryKeys.me, { id: 'owner' })
  for (const status of [403, 401]) {
    const mutation = client.getMutationCache().build(client, { mutationFn: () => Promise.reject(new ApiError('denied', status)), meta: { silentError: true } })
    await expect(mutation.execute(undefined)).rejects.toThrow()
    expect(client.getQueryState(queryKeys.me)?.status).toBe(status === 401 ? 'error' : 'success')
  }
  client.clear()
})

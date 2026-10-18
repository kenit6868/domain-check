import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { WorkerHealthNotice } from '../src/components/shared/WorkerHealthNotice'
import { installApiMock, installNetworkFailure } from './httpMock'

let restore: (() => void) | undefined
afterEach(() => { cleanup(); restore?.(); vi.restoreAllMocks() })
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><WorkerHealthNotice /></QueryClientProvider>)
}

it('shows no notice when all permitted workers are ready', async () => {
  let requested = false
  restore = installApiMock(() => { requested = true; return { services: [{ name: 'quick_report', state: 'ready', slots: 2 }] } })
  show()
  await waitFor(() => { expect(requested).toBe(true) })
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it.each(['offline', 'draining', 'unknown'])('explains waiting jobs when worker is %s', async (state) => {
  restore = installApiMock(() => ({ services: [{ name: 'quick_report', state, slots: 0 }] }))
  show()
  expect(await screen.findByRole('status')).toHaveTextContent('Quick Report')
  expect(screen.getByRole('status')).toHaveTextContent('không tạo lại job')
})

it('shows safe status when the health request fails', async () => {
  restore = installNetworkFailure()
  show()
  expect(await screen.findByRole('status')).toHaveTextContent('Chưa thể kiểm tra dịch vụ xử lý')
})

it('refreshes worker metadata when the user or permission scope changes', async () => {
  let calls = 0
  restore = installApiMock(() => ({ services: [{ name: 'quick_report', state: ++calls === 1 ? 'offline' : 'ready', slots: calls === 1 ? 0 : 2 }] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><WorkerHealthNotice scope="user-a:quick_report.view" /></QueryClientProvider>)
  expect(await screen.findByRole('status')).toHaveTextContent('Quick Report')
  view.rerender(<QueryClientProvider client={client}><WorkerHealthNotice scope="user-b:check_domain.view" /></QueryClientProvider>)
  await waitFor(() => { expect(calls).toBe(2); expect(screen.queryByRole('status')).not.toBeInTheDocument() })
})

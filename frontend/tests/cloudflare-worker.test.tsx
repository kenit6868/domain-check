import { Theme } from '@radix-ui/themes'
import { QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { CloudflareWorkerPage } from '../src/features/cloudflare-worker/CloudflareWorkerPage'
import { CloudflareDashboardSettings } from '../src/features/mail-settings/components/CloudflareDashboardSettings'
import { createQueryClient } from '../src/lib/query/client'
import { installApiMock } from './httpMock'

const id = '11111111-1111-4111-8111-111111111111'
const me = { id, permissions: ['view', 'precheck', 'start', 'stop', 'resume', 'retry', 'clear_cache'].map((action) => `cloudflare_worker.${action}`) }
const batch = { id, day: '2026-10-17', phase: 'CHECKED', urls: ['https://example.com/a'], invalid: [], duplicates: [], processed: 1, current_id: null, error: null, delay_seconds: 30 }
const row = { id: 'record-one', url: 'https://example.com/a', state: 'READY', attempts: 0, error: null, report_id: null, draft: 'Exact approved phishing report for https://example.com/a' }
function mount(data = me) {
  const client = createQueryClient()
  const view = render(<QueryClientProvider client={client}><Theme><MemoryRouter><Routes><Route element={<Outlet context={{ me: data }} />}><Route index element={<CloudflareWorkerPage />} /></Route></Routes></MemoryRouter></Theme></QueryClientProvider>)
  return { ...view, client }
}
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('prechecks exact URLs, previews drafts and requires explicit Cookie and confirmation to send', async () => {
  let prepared = false
  let phase = 'CHECKED'
  const calls: Array<{ url: string; data: unknown }> = []
  const restore = installApiMock((config) => {
    if (config.method === 'get') return { batch: prepared ? { ...batch, phase } : null, records: prepared ? [row] : [], submit_enabled: true }
    calls.push({ url: String(config.url), data: config.data ? JSON.parse(String(config.data)) : null })
    if (config.url?.endsWith('/precheck')) { prepared = true; return {} }
    if (config.url?.endsWith('/start')) { phase = 'SENDING'; return {} }
    throw new Error('Unexpected mutation')
  })
  try {
    mount()
    const input = await screen.findByLabelText('Danh sách domain/URL')
    await waitFor(() => { expect(input).toBeEnabled() })
    await userEvent.type(input, 'https://example.com/a')
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra Cloudflare' }))
    expect(await screen.findByText('Sẵn sàng')).toBeVisible()
    expect(calls[0]?.data).toEqual({ urls: ['https://example.com/a'] })
    await userEvent.click(screen.getByText('Xem nội dung report'))
    expect(screen.getByText(row.draft)).toBeVisible()
    const start = screen.getByRole('button', { name: 'Bắt đầu gửi' })
    expect(start).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Cookie Cloudflare'), 'fixture-cookie')
    expect(start).toBeDisabled()
    await userEvent.click(screen.getByLabelText('Tôi đã kiểm tra nội dung report và xác nhận gửi.'))
    await userEvent.click(start)
    await waitFor(() => { expect(calls[1]?.data).toEqual({ cookie: 'fixture-cookie', confirmed: true, delay_seconds: 30 }) })
    expect(screen.getByLabelText('Cookie Cloudflare')).toHaveValue('')
    expect(localStorage.getItem('cookie')).toBeNull()
    expect(sessionStorage.getItem('cookie')).toBeNull()
  } finally { restore() }
})

it('restores a running batch on reload and pauses/resumes without restarting accepted rows', async () => {
  let phase = 'SENDING'
  const calls: string[] = []
  const restore = installApiMock((config) => {
    if (config.method === 'get') return { batch: { ...batch, phase }, records: [{ ...row, state: 'SUBMITTED', attempts: 1, report_id: 'report-123' }, { ...row, id: 'two', state: 'QUEUED' }], submit_enabled: true }
    calls.push(String(config.url))
    phase = config.url?.endsWith('/pause') ? 'PAUSED' : 'SENDING'
    return {}
  })
  try {
    mount()
    expect(await screen.findByLabelText('Danh sách domain/URL')).toBeDisabled()
    expect(await screen.findByText('report-123')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Tạm dừng' }))
    expect(await screen.findByText('Đã tạm dừng')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Tiếp tục' })).toBeDisabled()
    await userEvent.click(screen.getByLabelText('Tôi đã kiểm tra nội dung report và xác nhận gửi.'))
    await userEvent.click(screen.getByRole('button', { name: 'Tiếp tục' }))
    await waitFor(() => { expect(calls).toEqual([`/cloudflare-worker/${id}/pause`, `/cloudflare-worker/${id}/resume`]) })
  } finally { restore() }
})

it('shows unknown reports as locked and exposes failed-only retry after completion', async () => {
  const calls: unknown[] = []
  const restore = installApiMock((config) => {
    if (config.method === 'get') return { batch: { ...batch, phase: 'COMPLETED' }, records: [{ ...row, state: 'UNKNOWN' }, { ...row, id: 'two', state: 'FAILED' }], submit_enabled: true }
    calls.push(JSON.parse(String(config.data))); return {}
  })
  try {
    mount()
    expect(await screen.findByText('URL chưa rõ kết quả được khóa gửi lại. Hãy đối soát trên Cloudflare.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Bắt đầu gửi' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Cookie Cloudflare'), 'fixture')
    await userEvent.click(screen.getByLabelText('Tôi đã kiểm tra nội dung report và xác nhận gửi.'))
    await userEvent.click(screen.getByRole('button', { name: 'Thử lại URL lỗi' }))
    await waitFor(() => { expect(calls).toHaveLength(1) })
  } finally { restore() }
})

it('does not enable send while server flag is off or view permission is missing', async () => {
  const restore = installApiMock(() => ({ batch, records: [row], submit_enabled: false }))
  try {
    const view = mount()
    expect(await screen.findByText('Gửi Cloudflare đang tắt trên backend. Bạn vẫn có thể kiểm tra và xem nội dung report.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Bắt đầu gửi' })).toBeDisabled()
    view.unmount()
    mount({ ...me, permissions: [] })
    expect(screen.getByRole('alert')).toHaveTextContent('Bạn không có quyền xem Cloudflare Worker.')
  } finally { restore() }
})

it('saves Cloudflare Account ID independently from SMTP settings and rejects malformed IDs', async () => {
  let saved = ''
  const restore = installApiMock((config) => {
    if (config.method === 'put') { saved = (JSON.parse(String(config.data)) as { account_id: string }).account_id }
    return { account_id: saved }
  })
  try {
    render(<QueryClientProvider client={createQueryClient()}><CloudflareDashboardSettings disabled={false} /></QueryClientProvider>)
    const input = await screen.findByLabelText('Cloudflare Account ID')
    await waitFor(() => { expect(input).toBeEnabled() })
    await userEvent.type(input, 'invalid')
    expect(screen.getByRole('button', { name: 'Lưu cấu hình Cloudflare' })).toBeDisabled()
    await userEvent.clear(input)
    await userEvent.type(input, 'a'.repeat(32))
    await userEvent.click(screen.getByRole('button', { name: 'Lưu cấu hình Cloudflare' }))
    expect(await screen.findByText('Đã lưu Cloudflare Account ID.')).toBeVisible()
    expect(saved).toBe('a'.repeat(32))
  } finally { restore() }
})

it('hides precheck exclusions until requested and keeps accepted batch rows visible', async () => {
  const restore = installApiMock(() => ({ batch, submit_enabled: true, records: [
    { ...row, state: 'SUBMITTED', report_id: 'kept-in-batch' },
    { ...row, id: 'excluded', url: 'https://excluded.com/', state: 'NOT_CLOUDFLARE', excluded: true, draft: '' },
  ] }))
  try {
    mount()
    expect(await screen.findByText('kept-in-batch')).toBeVisible()
    expect(screen.queryByText('Không dùng Cloudflare', { exact: true })).not.toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('Hiện URL bị loại (1)'))
    expect(screen.getByText('Không dùng Cloudflare', { exact: true })).toBeVisible()
  } finally { restore() }
})

it('shows measured precheck progress, current URL and completion from backend snapshots', async () => {
  const checking = { ...batch, phase: 'CHECKING', processed: 1, urls: ['https://example.com/a', 'https://example.com/b', 'https://example.com/c'], current_id: 'https://example.com/b' }
  const restore = installApiMock(() => ({ batch: checking, records: [row], submit_enabled: true }))
  try {
    const view = mount()
    const region = await screen.findByRole('region', { name: 'Tiến độ kiểm tra Cloudflare' })
    expect(within(region).getByRole('progressbar')).toHaveAttribute('value', '1')
    expect(within(region).getByRole('progressbar')).toHaveAttribute('max', '3')
    expect(within(region).getByText('33%')).toBeVisible()
    expect(within(region).getByText('https://example.com/b')).toBeVisible()
    act(() => { view.client.setQueryData(['cloudflare-worker', id], { batch: { ...checking, phase: 'CHECKED', processed: 3, current_id: null }, records: [row], submit_enabled: true }) })
    await waitFor(() => { expect(within(region).getByRole('progressbar')).toHaveAttribute('value', '3') })
    expect(within(region).getByText('100%')).toBeVisible()
    expect(within(region).getByRole('status')).toHaveTextContent('Đã hoàn tất kiểm tra Cloudflare')
  } finally { restore() }
})

it('shows indeterminate preparation immediately on click without inventing a percentage', async () => {
  let release: (() => void) | undefined
  const restore = installApiMock(async (config) => {
    if (config.method === 'get') return { batch: null, records: [], submit_enabled: true }
    await new Promise<void>((resolve) => { release = resolve })
    return {}
  })
  try {
    mount()
    const input = await screen.findByLabelText('Danh sách domain/URL')
    await waitFor(() => { expect(input).toBeEnabled() })
    await userEvent.type(input, 'example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra Cloudflare' }))
    const region = screen.getByRole('region', { name: 'Tiến độ kiểm tra Cloudflare' })
    expect(within(region).getByRole('progressbar')).not.toHaveAttribute('value')
    expect(within(region).getByRole('status')).toHaveTextContent('Đang chuẩn bị kiểm tra')
    expect(within(region).queryByText('0%')).not.toBeInTheDocument()
    await act(async () => { release?.(); await Promise.resolve() })
  } finally { restore() }
})

it('displays the Account ID bound by the backend even when settings were not filled manually', async () => {
  const accountId = 'b'.repeat(32)
  const restore = installApiMock(() => ({ batch: { ...batch, phase: 'COMPLETED', account_id: accountId }, records: [{ ...row, state: 'SUBMITTED' }], submit_enabled: true }))
  try {
    mount()
    expect(await screen.findByText(accountId)).toBeVisible()
    expect(screen.getByText(/Account ID đã chốt cho batch:/)).toBeVisible()
  } finally { restore() }
})

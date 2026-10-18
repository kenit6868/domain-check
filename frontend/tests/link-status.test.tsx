import { Theme } from '@radix-ui/themes'
import { chooseSelect } from './selectHelper'
import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { LinkStatusPage } from '../src/features/link-status/LinkStatusPage'
import { createQueryClient } from '../src/lib/query/client'

const me = { id: 'owner', permissions: ['link_status.view', 'link_status.check'] }
const row = { url: 'https://hold.example.com/a', domain: 'example.com', status: 'DOMAIN_HOLD', domain_status: 'DOMAIN_HOLD', website_status: 'UNKNOWN', http_code: null, final_url: '', provider: '', reason: 'dns_nxdomain', checked_at: '2026-10-18T00:00:00Z', redirects: [], registration: { state: 'HOLD', source: 'WHOIS', statuses: ['serverHold'], hold_flags: ['serverhold'] }, dns: { state: 'NXDOMAIN', records: {} } }
const rows = [row, { ...row, url: 'https://challenge.example.com', status: 'CF_CHALLENGE', domain_status: 'DNS_OK', website_status: 'CF_CHALLENGE', http_code: 403, provider: 'Cloudflare' }, { ...row, url: 'https://live.example.com', status: 'HTTP_OK', domain_status: 'DNS_OK', website_status: 'HTTP_OK', http_code: 200 }]
const batch = { id: 'run', phase: 'COMPLETED', urls: rows.map((r) => r.url), results: rows, processed: 3, total: 3, invalid_count: 0, duplicate_count: 0, started_at: '2026-10-18T00:00:00Z' }
function mount(permissions = me.permissions) {
  return render(<QueryClientProvider client={createQueryClient()}><Theme><MemoryRouter><Routes><Route element={<Outlet context={{ me: { ...me, permissions } }} />}><Route index element={<LinkStatusPage />} /></Route></Routes></MemoryRouter></Theme></QueryClientProvider>)
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

function streamResponse(value = batch) {
  return new Response([
    JSON.stringify({ type: 'start', batch: { ...value, phase: 'CHECKING', results: [], processed: 0 } }),
    ...value.results.map((result, index) => JSON.stringify({ type: 'result', index, result, processed: index + 1 })),
    JSON.stringify({ type: 'complete', processed: value.processed }),
  ].join('\n') + '\n', { headers: { 'Content-Type': 'application/x-ndjson' } })
}
async function check() {
  await userEvent.type(screen.getByLabelText('Danh sách domain/URL'), 'example.com')
  await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra trạng thái' }))
  await screen.findByText('Đã kiểm tra 3/3')
}

it('starts empty without a workspace request, filters results and clears on remount', async () => {
  const fetcher = vi.fn(() => Promise.resolve(streamResponse()))
  vi.stubGlobal('fetch', fetcher)
  mount()
  expect(fetcher).not.toHaveBeenCalled()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  await check()
  const table = screen.getByRole('table')
  const summary = screen.getByRole('group', { name: 'Tổng hợp trạng thái' })
  expect(within(summary).getByText('Domain Hold').parentElement).toHaveTextContent('1')
  expect(within(summary).getByText('Lỗi DNS').parentElement).toHaveTextContent('0')
  expect(within(summary).getByText('CF Phishing').parentElement).toHaveTextContent('0')
  const copy = within(table).getByRole('button', { name: `Sao chép: ${row.url}` })
  expect(copy).toHaveTextContent('')
  expect(within(table).getAllByRole('row')).toHaveLength(4)
  await chooseSelect(screen.getByLabelText('Lọc trạng thái tên miền'), 'DNS_OK')
  await chooseSelect(screen.getByLabelText('Lọc trạng thái website'), 'CF_CHALLENGE')
  expect(within(table).getAllByRole('row')).toHaveLength(2)
  expect(within(summary).getByText('Domain Hold').parentElement).toHaveTextContent('1')
  await userEvent.click(screen.getByRole('button', { name: 'Xóa bộ lọc' }))
  expect(within(table).getAllByRole('row')).toHaveLength(4)
  cleanup(); mount()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('rechecks issue a fresh request each time and respect check permission', async () => {
  const fetcher = vi.fn(() => Promise.resolve(streamResponse()))
  vi.stubGlobal('fetch', fetcher)
  mount(); await check()
  await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra lại danh sách' }))
  await waitFor(() => { expect(fetcher).toHaveBeenCalledTimes(2) })
  await screen.findByText('Đã kiểm tra 3/3')
  cleanup(); mount(['link_status.view'])
  await userEvent.type(screen.getByLabelText('Danh sách domain/URL'), 'example.com')
  expect(screen.getByRole('button', { name: 'Kiểm tra trạng thái' })).toBeDisabled()
})

it('keeps partial progress on a truncated stream and allows another check', async () => {
  const partial = JSON.stringify({ type: 'start', batch: { ...batch, results: [], processed: 0, phase: 'CHECKING' } }) + '\n' + JSON.stringify({ type: 'result', index: 0, result: row, processed: 1 }) + '\n'
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(partial))))
  mount()
  await userEvent.type(screen.getByLabelText('Danh sách domain/URL'), 'example.com')
  await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra trạng thái' }))
  expect(await screen.findByRole('alert')).toBeVisible()
  expect(screen.getByRole('progressbar')).toHaveAttribute('value', '1')
  expect(screen.getByRole('button', { name: 'Kiểm tra lại danh sách' })).toBeEnabled()
})

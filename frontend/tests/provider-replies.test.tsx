import { Theme } from '@radix-ui/themes'
import { chooseSelect } from './selectHelper'
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { AppRouter } from '../src/app/router/AppRouter'
import { createQueryClient } from '../src/lib/query/client'
import { installApiMock } from './httpMock'

const owner = '11111111-1111-4111-8111-111111111111'
Element.prototype.scrollIntoView = vi.fn()
const account = '22222222-2222-4222-8222-222222222222'
const caseId = '33333333-3333-4333-8333-333333333333'
const plan = '44444444-4444-4444-8444-444444444444'
const replyBody = 'Exact English reply with reviewed evidence.'
const me = { id: owner, display_name: 'Operator', status: 'active', timezone: 'Asia/Bangkok',
  permissions: ['provider_replies.view','provider_replies.sync','provider_replies.preview','provider_replies.send','provider_replies.mark_seen'] }
const row = { id: caseId, account_id: account, state: 'pending', subject: 'Please supply evidence', sender: 'abuse@cloudflare.com',
  provider: 'cloudflare', provider_label: 'Cloudflare', request_label: 'Ảnh bằng chứng', needs_reply: true, linked_delivery_id: plan,
  received_at: '2026-10-17T01:00:00Z', location: { mailbox: 'INBOX' }, seen: false, requested_url: 'https://example.com/' }
const exact = { id: plan, revision: 1, fingerprint: 'a'.repeat(64), expires_at: '2100-01-01T00:00:00Z', deliveries: [
  { sender_account_id: account, recipient: 'abuse@cloudflare.com', subject: 'Re: Evidence', text_body: replyBody, in_reply_to: '<provider@example.com>' }] }
const base = { id: caseId, account_id: account, state: 'pending', matched: true, reply_supported: true, reply_block_reason: null, match_reason: 'message_id', needs_reply: true, seen: false,
  sender_email: 'sender@example.com', suggestion: { subject: 'Re: Evidence', body: 'English reference draft' }, reading_aid: 'NCC yêu cầu bổ sung bằng chứng.',
  mail: { subject: row.subject, sender: row.sender, reply_to: 'abuse@cloudflare.com', body: 'Please provide a screenshot.', provider: 'cloudflare', provider_label: 'Cloudflare', request_label: row.request_label,
    risk: 'review', request_type: 'screenshot', channel: 'email', ticket: 'DYN-1', message_id: '<provider@example.com>', source_mailbox: 'INBOX' },
  context: { reported_url: 'https://example.com/', official_url: '', evidence: '' }, images: [], preview: null, attempt_count: 0, error_code: null, sent_copy_status: null }
function view(selected = true) {
  return render(<QueryClientProvider client={createQueryClient()}><Theme><MemoryRouter initialEntries={[`/provider-replies?account=${account}${selected ? `&case=${caseId}` : ''}`]}><AppRouter /></MemoryRouter></Theme></QueryClientProvider>)
}
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('restores live folder counts after reload without starting another sync', async () => {
  const posts: string[] = []
  const restore = installApiMock((config) => {
    if (config.method === 'post') posts.push(config.url ?? '')
    if (config.url === '/me') return me
    if (config.url === '/provider-replies/accounts') return [{ id: account, sender_email: 'sender@example.com', enabled: true, imap_configured: true }]
    if (config.url?.startsWith('/provider-replies/sync-progress?')) return { progress: { sync_id: plan, status: 'running', stage: 'reading', folder: 'INBOX', processed: 2, total: 10, folder_index: 1, folder_total: 5, updated_at: '2026-10-17T01:00:00Z', folders: [] } }
    if (config.url?.startsWith('/provider-replies?')) return { items: [], last_sync: null }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view(false)
    expect(await screen.findByText('Đang đồng bộ · INBOX')).toBeVisible()
    const bar = screen.getByRole('progressbar', { name: 'Tiến độ thư mục hiện tại' })
    expect(bar).toHaveAttribute('value', '2')
    expect(bar).toHaveAttribute('max', '10')
    expect(screen.getByRole('button', { name: 'Đồng bộ phản hồi' })).toBeDisabled()
    expect(posts).toEqual([])
  } finally { restore() }
})

it('filters providers and enables Cloudflare capture without web delivery history', async () => {
  let captured = false
  const restore = installApiMock((config) => {
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'check_domain.capture'] }
    if (config.url === '/provider-replies/accounts') return [{ id: account, sender_email: 'sender@example.com', enabled: true, imap_configured: true }]
    if (config.url?.startsWith('/provider-replies/sync-progress?')) return { progress: null }
    if (config.url?.startsWith('/provider-replies?')) return { items: [row, { ...row, id: plan, provider: 'dynadot', provider_label: 'Dynadot', subject: 'Other provider request' }], last_sync: null }
    if (config.url === `/provider-replies/${caseId}`) return { ...base, matched: false }
    if (config.url === `/provider-replies/${caseId}/capture`) { expect(JSON.parse(String(config.data))).toMatchObject({ show_browser: false }); captured = true; return { ...base, matched: false } }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    const capture = await screen.findByRole('button', { name: 'Chụp URL nguồn và đích từ DOM' })
    expect(capture).toBeEnabled()
    expect(screen.queryByText('Other provider request')).not.toBeInTheDocument()
    await chooseSelect(screen.getByLabelText('Lọc NCC'), 'dynadot')
    expect(await screen.findByText('Other provider request')).toBeVisible()
    await userEvent.click(capture)
    expect(captured).toBe(true)
  } finally { restore() }
})

it('opens a case directly in the detail panel and returns to a bounded long list', async () => {
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url?.startsWith('/provider-replies/sync-progress?')) return { progress: null }
    if (config.url === '/provider-replies/accounts') return [{ id: account, sender_email: 'sender@example.com', enabled: true, imap_configured: true }]
    if (config.url?.startsWith('/provider-replies?')) return { items: Array.from({ length: 30 }, (_, index) => ({ ...row,
      id: index === 0 ? caseId : `33333333-3333-4333-8333-${String(index).padStart(12, '0')}` })), last_sync: null }
    if (config.url === `/provider-replies/${caseId}`) return { ...base, preview: exact }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view(false)
    const buttons = await screen.findAllByRole('button', { name: 'Mở thư' })
    const first = buttons[0]
    expect(first).toBeDefined()
    if (!first) throw new Error('Missing case action')
    await userEvent.click(first)
    expect(await screen.findByText(replyBody)).toBeVisible()
    expect(screen.getByRole('region', { name: 'Chi tiết case đang mở' })).toHaveFocus()
    const list = screen.getByLabelText('Danh sách phản hồi')
    expect(list).toHaveClass('max-h-80', 'overflow-auto')
    await userEvent.click(screen.getByRole('button', { name: 'Về danh sách (30)' }))
    expect(list).toHaveFocus()
    expect(screen.getByText(replyBody)).toBeVisible()
  } finally { restore() }
})

it('only reads cached data on load, syncs explicitly and retains per-folder errors', async () => {
  let synced = false
  const posts: string[] = []
  const restore = installApiMock((config) => {
    if (config.method === 'post') posts.push(config.url ?? '')
    if (config.url === '/me') return me
    if (config.url?.startsWith('/provider-replies/sync-progress?')) return { progress: null }
    if (config.url === '/provider-replies/accounts') return [{ id: account, sender_email: 'sender@example.com', enabled: true, imap_configured: true }]
    if (config.url?.startsWith('/provider-replies?')) return { items: synced ? [row] : [], last_sync: synced ? { synced_at: '2026-10-17T01:00:00Z', folders: [{ folder: 'Inbox', mailbox: 'INBOX', fetched: 1, matched: 1, error_code: null }, { folder: 'Cloudflare', mailbox: '2-Cloudflare', fetched: 0, matched: 0, error_code: 'mailbox_read_failed' }] } : null }
    if (config.url === '/provider-replies/sync') { synced = true; expect(JSON.parse(String(config.data))).toMatchObject({ account_id: account, unread_only: false }); return { imported: 1 } }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view(false)
    expect(await screen.findByText(/Không có phản hồi trong phạm vi/)).toBeVisible()
    expect(posts).toEqual([])
    await userEvent.click(screen.getByRole('button', { name: 'Đồng bộ phản hồi' }))
    expect(await screen.findByText(row.subject)).toBeVisible()
    expect(screen.getByText(/Cloudflare: 0\/0 · chưa đọc được/)).toBeVisible()
    expect(posts).toEqual(['/provider-replies/sync'])
  } finally { restore() }
})

it('uploads thumbnails, previews the exact thread and sends only after confirmation', async () => {
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:evidence')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  let prepared = false
  let sent = false
  const posts: string[] = []
  const restore = installApiMock((config) => {
    if (config.method === 'post') posts.push(config.url ?? '')
    if (config.url === '/me') return me
    if (config.url?.startsWith('/provider-replies/sync-progress?')) return { progress: null }
    if (config.url === '/provider-replies/accounts') return [{ id: account, sender_email: 'sender@example.com', enabled: true, imap_configured: true }]
    if (config.url?.startsWith('/provider-replies?')) return { items: [{ ...row, state: sent ? 'sent' : 'pending' }], last_sync: null }
    if (config.url === `/provider-replies/${caseId}`) return { ...base, preview: prepared ? exact : null }
    if (config.url === `/provider-replies/${caseId}/previews`) {
      expect(config.data).toBeInstanceOf(FormData)
      const form = config.data as FormData
      expect(form.getAll('files')).toHaveLength(1)
      prepared = true; return exact
    }
    if (config.url === `/provider-replies/${caseId}/send`) {
      expect(JSON.parse(String(config.data))).toEqual({ preview_id: plan, revision: 1, fingerprint: exact.fingerprint, confirmed: true })
      sent = true; return { ...base, state: 'sent', preview: exact, attempt_count: 1 }
    }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    const input = await screen.findByLabelText(/Ảnh bằng chứng \(/)
    const original = screen.getByText(/^Email gốc —/).closest('details')
    expect(original).not.toHaveAttribute('open')
    await userEvent.upload(input, new File(['fixture'], 'manual.png', { type: 'image/png' }))
    expect(screen.getByAltText('Ảnh đã chọn 1')).toHaveAttribute('src', 'blob:evidence')
    expect(screen.getByRole('button', { name: 'Gửi phản hồi đúng thread' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Tạo / cập nhật draft phản hồi' }))
    expect(await screen.findByText(replyBody)).toBeVisible()
    await userEvent.click(screen.getByRole('checkbox', { name: /Tôi đã đọc email gốc/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Gửi phản hồi đúng thread' }))
    expect(await screen.findByText('Đã gửi phản hồi. Hệ thống chặn gửi lại thư này.')).toBeVisible()
    expect(sent).toBe(true)
    const list = screen.getByLabelText('Danh sách phản hồi')
    expect(within(list).getByText('Đã trả lời')).toBeVisible()
    expect(within(list).getByRole('button', { name: 'Mở thư' })).toBeVisible()
    expect(posts).toEqual([`/provider-replies/${caseId}/previews`, `/provider-replies/${caseId}/send`])
  } finally { restore() }
})

it('restores the exact draft after reload, clears approval on edits and blocks unknown retries', async () => {
  let unknown = false
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url?.startsWith('/provider-replies/sync-progress?')) return { progress: null }
    if (config.url === '/provider-replies/accounts') return [{ id: account, sender_email: 'sender@example.com', enabled: true, imap_configured: true }]
    if (config.url?.startsWith('/provider-replies?')) return { items: [row], last_sync: null }
    if (config.url === `/provider-replies/${caseId}`) return { ...base, preview: exact, state: unknown ? 'unknown' : 'pending' }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    let mounted = view()
    await screen.findByText(replyBody)
    await userEvent.click(screen.getByRole('checkbox', { name: /Tôi đã đọc email gốc/ }))
    await userEvent.type(screen.getByLabelText('Website chính thức'), 'https://official.example.com/')
    expect(screen.getByRole('checkbox', { name: /Tôi đã đọc email gốc/ })).not.toBeChecked()
    expect(screen.queryByLabelText('Draft phản hồi sẽ gửi')).not.toBeInTheDocument()
    mounted.unmount(); unknown = true; mounted = view()
    expect(await screen.findByText(/Chưa rõ kết quả sau khi gửi/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Gửi phản hồi đúng thread' })).toBeDisabled()
    mounted.unmount()
  } finally { restore() }
})

it('keeps unmatched and portal messages readable with SMTP blocked', async () => {
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url?.startsWith('/provider-replies/sync-progress?')) return { progress: null }
    if (config.url === '/provider-replies/accounts') return [{ id: account, sender_email: 'sender@example.com', enabled: true, imap_configured: true }]
    if (config.url?.startsWith('/provider-replies?')) return { items: [row], last_sync: null }
    if (config.url === `/provider-replies/${caseId}`) return { ...base, matched: false, reply_supported: false, reply_block_reason: 'provider_not_supported', mail: { ...base.mail, channel: 'portal' } }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    expect(await screen.findByText(/NCC này chưa hỗ trợ phản hồi/)).toBeVisible()
    expect(screen.getByText(/NCC yêu cầu portal/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Tạo / cập nhật draft phản hồi' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Gửi phản hồi đúng thread' })).toBeDisabled()
  } finally { restore() }
})

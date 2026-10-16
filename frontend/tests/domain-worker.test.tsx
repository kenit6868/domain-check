import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { AppRouter } from '../src/app/router/AppRouter'
import { createQueryClient } from '../src/lib/query/client'
import { installApiMock } from './httpMock'

const userId = '11111111-1111-4111-8111-111111111111'
const operationId = '22222222-2222-4222-8222-222222222222'
const checkId = '33333333-3333-4333-8333-333333333333'
const caseId = '44444444-4444-4444-8444-444444444444'
const accountId = '55555555-5555-4555-8555-555555555555'
const secondAccount = '66666666-6666-4666-8666-666666666666'
const key = `domaincheck:v1:u:${userId}:domain-worker:operation`
const accounts = [{ id: accountId, label: 'Alice', sender_email: 'alice@example.com' },
  { id: secondAccount, label: 'Bob', sender_email: 'bob@example.com' }]
const me = { id: userId, display_name: 'Operator', status: 'active', telegram_username: 'operator', timezone: 'Asia/Bangkok',
  permissions: ['domain_worker.view', 'domain_worker.precheck', 'domain_worker.retry', 'cloaking_review.view',
    'domain_evidence_review.view', 'domain_evidence_review.upload'] }
const item = { id: checkId, item_id: checkId, requested_url: 'https://ready.example.com/login?q=1', domain: 'ready.example.com',
  status: 'succeeded', disposition: 'ready', recipients: [{ channel: 'registrar', email: 'abuse@example.com' }],
  cloaking_verdict: 'NO_SIGNAL', case_id: null, evidence_set_id: caseId, evidence_reason: null, browser_verification_pending: false }
const operation = { id: operationId, state: 'completed', stage: 'completed', business_day: '2026-10-16',
  sender_account_ids: [accountId], checks: [item] }
function view(path = '/domain-worker') { return render(<QueryClientProvider client={createQueryClient()}>
  <MemoryRouter initialEntries={[path]}><AppRouter /></MemoryRouter></QueryClientProvider>) }
afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks() })

it('selects accounts before precheck and keeps the exact account scope with the batch', async () => {
  const posts: unknown[] = []
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === '/domain-worker/prechecks') { posts.push(JSON.parse(String(config.data))); return operation }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return operation
    throw new Error(`Unexpected request ${config.url ?? ''}`)
  })
  try {
    view()
    expect(await screen.findByRole('checkbox', { name: /Alice/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Bob/ })).toBeChecked()
    await userEvent.click(screen.getByRole('checkbox', { name: /Bob/ }))
    await userEvent.type(screen.getByLabelText('Danh sách URL'), 'example.com\nhttps://example.com/login?q=1')
    await userEvent.click(screen.getByRole('button', { name: 'Precheck danh sách' }))
    expect(await screen.findByText('Đủ evidence')).toBeVisible()
    expect(posts).toEqual([{ targets: ['example.com', 'https://example.com/login?q=1'], sender_account_ids: [accountId] }])
    expect(screen.queryByRole('button', { name: /draft|preview/i })).not.toBeInTheDocument()
    expect(sessionStorage.getItem(key)).toBe(operationId)
  } finally { restore() }
})

it('keeps only ready domains in the normal list and routes cloaking/missing evidence to separate pages', async () => {
  sessionStorage.setItem(key, operationId)
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return { ...operation, checks: [item,
      { ...item, id: caseId, requested_url: 'https://cloak.example.com/', disposition: 'cloaking_review', case_id: caseId },
      { ...item, id: secondAccount, requested_url: 'https://missing.example.com/', disposition: 'evidence_review', evidence_set_id: null, case_id: secondAccount },
      { ...item, id: accountId, requested_url: 'https://no-email.example.com/', disposition: 'excluded_no_email', recipients: [] },
    ] }
    throw new Error('Unexpected request')
  })
  try {
    view(); expect(await screen.findByText(item.requested_url)).toBeVisible()
    expect(screen.queryByText('https://cloak.example.com/')).not.toBeInTheDocument()
    expect(screen.queryByText('https://missing.example.com/')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Cloaking Review (1)' })).toHaveAttribute('href', '/cloaking-review')
    expect(screen.getByRole('link', { name: 'Domain Evidence Review (1)' })).toHaveAttribute('href', '/domain-evidence-review')
    expect(screen.queryByRole('button', { name: /draft|preview/i })).not.toBeInTheDocument()
  } finally { restore() }
})

it('blocks precheck when no account is selected and preserves that selection after reload', async () => {
  const calls: string[] = []
  const restore = installApiMock((config) => {
    calls.push(`${config.method ?? ''} ${config.url ?? ''}`)
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    throw new Error('Unexpected request')
  })
  try {
    const first = view()
    await userEvent.click(await screen.findByRole('checkbox', { name: /Alice/ }))
    await userEvent.click(screen.getByRole('checkbox', { name: /Bob/ }))
    await userEvent.type(screen.getByLabelText('Danh sách URL'), 'example.com')
    expect(screen.getByRole('button', { name: 'Precheck danh sách' })).toBeDisabled()
    first.unmount(); view()
    expect(await screen.findByRole('checkbox', { name: /Alice/ })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Bob/ })).not.toBeChecked()
    expect(calls.some((call) => call.startsWith('post'))).toBe(false)
  } finally { restore() }
})

it('restores batch accounts by GET without changing them to the current form selection', async () => {
  sessionStorage.setItem(key, operationId)
  sessionStorage.setItem(`${key}:accounts`, JSON.stringify([secondAccount]))
  const calls: string[] = []
  const restore = installApiMock((config) => {
    calls.push(`${config.method ?? ''} ${config.url ?? ''}`)
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return operation
    throw new Error('Unexpected request')
  })
  try {
    view(); expect(await screen.findByText(/Tài khoản của lượt này: alice@example.com/)).toBeVisible()
    expect(screen.getByRole('checkbox', { name: /Bob/ })).toBeChecked()
    expect(calls.some((call) => call.startsWith('post'))).toBe(false)
  } finally { restore() }
})

it('blocks precheck without accounts or a complete organization profile', async () => {
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/accounts') return []
    if (config.url === '/domain-worker/readiness') return { ready: false, missing: ['brand_name'] }
    throw new Error('Unexpected request')
  })
  try { view(); await screen.findByText(/Cần hoàn thiện thương hiệu/)
    expect(screen.getByRole('button', { name: 'Precheck danh sách' })).toBeDisabled()
  } finally { restore() }
})

const review = { id: caseId, check_id: checkId, requested_url: 'https://missing.example.com/', state: 'pending',
  sender_account_ids: [accountId], recipients: item.recipients, cloaking_verdict: 'NO_SIGNAL', evidence_reason: 'capture_failed' }
it('previews manual images on the evidence page and submits only the selected case', async () => {
  const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:local-image')
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  let uploaded = false
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/reviews/evidence') return uploaded ? [] : [review]
    if (config.url === `/domain-worker/reviews/evidence/${caseId}/images`) { uploaded = true; expect(config.data).toBeInstanceOf(FormData); return { state: 'ready' } }
    throw new Error(`Unexpected request ${config.url ?? ''}`)
  })
  try {
    view(`/domain-evidence-review?case=${caseId}`)
    await userEvent.upload(await screen.findByLabelText('Ảnh bằng chứng'), new File(['fixture'], 'capture.png', { type: 'image/png' }))
    expect(await screen.findByAltText('Ảnh đã chọn 1')).toHaveAttribute('src', 'blob:local-image')
    await userEvent.click(screen.getByRole('button', { name: 'Dùng bằng chứng cho case này' }))
    expect(await screen.findByText(/Đã bổ sung bằng chứng/)).toBeVisible()
    expect(uploaded).toBe(true)
  } finally { restore(); create.mockRestore(); revoke.mockRestore() }
})

it('keeps cloaking in its own queue without normal upload, draft or send actions', async () => {
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/reviews/cloaking') return [{ ...review, cloaking_verdict: 'POSSIBLE' }]
    throw new Error('Unexpected request')
  })
  try {
    view(`/cloaking-review?case=${caseId}`)
    expect(await screen.findByText(/Case đã được tách khỏi batch thường/)).toBeVisible()
    expect(screen.queryByLabelText('Ảnh bằng chứng')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /gửi|preview|draft/i })).not.toBeInTheDocument()
  } finally { restore() }
})

it('shows counts during precheck, then configures and starts one batch with the chosen accounts', async () => {
  sessionStorage.setItem(key, operationId)
  let running = true
  const posts: unknown[] = []
  const jobId = '77777777-7777-4777-8777-777777777777'
  const job = { id: jobId, state: 'queued', business_day: operation.business_day, batch_size: 2, interval_seconds: 60,
    next_run_at: null, items: [{ id: checkId, requested_url: item.requested_url, state: 'pending', reason_code: null,
      deliveries: [{ id: caseId, recipient: 'abuse@example.com', sender_account_id: accountId, channel: 'registrar', state: 'pending' }] }] }
  const restore = installApiMock((config) => {
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_worker.start', 'domain_worker.stop'] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return { ...operation, state: running ? 'running' : 'completed' }
    if (config.url === `/domain-worker/prechecks/${operationId}/send-summary`) return { business_day: operation.business_day,
      ready_domains: 1, total_deliveries: 2, already_sent: 1, blocked_deliveries: 0, pending_deliveries: 1,
      checks: [{ check_id: checkId, requested_url: item.requested_url, pending_deliveries: 1 }], send_job_id: null }
    if (config.url === `/domain-worker/prechecks/${operationId}/send`) { posts.push(JSON.parse(String(config.data))); return job }
    if (config.url === `/domain-worker/send-jobs/${jobId}`) return job
    throw new Error(`Unexpected request ${config.url ?? ''}`)
  })
  try {
    const initial = view()
    expect(await screen.findByText(/Đủ điều kiện: 1/)).toBeVisible()
    expect(screen.queryByText(item.requested_url)).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Cấu hình gửi worker')).not.toBeInTheDocument()
    initial.unmount(); running = false; view()
    expect(await screen.findByText(/1 domain còn cần gửi/)).toBeVisible()
    const button = screen.getByRole('button', { name: 'Bắt đầu gửi batch' })
    expect(button).toBeDisabled()
    await userEvent.clear(screen.getByLabelText('Số domain mỗi batch'))
    await userEvent.type(screen.getByLabelText('Số domain mỗi batch'), '2')
    await userEvent.clear(screen.getByLabelText('Nghỉ giữa hai batch (phút)'))
    await userEvent.type(screen.getByLabelText('Nghỉ giữa hai batch (phút)'), '1')
    await userEvent.click(screen.getByRole('checkbox', { name: /Tôi xác nhận gửi/ }))
    await userEvent.click(button)
    expect(await screen.findByLabelText('Tiến trình gửi batch')).toBeVisible()
    expect(posts).toEqual([{ batch_size: 2, interval_seconds: 60, confirmed: true }])
    expect(screen.getByRole('button', { name: 'Dừng gửi' })).toBeEnabled()
    expect(button).toBeDisabled()
  } finally { restore() }
})

it('hides yesterday results and never restores yesterday send job', async () => {
  sessionStorage.setItem(key, operationId)
  const calls: string[] = []
  const restore = installApiMock((config) => {
    calls.push(config.url ?? '')
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_worker.start'] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return { ...operation, is_current_day: false }
    throw new Error('Unexpected request')
  })
  try {
    view(); expect(await screen.findByText(/Đã sang ngày mới/)).toBeVisible()
    expect(screen.queryByText(item.requested_url)).not.toBeInTheDocument()
    expect(calls.some((url) => /send-summary|send-jobs/.test(url))).toBe(false)
    expect(screen.getByRole('checkbox', { name: /Alice/ })).toBeEnabled()
  } finally { restore() }
})

it('shows ordinary sent status without internal Sent-copy implementation labels', async () => {
  sessionStorage.setItem(key, operationId)
  const jobId = '77777777-7777-4777-8777-777777777777'
  const restore = installApiMock((config) => {
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_worker.start'] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return operation
    if (config.url === `/domain-worker/prechecks/${operationId}/send-summary`) return { business_day: operation.business_day,
      ready_domains: 0, total_deliveries: 1, already_sent: 1, blocked_deliveries: 0, pending_deliveries: 0, checks: [], send_job_id: jobId }
    if (config.url === `/domain-worker/send-jobs/${jobId}`) return { id: jobId, state: 'completed', business_day: operation.business_day,
      batch_size: 5, interval_seconds: 300, next_run_at: null, items: [{ id: checkId, requested_url: item.requested_url, state: 'succeeded',
        deliveries: [{ id: caseId, recipient: 'abuse@example.com', sender_account_id: accountId, channel: 'registrar', state: 'sent' }] }] }
    throw new Error(`Unexpected request ${config.url ?? ''}`)
  })
  try {
    view()
    expect(await screen.findByRole('heading', { name: 'Hoàn tất gửi' })).toBeVisible()
    expect(screen.getByText(/Đã gửi: 1 · Lỗi: 0/)).toBeVisible()
    expect(screen.queryByText(/lưu bản sao|SMTP|IMAP/)).not.toBeInTheDocument()
    expect(screen.getByText(/abuse@example.com · Đã gửi/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Bắt đầu gửi batch' })).toBeDisabled()
  } finally { restore() }
})

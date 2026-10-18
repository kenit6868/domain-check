import { Theme } from '@radix-ui/themes'
import { QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { AppRouter } from '../src/app/router/AppRouter'
import { createQueryClient } from '../src/lib/query/client'
import { installApiMock } from './httpMock'
import { BatchCountdown } from '../src/features/domain-worker/WorkerProgress'

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
  <Theme><MemoryRouter initialEntries={[path]}><AppRouter /></MemoryRouter></Theme></QueryClientProvider>) }
afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks() })

it('drops yesterday URL and saved selection but allows explicitly opening history', async () => {
  sessionStorage.setItem(key, operationId)
  const calls: string[] = []
  const restore = installApiMock((config) => {
    calls.push(config.url ?? '')
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/workspace') return { business_day: '2026-10-18', current_id: null, send_job_id: null, phase: 'idle', locked: false, history: [{ ...historyEntry, business_day: '2026-10-17', send_job_id: null, send_state: null }] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return { ...operation, business_day: '2026-10-17', is_current_day: false }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view(`/domain-worker?batch=${operationId}`)
    await screen.findByText('Lịch sử batch (50 lượt gần nhất)')
    expect(screen.queryByLabelText('Tiến trình precheck')).not.toBeInTheDocument()
    expect(calls).not.toContain(`/domain-worker/prechecks/${operationId}`)
    expect(sessionStorage.getItem(key)).toBeNull()
    expect(screen.getByLabelText('Danh sách URL')).toBeEnabled()
    await userEvent.click(screen.getByText('Lịch sử batch (50 lượt gần nhất)'))
    await userEvent.click(screen.getByRole('button', { name: 'Xem kết quả' }))
    expect(await screen.findByText(/Lịch sử ngày trước/)).toBeVisible()
    expect(screen.getByText(item.requested_url)).toBeVisible()
    expect(calls.some((url) => /send-summary|send-jobs/.test(url))).toBe(false)
  } finally { restore() }
})

it('clears the displayed completed precheck when workspace rolls into the next day', async () => {
  let nextDay = false
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/workspace') return { business_day: nextDay ? '2026-10-18' : '2026-10-17', current_id: nextDay ? null : operationId, send_job_id: null, phase: 'idle', locked: false, history: [{ ...historyEntry, business_day: '2026-10-17', send_job_id: null, send_state: null }] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return { ...operation, business_day: '2026-10-17', is_current_day: !nextDay }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    await screen.findByLabelText('Tiến trình precheck')
    nextDay = true
    await waitFor(() => { expect(screen.queryByLabelText('Tiến trình precheck')).not.toBeInTheDocument() }, { timeout: 5000 })
    expect(sessionStorage.getItem(key)).toBeNull()
    expect(screen.getByLabelText('Danh sách URL')).toBeEnabled()
  } finally { restore() }
})

it('pauses and restores a job, resumes it, then permanently stops without offering resume or retry', async () => {
  let state = 'running'
  let mode: 'pause' | 'cancel' | null = null
  const controls: string[] = []
  const restore = installApiMock((config) => {
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_worker.start', 'domain_worker.stop'] }
    if (config.url === '/domain-worker/workspace') return { current_id: operationId, send_job_id: sendingId,
      phase: mode === 'cancel' ? 'idle' : state === 'stopped' ? 'paused' : 'sending', locked: mode !== 'cancel', history: [historyEntry] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return operation
    if (config.url === `/domain-worker/prechecks/${operationId}/send-summary`) return readySummary
    if (config.url?.startsWith(`/domain-worker/send-jobs/${sendingId}`)) {
      if (config.method === 'post') {
        const action = config.url.split('/').at(-1) ?? ''
        controls.push(action)
        mode = action === 'pause' ? 'pause' : action === 'cancel' ? 'cancel' : null
        state = action === 'resume' ? 'queued' : 'stopped'
      }
      return { ...sendState(state), control_mode: mode }
    }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    const mounted = view()
    await userEvent.click(await screen.findByRole('button', { name: 'Tạm dừng job' }, { timeout: 4000 }))
    expect(await screen.findByRole('heading', { name: 'Job đang tạm dừng' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Tiếp tục job' })).toBeEnabled()
    expect(screen.getByLabelText('Danh sách URL')).toBeDisabled()
    mounted.unmount(); sessionStorage.clear(); view()
    await screen.findByRole('heading', { name: 'Job đang tạm dừng' })
    expect(controls).toEqual(['pause'])
    await userEvent.click(screen.getByRole('button', { name: 'Tiếp tục job' }))
    await screen.findByRole('button', { name: 'Tạm dừng job' })
    await userEvent.click(screen.getByRole('button', { name: 'Ngừng hẳn job' }))
    expect(await screen.findByRole('heading', { name: 'Job đã ngừng hẳn' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Tiếp tục job' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Thử lại các mục lỗi' })).not.toBeInTheDocument()
    expect(await screen.findAllByText('Đã ngừng — chưa gửi')).toHaveLength(2)
    expect(controls).toEqual(['pause', 'resume', 'cancel'])
  } finally { restore() }
})

it('updates the ready table per sender and marks the domain sent only when both accounts finish every recipient', async () => {
  let retried = false
  let startCalls = 0
  const secondRecipient = 'hosting@example.com'
  const snapshot = () => ({ ...sendState(retried ? 'completed' : 'partial'), items: [{ ...sendState().items[0], state: retried ? 'succeeded' : 'failed', deliveries:
    [accountId, secondAccount].flatMap((sender, accountIndex) => ['abuse@example.com', secondRecipient].map((recipient, recipientIndex) => ({
      id: `88888888-8888-4888-8888-${String(accountIndex * 2 + recipientIndex).padStart(12, '0')}`,
      sender_account_id: sender, recipient, channel: recipientIndex ? 'hosting' : 'registrar',
      state: !retried && accountIndex === 1 && recipientIndex === 1 ? 'failed' : 'sent',
      attempt_count: retried && accountIndex === 1 && recipientIndex === 1 ? 2 : 1,
    }))),
  }] })
  const restore = installApiMock((config) => {
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_worker.start'] }
    if (config.url === '/domain-worker/workspace') return { current_id: operationId, send_job_id: sendingId, phase: 'idle', locked: false, history: [historyEntry] }
    if (config.url === '/domain-worker/accounts') return accounts.map((account) => ({ ...account, label: account.sender_email }))
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return { ...operation, sender_account_ids: [accountId, secondAccount],
      checks: [{ ...item, recipients: [...item.recipients, { channel: 'hosting', email: secondRecipient }] }] }
    if (config.url === `/domain-worker/prechecks/${operationId}/send-summary`) return readySummary
    if (config.url === `/domain-worker/prechecks/${operationId}/send`) { startCalls += 1; return snapshot() }
    if (config.url === `/domain-worker/send-jobs/${sendingId}/retry`) { retried = true; return snapshot() }
    if (config.url === `/domain-worker/send-jobs/${sendingId}`) return snapshot()
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    const mounted = view()
    const table = within(await screen.findByLabelText('Domain đủ điều kiện gửi'))
    const alice = within(await table.findByLabelText('Kết quả tài khoản alice@example.com'))
    const bob = within(table.getByLabelText('Kết quả tài khoản bob@example.com'))
    expect(bob.getByText('bob@example.com')).toBeVisible()
    expect(bob.queryByText('bob@example.com · bob@example.com')).not.toBeInTheDocument()
    expect(await alice.findByText('Đã gửi thành công')).toBeVisible()
    expect(alice.getByText(/Đã gửi 2\/2/)).toBeVisible()
    expect(bob.getByText('Gửi một phần')).toBeVisible()
    expect(bob.getByText(/Đã gửi 1\/2 · Lỗi 1/)).toBeVisible()
    expect(table.getAllByText('Đã gửi thành công')).toHaveLength(1)
    expect(screen.getAllByRole('table')).toHaveLength(1)
    expect(screen.queryByLabelText('Đã kiểm tra: 1/1')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Đã gửi hôm nay: 0')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Email đã gửi: 3')).toBeVisible()
    expect(screen.queryByText('Chi tiết gửi')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Thử lại các mục lỗi' }))
    await screen.findByRole('heading', { name: 'Job đã hoàn thành' })
    expect(table.getAllByText('Đã gửi thành công')).toHaveLength(3)
    expect(screen.getByLabelText('Email đã gửi: 4')).toBeVisible()
    expect(bob.getByText('Đã gửi 2/2')).toBeVisible()
    mounted.unmount(); sessionStorage.clear(); view()
    await screen.findByRole('heading', { name: 'Job đã hoàn thành' })
    expect(within(screen.getByLabelText('Domain đủ điều kiện gửi')).getAllByText('Đã gửi thành công')).toHaveLength(3)
    expect(startCalls).toBe(0)
  } finally { restore() }
})

it('explains cached no-email and sent exclusions without creating an empty batch', async () => {
  let creates = 0
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/workspace') return { current_id: null, send_job_id: null, phase: 'idle', locked: false, history: [] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === '/domain-worker/prechecks') {
      creates += 1
      return { ...operation, id: null, no_job: true, checks: [], input_summary: {
        input_count: 4, unique_count: 4, queued_count: 0, duplicate_count: 0, sent_count: 2, no_email_count: 2, processing_count: 0,
        excluded: ['a', 'b', 'c', 'd'].map((name, index) => ({ requested_url: `https://${name}.example.com/`, reason: index < 2 ? 'no_email' : 'already_sent' })),
      } }
    }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    await screen.findByRole('checkbox', { name: /Alice/ })
    await userEvent.type(screen.getByLabelText('Danh sách URL'), 'a.example.com\nb.example.com\nc.example.com\nd.example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Precheck danh sách' }))
    expect(await screen.findByText(/Không có domain mới cần precheck/)).toBeVisible()
    expect(screen.getByText(/Không có email \(đã kiểm tra\): 2/)).toBeVisible()
    await userEvent.click(screen.getByText('Xem mục bị loại (4)'))
    expect(screen.getByText('https://a.example.com/ · Không có email — đã kiểm tra hôm nay')).toBeVisible()
    expect(screen.getByText('https://c.example.com/ · Đã gửi thành công')).toBeVisible()
    expect(screen.queryByLabelText('Tiến trình precheck')).not.toBeInTheDocument()
    expect(creates).toBe(1)
  } finally { restore() }
})

it('counts down from the server deadline and waits for worker confirmation at zero', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-16T01:00:00Z'))
  try {
    const mounted = render(<BatchCountdown deadline="2026-10-16T01:00:03Z" compact />)
    expect(screen.getByRole('timer')).toHaveTextContent('00:00:03')
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(screen.getByRole('timer')).toHaveTextContent('00:00:01')
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(screen.getByRole('timer')).toHaveTextContent('00:00:00')
    expect(screen.getByText(/Đã đến giờ. Đang chờ worker/)).toBeVisible()
    mounted.unmount()
    render(<BatchCountdown deadline="2026-10-16T01:00:08Z" compact />)
    expect(screen.getByRole('timer')).toHaveTextContent('00:00:03')
  } finally { vi.useRealTimers() }
})

it('adds ready domains incrementally while precheck continues and highlights the running URL', async () => {
  let reads = 0
  const currentUrl = 'https://checking.example.com/'
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/workspace') return { current_id: operationId, send_job_id: null, phase: 'precheck', locked: true, history: [] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) {
      reads += 1
      return { ...operation, state: 'running', checks: [
        { ...item, status: reads > 1 ? 'succeeded' : 'pending', disposition: reads > 1 ? 'ready' : 'pending' },
        { ...item, id: caseId, requested_url: currentUrl, status: 'running', disposition: 'pending' },
      ] }
    }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    expect(await screen.findByText(currentUrl)).toBeVisible()
    expect(screen.getByLabelText('Đủ điều kiện: 0')).toBeVisible()
    expect(within(screen.getByLabelText('Domain đủ điều kiện gửi')).queryByText(item.requested_url)).not.toBeInTheDocument()
    expect(await within(screen.getByLabelText('Domain đủ điều kiện gửi')).findByText(item.requested_url, {}, { timeout: 4000 })).toBeVisible()
    expect(screen.getByLabelText('Đủ điều kiện: 1')).toBeVisible()
    expect(screen.getByLabelText('Đã kiểm tra: 1/2')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Bắt đầu gửi batch' })).not.toBeInTheDocument()
  } finally { restore() }
})

it('shows an empty precheck outcome without a send configuration or domain results', async () => {
  sessionStorage.setItem(key, operationId)
  const restore = installApiMock((config) => {
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_worker.start'] }
    if (config.url === '/domain-worker/workspace') return { current_id: operationId, send_job_id: null, phase: 'idle', locked: false, history: [] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return { ...operation, checks: [{ ...item, disposition: 'excluded_no_email', recipients: [], evidence_set_id: null }] }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    expect(await screen.findByText(/Không có domain đủ điều kiện gửi email/)).toBeVisible()
    expect(screen.queryByLabelText('Cấu hình gửi worker')).not.toBeInTheDocument()
    expect(screen.queryByText(item.requested_url)).not.toBeInTheDocument()
  } finally { restore() }
})

it('shows the running batch number and domain progress independently of delivery count', async () => {
  const restore = installApiMock((config) => {
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_worker.start'] }
    if (config.url === '/domain-worker/workspace') return { current_id: operationId, send_job_id: sendingId, phase: 'sending', locked: true,
      history: [{ id: operationId, created_at: '2026-10-16T01:00:00Z', business_day: '2026-10-16', state: 'completed', send_job_id: sendingId, send_state: 'running' }] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return operation
    if (config.url === `/domain-worker/prechecks/${operationId}/send-summary`) return readySummary
    if (config.url === `/domain-worker/send-jobs/${sendingId}`) return { ...sendState('running'), batch_size: 2,
      items: Array.from({ length: 20 }, (_, index) => ({ ...sendState().items[0], id: `77777777-7777-4777-8777-${String(index).padStart(12, '0')}`, state: index === 0 ? 'running' : 'pending' })) }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    expect(await screen.findByRole('heading', { name: 'Đang chạy batch 1/10' })).toBeVisible()
    expect(screen.getByRole('progressbar', { name: 'Tiến độ gửi email' })).toHaveAttribute('value', '0')
    expect(screen.queryByRole('button', { name: 'Bắt đầu gửi batch' })).not.toBeInTheDocument()
  } finally { restore() }
})

it('selects accounts before precheck and keeps the exact account scope with the batch', async () => {
  const posts: unknown[] = []
  const restore = installApiMock((config) => {
    if (config.url === '/domain-worker/workspace') return { current_id: null, send_job_id: null, phase: 'idle', locked: false, history: [] }
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
    expect(await screen.findByLabelText('Đủ điều kiện: 1')).toBeVisible()
    expect(posts).toEqual([{ targets: ['example.com', 'https://example.com/login?q=1'], sender_account_ids: [accountId] }])
    expect(screen.queryByRole('button', { name: /draft|preview/i })).not.toBeInTheDocument()
    expect(sessionStorage.getItem(key)).toBe(operationId)
  } finally { restore() }
})

it('keeps only ready domains in the normal list and routes cloaking/missing evidence to separate pages', async () => {
  sessionStorage.setItem(key, operationId)
  const restore = installApiMock((config) => {
    if (config.url === '/domain-worker/workspace') return { current_id: null, send_job_id: null, phase: 'idle', locked: false, history: [] }
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
    view(); expect(await screen.findByLabelText('Đủ điều kiện: 1')).toBeVisible()
    expect(screen.getByText(item.requested_url)).toBeVisible()
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
    if (config.url === '/domain-worker/workspace') return { current_id: null, send_job_id: null, phase: 'idle', locked: false, history: [] }
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
    if (config.url === '/domain-worker/workspace') return { current_id: null, send_job_id: null, phase: 'idle', locked: false, history: [] }
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
    if (config.url === '/domain-worker/workspace') return { current_id: null, send_job_id: null, phase: 'idle', locked: false, history: [] }
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
    if (config.url === '/domain-worker/workspace') return { current_id: null, send_job_id: null, phase: 'idle', locked: false, history: [] }
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_evidence_review.preview'] }
    if (config.url === '/domain-worker/reviews/evidence') return uploaded ? [] : [review]
    const preview = { id: operationId, check_id: checkId, revision: 1, fingerprint: 'a'.repeat(64), requested_url: review.requested_url,
      expires_at: '2100-01-01T00:00:00Z', deliveries: [{ sender_account_id: accountId, recipient: 'abuse@example.com', channel: 'registrar', draft_identity: 'report', subject: 'Fixture', text_body: 'Exact fixture draft' }] }
    if (config.url === `/domain-worker/reviews/evidence/${caseId}`) return { ...review, evidence_version: uploaded ? 1 : 0,
      accounts: [{ ...accounts[0], enabled: true }], source_job_ids: [operationId], images: uploaded ? [{ id: secondAccount, mime_type: 'image/png', byte_size: 100 }] : [],
      preview: uploaded ? preview : null, deliveries: [] }
    if (config.url === `/domain-worker/reviews/evidence/${caseId}/previews`) { uploaded = true; expect(config.data).toBeInstanceOf(FormData); return { preview, case_id: caseId, evidence_version: 1 } }
    throw new Error(`Unexpected request ${config.url ?? ''}`)
  })
  try {
    view(`/domain-evidence-review?case=${caseId}`)
    await userEvent.upload(await screen.findByLabelText(/Ảnh bằng chứng \(/), new File(['fixture'], 'capture.png', { type: 'image/png' }))
    expect(await screen.findByAltText('Ảnh đã chọn 1')).toHaveAttribute('src', 'blob:local-image')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo / cập nhật draft để xem' }))
    expect(await screen.findByText('Exact fixture draft')).toBeVisible()
    expect(uploaded).toBe(true)
  } finally { restore(); create.mockRestore(); revoke.mockRestore() }
})

it('keeps cloaking in its own queue and respects view-only permissions', async () => {
  const restore = installApiMock((config) => {
    if (config.url === '/domain-worker/workspace') return { current_id: null, send_job_id: null, phase: 'idle', locked: false, history: [] }
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/reviews/cloaking') return [{ ...review, cloaking_verdict: 'POSSIBLE' }]
    if (config.url === `/domain-worker/reviews/cloaking/${caseId}`) return { ...review, evidence_version: 0,
      accounts: [{ id: accountId, sender_email: 'sender@example.com', enabled: true }], source_job_ids: [],
      images: [], preview: null, deliveries: [], cloaking: { verdict: 'POSSIBLE', score: 20 } }
    throw new Error('Unexpected request')
  })
  try {
    view(`/cloaking-review?case=${caseId}`)
    expect(await screen.findByText(/Kết quả precheck: POSSIBLE/)).toBeVisible()
    expect(screen.queryByLabelText('Ảnh bằng chứng')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Gửi mail domain này' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Tạo / cập nhật draft để xem' })).toBeDisabled()
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
    if (config.url === '/domain-worker/workspace') return { current_id: null, send_job_id: null, phase: 'idle', locked: false, history: [] }
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
    expect(await screen.findByLabelText('Đủ điều kiện: 1')).toBeVisible()
    expect(screen.getByText(item.requested_url)).toBeVisible()
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
    expect(screen.getByRole('button', { name: 'Tạm dừng job' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Bắt đầu gửi batch' })).not.toBeInTheDocument()
  } finally { restore() }
})

it('hides yesterday results and never restores yesterday send job', async () => {
  sessionStorage.setItem(key, operationId)
  const calls: string[] = []
  const restore = installApiMock((config) => {
    calls.push(config.url ?? '')
    if (config.url === '/domain-worker/workspace') return { current_id: null, send_job_id: null, phase: 'idle', locked: false, history: [] }
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_worker.start'] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return { ...operation, is_current_day: false }
    throw new Error('Unexpected request')
  })
  try {
    view(); expect(await screen.findByText(/Lịch sử ngày trước/)).toBeVisible()
    expect(screen.getByText(item.requested_url)).toBeVisible()
    expect(calls.some((url) => /send-summary|send-jobs/.test(url))).toBe(false)
    expect(screen.getByRole('checkbox', { name: /Alice/ })).toBeEnabled()
  } finally { restore() }
})

it('shows ordinary sent status without internal Sent-copy implementation labels', async () => {
  sessionStorage.setItem(key, operationId)
  const jobId = '77777777-7777-4777-8777-777777777777'
  const restore = installApiMock((config) => {
    if (config.url === '/domain-worker/workspace') return { current_id: null, send_job_id: null, phase: 'idle', locked: false, history: [] }
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
    expect(await screen.findByRole('heading', { name: 'Job đã hoàn thành' })).toBeVisible()
    expect(screen.getByLabelText('Email đã gửi: 1')).toBeVisible()
    expect(screen.getByLabelText('Email lỗi: 0')).toBeVisible()
    expect(screen.queryByText(/lưu bản sao|SMTP|IMAP/)).not.toBeInTheDocument()
    expect(within(screen.getByLabelText('Kết quả tài khoản alice@example.com')).getByText('Đã gửi thành công')).toBeVisible()
    expect(screen.queryByText('Chi tiết gửi')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Bắt đầu gửi batch' })).not.toBeInTheDocument()
  } finally { restore() }
})

const sendingId = '77777777-7777-4777-8777-777777777777'
const archivedId = '88888888-8888-4888-8888-888888888888'
const historyEntry = { id: operationId, created_at: '2026-10-16T08:00:00Z', business_day: '2026-10-16',
  state: 'completed', send_job_id: sendingId, send_state: 'queued' }
function sendState(state = 'queued') { return { id: sendingId, state, business_day: '2026-10-16', batch_size: 5,
  interval_seconds: 300, next_run_at: null, items: [{ id: checkId, requested_url: item.requested_url, state: 'pending',
    deliveries: [{ id: caseId, recipient: 'abuse@example.com', sender_account_id: accountId, channel: 'registrar', state: 'pending' }] }] } }
const readySummary = { business_day: '2026-10-16', ready_domains: 1, total_deliveries: 1, already_sent: 0,
  blocked_deliveries: 0, pending_deliveries: 1, checks: [{ check_id: checkId, requested_url: item.requested_url, pending_deliveries: 1 }], send_job_id: sendingId }

it('restores the backend active batch instead of stale storage or URL and blocks new input across reload', async () => {
  sessionStorage.setItem(key, archivedId)
  const calls: string[] = []
  const restore = installApiMock((config) => {
    calls.push(`${config.method ?? ''} ${config.url ?? ''}`)
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_worker.start'] }
    if (config.url === '/domain-worker/workspace') return { current_id: operationId, send_job_id: sendingId, phase: 'sending', locked: true, history: [historyEntry] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return operation
    if (config.url === `/domain-worker/prechecks/${operationId}/send-summary`) return readySummary
    if (config.url === `/domain-worker/send-jobs/${sendingId}`) return sendState()
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    const first = view(`/domain-worker?batch=${archivedId}`)
    expect(await screen.findByLabelText('Tiến trình gửi batch')).toBeVisible()
    expect(screen.getByLabelText('Danh sách URL')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Precheck danh sách' })).toBeDisabled()
    expect(screen.getByText('Đang xử lý batch hiện tại. Vui lòng đợi hoàn thành trước khi kiểm tra danh sách mới.')).toBeVisible()
    await userEvent.type(screen.getByLabelText('Danh sách URL'), 'conflict.example.com')
    expect(screen.getByLabelText('Danh sách URL')).toHaveValue('')
    first.unmount(); sessionStorage.clear(); view()
    expect(await screen.findByLabelText('Tiến trình gửi batch')).toBeVisible()
    expect(calls.some((call) => call.startsWith('post'))).toBe(false)
    expect(calls.some((call) => call.includes(`/prechecks/${archivedId}`))).toBe(false)
  } finally { restore() }
})

it('keeps awaiting-send scope locked until the operator ends the batch, preserving its history', async () => {
  let closed = false
  const restore = installApiMock((config) => {
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_worker.stop'] }
    if (config.url === '/domain-worker/workspace') return { current_id: operationId, send_job_id: null, phase: closed ? 'idle' : 'awaiting_send', locked: !closed,
      history: [{ ...historyEntry, send_job_id: null, send_state: null }] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return operation
    if (config.url === `/domain-worker/prechecks/${operationId}/close`) { closed = true; return operation }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view(); await screen.findByRole('button', { name: 'Kết thúc lượt này' })
    expect(screen.getByLabelText('Danh sách URL')).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Kết thúc lượt này' }))
    await screen.findByText('Lịch sử batch (50 lượt gần nhất)')
    expect(closed).toBe(true)
    expect(screen.getByLabelText('Danh sách URL')).toBeEnabled()
    expect(screen.getByLabelText('Tiến trình precheck')).toBeVisible()
  } finally { restore() }
})

it('reports all-sent filtering without replacing the previous operation or creating an empty job', async () => {
  sessionStorage.setItem(key, operationId)
  const posts: string[] = []
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/workspace') return { current_id: operationId, send_job_id: null, phase: 'idle', locked: false, history: [] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return operation
    if (config.url === '/domain-worker/prechecks') { posts.push(config.url); return { ...operation, id: null, no_job: true, checks: [], stage: 'all_filtered',
      input_summary: { input_count: 4, unique_count: 3, queued_count: 0, duplicate_count: 1, sent_count: 3, processing_count: 0,
        excluded: [{ requested_url: 'https://a.example.com/', reason: 'already_sent' }] } } }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view(); await screen.findByLabelText('Tiến trình precheck')
    await userEvent.type(screen.getByLabelText('Danh sách URL'), 'a.example.com\nb.example.com\nc.example.com\na.example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Precheck danh sách' }))
    expect(await screen.findByText('Tất cả domain đã gửi thành công trong ngày. Không tạo job mới.')).toBeVisible()
    expect(screen.getByText(/Còn precheck: 0 · Trùng: 1 · Đã gửi thành công: 3/)).toBeVisible()
    expect(screen.getByLabelText('Tiến trình precheck')).toBeVisible()
    expect(sessionStorage.getItem(key)).toBe(operationId); expect(posts).toHaveLength(1)
  } finally { restore() }
})

it('retries known failures within an uncertain item and locks the parent form immediately', async () => {
  let retrying = false
  const calls: string[] = []
  const restore = installApiMock((config) => {
    calls.push(`${config.method ?? ''} ${config.url ?? ''}`)
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_worker.start'] }
    if (config.url === '/domain-worker/workspace') return { current_id: operationId, send_job_id: sendingId, phase: retrying ? 'sending' : 'idle', locked: retrying,
      history: [{ ...historyEntry, send_state: retrying ? 'queued' : 'partial' }] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return operation
    if (config.url === `/domain-worker/prechecks/${operationId}/send-summary`) return readySummary
    if (config.url === `/domain-worker/send-jobs/${sendingId}/retry`) retrying = true
    if (config.url?.startsWith(`/domain-worker/send-jobs/${sendingId}`)) return { ...sendState(retrying ? 'queued' : 'partial'), items: [{ id: checkId, requested_url: item.requested_url, state: retrying ? 'pending' : 'unknown',
      deliveries: [
        { id: caseId, recipient: 'success@example.com', sender_account_id: accountId, channel: 'registrar', state: 'sent', attempt_count: 1 },
        { id: archivedId, recipient: 'failed@example.com', sender_account_id: accountId, channel: 'registry', state: retrying ? 'pending' : 'failed', attempt_count: 2, last_error_code: 'smtp_rejected', smtp_stage: 'authenticate' },
        { id: accountId, recipient: 'unknown@example.com', sender_account_id: accountId, channel: 'hosting', state: 'unknown', attempt_count: 1, last_error_code: 'smtp_outcome_uncertain', message_id: '<unknown@example.com>' },
      ] }] }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view(); await screen.findByRole('button', { name: 'Thử lại các mục lỗi' })
    expect(screen.getByLabelText('Email lỗi: 1')).toBeVisible()
    expect(screen.getByText(/Cần đối soát với log máy chủ mail/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Thử lại các mục lỗi' }))
    expect(screen.getByLabelText('Email đã gửi: 1')).toBeVisible()
    expect(screen.getByLabelText('Danh sách URL')).toBeDisabled()
    expect(calls.filter((call) => call.startsWith('post'))).toEqual([`post /domain-worker/send-jobs/${sendingId}/retry`])
    expect(screen.getByLabelText('Chưa rõ kết quả: 1')).toBeVisible()
  } finally { restore() }
})

it('opens previous batch results from backend history without hiding or overwriting the latest batch', async () => {
  const oldTarget = 'https://archived.example.com/'
  const oldSendId = '99999999-9999-4999-8999-999999999999'
  const calls: string[] = []
  const restore = installApiMock((config) => {
    calls.push(config.url ?? '')
    if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'domain_worker.start'] }
    if (config.url === '/domain-worker/workspace') return { current_id: operationId, send_job_id: sendingId, phase: 'idle', locked: false,
      history: [{ ...historyEntry, send_state: 'completed' }, { ...historyEntry, id: archivedId, business_day: '2026-10-15', send_job_id: oldSendId, send_state: 'partial' }] }
    if (config.url === '/domain-worker/accounts') return accounts
    if (config.url === '/domain-worker/readiness') return { ready: true, missing: [] }
    if (config.url === `/domain-worker/prechecks/${operationId}`) return operation
    if (config.url === `/domain-worker/prechecks/${archivedId}`) return { ...operation, id: archivedId, is_current_day: false, business_day: '2026-10-15', checks: [{ ...item, requested_url: oldTarget }] }
    if (config.url === `/domain-worker/prechecks/${operationId}/send-summary`) return { ...readySummary, ready_domains: 0, pending_deliveries: 0 }
    if (config.url === `/domain-worker/send-jobs/${sendingId}`) return sendState('completed')
    if (config.url === `/domain-worker/send-jobs/${oldSendId}`) return { ...sendState('partial'), id: oldSendId, business_day: '2026-10-15', items: [{ ...sendState().items[0], requested_url: oldTarget }] }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view(); await screen.findByLabelText('Tiến trình gửi batch')
    await userEvent.click(screen.getByText('Lịch sử batch (50 lượt gần nhất)'))
    const open = screen.getAllByRole('button', { name: 'Xem kết quả' }).find((button) => !button.hasAttribute('disabled'))
    expect(open).toBeDefined()
    if (!open) throw new Error('Missing history action')
    await userEvent.click(open)
    expect(await screen.findByText(/Lịch sử ngày trước/)).toBeVisible()
    expect(within(screen.getByLabelText('Domain đủ điều kiện gửi')).getByText(oldTarget)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Bắt đầu gửi batch' })).not.toBeInTheDocument()
    expect(calls).not.toContain(`/domain-worker/prechecks/${archivedId}/send-summary`)
  } finally { restore() }
})

import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { AppRouter } from '../src/app/router/AppRouter'
import { createQueryClient } from '../src/lib/query/client'
import { installApiMock } from './httpMock'

const owner = '11111111-1111-4111-8111-111111111111'
const caseId = '22222222-2222-4222-8222-222222222222'
const checkId = '33333333-3333-4333-8333-333333333333'
const accountA = '44444444-4444-4444-8444-444444444444'
const accountB = '55555555-5555-4555-8555-555555555555'
const planId = '66666666-6666-4666-8666-666666666666'
const imageId = '77777777-7777-4777-8777-777777777777'
const target = 'https://evidence.example.com/path?q=1'
const accounts = [{ id: accountA, sender_email: 'alice@example.com', enabled: true }, { id: accountB, sender_email: 'bob@example.com', enabled: true }]
const me = { id: owner, display_name: 'Operator', status: 'active', timezone: 'Asia/Bangkok', permissions:
  ['domain_evidence_review.view', 'domain_evidence_review.upload', 'domain_evidence_review.preview', 'domain_evidence_review.send'] }
const row = { id: caseId, requested_url: target, state: 'pending', sender_account_ids: [accountA, accountB],
  recipients: [{ channel: 'registrar', email: 'abuse@example.com' }], evidence_reason: 'capture_failed' }
const exact = { id: planId, check_id: checkId, revision: 1, fingerprint: 'a'.repeat(64), expires_at: '2100-01-01T00:00:00Z', requested_url: target,
  deliveries: accounts.map((account) => ({ sender_account_id: account.id, recipient: 'abuse@example.com', channel: 'registrar',
    draft_identity: 'registrar', subject: `Report ${account.sender_email}`, text_body: `Exact English report for ${account.sender_email}` })) }
const base = { ...row, check_id: checkId, evidence_version: 0, accounts, source_job_ids: [planId], images: [], preview: null, deliveries: [] }
const images = [{ id: imageId, mime_type: 'image/png', byte_size: 100 }]
function view(path = `/domain-evidence-review?case=${caseId}`) {
  return render(<QueryClientProvider client={createQueryClient()}><MemoryRouter initialEntries={[path]}><AppRouter /></MemoryRouter></QueryClientProvider>)
}
afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks() })

it('previews uploaded evidence and exact per-account drafts, then sends directly without creating a worker job', async () => {
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:manual')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  let prepared = false
  let sent = false
  const posts: string[] = []
  const restore = installApiMock((config) => {
    if (config.method === 'post') posts.push(config.url ?? '')
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/reviews/evidence') return sent ? [] : [{ ...row, state: prepared ? 'ready' : 'pending' }]
    if (config.url === `/domain-worker/reviews/evidence/${caseId}`) return { ...base, evidence_version: prepared ? 1 : 0, images: prepared ? images : [], preview: prepared ? exact : null }
    if (config.url === `/domain-worker/reviews/evidence/${caseId}/previews`) {
      expect(config.data).toBeInstanceOf(FormData)
      const form = config.data as FormData
      expect(form.get('account_ids')).toBe(JSON.stringify([accountA, accountB]))
      expect(form.getAll('files')).toHaveLength(1)
      prepared = true
      return { preview: exact, case_id: caseId, evidence_version: 1 }
    }
    if (config.url === `/domain-worker/reviews/evidence/${caseId}/send`) {
      expect(JSON.parse(String(config.data))).toEqual({ check_id: checkId, preview_id: planId, revision: 1, fingerprint: exact.fingerprint, confirmed: true })
      sent = true
      return { ...base, state: 'sent', evidence_version: 1, images, preview: null }
    }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    await userEvent.upload(await screen.findByLabelText(/Ảnh bằng chứng \(/, {}, { timeout: 4000 }), new File(['fixture'], 'manual.png', { type: 'image/png' }))
    expect(screen.getByAltText('Ảnh đã chọn 1')).toHaveAttribute('src', 'blob:manual')
    expect(screen.getByRole('button', { name: 'Gửi mail domain này' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Tạo / cập nhật draft để xem' }))
    expect(await screen.findByText('Exact English report for alice@example.com')).toBeVisible()
    expect(screen.getByRole('checkbox', { name: 'bob@example.com' })).toBeChecked()
    expect(posts).toHaveLength(1)
    await userEvent.click(screen.getByRole('checkbox', { name: /Tôi đã đọc draft/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Gửi mail domain này' }))
    expect(await screen.findByText(/Đã gửi đủ mọi tài khoản/)).toBeVisible()
    expect(posts).toEqual([`/domain-worker/reviews/evidence/${caseId}/previews`, `/domain-worker/reviews/evidence/${caseId}/send`])
  } finally { restore() }
})

it('restores saved images and preview after reload, resets confirmation and rejects a batch of four images', async () => {
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:manual')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  const calls: string[] = []
  const restore = installApiMock((config) => {
    calls.push(`${config.method ?? ''} ${config.url ?? ''}`)
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/reviews/evidence') return [{ ...row, state: 'ready' }]
    if (config.url === `/domain-worker/reviews/evidence/${caseId}`) return { ...base, state: 'ready', evidence_version: 1, images, preview: exact }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    const mounted = view()
    expect(await screen.findByAltText('Ảnh đã lưu 1')).toHaveAttribute('src', `/api/v1/domain-worker/reviews/evidence/${caseId}/images/${imageId}`)
    await userEvent.click(screen.getByRole('checkbox', { name: /Tôi đã đọc draft/ }))
    expect(screen.getByRole('button', { name: 'Gửi mail domain này' })).toBeEnabled()
    mounted.unmount(); view()
    expect(await screen.findByRole('checkbox', { name: /Tôi đã đọc draft/ })).not.toBeChecked()
    await userEvent.click(screen.getByRole('checkbox', { name: 'bob@example.com' }))
    expect(screen.queryByLabelText('Nội dung draft sẽ gửi')).not.toBeInTheDocument()
    await userEvent.upload(screen.getByLabelText(/Ảnh bằng chứng \(/), Array.from({ length: 4 }, (_, index) => new File(['fixture'], `manual${String(index)}.png`, { type: 'image/png' })))
    expect(await screen.findByRole('alert')).toHaveTextContent('Chọn tối đa 3 ảnh')
    expect(screen.getByRole('button', { name: 'Tạo / cập nhật draft để xem' })).toBeDisabled()
    expect(calls.some((call) => call.startsWith('post'))).toBe(false)
  } finally { restore() }
})

it('keeps partial and unknown deliveries visible, retries only through the exact plan and blocks pure unknown retries', async () => {
  let retried = false
  const ledger = () => [{ id: accountA, sender_account_id: accountA, recipient: 'abuse@example.com', channel: 'registrar', state: 'sent' },
    { id: accountB, sender_account_id: accountB, recipient: 'abuse@example.com', channel: 'registrar', state: retried ? 'unknown' : 'failed', error_code: retried ? 'smtp_outcome_uncertain' : 'smtp_rejected' }]
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/reviews/evidence') return [{ ...row, state: 'partial' }]
    if (config.url === `/domain-worker/reviews/evidence/${caseId}/send`) retried = true
    if (config.url?.startsWith(`/domain-worker/reviews/evidence/${caseId}`)) return { ...base, state: 'partial', evidence_version: 1, images, preview: exact, deliveries: ledger() }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    await screen.findByText('Máy chủ từ chối thư hoặc đăng nhập')
    await userEvent.click(screen.getByRole('checkbox', { name: /Tôi đã đọc draft/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Gửi tiếp các mục còn thiếu' }))
    expect(await screen.findByText('Chưa rõ — không tự gửi lại')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Gửi tiếp các mục còn thiếu' })).toBeDisabled()
    expect(screen.getByText(/Mục chưa rõ không tự gửi lại/)).toBeVisible()
    expect(screen.queryByText('Đã gửi đủ tài khoản')).not.toBeInTheDocument()
  } finally { restore() }
})

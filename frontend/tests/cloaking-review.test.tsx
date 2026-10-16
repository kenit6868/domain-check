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
  ['cloaking_review.view', 'cloaking_review.upload', 'cloaking_review.preview', 'cloaking_review.send', 'cloaking_review.approve', 'cloaking_review.skip', 'check_domain.capture'] }
const row = { id: caseId, requested_url: target, state: 'pending', sender_account_ids: [accountA, accountB],
  recipients: [{ channel: 'registrar', email: 'abuse@example.com' }], evidence_reason: 'capture_failed' }
const exact = { id: planId, check_id: checkId, revision: 1, fingerprint: 'a'.repeat(64), expires_at: '2100-01-01T00:00:00Z', requested_url: target,
  deliveries: accounts.map((account) => ({ sender_account_id: account.id, recipient: 'abuse@example.com', channel: 'registrar',
    draft_identity: 'registrar', subject: `Report ${account.sender_email}`, text_body: `Exact English report for ${account.sender_email}` })) }
const base = { ...row, check_id: checkId, evidence_version: 0, accounts, source_job_ids: [planId], images: [], preview: null, deliveries: [], decision: null, cloaking: { verdict: 'POSSIBLE', score: 30 } }
const images = [{ id: imageId, mime_type: 'image/png', byte_size: 100 }]
function view(path = `/cloaking-review?case=${caseId}`) {
  return render(<QueryClientProvider client={createQueryClient()}><MemoryRouter initialEntries={[path]}><AppRouter /></MemoryRouter></QueryClientProvider>)
}
afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks() })

it('shows the pair captured during precheck without another capture request', async () => {
  const posts: string[] = []
  const restore = installApiMock((config) => {
    if (config.method === 'post') posts.push(config.url ?? '')
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/reviews/cloaking') return [row]
    if (config.url === `/domain-worker/reviews/cloaking/${caseId}`) return { ...base,
      capture_pending: false, capture_reason: null, capture_decision: 'confirmed_cloaking',
      capture_comparison_only: true,
      capture_images: [images[0], { ...images[0], id: planId }], capture_profile_names: ['desktop_direct', 'googlebot_smartphone'] }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    expect(await screen.findByAltText('Ảnh capture 1')).toBeVisible()
    expect(screen.getByAltText('Ảnh capture 2')).toBeVisible()
    expect(screen.getByText('googlebot_smartphone')).toBeVisible()
    expect(screen.getByText(/browser chưa quan sát được khác biệt nội dung rõ ràng/)).toBeVisible()
    expect(screen.getByRole('checkbox', { name: 'Dùng bộ ảnh tự động này cho draft mới' })).toBeChecked()
    expect(screen.getByRole('button', { name: 'Chụp lại bằng chứng' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Tạo / cập nhật draft để xem' })).toBeDisabled()
    await userEvent.click(screen.getByRole('checkbox', { name: /Tôi xác nhận hai ảnh/ }))
    expect(screen.getByRole('button', { name: 'Tạo / cập nhật draft để xem' })).toBeEnabled()
    expect(posts).toEqual([])
  } finally { restore() }
})

it('requires a reviewed comparison pair and sends the exact draft directly', async () => {
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:pair')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  let prepared = false
  let sent = false
  const posts: string[] = []
  const restore = installApiMock((config) => {
    if (config.method === 'post') posts.push(config.url ?? '')
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/reviews/cloaking') return sent ? [] : [row]
    if (config.url === `/domain-worker/reviews/cloaking/${caseId}`) return { ...base, images: prepared ? [...images, ...images] : [], preview: prepared ? exact : null, decision: prepared ? 'confirmed_cloaking' : null }
    if (config.url === `/domain-worker/reviews/cloaking/${caseId}/previews`) {
      const form = config.data as FormData
      expect(form.get('decision')).toBe('confirmed_cloaking')
      expect(form.get('confirmed_difference')).toBe('true')
      expect(form.getAll('files')).toHaveLength(2)
      prepared = true
      return { preview: exact, case_id: caseId, evidence_version: 1 }
    }
    if (config.url === `/domain-worker/reviews/cloaking/${caseId}/send`) {
      expect(JSON.parse(String(config.data))).toMatchObject({ fingerprint: exact.fingerprint, confirmed: true })
      sent = true
      return { ...base, state: 'sent' }
    }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    const input = await screen.findByLabelText(/Ảnh bằng chứng \(/)
    await userEvent.upload(input, [new File(['a'], 'desktop.png', { type: 'image/png' })])
    const preview = screen.getByRole('button', { name: 'Tạo / cập nhật draft để xem' })
    expect(preview).toBeDisabled()
    await userEvent.upload(input, [new File(['a'], 'desktop.png', { type: 'image/png' }), new File(['b'], 'mobile.png', { type: 'image/png' })])
    expect(preview).toBeDisabled()
    await userEvent.click(screen.getByRole('checkbox', { name: /Tôi xác nhận hai ảnh/ }))
    await userEvent.click(preview)
    expect(await screen.findByText('Exact English report for alice@example.com')).toBeVisible()
    await userEvent.click(screen.getByRole('checkbox', { name: /Tôi đã đọc draft/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Gửi mail domain này' }))
    expect(await screen.findByText(/Đã gửi đủ mọi tài khoản/)).toBeVisible()
    expect(posts).toEqual([`/domain-worker/reviews/cloaking/${caseId}/previews`, `/domain-worker/reviews/cloaking/${caseId}/send`])
  } finally { restore() }
})

it('changes to a normal report with separate evidence and clears approval', async () => {
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:normal')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  let normal = false
  const restore = installApiMock((config) => {
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/reviews/cloaking') return [row]
    if (config.url === `/domain-worker/reviews/cloaking/${caseId}`) return { ...base, images, preview: normal ? exact : null, decision: normal ? 'not_cloaking' : 'confirmed_cloaking' }
    if (config.url === `/domain-worker/reviews/cloaking/${caseId}/previews`) {
      const form = config.data as FormData
      expect(form.get('decision')).toBe('not_cloaking')
      expect(form.getAll('files')).toHaveLength(1)
      normal = true
      return { preview: exact, case_id: caseId, evidence_version: 1 }
    }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    await userEvent.selectOptions(await screen.findByLabelText('Kết luận duyệt'), 'not_cloaking')
    expect(screen.queryByRole('checkbox', { name: /Tôi xác nhận hai ảnh/ })).not.toBeInTheDocument()
    expect(screen.getByText(/Bổ sung 1–3 ảnh report thường/)).toBeVisible()
    await userEvent.upload(screen.getByLabelText(/Ảnh bằng chứng \(/), new File(['normal'], 'normal.png', { type: 'image/png' }))
    await userEvent.click(screen.getByRole('button', { name: 'Tạo / cập nhật draft để xem' }))
    expect(await screen.findByText('Exact English report for alice@example.com')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Gửi mail domain này' })).toBeDisabled()
  } finally { restore() }
})

it('shows passive capture thumbnails before approval and skips without sending', async () => {
  let captured = false
  let skipped = false
  const posts: string[] = []
  const restore = installApiMock((config) => {
    if (config.method === 'post') posts.push(config.url ?? '')
    if (config.url === '/me') return me
    if (config.url === '/domain-worker/reviews/cloaking') return skipped ? [] : [row]
    if (config.url === `/domain-worker/reviews/cloaking/${caseId}`) return { ...base, capture_decision: captured ? 'confirmed_cloaking' : null, capture_images: captured ? images : [] }
    if (config.url === `/domain-worker/reviews/cloaking/${caseId}/capture`) {
      captured = true
      return { ...base, capture_decision: 'confirmed_cloaking', capture_images: images }
    }
    if (config.url === `/domain-worker/reviews/cloaking/${caseId}/skip`) { skipped = true; return { id: caseId, state: 'skipped' } }
    throw new Error(`Unexpected ${config.url ?? ''}`)
  })
  try {
    view()
    await userEvent.click(await screen.findByRole('button', { name: 'Chụp bằng chứng tự động' }))
    expect(await screen.findByAltText('Ảnh capture 1')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Gửi mail domain này' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Bỏ qua case này' }))
    expect(await screen.findByText('Đã bỏ qua case. Không gửi email.')).toBeVisible()
    expect(posts).toEqual([`/domain-worker/reviews/cloaking/${caseId}/capture`, `/domain-worker/reviews/cloaking/${caseId}/skip`])
  } finally { restore() }
})


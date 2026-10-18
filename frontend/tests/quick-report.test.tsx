import { Theme } from '@radix-ui/themes'
import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppRouter } from '../src/app/router/AppRouter'
import { createQueryClient } from '../src/lib/query/client'
import { installApiMock as installMock } from './httpMock'
import * as extensionBridge from '../src/features/quick-report/extension/extensionBridge'

// Existing action tests keep the independent daily request pending; daily scenarios mock it below.
const installApiMock = (handler: Parameters<typeof installMock>[0]) => installMock((config) =>
  config.url === '/quick-reports/daily' ? new Promise(() => {}) : handler(config))

const me = {
  id: '11111111-1111-4111-8111-111111111111', display_name: 'Operator', telegram_username: 'operator',
  status: 'active', timezone: 'Asia/Bangkok', permissions: ['quick_report.view', 'quick_report.check'],
}

function operation(state = 'completed') {
  return {
    id: '22222222-2222-4222-8222-222222222222', state, stage: state === 'completed' ? 'completed' : 'checking',
    business_day: '2026-10-15', duplicates: ['https://example.test/login'],
    checks: [{
      id: '33333333-3333-4333-8333-333333333333', requested_url: 'https://example.test/login',
      domain: 'example.test', status: state === 'completed' ? 'succeeded' : 'running',
      result: state === 'completed' ? {
        domain: 'example.test', cloudflare: true, cdn_detected: ['Cloudflare'], registrar: 'Example Registrar',
        registry_contact: { registry: 'Example Registry' }, report_recipients: [{ channel: 'registrar', email: 'abuse@example.test' }],
        cloaking: { verdict: 'NO_SIGNAL' },
      } : {},
      cache_provenance: state === 'completed' ? { reused: false, source: 'network' } : {},
    }],
  }
}

function renderQuickReport() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <Theme><MemoryRouter initialEntries={['/quick-report']}><AppRouter /></MemoryRouter></Theme>
    </QueryClientProvider>,
  )
}

describe('Quick Report workflow', () => {
  afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks() })

  it('displays current input order and numbering even when daily history arrives in a different order', async () => {
    const first = operation().checks[0]
    if (!first) throw new Error('Missing fixture')
    const checks = ['succeeded', 'running', 'pending'].map((status, index) => ({ ...first,
      id: `44444444-4444-4444-8444-${String(index).padStart(12, '0')}`,
      requested_url: `https://input${String(index + 1)}.test/`, status,
      result: status === 'succeeded' ? first.result : {}, cache_provenance: status === 'succeeded' ? first.cache_provenance : {},
    }))
    const fixture = { ...operation('running'), checks, duplicates: [] }
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, fixture.id)
    const restore = installMock((config) => {
      if (config.url === '/me') return me
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url === '/quick-reports/daily') return { business_day: fixture.business_day, checks: [...checks].reverse() }
      if (config.url?.startsWith('/quick-reports/checks/')) return fixture
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    await screen.findByText(`Kết quả hôm nay · ${fixture.business_day}`)
    const cards = screen.getAllByRole('article')
    expect(cards.map((card) => within(card).getByRole('heading').textContent)).toEqual([
      'https://input1.test/', 'https://input2.test/', 'https://input3.test/',
    ])
    cards.forEach((card, index) => { expect(within(card).getByText(`#${String(index + 1)}`)).toBeInTheDocument() })
    expect(within(cards[1] as HTMLElement).getByText('Worker đang kiểm tra URL này.')).toBeInTheDocument()
    expect(within(cards[2] as HTMLElement).getByText('URL đang chờ worker nhận xử lý.')).toBeInTheDocument()
    restore()
  })

  it('explains an orphan historical check instead of claiming it is waiting for a worker', async () => {
    const fixture = { ...operation(), checks: operation().checks.map((check) => ({ ...check, status: 'not_queued', result: {}, cache_provenance: {} })) }
    const restore = installMock((config) => {
      if (config.url === '/me') return me
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url === '/quick-reports/daily') return { business_day: fixture.business_day, checks: fixture.checks }
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Ẩn kết quả đã check trong ngày (1)' }))
    expect(await screen.findByText('Chưa có job')).toBeInTheDocument()
    expect(screen.getByText(/Bản ghi cũ chưa có job xử lý/)).toBeInTheDocument()
    expect(screen.queryByText('URL đang chờ worker nhận xử lý.')).not.toBeInTheDocument()
    expect(screen.getByLabelText('URL cần kiểm tra')).toBeInTheDocument()
    restore()
  })

  it('keeps input visible and accumulates 10 previous plus 2 new daily results across another batch and refresh', async () => {
    const first = operation().checks[0]
    if (!first) throw new Error('Missing fixture')
    const oldChecks = Array.from({ length: 10 }, (_, index) => ({ ...first,
      id: `55555555-5555-4555-8555-${String(index).padStart(12, '0')}`,
      requested_url: `https://old${String(index)}.test/`, domain: `old${String(index)}.test`,
    }))
    const newChecks = [first, { ...first, id: '66666666-6666-4666-8666-666666666666', requested_url: 'https://new.test/', domain: 'new.test' }]
    const latest = { ...operation(), checks: newChecks, duplicates: [] }
    let started = false
    let posts = 0
    const restore = installMock((config) => {
      if (config.url === '/me') return me
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url === '/quick-reports/daily') return { business_day: latest.business_day, checks: [...oldChecks, ...(started ? newChecks : [])] }
      if (config.url === '/quick-reports/checks' && config.method === 'post') { started = true; posts += 1; return latest }
      if (config.url?.startsWith('/quick-reports/checks/')) return latest
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    expect(await screen.findByText('10 kết quả cũ · 0 check mới · 10 URL trong ngày')).toBeInTheDocument()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('URL cần kiểm tra'), 'https://example.test/login{enter}https://new.test/')
    await userEvent.click(screen.getByRole('button', { name: /Lọc danh sách/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra' }))
    expect(await screen.findByText('10 kết quả cũ · 2 check mới · 12 URL trong ngày')).toBeInTheDocument()
    expect(screen.getAllByRole('article')).toHaveLength(2)
    expect(screen.getByLabelText('URL cần kiểm tra')).toBeInTheDocument()
    cleanup()
    renderQuickReport()
    expect(await screen.findByText('10 kết quả cũ · 2 check mới · 12 URL trong ngày')).toBeInTheDocument()
    expect(screen.getAllByRole('article')).toHaveLength(2)
    expect(posts).toBe(1)
    restore()
  })

  it('shows only the new batch, empties the screen after cache clear and shows all 20 URLs when rechecked', async () => {
    const first = operation().checks[0]
    if (!first) throw new Error('Missing fixture')
    const makeChecks = (offset: number, count: number, revision: string) => Array.from({ length: count }, (_, index) => ({ ...first,
      id: `${revision}-7777-4777-8777-${String(index).padStart(12, '0')}`,
      requested_url: `https://daily${String(index + offset)}.test/`, domain: `daily${String(index + offset)}.test`,
    }))
    const old = makeChecks(0, 10, '77777777')
    const fresh = makeChecks(10, 10, '88888888')
    const rechecked = makeChecks(0, 20, '99999999')
    let phase = 0
    let checksCreated = 0
    const restore = installMock((config) => {
      if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'quick_report.clear_cache'] }
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url === '/cache/clear') { phase = 2; return { feature: 'quick-report', business_day: '2026-10-15', deleted_count: 20 } }
      if (config.url === '/quick-reports/daily') return { business_day: operation().business_day, checks: phase === 0 ? old : phase === 3 ? rechecked : [...old, ...fresh] }
      if (config.url === '/quick-reports/checks' && config.method === 'post') { phase = phase === 2 ? 3 : 1; checksCreated += 1 }
      if (config.url?.startsWith('/quick-reports/checks')) return { ...operation(),
        id: phase === 3 ? '99999999-9999-4999-8999-999999999999' : operation().id,
        checks: phase === 3 ? rechecked : fresh, duplicates: [] }
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    await screen.findByText('10 kết quả cũ · 0 check mới · 10 URL trong ngày')
    const input = screen.getByLabelText('URL cần kiểm tra')
    fireEvent.change(input, { target: { value: fresh.map((check) => check.requested_url).join('\n') } })
    await userEvent.click(screen.getByRole('button', { name: /Lọc danh sách/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra' }))
    await screen.findByText('10 kết quả cũ · 10 check mới · 20 URL trong ngày')
    expect(screen.getAllByRole('article')).toHaveLength(10)
    expect(screen.queryByRole('heading', { name: 'https://daily0.test/' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Xoá cache hôm nay' }))
    await screen.findByText(/Đã xoá 20 mục cache/)
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    expect(checksCreated).toBe(1)
    await userEvent.clear(input)
    fireEvent.change(input, { target: { value: rechecked.map((check) => check.requested_url).join('\n') } })
    await userEvent.click(screen.getByRole('button', { name: /Lọc danh sách/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra' }))
    await screen.findByText('0 kết quả cũ · 20 check mới · 20 URL trong ngày')
    expect(screen.getAllByRole('article')).toHaveLength(20)
    expect(checksCreated).toBe(2)
    restore()
  })

  it('counts 20 cached and 20 new checks, hides cached cards by default and restores them without a new check', async () => {
    const first = operation().checks[0]
    if (!first) throw new Error('Missing check fixture')
    const fixture = { ...operation(), duplicates: [], checks: Array.from({ length: 40 }, (_, index) => ({ ...first,
      id: `33333333-3333-4333-8333-${String(index).padStart(12, '0')}`,
      requested_url: `https://fixture${String(index)}.test/`, domain: `fixture${String(index)}.test`,
      cache_provenance: { reused: index < 20, source: index < 20 ? 'redis' : 'network' },
    })) }
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, fixture.id)
    const writes: string[] = []
    const restore = installApiMock((config) => {
      if (config.method === 'post') { writes.push(config.url ?? ''); throw new Error('Must not check again') }
      if (config.url === '/me') return me
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url?.startsWith('/quick-reports/checks/')) return fixture
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    expect(await screen.findByText('20 kết quả cache · 20 check mới')).toBeInTheDocument()
    expect(screen.getAllByRole('article')).toHaveLength(20)
    expect(screen.queryByRole('heading', { name: 'https://fixture0.test/' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Ẩn kết quả đã check trong ngày (20)' }))
    expect(screen.getAllByRole('article')).toHaveLength(40)
    expect(screen.getByRole('heading', { name: 'https://fixture0.test/' })).toBeInTheDocument()
    expect(writes).toHaveLength(0)
    expect(screen.queryByRole('button', { name: 'Xoá cache hôm nay' })).not.toBeInTheDocument()
    restore()
  })

  it('clears today cache and the visible batch without creating another check', async () => {
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, operation().id)
    const writes: { url: string; data: unknown }[] = []
    const restore = installApiMock((config) => {
      if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'quick_report.clear_cache'] }
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.method === 'post') {
        writes.push({ url: config.url ?? '', data: JSON.parse(String(config.data)) as unknown })
        return { feature: 'quick-report', business_day: '2026-10-16', deleted_count: 20 }
      }
      if (config.url?.startsWith('/quick-reports/checks/')) return operation()
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    expect(await screen.findByText('abuse@example.test')).toBeInTheDocument()
    expect(writes).toHaveLength(0)
    await userEvent.click(screen.getByRole('button', { name: 'Xoá cache hôm nay' }))
    expect(await screen.findByText(/Đã xoá 20 mục cache ngày 2026-10-16/)).toBeInTheDocument()
    expect(writes).toEqual([{ url: '/cache/clear', data: { feature: 'quick-report' } }])
    expect(screen.queryByText('abuse@example.test')).not.toBeInTheDocument()
    restore()
  })

  it('disables cache clearing while a worker operation is active', async () => {
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, operation().id)
    const restore = installApiMock((config) => {
      if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'quick_report.clear_cache'] }
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url?.startsWith('/quick-reports/checks/')) return operation('running')
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    await screen.findByText('Worker đang kiểm tra URL này.')
    expect(screen.getByRole('button', { name: 'Xoá cache hôm nay' })).toBeDisabled()
    restore()
  })

  it('explains an all-cache operation and shows its results on demand', async () => {
    const fixture = { ...operation(), checks: operation().checks.map((check) => ({ ...check, cache_provenance: { reused: true, source: 'redis' } })) }
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, fixture.id)
    const restore = installApiMock((config) => {
      if (config.url === '/me') return me
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url?.startsWith('/quick-reports/checks/')) return fixture
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    expect(await screen.findByText(/Kết quả cũ đã được ẩn/)).toBeInTheDocument()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Ẩn kết quả đã check trong ngày (1)' }))
    expect(screen.getByRole('article')).toBeInTheDocument()
    restore()
  })

  it('shows a cache clear error without claiming success or removing results', async () => {
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, operation().id)
    const restore = installApiMock((config) => {
      if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'quick_report.clear_cache'] }
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url === '/cache/clear') throw new Error('Cache service unavailable')
      if (config.url?.startsWith('/quick-reports/checks/')) return operation()
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    await screen.findByText('abuse@example.test')
    await userEvent.click(screen.getByRole('button', { name: 'Xoá cache hôm nay' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText(/Đã xoá \d+ mục cache/)).not.toBeInTheDocument()
    expect(screen.getByText('abuse@example.test')).toBeInTheDocument()
    restore()
  })

  it('orders provider buttons and uses only the correct extension action for supported routing forms', async () => {
    vi.spyOn(extensionBridge, 'pingExtension').mockResolvedValue({ deviceId: 'fixture-device', extensionVersion: '3.0.0' })
    const taskId = '33333333-3333-4333-8333-333333333333'
    const openForm = vi.spyOn(extensionBridge, 'openExtensionForm').mockResolvedValue({ taskId, tabId: 12 })
    const providers = [
      ['registry_co_phishing', 'Registry CO'], ['godaddy_phishing', 'GoDaddy'], ['cloudflare', 'Cloudflare'],
      ['coccoc_safe', 'Cốc Cốc'], ['chongluadao', 'Chống Lừa Đảo'], ['microsoft_smartscreen', 'Microsoft'], ['google_gsb', 'Google'],
    ].map(([id, label]) => ({ id, label, form_url: 'https://provider.example/form' }))
    const report_forms = [
      { channel: 'registry', label: 'Registry CO', form_url: 'https://registry.example/form', autofill_provider: 'registry_co_phishing', note: '' },
      { channel: 'registrar', label: 'GoDaddy', form_url: 'https://registrar.example/form', autofill_provider: 'godaddy_phishing', note: '' },
    ]
    const fixture = { ...operation(), checks: operation().checks.map((check) => ({ ...check, providers, report_forms })) }
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, fixture.id)
    const submitted: string[] = []
    const task = { id: taskId, provider: 'godaddy_phishing', form_url: 'https://registrar.example/form', target_url: 'https://example.test/login', mode: 'fill_only', state: 'FILLED', expires_at: '2026-10-17T12:00:00Z', status: null }
    const restore = installApiMock((config) => {
      if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'quick_report.open_form'] }
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url === '/extension/devices') return { registered: true, extension_version: '3.0.0' }
      if (config.url?.endsWith('/form-tasks')) { submitted.push((JSON.parse(String(config.data)) as { provider: string }).provider); return { ...task, capability_token: 'fixture-capability' } }
      if (config.url?.startsWith('/form-tasks/')) return task
      if (config.url?.includes('/form-drafts?')) return [
        { channel: 'general', label: 'Google', form_url: 'https://provider.example/form', draft: 'General report fixture', note: '', autofill_provider: 'google_gsb' },
        { channel: 'infrastructure', label: 'Cloudflare', form_url: 'https://provider.example/form', draft: 'Infrastructure report fixture', note: '', autofill_provider: 'cloudflare' },
        ...report_forms.map((form) => ({ ...form, draft: `Routing report for ${form.label}` })),
      ]
      if (config.url?.endsWith('/form-drafts')) throw new Error('Autofill must not open the manual draft')
      if (config.url?.startsWith('/quick-reports/checks/')) return fixture
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    const goDaddy = await screen.findByRole('button', { name: 'Registrar · GoDaddy — Mở và tự điền' })
    await waitFor(() => { expect(goDaddy).toBeEnabled() })
    expect(within(screen.getByLabelText('Kênh báo cáo')).getAllByRole('button').filter((button) => button.textContent.includes('Mở và tự điền')).map((button) => button.textContent.replace('Mở và tự điền', ''))).toEqual([
      'Google', 'Microsoft', 'Chống Lừa Đảo', 'Cốc Cốc', 'Cloudflare', 'Registrar · GoDaddy', 'Registry · Registry CO',
    ])
    expect(screen.queryByRole('link', { name: /GoDaddy/ })).not.toBeInTheDocument()
    await userEvent.click(goDaddy)
    await waitFor(() => { expect(openForm).toHaveBeenCalledWith('fixture-capability') })
    expect(submitted).toEqual(['godaddy_phishing'])
    expect(await screen.findByText('General report fixture')).toBeInTheDocument()
    expect(screen.getByText('Infrastructure report fixture')).toBeInTheDocument()
    expect(screen.getByText('Routing report for Registry CO')).toBeInTheDocument()
    restore()
  })

  it('opens manual registrar and registry forms directly without a duplicate draft dialog', async () => {
    const forms = [
      { channel: 'registrar', label: 'Dynadot LLC', form_url: 'https://www.dynadot.com/report-abuse', autofill_provider: null, note: '', draft: 'Registrar draft fixture' },
      { channel: 'registry', label: 'Verisign', form_url: 'https://verisign.my.site.com/DNSAbuse', autofill_provider: null, note: '', draft: 'Registry draft fixture' },
    ]
    const fixture = { ...operation(), checks: operation().checks.map((check) => ({ ...check, report_forms: forms })) }
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, fixture.id)
    const restore = installApiMock((config) => {
      if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'quick_report.open_form'] }
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url?.includes('/form-drafts?')) return forms
      if (config.url?.startsWith('/quick-reports/checks/')) return fixture
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    expect(await screen.findByRole('link', { name: 'Registrar · Dynadot LLC' })).toHaveAttribute('href', forms[0]?.form_url)
    expect(screen.getByRole('link', { name: 'Registry · Verisign' })).toHaveAttribute('href', forms[1]?.form_url)
    expect(screen.getByRole('link', { name: 'Registry · Verisign' })).toHaveAttribute('target', '_blank')
    expect(await screen.findByText('Registrar draft fixture')).toBeInTheDocument()
    expect(screen.getByText('Registry draft fixture')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Registrar' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Registry' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'CDN' })).not.toBeInTheDocument()
    expect(screen.queryByText('Không có form áp dụng cho nhóm này.')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    restore()
  })
  it('describes queued work as waiting for a worker rather than checking the URL', async () => {
    const queued = operation('queued')
    const first = queued.checks[0]
    if (!first) throw new Error('Fixture check is missing')
    first.status = 'pending'
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, queued.id)
    const restore = installApiMock((config) => {
      if (config.url === '/me') return me
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url?.startsWith('/quick-reports/checks/') && config.method !== 'post') return queued
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    expect(await screen.findByText('URL đang chờ worker nhận xử lý.')).toBeInTheDocument()
    expect(screen.getByText(/Lượt kiểm tra đang xếp hàng/)).toBeInTheDocument()
    expect(screen.queryByText('Worker đang kiểm tra URL này.')).not.toBeInTheDocument()
    restore()
  })

  it('blocks all workflow actions until the organization profile is configured', async () => {
    const calls: string[] = []
    let ready = false
    const restore = installApiMock((config) => {
      calls.push(config.url ?? '')
      if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'mail_accounts.view_own', 'mail_accounts.manage_own'] }
      if (config.url === '/quick-reports/readiness') return ready ? { ready: true, missing: [] } : { ready: false, missing: ['brand_name', 'contact_email'] }
      throw new Error(`Unexpected request ${config.url ?? ''}`)
    })
    renderQuickReport()
    expect(await screen.findByText('Hoàn tất cấu hình trước khi sử dụng')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Đi tới cấu hình hồ sơ' })).toHaveAttribute('href', '/settings/mail')
    expect(screen.queryByLabelText('URL cần kiểm tra')).not.toBeInTheDocument()
    expect(calls.some((url) => url.includes('/checks'))).toBe(false)
    ready = true
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra lại' }))
    expect(await screen.findByLabelText('URL cần kiểm tra')).toBeInTheDocument()
    restore()
  })

  it('filters locally before creating a real operation and renders backend DTOs', async () => {
    const calls: string[] = []
    const restore = installApiMock((config) => {
      calls.push(`${config.method ?? 'get'} ${config.url ?? ''}`)
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url === '/me') return me
      if (config.url === '/quick-reports/checks' && config.method === 'post') return operation()
      if (config.url?.startsWith('/quick-reports/checks/')) return operation()
      throw new Error(`Unexpected request ${config.url ?? ''}`)
    })
    renderQuickReport()
    const input = await screen.findByLabelText('URL cần kiểm tra')
    await userEvent.type(input, 'https://example.test/login{enter}not-a-domain')
    expect(calls.filter((call) => call.includes('/quick-reports/checks'))).toHaveLength(0)
    await userEvent.click(screen.getByRole('button', { name: /Lọc danh sách/ }))
    expect(screen.getByText(/Bỏ qua:/)).toHaveTextContent('not-a-domain')
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra' }))
    expect(await screen.findByText('abuse@example.test')).toBeInTheDocument()
    expect(screen.getByText('abuse@example.test')).toBeInTheDocument()
    expect(screen.queryByText('NO_SIGNAL')).not.toBeInTheDocument()
    expect(screen.queryByText('Cloaking HTTP')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'CDN' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Registrar' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Registry' })).not.toBeInTheDocument()
    restore()
  })

  it('restores the owned operation after refresh without creating another check', async () => {
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, operation().id)
    let getCount = 0
    const restore = installApiMock((config) => {
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url === '/me') return me
      if (config.url?.startsWith('/quick-reports/checks/')) { getCount += 1; return operation() }
      throw new Error(`Unexpected request ${config.url ?? ''}`)
    })
    renderQuickReport()
    expect(await screen.findByText('abuse@example.test')).toBeInTheDocument()
    await waitFor(() => { expect(getCount).toBe(1) })
    expect(screen.getByLabelText('URL cần kiểm tra')).toBeInTheDocument()
    restore()
  })

  it('disables autofill without an extension and never duplicates it with manual provider links', async () => {
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, operation().id)
    const calls: string[] = []
    const restore = installApiMock((config) => {
      calls.push(`${config.method ?? 'get'} ${config.url ?? ''}`)
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'quick_report.open_form'] }
      if (config.url?.startsWith('/quick-reports/checks/')) return operation()
      throw new Error(`Unexpected request ${config.url ?? ''}`)
    })
    renderQuickReport()
    expect(await screen.findByText(/Chưa kết nối extension/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Google — Mở và tự điền' })).toBeDisabled()
    expect(screen.queryByRole('link', { name: /Google/ })).not.toBeInTheDocument()
    expect(calls.some((call) => call.includes('/form-tasks'))).toBe(false)
    restore()
  })
  it.each(['SUBMITTED', 'SENDING', 'UNKNOWN', 'DISABLED'])('does not send Netcraft when server status is %s', async (state) => {
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, operation().id)
    let writes = 0
    const restore = installApiMock((config) => {
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'quick_report.submit_netcraft'] }
      if (config.method === 'post') { writes += 1; throw new Error('Must not send') }
      if (config.url?.endsWith('/netcraft')) return { id: '44444444-4444-4444-8444-444444444444', state: state === 'DISABLED' ? 'PREVIEW' : state,
        revision: 1, fingerprint: 'a'.repeat(64), expires_at: '2026-10-16T12:00:00Z', submit_enabled: state !== 'DISABLED', payload: null, result: {} }
      if (config.url?.startsWith('/quick-reports/checks/')) return operation()
      throw new Error('Unexpected request')
    })
    renderQuickReport()
    const send = await screen.findByRole('button', { name: 'Gửi API Netcraft' })
    await waitFor(() => { expect(send).toBeDisabled() })
    await userEvent.click(send)
    expect(writes).toBe(0)
    restore()
  })

  it('sends Netcraft on one click using an exact server preview and prevents duplicate submission', async () => {
    sessionStorage.setItem(`domaincheck:v1:u:${me.id}:quick-report:active-operation`, operation().id)
    const preview = { id: '44444444-4444-4444-8444-444444444444', state: 'PREVIEW', revision: 1,
      fingerprint: 'a'.repeat(64), expires_at: '2026-10-16T12:00:00Z', submit_enabled: true,
      payload: { email: 'fixture@example.test', urls: [{ url: 'https://example.test/login', country: 'VN', reason: 'Please investigate suspected phishing.' }] }, result: {} }
    let submitted = false
    const posts: string[] = []
    const restore = installApiMock((config) => {
      if (config.url === '/quick-reports/readiness') return { ready: true, missing: [] }
      if (config.url === '/me') return { ...me, permissions: [...me.permissions, 'quick_report.submit_netcraft'] }
      if (config.url?.endsWith('/netcraft/preview')) { posts.push(config.url); return preview }
      if (config.url?.endsWith('/submit')) { posts.push(config.url); submitted = true; return { ...preview, state: 'SUBMITTED' } }
      if (config.url?.endsWith('/netcraft')) return submitted ? { ...preview, state: 'SUBMITTED' } : null
      if (config.url?.startsWith('/quick-reports/checks/')) return operation()
      throw new Error(`Unexpected request ${config.url ?? ''}`)
    })
    renderQuickReport()
    const send = await screen.findByRole('button', { name: 'Gửi API Netcraft' })
    await waitFor(() => { expect(send).toBeEnabled() })
    expect(posts).toHaveLength(0)
    await userEvent.click(send)
    await waitFor(() => { expect(posts).toHaveLength(2) })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(await screen.findByText('Netcraft đã nhận báo cáo')).toBeInTheDocument()
    expect(send).toBeDisabled()
    await userEvent.click(send)
    expect(posts).toHaveLength(2)
    restore()
  })

})







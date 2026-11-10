import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { runInNewContext } from 'node:vm'
import { expect, it, vi } from 'vitest'

const source = (name: string) => readFileSync(resolve('extensions/cloudflare-profile-worker', name), 'utf8')
type Listener = (message: unknown, sender: unknown, reply: (value: Record<string, unknown>) => void) => boolean

function background(mode = 'fill_only', initial: Record<string, string> = { domaincheckOrigin: 'https://app.example', deviceId: 'device-fixture' }) {
  const local = {...initial}
  const chrome = {
    runtime: { getManifest: () => ({ version: '3.1.0' }), onMessage: { addListener: vi.fn() }, onMessageExternal: { addListener: vi.fn() } },
    storage: {
      local: { get: vi.fn(() => Promise.resolve({...local})), set: vi.fn((value: Record<string, string>) => { Object.assign(local, value); return Promise.resolve() }) },
      session: { set: vi.fn((value: unknown) => Promise.resolve(value)), get: vi.fn(() => Promise.resolve({})), remove: vi.fn(() => Promise.resolve()) },
    },
    tabs: { create: vi.fn(() => Promise.resolve({ id: 7 })), onRemoved: { addListener: vi.fn() }, onUpdated: { addListener: vi.fn() } },
  }
  const fetcher = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({
    id: 'fixture', provider: 'cloudflare', mode, form_url: 'https://abuse.cloudflare.com/phishing',
    target_url: 'https://suspect.example/', status_token: 'fixture-status',
    registrar: 'Fixture Registrar', target_domain: 'suspect.example',
  }) }))
  runInNewContext(source('background.js'), { chrome, fetch: fetcher, URL, crypto: {randomUUID: () => 'new-device'}, AbortSignal })
  const external = chrome.runtime.onMessageExternal.addListener.mock.calls[0]?.[0] as Listener
  const internal = chrome.runtime.onMessage.addListener.mock.calls[0]?.[0] as Listener
  const send = (message: unknown, url = 'https://app.example/quick-report') => new Promise<Record<string, unknown>>((reply) => {
    external(message, { url }, reply)
  })
  return { chrome, fetcher, send, internal }
}

it('keeps exact-origin handshake and rejects messages from another origin without requests', async () => {
  const worker = background()
  expect(await worker.send({ type: 'domaincheck-ping' })).toMatchObject({ ok: true, extensionVersion: '3.1.0' })
  expect(await worker.send({ type: 'domaincheck-ping' }, 'https://other.example')).toMatchObject({ ok: false, error: 'origin_not_allowed' })
  expect(worker.fetcher).not.toHaveBeenCalled()
})

it('exchanges a capability and stores an owner-bound fill-only web task', async () => {
  const worker = background()
  expect(await worker.send({ type: 'domaincheck-open-form', capabilityToken: 'x'.repeat(50) })).toMatchObject({ ok: true, tabId: 7 })
  expect(worker.fetcher).toHaveBeenCalledWith('https://app.example/api/v1/form-tasks/exchange', expect.objectContaining({ credentials: 'omit' }))
  const stored = worker.chrome.storage.session.set.mock.calls[0]?.[0] as Record<string, { mode: string; apiOrigin: string; registrar: string; target_domain: string }>
  expect(stored['formTask:7'].mode).toBe('fill_only')
  expect(stored['formTask:7'].apiOrigin).toBe('https://app.example')
  expect(stored['formTask:7'].registrar).toBe('Fixture Registrar')
  expect(stored['formTask:7'].target_domain).toBe('suspect.example')
  worker.chrome.storage.session.get.mockResolvedValue(stored)
  const reply = await new Promise<Record<string, unknown>>((resolveReply) => {
    worker.internal({ type: 'form-task-for-tab' }, { tab: { id: 7, url: 'https://abuse.cloudflare.com/phishing' } }, resolveReply)
  })
  expect(reply).toMatchObject({ ok: true, task: { registrar: 'Fixture Registrar', target_domain: 'suspect.example' } })
})

it('rejects submit tasks and has no retired localhost bridge fetch handler', async () => {
  const worker = background('submit')
  expect(await worker.send({ type: 'domaincheck-open-form', capabilityToken: 'x'.repeat(50) })).toMatchObject({ ok: false, error: 'invalid_task_policy' })
  expect(worker.chrome.tabs.create).not.toHaveBeenCalled()
  worker.fetcher.mockClear()
  expect(worker.internal({ type: 'bridge-fetch', url: 'http://127.0.0.1:1234/task/fixture' }, {}, vi.fn())).toBe(false)
  expect(worker.fetcher).not.toHaveBeenCalled()
})

it.each(['fill_only', 'submit'])('content script handles %s without submitting or reading old bridge tokens', async (mode) => {
  const fill = vi.fn(() => Promise.resolve({ valid: true, status: 'filled', message: 'Ready for manual submission' }))
  const submit = vi.fn()
  const sendMessage = vi.fn((message: { type: string }) => Promise.resolve(message.type === 'form-task-for-tab'
    ? { ok: true, task: { provider: 'cloudflare', mode } } : { ok: true }))
  runInNewContext(source('content.js'), {
    chrome: { runtime: { sendMessage, onMessage: { addListener: vi.fn() } } },
    location: { hostname: 'abuse.cloudflare.com', hash: '#ptask=ignored&port=1234' }, document: {}, setTimeout,
    sessionStorage: { getItem: () => { throw new Error('retired bridge storage accessed') } },
    PhishingToolFormAdapters: [{ id: 'cloudflare', version: 'fixture', matches: () => true,
      waitUntilReady: () => Promise.resolve({ url: { value: 'fixture' } }), fill, captchaPending: () => false, submit }],
  })
  await vi.waitFor(() => { expect(sendMessage).toHaveBeenCalledWith(expect.objectContaining({ type: 'form-task-status', state: mode === 'fill_only' ? 'FILLED' : 'FAILED' })) })
  expect(fill).toHaveBeenCalledTimes(mode === 'fill_only' ? 1 : 0)
  expect(submit).not.toHaveBeenCalled()
})

it('declares default application permissions and keeps custom origin support', () => {
  const manifest = JSON.parse(source('manifest.json')) as { version: string; host_permissions: string[]; optional_host_permissions: string[] }
  expect(manifest.version).toBe('3.1.7')
  expect(manifest.host_permissions).toContain('https://verisign.my.site.com/ReportAbuse*')
  expect(manifest.host_permissions).toContain('https://verisign.my.site.com/DNSAbuse*')
  expect(manifest.host_permissions).toContain('http://127.0.0.1/*')
  expect(manifest.host_permissions).toContain('https://tool-check.camellrp.com/*')
  expect(manifest.optional_host_permissions).toContain('http://127.0.0.1/*')
})

it('connects fresh production and local installs without Options and rejects untrusted origins', async () => {
  const worker = background('fill_only', {})
  const urls = ['https://tool-check.camellrp.com/quick-report', 'http://localhost:5173/', 'http://127.0.0.1:5173/']
  const replies = await Promise.all(urls.map(url => worker.send({type: 'domaincheck-ping'}, url)))
  for (const reply of replies) expect(reply).toMatchObject({ok: true, deviceId: 'new-device'})
  expect(worker.chrome.storage.local.set).toHaveBeenCalledTimes(1)
  for (const url of ['https://tool-check.camellrp.com.evil.test', 'http://tool-check.camellrp.com', 'http://localhost:5174', 'https://evil.test', '']) {
    expect(await worker.send({type: 'domaincheck-ping', origin: urls[0]}, url)).toMatchObject({ok: false, error: 'origin_not_allowed'})
  }
  expect(worker.fetcher).not.toHaveBeenCalled()
})

it('routes exchanges by sender and keeps status bound to the original task environment', async () => {
  const worker = background()
  await worker.send({type: 'domaincheck-open-form', capabilityToken: 'x'.repeat(50)}, 'https://tool-check.camellrp.com/')
  const stored = worker.chrome.storage.session.set.mock.calls[0][0] as Record<string, {apiOrigin: string}>
  expect(stored['formTask:7'].apiOrigin).toBe('https://tool-check.camellrp.com')
  await worker.send({type: 'domaincheck-open-form', capabilityToken: 'x'.repeat(50)}, 'http://localhost:5173/')
  expect(worker.fetcher).toHaveBeenLastCalledWith('http://localhost:5173/api/v1/form-tasks/exchange', expect.anything())
  worker.chrome.storage.session.get.mockResolvedValue(stored)
  await new Promise<Record<string, unknown>>(reply => worker.internal({type: 'form-task-status', state: 'FILLED'}, {tab: {id: 7}}, reply))
  expect(worker.fetcher).toHaveBeenLastCalledWith('https://tool-check.camellrp.com/api/v1/form-tasks/fixture/status', expect.anything())
})

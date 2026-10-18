import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { runInNewContext } from 'node:vm'
import { expect, it, vi } from 'vitest'

const source = (name: string) => readFileSync(resolve('extensions/cloudflare-profile-worker', name), 'utf8')
type Listener = (message: unknown, sender: unknown, reply: (value: Record<string, unknown>) => void) => boolean

function background(mode = 'fill_only') {
  const chrome = {
    runtime: { getManifest: () => ({ version: '3.1.0' }), onMessage: { addListener: vi.fn() }, onMessageExternal: { addListener: vi.fn() } },
    storage: {
      local: { get: vi.fn(() => Promise.resolve({ domaincheckOrigin: 'https://app.example', deviceId: 'device-fixture' })) },
      session: { set: vi.fn((value: unknown) => Promise.resolve(value)), get: vi.fn(() => Promise.resolve({})), remove: vi.fn(() => Promise.resolve()) },
    },
    tabs: { create: vi.fn(() => Promise.resolve({ id: 7 })), onRemoved: { addListener: vi.fn() }, onUpdated: { addListener: vi.fn() } },
  }
  const fetcher = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({
    id: 'fixture', provider: 'cloudflare', mode, form_url: 'https://abuse.cloudflare.com/phishing',
    target_url: 'https://suspect.example/', status_token: 'fixture-status',
  }) }))
  runInNewContext(source('background.js'), { chrome, fetch: fetcher, URL })
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
  const stored = worker.chrome.storage.session.set.mock.calls[0]?.[0] as Record<string, { mode: string; apiOrigin: string }>
  expect(stored['formTask:7'].mode).toBe('fill_only')
  expect(stored['formTask:7'].apiOrigin).toBe('https://app.example')
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

it('keeps explicit optional localhost app support without mandatory legacy bridge access', () => {
  const manifest = JSON.parse(source('manifest.json')) as { version: string; host_permissions: string[]; optional_host_permissions: string[] }
  expect(manifest.version).toBe('3.1.0')
  expect(manifest.host_permissions).not.toContain('http://127.0.0.1/*')
  expect(manifest.optional_host_permissions).toContain('http://127.0.0.1/*')
})

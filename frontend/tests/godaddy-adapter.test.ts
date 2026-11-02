import source from '../extensions/cloudflare-profile-worker/adapters/godaddy_phishing.js?raw'
import { afterEach, expect, it, vi } from 'vitest'

type Fields = Record<'email' | 'company' | 'source' | 'details', HTMLInputElement>
interface Adapter {
  waitUntilReady(context: {document: Document; status: () => void; sleep: () => Promise<void>}): Promise<Fields>
  fill(task: Record<string, string>, fields: Fields, context: {sleep: () => Promise<void>}): Promise<{valid: boolean}>
  validate(fields: Fields): {valid: boolean}
  submit(): boolean
}
const task = {contact_email: 'report@example.test', brand_name: 'Fixture Brand', target_url: 'https://fixture.test/login', draft: 'Fixture English report'}
afterEach(() => { document.body.innerHTML = ''; vi.restoreAllMocks() })

async function fixture() {
  document.body.innerHTML = `<form><input id="email-field"><input id="company-field"><textarea id="source-field"></textarea>
    <textarea id="info-field"></textarea><input type="checkbox" id="good-faith"><button>Send Report</button></form>`
  vi.spyOn(Element.prototype, 'getClientRects').mockReturnValue([{}] as unknown as DOMRectList)
  // Execute the standalone content script in the fixture DOM.
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const load = new Function(`${source}; return globalThis.PhishingToolFormAdapters.pop();`) as () => Adapter
  const adapter = load()
  const fields = await adapter.waitUntilReady({document, status: () => {}, sleep: async () => {}})
  const submit = vi.fn((event: Event) => { event.preventDefault() })
  document.querySelector('form')?.addEventListener('submit', submit)
  return {adapter, fields, submit}
}

it.each([false, true])('repairs a first-open email reset (replaced input: %s) without F5 or submission', async (replace) => {
  const {adapter, fields, submit} = await fixture()
  let ticks = 0
  const sleep = vi.fn(() => {
    ticks += 1
    if (ticks === 3) {
      if (replace) fields.email.outerHTML = '<input id="email-field">'
      else fields.email.value = ''
    }
    return Promise.resolve()
  })
  expect((await adapter.fill(task, fields, {sleep})).valid).toBe(true)
  expect(fields.email).toBe(document.querySelector('#email-field'))
  expect(fields.email.value).toBe(task.contact_email)
  expect(fields.company.value).toBe(task.brand_name)
  expect(fields.source.value).toBe(task.target_url)
  expect(fields.details.value).toBe(task.draft)
  expect(sleep.mock.calls.length).toBeGreaterThanOrEqual(8)
  expect(sleep.mock.calls.length).toBeLessThanOrEqual(20)
  expect((document.querySelector('#good-faith') as HTMLInputElement).checked).toBe(false)
  expect(adapter.submit()).toBe(false)
  expect(submit).not.toHaveBeenCalled()
})

it('preserves an operator email change and stops retrying after the bounded window', async () => {
  const {adapter, fields} = await fixture()
  let ticks = 0
  const sleep = vi.fn(() => {
    if (++ticks === 2) fields.email.value = 'operator@example.test'
    return Promise.resolve()
  })
  expect((await adapter.fill(task, fields, {sleep})).valid).toBe(false)
  expect(fields.email.value).toBe('operator@example.test')
  expect(sleep).toHaveBeenCalledTimes(20)
})

it('does not claim success when email is repeatedly cleared or required fields are missing', async () => {
  const {adapter, fields} = await fixture()
  const sleep = vi.fn(() => { fields.email.value = ''; return Promise.resolve() })
  expect((await adapter.fill(task, fields, {sleep})).valid).toBe(false)
  expect(sleep).toHaveBeenCalledTimes(20)
  expect(adapter.validate(fields).valid).toBe(false)
  fields.email.value = task.contact_email
  expect(adapter.validate(fields).valid).toBe(true)
  fields.details.value = ''
  expect(adapter.validate(fields).valid).toBe(false)
})

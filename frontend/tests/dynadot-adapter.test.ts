import source from '../extensions/cloudflare-profile-worker/adapters/dynadot_phishing.js?raw'
import { afterEach, expect, it, vi } from 'vitest'

type Fields = Record<'name' | 'email' | 'confirm' | 'domains' | 'official' | 'proxy' | 'url' | 'details', HTMLInputElement>
interface Adapter {
  matches(url: URL): boolean
  waitUntilReady(context: { document: Document; sleep: () => Promise<void> }): Promise<Fields | null>
  fill(task: Record<string, string>, fields: Fields, context: { sleep: () => Promise<void> }): Promise<{ valid: boolean }>
  captchaPending(doc: Document): boolean
  submit(): boolean
}
const task = {contact_name: 'Fixture Reporter', contact_email: 'report@example.test',
  target_url: 'https://sub.fixture.test/wap/#/register', draft: 'Fixture English report'}
const form = () => `<form>${['full_name_2', 'email_2', 'email2_2', 'phone_2', 'domains_2',
  'official_website_2', 'proxy_locations_2', 'domain_path_2'].map((name) => `<input name="${name}">`).join('')}
  <textarea name="comments_2"></textarea><input type="file" name="abuse_file">
  <input type="hidden" name="cf-turnstile-response" value="fixture-only">
  <input type="submit" name="save_form_2" value="Submit"></form>`
const sleep = async () => {}
function load() {
  vi.spyOn(Element.prototype, 'getClientRects').mockReturnValue([{}] as unknown as DOMRectList)
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return (new Function(`${source}; return globalThis.PhishingToolFormAdapters.pop();`) as () => Adapter)()
}
afterEach(() => { document.body.innerHTML = ''; vi.restoreAllMocks() })

it('fills the supplied Phishing fields and N/A without touching verification, files or Submit', async () => {
  document.body.innerHTML = form()
  const adapter = load()
  const submit = vi.fn((event: Event) => { event.preventDefault() })
  document.querySelector('form')?.addEventListener('submit', submit)
  const fields = await adapter.waitUntilReady({document, sleep})
  if (!fields) throw new Error('Form missing')
  expect((await adapter.fill(task, fields, {sleep})).valid).toBe(true)
  expect(fields.domains.value).toBe('sub.fixture.test')
  expect(fields.url.value).toBe(task.target_url)
  expect(fields.official.value).toBe('N/A')
  expect(fields.proxy.value).toBe('N/A')
  expect(fields.confirm.value).toBe(task.contact_email)
  expect(fields.name.value).toBe(task.contact_name)
  expect(fields.details.value).toBe(task.draft)
  expect(document.querySelector<HTMLInputElement>('[name="phone_2"]')?.value).toBe('')
  expect(document.querySelector<HTMLInputElement>('[name="abuse_file"]')?.value).toBe('')
  expect(document.querySelector<HTMLInputElement>('[name="cf-turnstile-response"]')?.value).toBe('fixture-only')
  expect(adapter.captchaPending(document)).toBe(false)
  const response = document.querySelector<HTMLInputElement>('[name="cf-turnstile-response"]')
  if (!response) throw new Error('Missing verification fixture')
  response.value = '' 
  expect(adapter.captchaPending(document)).toBe(true)
  fields.email.value = 'operator@example.test'
  expect((await adapter.fill(task, fields, {sleep})).valid).toBe(false)
  expect(fields.email.value).toBe('operator@example.test')
  expect(adapter.submit()).toBe(false)
  expect(submit).not.toHaveBeenCalled()
  expect(adapter.matches(new URL('https://www.dynadot.com/report-abuse'))).toBe(true)
  for (const url of ['http://www.dynadot.com/report-abuse', 'https://evil.test/report-abuse', 'https://www.dynadot.com/report-abuse-other']) {
    expect(adapter.matches(new URL(url))).toBe(false)
  }
})

it('waits for the form revealed by an exact native Phishing option', async () => {
  document.body.innerHTML = '<select><option value="">Choose</option><option value="2">Phishing</option><option>Spam</option></select><main></main>'
  document.querySelector('select')?.addEventListener('change', () => {
    const main = document.querySelector('main')
    if (main) main.innerHTML = form()
  })
  const adapter = load()
  expect(await adapter.waitUntilReady({document, sleep})).not.toBeNull()
  expect(document.querySelector('select')?.value).toBe('2')
})

it('does not guess ambiguous selectors and permits manual selection while waiting', async () => {
  document.body.innerHTML = '<select><option>Choose</option><option>Phishing</option></select>'.repeat(2)
  const adapter = load()
  expect(await adapter.waitUntilReady({document, sleep})).toBeNull()
  expect([...document.querySelectorAll('select')].every((el) => el.value === 'Choose')).toBe(true)
  let waits = 0
  expect(await adapter.waitUntilReady({document, sleep: () => {
    if (++waits === 2) document.body.innerHTML = form()
    return Promise.resolve()
  }})).not.toBeNull()
})

it('repairs fields replaced by Vue and reports missing task information', async () => {
  document.body.innerHTML = form()
  const adapter = load()
  const fields = await adapter.waitUntilReady({document, sleep})
  if (!fields) throw new Error('Form missing')
  let waits = 0
  expect((await adapter.fill(task, fields, {sleep: () => {
    if (++waits === 1) document.body.innerHTML = form()
    return Promise.resolve()
  }})).valid).toBe(true)
  document.body.innerHTML = form()
  const empty = await adapter.waitUntilReady({document, sleep})
  if (!empty) throw new Error('Form missing')
  expect((await adapter.fill({...task, contact_name: ''}, empty, {sleep})).valid).toBe(false)
})

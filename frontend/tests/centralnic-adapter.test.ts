import source from '../extensions/cloudflare-profile-worker/adapters/centralnic_registry.js?raw'
import { afterEach, expect, it, vi } from 'vitest'

type Fields = Record<'firstName' | 'lastName' | 'email' | 'phone' | 'type' | 'details', HTMLInputElement>
interface Adapter {
  matches(url: URL): boolean
  waitUntilReady(context: {document: Document; sleep: () => Promise<void>}): Promise<Fields | null>
  fill(task: Record<string, string>, fields: Fields): Promise<{valid: boolean}>
  validate(fields: Fields): {valid: boolean}
  captchaPending(document: Document): boolean
  submit(): boolean
}
const formId = 'a89570f8-bec3-40be-a33b-10c9c21a09fe'
afterEach(() => { document.body.innerHTML = ''; vi.restoreAllMocks() })

it.each([false, true])('fills the exact CentralNic form (shadow root: %s) without submission', async (shadow) => {
  document.body.innerHTML = `<hubspot-form data-form-id="${formId}"></hubspot-form>`
  const host = document.querySelector('hubspot-form')
  if (!host) throw new Error('Missing fixture host')
  const root = shadow ? host.attachShadow({mode: 'open'}) : host
  root.innerHTML = `<form data-form-id="${formId}">
    <input name="firstname"><input name="lastname"><input name="email"><input name="phone">
    <select name="registry___how_can_we_help"><option value="">Please Select</option><option>Report Abuse</option></select>
    <textarea name="registry___notes"></textarea><textarea name="g-recaptcha-response"></textarea>
    <input type="hidden" name="hs_context" value="untouched"><button type="submit">Submit</button></form>`
  const submit = vi.fn((event: Event) => { event.preventDefault() })
  root.querySelector('form')?.addEventListener('submit', submit)
  vi.spyOn(Element.prototype, 'getClientRects').mockReturnValue([{}] as unknown as DOMRectList)
  // Execute the standalone extension script against the fixture DOM.
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const load = new Function(`${source}; return globalThis.PhishingToolFormAdapters.pop();`) as () => Adapter
  const adapter = load()
  expect(adapter.matches(new URL('https://centralnicregistry.com/contact/'))).toBe(true)
  for (const url of ['http://centralnicregistry.com/contact/', 'https://evil.test/contact/', 'https://centralnicregistry.com/contact-other']) {
    expect(adapter.matches(new URL(url))).toBe(false)
  }
  const fields = await adapter.waitUntilReady({document, sleep: async () => {}})
  if (!fields) throw new Error('Fixture form not detected')
  const task = {contact_name: 'Alice Example Reporter', contact_email: 'report@example.test', draft: 'Fixture English abuse report: https://fixture.uk.com/login'}
  expect((await adapter.fill(task, fields)).valid).toBe(false)
  expect(fields.firstName.value).toBe('Alice')
  expect(fields.lastName.value).toBe('Example Reporter')
  expect(fields.email.value).toBe(task.contact_email)
  expect(fields.details.value).toBe(task.draft)
  expect(fields.type.value).toBe('Report Abuse')
  expect(fields.phone.value).toBe('')
  expect(adapter.captchaPending(document)).toBe(true)
  fields.phone.value = '+1 202 555 0100'
  await adapter.fill(task, fields)
  expect(fields.phone.value).toBe('+1 202 555 0100')
  expect(adapter.validate(fields).valid).toBe(true)
  fields.lastName.value = ''
  await adapter.fill({...task, contact_name: 'Alice'}, fields)
  expect(fields.lastName.value).toBe('')
  expect(adapter.validate(fields).valid).toBe(false)
  expect((root.querySelector('[name="hs_context"]') as HTMLInputElement).value).toBe('untouched')
  expect((root.querySelector('[name="g-recaptcha-response"]') as HTMLTextAreaElement).value).toBe('')
  expect(adapter.submit()).toBe(false)
  expect(submit).not.toHaveBeenCalled()
})

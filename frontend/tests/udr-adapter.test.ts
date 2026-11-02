import source from '../extensions/cloudflare-profile-worker/adapters/udr_registry.js?raw'
import { expect, it, vi } from 'vitest'

type Fields = Record<'name' | 'email' | 'company' | 'phone' | 'domain' | 'type' | 'details', HTMLInputElement>
interface Adapter {
  matches(url: URL): boolean
  waitUntilReady(context: {document: Document; sleep: ReturnType<typeof vi.fn>}): Promise<Fields>
  fill(task: Record<string, string>, fields: Fields): Promise<{valid: boolean}>
  validate(fields: Fields): {valid: boolean}
  submit(): boolean
}

it('fills the UDR form while leaving phone, honeypot and submission manual', async () => {
  document.body.innerHTML = `<form class="contact-form">
    <input name="d0ntf1llth1s1n" value=" ">
    ${['name', 'email', 'company', 'phone', 'website'].map(id => `<input id="contact_${id}">`).join('')}
    <select id="contact_subject"><option>--Please Select--</option><option>Phishing or Pharming</option></select>
    <textarea id="contact_message"></textarea><input type="submit" value="Send">
  </form>`
  const submit = vi.fn((event: Event) => { event.preventDefault() })
  document.querySelector('form')?.addEventListener('submit', submit)
  const rect = vi.spyOn(Element.prototype, 'getClientRects').mockReturnValue([{}] as unknown as DOMRectList)
  // Execute the shipped standalone content script in the fixture DOM.
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const load = new Function(`${source}; return globalThis.PhishingToolFormAdapters.pop();`) as () => Adapter
  const adapter = load()
  expect(adapter.matches(new URL('https://www.udr.hk.com/report-abuse?utm_source=test'))).toBe(true)
  expect(adapter.matches(new URL('https://www.udr.hk.com/report-abuse/'))).toBe(true)
  for (const url of ['http://www.udr.hk.com/report-abuse', 'https://www.udr.hk.com/report-abuse-other', 'https://evil.test/report-abuse']) {
    expect(adapter.matches(new URL(url))).toBe(false)
  }
  const fields = await adapter.waitUntilReady({ document, sleep: vi.fn() })
  const task = {target_url: 'https://fixture.hk.com/login?q=1', contact_name: 'Reporter', contact_email: 'report@example.test', company_name: 'Fixture Company', draft: 'Fixture English report'}
  const result = await adapter.fill(task, fields)
  expect(result.valid).toBe(false)
  expect(fields.name.value).toBe(task.contact_name)
  expect(fields.email.value).toBe(task.contact_email)
  expect(fields.company.value).toBe(task.company_name)
  expect(fields.domain.value).toBe('fixture.hk.com')
  expect(fields.type.value).toBe('Phishing or Pharming')
  expect(fields.details.value).toBe(task.draft)
  expect(fields.phone.value).toBe('')
  fields.phone.value = '+1 202 555 0100'
  await adapter.fill(task, fields)
  expect(fields.phone.value).toBe('+1 202 555 0100')
  expect(adapter.validate(fields).valid).toBe(true)
  expect((document.querySelector('[name="d0ntf1llth1s1n"]') as HTMLInputElement).value).toBe(' ')
  expect(adapter.submit()).toBe(false)
  expect(submit).not.toHaveBeenCalled()
  rect.mockRestore()
  document.body.innerHTML = ''
})

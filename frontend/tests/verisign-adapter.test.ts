import source from '../extensions/cloudflare-profile-worker/adapters/verisign_dns_abuse.js?raw'
import { afterEach, expect, it, vi } from 'vitest'

type Fields = Record<string, HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>
interface Adapter {
  matches(url: URL): boolean
  waitUntilReady(ctx: { document: Document; sleep: () => Promise<void> }): Promise<Fields | null>
  fill(task: Record<string, string>, fields: Fields, ctx: { sleep: () => Promise<void> }): Promise<{ valid: boolean }>
  validate(fields: Fields): { valid: boolean }
  submit(): boolean
}
const sleep = async () => {}
const task = {target_url: 'https://sub.example.com/wap/#/register', target_domain: 'example.com',
  registrar: 'Fixture Registrar', contact_email: 'report@example.test', draft: 'English fixture report', brand_name: 'Fixture Brand'}
function mount(shadowDates = true) {
  const host = document.createElement('c-abuse-form')
  document.body.append(host)
  const root = host.attachShadow({mode: 'open'})
  root.innerHTML = '<select name="abuseType"><option value=""></option><option>DNS Abuse</option><option>Other</option></select><main></main>'
  root.querySelector('select')?.addEventListener('change', () => {
    const main = root.querySelector('main')
    if (!main) throw new Error('Missing main')
    main.innerHTML = `<select name="abuseCategory"><option value=""></option><option value="PHISHING">Phishing</option></select>
      <select name="tld"><option value=""></option>${['com', 'net', 'name', 'cc'].map(tld => `<option>${tld}</option>`).join('')}</select>
      <input name="domain"><input name="url"><input name="notifiedRegistrar"><input name="reporterEmail">
      <textarea name="description" maxlength="3000"></textarea><button type="button">Next</button>
      <input type="file"><textarea name="g-recaptcha-response"></textarea>`
    main.querySelector('select[name="abuseCategory"]')?.addEventListener('change', () => {
      if (!main.querySelector('[name="phishingDetails"]')) main.insertAdjacentHTML('beforeend', '<input name="phishingDetails">')
    })
    for (const name of ['observedDate', 'notifiedRegistrarDate']) {
      const picker = document.createElement('lightning-datepicker')
      main.append(picker)
      const shadow = shadowDates ? picker.attachShadow({mode: 'open'}) : picker
      shadow.innerHTML = `<input name="${name}"><button type="button" hidden>Today</button>`
      const input = shadow.querySelector('input')
      const today = shadow.querySelector('button')
      if (!input || !today) throw new Error('Missing date fixture')
      input.addEventListener('click', () => { today.hidden = false })
      today.addEventListener('click', () => {
        input.value = '11/10/2026'
        input.dispatchEvent(new Event('change', {bubbles: true, composed: true}))
        today.hidden = true
      })
    }
  })
  return root
}
function load() {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(2026, 10, 10, 23, 30))
  vi.spyOn(Element.prototype, 'getClientRects').mockImplementation(function (this: Element) {
    return (this.hasAttribute('hidden') ? [] : [{}]) as unknown as DOMRectList
  })
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return (new Function(`${source}; return globalThis.PhishingToolFormAdapters.pop();`) as () => Adapter)()
}
afterEach(() => { document.body.innerHTML = ''; vi.restoreAllMocks(); vi.useRealTimers() })

it('opens DNS Abuse through shadow DOM and fills Phishing, TLD, calendar Today, registrar and configured data', async () => {
  const root = mount(), adapter = load()
  const fields = await adapter.waitUntilReady({document, sleep})
  if (!fields) throw new Error('Form missing')
  const next = vi.fn()
  root.querySelector('main > button')?.addEventListener('click', next)
  expect((await adapter.fill(task, fields, {sleep})).valid).toBe(true)
  expect(fields.domains?.value).toBe('example.com')
  expect(fields.tld?.value).toBe('com')
  expect(fields.category?.value).toBe('PHISHING')
  expect(fields.observedDate?.value).toBe('11/10/2026')
  expect(fields.notificationDate?.value).toBe('11/10/2026')
  expect(fields.registrar?.value).toBe(task.registrar)
  expect(fields.email?.value).toBe(task.contact_email)
  expect(fields.url?.value).toBe(task.target_url)
  expect(fields.details?.value).toBe(task.draft)
  expect(fields.brand?.value).toBe(task.brand_name)
  expect(adapter.validate(fields).valid).toBe(true)
  expect(adapter.submit()).toBe(false)
  expect(next).not.toHaveBeenCalled()
  expect(root.querySelector<HTMLTextAreaElement>('[name="g-recaptcha-response"]')?.value).toBe('')
  expect(root.querySelector<HTMLInputElement>('[type="file"]')?.value).toBe('')
})

it('selects both dates in synthetic light DOM as seen by isolated content scripts', async () => {
  mount(false)
  const adapter = load(), fields = await adapter.waitUntilReady({document, sleep})
  if (!fields) throw new Error('Missing fields')
  expect((await adapter.fill(task, fields, {sleep})).valid).toBe(true)
  expect(fields.observedDate?.value).toBe('11/10/2026')
  expect(fields.notificationDate?.value).toBe('11/10/2026')
  if (fields.brand) fields.brand.value = 'Operator Brand'
  if (fields.observedDate) fields.observedDate.value = '11/09/2026'
  expect((await adapter.fill(task, fields, {sleep})).valid).toBe(false)
  expect(fields.brand?.value).toBe('Operator Brand')
  expect(fields.observedDate?.value).toBe('11/09/2026')
})

it('preserves operator values and marks conflicting data, unsupported TLD and oversized drafts for review', async () => {
  mount()
  const adapter = load(), fields = await adapter.waitUntilReady({document, sleep})
  if (!fields?.email) throw new Error('Form missing')
  fields.email.value = 'operator@example.test'
  expect((await adapter.fill(task, fields, {sleep})).valid).toBe(false)
  expect(fields.email.value).toBe('operator@example.test')
  fields.email.value = ''
  expect((await adapter.fill({...task, target_url: 'https://example.org', target_domain: 'example.org'}, fields, {sleep})).valid).toBe(false)
  expect(fields.tld?.value).toBe('com')
  if (fields.details) fields.details.value = ''
  expect((await adapter.fill({...task, draft: 'x'.repeat(3001)}, fields, {sleep})).valid).toBe(false)
  expect(fields.details?.value).toBe('')
})

it('allows missing registrar, refuses conflicting abuse selection and ambiguous form hosts', async () => {
  const root = mount(), adapter = load()
  const type = root.querySelector('select')
  if (!type) throw new Error('Missing type')
  type.value = 'Other'
  expect(await adapter.waitUntilReady({document, sleep})).toBeNull()
  type.value = ''
  const fields = await adapter.waitUntilReady({document, sleep})
  if (!fields) throw new Error('Missing fields')
  expect((await adapter.fill({...task, registrar: ''}, fields, {sleep})).valid).toBe(true)
  expect(fields.registrar?.value).toBe('')
  mount()
  expect(await adapter.waitUntilReady({document, sleep})).toBeNull()
})

it('matches only the verified HTTPS Verisign paths, including the redirect destination', () => {
  const adapter = load()
  for (const path of ['/DNSAbuse', '/ReportAbuse/']) expect(adapter.matches(new URL(`https://verisign.my.site.com${path}`))).toBe(true)
  for (const url of ['http://verisign.my.site.com/DNSAbuse', 'https://evil.test/DNSAbuse', 'https://verisign.my.site.com/DNSAbuseOther']) {
    expect(adapter.matches(new URL(url))).toBe(false)
  }
})

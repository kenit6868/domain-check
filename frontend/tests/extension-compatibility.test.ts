import { expect, it } from 'vitest'
import { compareExtensionVersions, minimumExtensionVersion, supportsFormProvider } from '../src/features/quick-report/extension/extensionCompatibility'
import type { FormProvider } from '../src/features/quick-report/schemas/formTaskSchema'

it.each([
  ['3.1.4', 'verisign_dns_abuse', false], ['3.1.5', 'verisign_dns_abuse', false],
  ['3.1.6', 'verisign_dns_abuse', true], ['3.10.0', 'verisign_dns_abuse', true],
  ['3.1.4', 'google_gsb', true], ['3.1.4', 'dynadot_phishing', true],
  ['3.1.0', 'udr_registry_abuse', false], ['3.1.1', 'udr_registry_abuse', true],
  ['3.1.1', 'centralnic_registry_abuse', false], ['3.1.2', 'centralnic_registry_abuse', true],
  ['3.1.2', 'godaddy_phishing', false], ['3.1.3', 'godaddy_phishing', true],
  ['3.0.0', 'google_gsb', false], ['unknown', 'google_gsb', false],
] as const)('checks %s compatibility with %s', (version, provider, expected) => {
  expect(supportsFormProvider(version, provider)).toBe(expected)
})

it('compares numeric versions, rejects invalid versions and covers every provider minimum', () => {
  expect(compareExtensionVersions('3.1.6.0', '3.1.6')).toBe(0)
  expect(compareExtensionVersions('invalid', '3.1.6')).toBeNull()
  expect(compareExtensionVersions('3.1.6', '3.1.x')).toBeNull()
  for (const [provider, minimum] of Object.entries(minimumExtensionVersion)) {
    expect(supportsFormProvider(minimum, provider as FormProvider)).toBe(true)
  }
})

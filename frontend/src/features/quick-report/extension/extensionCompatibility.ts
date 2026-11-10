import manifest from '../../../../extensions/cloudflare-profile-worker/manifest.json'
import type { FormProvider } from '../schemas/formTaskSchema'

// Import the manifest as a Vite module so dev HMR also sees version changes.
export const bundledExtensionVersion = manifest.version
export const minimumExtensionVersion: Record<FormProvider, string> = {
  google_gsb: '3.1.0', microsoft_smartscreen: '3.1.0', cloudflare: '3.1.0',
  chongluadao: '3.1.0', coccoc_safe: '3.1.0', registry_co_phishing: '3.1.0', xyz_registry_abuse: '3.1.0',
  udr_registry_abuse: '3.1.1', centralnic_registry_abuse: '3.1.2', godaddy_phishing: '3.1.3',
  dynadot_phishing: '3.1.4', verisign_dns_abuse: '3.1.6',
}
export function compareExtensionVersions(left: string, right: string): number | null {
  if (![left, right].every((value) => /^\d+(?:\.\d+){0,3}$/.test(value))) return null
  const a = left.split('.').map(Number), b = right.split('.').map(Number)
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const difference = (a[i] ?? 0) - (b[i] ?? 0)
    if (difference) return difference
  }
  return 0
}
export function supportsFormProvider(version: string, provider: FormProvider): boolean {
  const comparison = compareExtensionVersions(version, minimumExtensionVersion[provider])
  return comparison !== null && comparison >= 0
}

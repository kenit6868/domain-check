export interface ParsedTargets {
  valid: string[]
  invalid: string[]
}

const HOST_LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i

function isSupportedTarget(value: string): boolean {
  try {
    const parsed = new URL(value.includes('://') ? value : `https://${value}`)
    return ['http:', 'https:'].includes(parsed.protocol)
      && parsed.hostname.length <= 253
      && parsed.hostname.includes('.')
      && parsed.hostname.split('.').every((label) => HOST_LABEL.test(label))
  } catch {
    return false
  }
}

export function parseQuickReportTargets(raw: string): ParsedTargets {
  const valid: string[] = []
  const invalid: string[] = []
  for (const candidate of raw.split(/[\n,;]+/).map((item) => item.trim()).filter(Boolean)) {
    if (isSupportedTarget(candidate)) valid.push(candidate)
    else invalid.push(candidate)
  }
  return { valid, invalid }
}

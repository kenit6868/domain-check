import { z } from 'zod'
import { request } from '../../../lib/http/client'

const checkSchema = z.object({
  id: z.uuid(), requested_url: z.string(), domain: z.string(), status: z.string(),
  result: z.record(z.string(), z.unknown()), cache_provenance: z.record(z.string(), z.unknown()),
})
const operationSchema = z.object({
  id: z.uuid(), state: z.string(), stage: z.string(), business_day: z.string(), check: checkSchema,
})
const guidanceSchema = z.array(z.object({
  channel: z.enum(['browser', 'cdn', 'registrar', 'registry']), label: z.string(),
  form_url: z.url().nullable(), draft: z.string(), note: z.string(),
}))

export type DomainCheckOperation = z.infer<typeof operationSchema>
export type ReportGuide = z.infer<typeof guidanceSchema>[number]

export async function createDomainCheck(target: string, submitVt: boolean) {
  return operationSchema.parse(await request<unknown>({ url: '/domain-checks/checks', method: 'POST',
    data: { target, submit_vt: submitVt } }))
}
export async function getDomainCheck(id: string, signal?: AbortSignal) {
  return operationSchema.parse(await request<unknown>({ url: `/domain-checks/checks/${id}`,
    ...(signal ? { signal } : {}) }))
}
export async function getReportGuidance(checkId: string, signal?: AbortSignal) {
  return guidanceSchema.parse(await request<unknown>({ url: `/domain-checks/checks/${checkId}/report-guidance`,
    ...(signal ? { signal } : {}) }))
}

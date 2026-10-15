import { z } from 'zod'

export const reportFormSchema = z.object({ channel: z.enum(['registrar', 'registry']), label: z.string(),
  form_url: z.url(), autofill_provider: z.string().nullable(), note: z.string() })

export const quickReportCheckSchema = z.object({
  id: z.uuid(),
  requested_url: z.string(),
  domain: z.string(),
  status: z.string(),
  result: z.record(z.string(), z.unknown()),
  report_forms: z.array(reportFormSchema).optional(),
  providers: z.array(z.object({ id: z.enum(['google_gsb', 'microsoft_smartscreen', 'cloudflare', 'chongluadao', 'coccoc_safe', 'godaddy_phishing', 'registry_co_phishing', 'xyz_registry_abuse']), label: z.string(), form_url: z.url() })).optional(),
  cache_provenance: z.record(z.string(), z.unknown()),
})

export const quickReportOperationSchema = z.object({
  id: z.uuid(),
  state: z.string(),
  stage: z.string(),
  business_day: z.string(),
  checks: z.array(quickReportCheckSchema),
  duplicates: z.array(z.string()),
})

export type QuickReportCheck = z.infer<typeof quickReportCheckSchema>
export type QuickReportOperation = z.infer<typeof quickReportOperationSchema>

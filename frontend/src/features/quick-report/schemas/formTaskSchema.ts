import { z } from 'zod'

export const formTaskCreatedSchema = z.object({
  id: z.uuid(), capability_token: z.string(), provider: z.string(), form_url: z.url(),
  target_url: z.url(), mode: z.literal('fill_only'), state: z.string(), expires_at: z.string(),
})

export const formTaskStatusSchema = z.object({
  id: z.uuid(), provider: z.string(), target_url: z.url(), form_url: z.url(),
  mode: z.literal('fill_only'), state: z.string(), expires_at: z.string(),
  status: z.record(z.string(), z.unknown()).nullable(),
})

export type FormTaskStatus = z.infer<typeof formTaskStatusSchema>
export type FormProvider = 'google_gsb' | 'microsoft_smartscreen' | 'cloudflare' | 'chongluadao' | 'coccoc_safe' | 'godaddy_phishing' | 'registry_co_phishing' | 'xyz_registry_abuse'

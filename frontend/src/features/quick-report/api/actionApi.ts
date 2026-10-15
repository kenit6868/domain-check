import { z } from 'zod'
import { request } from '../../../lib/http/client'

const submissionSchema = z.object({
  id: z.uuid(), state: z.enum(['PREVIEW', 'SENDING', 'SUBMITTED', 'FAILED', 'UNKNOWN']),
  revision: z.number().int(), fingerprint: z.string(), expires_at: z.string(),
  submit_enabled: z.boolean().optional(),
  payload: z.object({ email: z.string(), urls: z.array(z.object({ url: z.string(), country: z.string(), reason: z.string() })) }).nullable(),
  result: z.record(z.string(), z.unknown()),
})
export type NetcraftSubmission = z.infer<typeof submissionSchema>

export async function getNetcraft(checkId: string, signal?: AbortSignal) {
  const data = await request<unknown>({ url: `/quick-reports/checks/${checkId}/netcraft`, ...(signal ? { signal } : {}) })
  return data === null ? null : submissionSchema.parse(data)
}
export async function previewNetcraft(checkId: string) {
  return submissionSchema.parse(await request<unknown>({ url: `/quick-reports/checks/${checkId}/netcraft/preview`, method: 'POST' }))
}
export async function submitNetcraft(preview: NetcraftSubmission) {
  return submissionSchema.parse(await request<unknown>({ url: `/quick-reports/netcraft/${preview.id}/submit`, method: 'POST',
    data: { fingerprint: preview.fingerprint, revision: preview.revision, confirmed: true, retry: preview.state === 'FAILED' } }))
}

import { z } from 'zod'
import { request } from '../../lib/http/client'

const delivery = z.object({ sender_account_id: z.uuid(), recipient: z.string(), channel: z.string(),
  subject: z.string(), text_body: z.string(), html_body: z.string().optional(), draft_identity: z.string() })
const cloaking = z.object({ verdict: z.string().optional(), score: z.number().optional(),
  profiles: z.record(z.string(), z.object({ label: z.string().optional(), status_code: z.number().nullable().optional(),
    final_url: z.string().optional(), title: z.string().optional() })).optional() })
export const exactPreview = z.object({ id: z.uuid(), check_id: z.uuid(), revision: z.number(), fingerprint: z.string(),
  expires_at: z.string(), requested_url: z.string(), deliveries: z.array(delivery) })
export type EvidencePreview = z.infer<typeof exactPreview>
const detail = z.object({ id: z.uuid(), check_id: z.uuid(), state: z.string(), requested_url: z.string(), evidence_version: z.number(),
  accounts: z.array(z.object({ id: z.uuid(), sender_email: z.string(), enabled: z.boolean() })),
  sender_account_ids: z.array(z.uuid()), recipients: z.array(z.object({ channel: z.string(), email: z.string() })),
  evidence_reason: z.string().nullable(), source_job_ids: z.array(z.uuid()),
  images: z.array(z.object({ id: z.uuid(), mime_type: z.string(), byte_size: z.number() })),
  capture_decision: z.string().nullable().optional(), capture_images: z.array(z.object({ id: z.uuid(), mime_type: z.string(), byte_size: z.number() })).optional(),
  capture_profile_names: z.array(z.string()).optional(),
  capture_comparison_only: z.boolean().optional(),
  capture_pending: z.boolean().optional(), capture_reason: z.string().nullable().optional(),
  decision: z.string().nullable().optional(), cloaking: cloaking.optional(),
  preview: exactPreview.nullable(), deliveries: z.array(z.object({ id: z.uuid().nullable(), sender_account_id: z.uuid(),
    recipient: z.string(), channel: z.string(), state: z.string(), attempt_count: z.number().default(0),
    error_code: z.string().nullable().optional(), sent_copy_status: z.string().nullable().optional() })), errors: z.array(z.string()).optional() })
export async function getEvidenceReview(id: string, signal?: AbortSignal, kind: 'evidence' | 'cloaking' = 'evidence') {
  return detail.parse(await request({ url: `/domain-worker/reviews/${kind}/${id}`, timeout: 30000, ...(signal ? { signal } : {}) }))
}
export async function previewEvidenceReview(id: string, accounts: string[], files: File[], kind: 'evidence' | 'cloaking' = 'evidence', decision = '', confirmedDifference = false, useCapture = false) {
  const data = new FormData()
  if (kind === 'cloaking') { data.append('use_capture', String(useCapture)); data.append('decision', decision); data.append('confirmed_difference', String(confirmedDifference)) }
  data.append('account_ids', JSON.stringify(accounts)); files.forEach((file) => { data.append('files', file) })
  return z.object({ preview: exactPreview, case_id: z.uuid(), evidence_version: z.number() }).parse(await request({
    url: `/domain-worker/reviews/${kind}/${id}/previews`, method: 'POST', data, timeout: 120000 }))
}
export async function sendEvidenceReview(id: string, preview: EvidencePreview, kind: 'evidence' | 'cloaking' = 'evidence') {
  return detail.parse(await request({ url: `/domain-worker/reviews/${kind}/${id}/send`, method: 'POST', timeout: 600000,
    data: { check_id: preview.check_id, preview_id: preview.id, revision: preview.revision, fingerprint: preview.fingerprint, confirmed: true } }))
}

export async function captureCloakingReview(id: string, decision: string) {
  return detail.parse(await request({ url: `/domain-worker/reviews/cloaking/${id}/capture`, method: 'POST', data: { decision }, timeout: 210000 }))
}

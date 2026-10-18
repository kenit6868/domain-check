import { z } from 'zod'
import { request } from '../../lib/http/client'

export const accountSchema = z.array(z.object({ id: z.uuid(), sender_email: z.string(), enabled: z.boolean(), imap_configured: z.boolean() }))
const delivery = z.object({ sender_account_id: z.uuid(), recipient: z.string(), subject: z.string(), text_body: z.string(), in_reply_to: z.string() })
export const previewSchema = z.object({ id: z.uuid(), revision: z.number(), fingerprint: z.string(), deliveries: z.array(delivery), expires_at: z.string().optional(), warnings: z.array(z.string()).optional() })
export type ReplyPreview = z.infer<typeof previewSchema>
const folders = z.array(z.object({ folder: z.string(), mailbox: z.string(), fetched: z.number(), matched: z.number(), skipped_large: z.number().default(0), error_code: z.string().nullable(), limited: z.boolean().default(false), processed: z.number().optional(), total: z.number().optional() }))
export const progressSchema = z.object({ progress: z.object({ sync_id: z.string(), status: z.string(), stage: z.string(), folder: z.string(), processed: z.number(), total: z.number(), folder_index: z.number(), folder_total: z.number(), updated_at: z.string(), folders,
  imported: z.number().optional() }).nullable() })
export const listSchema = z.object({ items: z.array(z.object({ id: z.uuid(), account_id: z.uuid(), state: z.string(), subject: z.string(), sender: z.string(), provider: z.string().default('unknown'), provider_label: z.string(), request_label: z.string(), needs_reply: z.boolean(), linked_delivery_id: z.string().nullable(), matched: z.boolean().default(false), received_at: z.string(), location: z.object({ mailbox: z.string() }), seen: z.boolean(), requested_url: z.string() })),
  last_sync: z.object({ synced_at: z.string(), date_from: z.string().optional(), date_to: z.string().optional(), folders }).nullable() })
export type ReplyList = z.infer<typeof listSchema>
export const detailSchema = z.object({ id: z.uuid(), account_id: z.uuid(), state: z.string(), matched: z.boolean(), match_reason: z.string().nullable(), needs_reply: z.boolean(), seen: z.boolean(),
  reply_supported: z.boolean(), reply_block_reason: z.string().nullable(), capture_enabled: z.boolean().optional(),
  capture_summary: z.object({ label: z.string(), resolved_destination: z.string() }).nullable().optional(),
  sender_email: z.string(), suggestion: z.object({ subject: z.string(), body: z.string() }),
  reading_aid: z.string(),
  mail: z.object({ subject: z.string(), sender: z.string(), reply_to: z.string(), body: z.string(), provider_label: z.string(), request_label: z.string(), risk: z.string(), request_type: z.string(), channel: z.string(), ticket: z.string(), message_id: z.string(), source_mailbox: z.string() }),
  context: z.object({ reported_url: z.string(), official_url: z.string(), evidence: z.string() }), preview: previewSchema.nullable(),
  images: z.array(z.object({ id: z.uuid(), mime_type: z.string(), byte_size: z.number() })), attempt_count: z.number(), error_code: z.string().nullable(), sent_copy_status: z.string().nullable() })
export type ReplyDetail = z.infer<typeof detailSchema>
export type ReplyFields = { reported_url: string; official_url: string; evidence: string; subject: string; body: string; legal_approved: boolean }
export const getReply = async (id: string, signal?: AbortSignal) => detailSchema.parse(await request({ url: `/provider-replies/${id}`, ...(signal ? { signal } : {}) }))
export async function makePreview(id: string, details: ReplyFields, files: File[]) {
  const data = new FormData(); data.append('details', JSON.stringify(details)); files.forEach((file) => { data.append('files', file) })
  return previewSchema.parse(await request({ url: `/provider-replies/${id}/previews`, method: 'POST', data, timeout: 120000 }))
}
export async function sendReply(id: string, preview: ReplyPreview) {
  return detailSchema.parse(await request({ url: `/provider-replies/${id}/send`, method: 'POST', timeout: 300000,
    data: { preview_id: preview.id, revision: preview.revision, fingerprint: preview.fingerprint, confirmed: true } }))
}

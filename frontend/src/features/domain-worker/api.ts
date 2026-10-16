import { z } from 'zod'
import { request } from '../../lib/http/client'

const item = z.object({ id: z.uuid(), item_id: z.uuid(), requested_url: z.string(), domain: z.string(),
  status: z.string(), disposition: z.string(), recipients: z.array(z.object({ channel: z.string(), email: z.string() })),
  cloaking_verdict: z.string().nullable(), case_id: z.uuid().nullable(), evidence_set_id: z.uuid().nullable(),
  evidence_reason: z.string().nullable(), evidence_failure_code: z.string().nullable().optional(), browser_verification_pending: z.boolean() })
const operation = z.object({ id: z.uuid(), state: z.string(), stage: z.string(), business_day: z.string(),
  is_current_day: z.boolean().default(true), sender_account_ids: z.array(z.uuid()).default([]), checks: z.array(item) })
export type WorkerOperation = z.infer<typeof operation>
export type WorkerItem = z.infer<typeof item>
export async function createPrecheck(targets: string[], senderAccountIds: string[]) {
  return operation.parse(await request({ url: '/domain-worker/prechecks', method: 'POST', data: { targets, sender_account_ids: senderAccountIds } }))
}
export async function getWorkerAccounts(signal?: AbortSignal) {
  return z.array(z.object({ id: z.uuid(), label: z.string(), sender_email: z.string() })).parse(
    await request({ url: '/domain-worker/accounts', ...(signal ? { signal } : {}) }))
}
export async function getPrecheck(id: string, signal?: AbortSignal) {
  return operation.parse(await request({ url: `/domain-worker/prechecks/${id}`, ...(signal ? { signal } : {}) }))
}
export async function controlPrecheck(id: string, action: 'stop' | 'resume' | 'retry', itemIds: string[] = []) {
  return operation.parse(await request({ url: `/domain-worker/prechecks/${id}/${action}`, method: 'POST',
    ...(action === 'retry' ? { data: { item_ids: itemIds } } : {}) }))
}
export async function getReadiness(signal?: AbortSignal) {
  return z.object({ ready: z.boolean(), missing: z.array(z.string()) }).parse(await request({
    url: '/domain-worker/readiness', ...(signal ? { signal } : {}) }))
}

const sendSummary = z.object({ business_day: z.string(), ready_domains: z.number(), total_deliveries: z.number(),
  already_sent: z.number(), blocked_deliveries: z.number(), pending_deliveries: z.number(),
  checks: z.array(z.object({ check_id: z.uuid(), requested_url: z.string(), pending_deliveries: z.number() })),
  send_job_id: z.uuid().nullable() })
const sendJob = z.object({ id: z.uuid(), state: z.string(), business_day: z.string(), batch_size: z.number(),
  interval_seconds: z.number(), next_run_at: z.string().nullable(), items: z.array(z.object({
    id: z.uuid(), requested_url: z.string(), state: z.string(), reason_code: z.string().nullable().optional(),
    deliveries: z.array(z.object({ id: z.uuid(), recipient: z.string(), sender_account_id: z.uuid(), channel: z.string(), state: z.string(),
      sent_copy_status: z.string().default('not_recorded') })) })) })
export async function getSendSummary(id: string, signal?: AbortSignal) {
  return sendSummary.parse(await request({ url: `/domain-worker/prechecks/${id}/send-summary`, ...(signal ? { signal } : {}) }))
}
export async function startSendBatch(id: string, batchSize: number, intervalSeconds: number, confirmed: boolean) {
  return sendJob.parse(await request({ url: `/domain-worker/prechecks/${id}/send`, method: 'POST',
    data: { batch_size: batchSize, interval_seconds: intervalSeconds, confirmed } }))
}
export async function getSendJob(id: string, signal?: AbortSignal) {
  return sendJob.parse(await request({ url: `/domain-worker/send-jobs/${id}`, ...(signal ? { signal } : {}) }))
}
export async function controlSendJob(id: string, action: 'stop' | 'resume' | 'retry') {
  return sendJob.parse(await request({ url: `/domain-worker/send-jobs/${id}/${action}`, method: 'POST' }))
}

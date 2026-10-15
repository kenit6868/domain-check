import { request } from '../../../lib/http/client'
import { quickReportCheckSchema, quickReportOperationSchema, type QuickReportOperation } from '../schemas/quickReportSchema'
import { z } from 'zod'

export async function getDailyQuickReports(signal?: AbortSignal) {
  return z.object({ business_day: z.string(), checks: z.array(quickReportCheckSchema) }).parse(
    await request<unknown>({ url: '/quick-reports/daily', ...(signal ? { signal } : {}) }))
}

export async function createQuickReport(targets: string[]): Promise<QuickReportOperation> {
  const payload = await request<unknown>({
    url: '/quick-reports/checks',
    method: 'POST',
    data: { targets },
  })
  return quickReportOperationSchema.parse(payload)
}

export async function getQuickReport(operationId: string, signal?: AbortSignal): Promise<QuickReportOperation> {
  const payload = await request<unknown>({ url: `/quick-reports/checks/${operationId}`, ...(signal ? { signal } : {}) })
  return quickReportOperationSchema.parse(payload)
}

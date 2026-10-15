import { request } from '../../../lib/http/client'
import { formTaskCreatedSchema, formTaskStatusSchema, type FormProvider } from '../schemas/formTaskSchema'

export const registerExtensionDevice = (deviceId: string, extensionVersion: string) =>
  request<{ registered: boolean; extension_version: string }>({
    url: '/extension/devices', method: 'POST',
    data: { device_id: deviceId, extension_version: extensionVersion },
  })

export async function createFormTask(checkId: string, provider: FormProvider, deviceId: string) {
  const payload = await request<unknown>({
    url: `/quick-reports/checks/${checkId}/form-tasks`, method: 'POST',
    data: { provider, device_id: deviceId },
  })
  return formTaskCreatedSchema.parse(payload)
}

export async function getFormTask(taskId: string, signal?: AbortSignal) {
  const payload = await request<unknown>({
    url: `/form-tasks/${taskId}`, ...(signal ? { signal } : {}),
  })
  return formTaskStatusSchema.parse(payload)
}

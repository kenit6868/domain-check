import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import * as bridge from '../src/features/quick-report/extension/extensionBridge'
import { useExtensionConnection } from '../src/features/quick-report/hooks/useExtensionConnection'

const id = 'a'.repeat(32)
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('distinguishes missing ID, invalid ID and unavailable browser API', async () => {
  vi.stubEnv('VITE_FORM_ASSISTANT_EXTENSION_ID', '')
  await expect(bridge.pingExtension()).rejects.toThrow('Frontend chưa có VITE_FORM_ASSISTANT_EXTENSION_ID')
  vi.stubEnv('VITE_FORM_ASSISTANT_EXTENSION_ID', 'wrong-id')
  await expect(bridge.pingExtension()).rejects.toThrow('ID extension không hợp lệ')
  vi.stubEnv('VITE_FORM_ASSISTANT_EXTENSION_ID', id)
  vi.stubGlobal('chrome', undefined)
  await expect(bridge.pingExtension()).rejects.toThrow('Trang chưa có API kết nối extension')
})

it('trims public ID and accepts a real successful handshake', async () => {
  vi.stubEnv('VITE_FORM_ASSISTANT_EXTENSION_ID', ` ${id} `)
  const sendMessage = vi.fn((_id: string, _message: unknown, callback: (response: unknown) => void) => {
    callback({ ok: true, deviceId: 'fixture-device', extensionVersion: '3.0.0' })
  })
  vi.stubGlobal('chrome', { runtime: { sendMessage } })
  await expect(bridge.pingExtension()).resolves.toEqual({ deviceId: 'fixture-device', extensionVersion: '3.0.0' })
  expect(sendMessage).toHaveBeenCalledWith(id, { type: 'domaincheck-ping' }, expect.any(Function))
})

it('explains exact origin setup instead of exposing extension internal errors', async () => {
  vi.stubEnv('VITE_FORM_ASSISTANT_EXTENSION_ID', id)
  vi.stubGlobal('chrome', { runtime: { sendMessage: (_id: string, _message: unknown, callback: (response: unknown) => void) => {
    callback({ ok: false, error: 'Error: origin_not_allowed' })
  } } })
  await expect(bridge.pingExtension()).rejects.toThrow('Application origin')
})

it('distinguishes missing receiving extension from origin rejection', async () => {
  vi.stubEnv('VITE_FORM_ASSISTANT_EXTENSION_ID', id)
  vi.stubGlobal('chrome', { runtime: { lastError: { message: 'Could not establish connection. Receiving end does not exist.' },
    sendMessage: (_id: string, _message: unknown, callback: (response: unknown) => void) => { callback(undefined) } } })
  await expect(bridge.pingExtension()).rejects.toThrow('Không tìm thấy extension nhận message theo ID frontend')
})

it('handles a synchronous browser failure and times out a silent extension', async () => {
  vi.stubEnv('VITE_FORM_ASSISTANT_EXTENSION_ID', id)
  vi.stubGlobal('chrome', { runtime: { sendMessage: () => { throw new Error('private-runtime-data') } } })
  await expect(bridge.pingExtension()).rejects.toThrow('Không kết nối được extension')
  vi.useFakeTimers()
  vi.stubGlobal('chrome', { runtime: { sendMessage: () => undefined } })
  const result = expect(bridge.pingExtension()).rejects.toThrow('Extension không phản hồi')
  await vi.advanceTimersByTimeAsync(2_500)
  await result
})

it('lets manual retry recover and ignores an older failed initial ping', async () => {
  let rejectInitial: (error: Error) => void = () => undefined
  const initial = new Promise<bridge.ExtensionConnection>((_resolve, reject) => { rejectInitial = reject })
  vi.spyOn(bridge, 'pingExtension').mockReturnValueOnce(initial).mockResolvedValue({ deviceId: 'fixture', extensionVersion: '3.0.0' })
  const { result } = renderHook(() => useExtensionConnection(true))
  await act(async () => { await result.current.refresh() })
  expect(result.current.state).toBe('connected')
  await act(async () => { rejectInitial(new Error('old failure')); await Promise.resolve() })
  expect(result.current.state).toBe('connected')
  expect(result.current.message).toBe('')
})

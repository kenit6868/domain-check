import { Theme } from '@radix-ui/themes'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ExtensionConnectionStatus } from '../src/features/quick-report/components/ExtensionConnectionStatus'
import manifest from '../extensions/cloudflare-profile-worker/manifest.json'

afterEach(() => { cleanup(); vi.unstubAllEnvs() })

it('uses the current manifest even when an old startup version remains in the environment', () => {
  vi.stubEnv('VITE_BUNDLED_EXTENSION_VERSION', '3.1.4')
  render(<Theme><ExtensionConnectionStatus extension={{ connection: null, state: 'disconnected', message: '', refresh: vi.fn() }} /></Theme>)
  const link = screen.getByRole('link', { name: `Tải extension v${manifest.version}` })
  expect(link).toHaveAttribute('href', `/downloads/web-form-assistant.zip?v=${manifest.version}`)
  expect(link).toHaveAttribute('download', 'web-form-assistant.zip')
  expect(screen.getByText(/Giải nén ZIP/)).toHaveTextContent('đúng thư mục đã cài')
})

it.each([['3.1.4', true], ['3.1.5', true], [manifest.version, false], ['3.10.0', false], ['unknown', true]])('shows update and compatibility guidance for installed %s', (installed, update) => {
  render(<Theme><ExtensionConnectionStatus extension={{ connection: { deviceId: 'fixture', extensionVersion: installed }, state: 'connected', message: '', refresh: vi.fn() }} /></Theme>)
  expect(screen.getByRole('link', { name: `${update ? 'Tải bản cập nhật' : 'Tải extension'} v${manifest.version}` })).toBeInTheDocument()
  expect(screen.queryByRole('alert') !== null).toBe(update)
  if (update) expect(screen.getByRole('alert')).toHaveTextContent('các form cần bản mới vẫn có thể mở thủ công')
})

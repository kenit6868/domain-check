import { Theme } from '@radix-ui/themes'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ExtensionConnectionStatus } from '../src/features/quick-report/components/ExtensionConnectionStatus'

afterEach(() => { cleanup(); vi.unstubAllEnvs() })

it('offers the bundled ZIP and installation instructions when disconnected', () => {
  vi.stubEnv('VITE_BUNDLED_EXTENSION_VERSION', '3.0.0')
  render(<Theme><ExtensionConnectionStatus extension={{ connection: null, state: 'disconnected', message: '', refresh: vi.fn() }} /></Theme>)
  expect(screen.getByRole('link', { name: 'Tải extension v3.0.0' })).toHaveAttribute('href', '/downloads/web-form-assistant.zip?v=3.0.0')
  expect(screen.getByRole('link', { name: 'Tải extension v3.0.0' })).toHaveAttribute('download', 'web-form-assistant.zip')
  expect(screen.getByText(/Giải nén ZIP/)).toHaveTextContent('đúng thư mục đã cài')
})

it.each([['2.9.0', true], ['3.0.0', false], ['3.10.0', false]])('compares installed %s numerically before suggesting an update', (installed, update) => {
  vi.stubEnv('VITE_BUNDLED_EXTENSION_VERSION', '3.0.0')
  render(<Theme><ExtensionConnectionStatus extension={{ connection: { deviceId: 'fixture', extensionVersion: installed }, state: 'connected', message: '', refresh: vi.fn() }} /></Theme>)
  expect(screen.getByRole('link', { name: update ? 'Tải bản cập nhật v3.0.0' : 'Tải extension v3.0.0' })).toBeInTheDocument()
})

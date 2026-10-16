import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { SignatureLogoControl } from '../src/features/mail-settings/components/SignatureLogoControl'
import { createQueryClient } from '../src/lib/query/client'
import { installApiMock } from './httpMock'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('always displays the default logo and uploads a replacement only when selected', async () => {
  vi.stubGlobal('URL', class extends URL { static createObjectURL() { return 'blob:logo-preview' } static revokeObjectURL() {} })
  const saved = vi.fn()
  const calls: Array<{ method: string; data: unknown }> = []
  const id = '11111111-1111-4111-8111-111111111111'
  const restore = installApiMock((config) => { calls.push({ method: config.method ?? '', data: config.data as unknown }); return { logo_artifact_id: id } })
  const client = createQueryClient()
  const { rerender } = render(<QueryClientProvider client={client}><SignatureLogoControl artifactId={null} disabled={false} onSaved={saved} /></QueryClientProvider>)
  try {
    expect(screen.getByAltText('Logo chữ ký')).toHaveAttribute('src', '/api/v1/settings/profile/logo')
    expect(calls).toHaveLength(0)
    const file = new File(['png'], 'new-logo.png', { type: 'image/png' })
    await userEvent.upload(screen.getByLabelText('Thay logo (PNG/JPEG, tối đa 2 MB)'), file)
    expect(saved).toHaveBeenCalledOnce()
    expect(calls[0]?.method).toBe('put')
    expect((calls[0]?.data as FormData).get('file')).toBe(file)
    rerender(<QueryClientProvider client={client}><SignatureLogoControl artifactId={id} disabled={false} onSaved={saved} /></QueryClientProvider>)
    expect(screen.getByAltText('Logo chữ ký')).toHaveAttribute('src', `/api/v1/artifacts/${id}`)
    await userEvent.click(screen.getByRole('button', { name: 'Khôi phục logo mặc định' }))
    expect(calls[1]?.method).toBe('delete')
  } finally { restore() }
})

it('rejects an oversized logo without uploading', async () => {
  const restore = installApiMock(() => { throw new Error('Must not upload') })
  try {
    render(<QueryClientProvider client={createQueryClient()}><SignatureLogoControl artifactId={null} disabled={false} onSaved={vi.fn()} /></QueryClientProvider>)
    await userEvent.upload(screen.getByLabelText('Thay logo (PNG/JPEG, tối đa 2 MB)'),
      new File([new Uint8Array(2 * 1024 * 1024 + 1)], 'too-large.png', { type: 'image/png' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Chọn ảnh PNG/JPEG tối đa 2 MB.')
  } finally { restore() }
})

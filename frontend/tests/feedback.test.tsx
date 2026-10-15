import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppToaster } from '../src/components/shared/AppToaster'
import { AppErrorBoundary } from '../src/components/shared/AppErrorBoundary'
import { ThemeContext } from '../src/app/providers/theme-context'
import { ConnectionNotice } from '../src/components/shared/ConnectionNotice'
import { useConfirmation } from '../src/components/shared/useConfirmation'
import { createQueryClient } from '../src/lib/query/client'
import { notifyError, toast } from '../src/lib/notifications'
import { ApiError } from '../src/lib/http/errors'

afterEach(() => { cleanup(); toast.dismiss(); vi.restoreAllMocks() })

describe('application feedback', () => {
  it('shows accessible dismissible notifications', async () => {
    const view = render(<ThemeContext.Provider value={{ appearance: 'light', setAppearance: () => undefined }}><AppToaster /></ThemeContext.Provider>)
    act(() => { toast.success('Đã lưu cấu hình thử nghiệm.', { duration: Infinity }) })
    expect(await screen.findByText('Đã lưu cấu hình thử nghiệm.')).toBeInTheDocument()
    expect(view.container.querySelector('[data-sonner-toaster]')).toHaveAttribute('data-sonner-theme', 'light')
    view.rerender(<ThemeContext.Provider value={{ appearance: 'dark', setAppearance: () => undefined }}><AppToaster /></ThemeContext.Provider>)
    await waitFor(() => { expect(view.container.querySelector('[data-sonner-toaster]')).toHaveAttribute('data-sonner-theme', 'dark') })
    fireEvent.click(screen.getByRole('button', { name: 'Đóng thông báo' }))
  })

  it('provides a recovery screen instead of leaving the page blank after a render error', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    function Broken(): never { throw new Error('fixture') }
    render(<AppErrorBoundary><Broken /></AppErrorBoundary>)
    expect(screen.getByRole('alert')).toHaveTextContent('Không thể hiển thị trang')
    expect(screen.getByRole('button', { name: 'Tải lại trang' })).toBeInTheDocument()
  })

  it('updates the pending toast on success and keeps query polling silent', async () => {
    const loading = vi.spyOn(toast, 'loading').mockReturnValue('pending-fixture')
    const success = vi.spyOn(toast, 'success')
    const client = createQueryClient()
    const mutation = client.getMutationCache().build(client, {
      mutationFn: () => Promise.resolve('saved'), meta: { pendingMessage: 'Saving', successMessage: 'Saved' },
    })
    await mutation.execute(undefined)
    expect(loading).toHaveBeenCalledWith('Saving')
    expect(success).toHaveBeenCalledWith('Saved', { id: 'pending-fixture' })
    await client.query({ queryKey: ['poll-fixture'], queryFn: () => Promise.resolve('complete') })
    expect(success).toHaveBeenCalledTimes(1)
    client.clear()
  })

  it('reports failed mutations without exposing raw API content', async () => {
    const error = vi.spyOn(toast, 'error')
    const client = createQueryClient()
    const mutation = client.getMutationCache().build(client, {
      mutationFn: () => Promise.reject(new ApiError('private-token-and-contact', 403)),
    })
    await expect(mutation.execute(undefined)).rejects.toThrow('private-token')
    expect(error).toHaveBeenCalledWith('Bạn không có quyền thực hiện thao tác này.', expect.any(Object))
    expect(JSON.stringify(error.mock.calls)).not.toContain('private-token')
    client.clear()
    notifyError(new ApiError('secret', 401))
    expect(error).toHaveBeenLastCalledWith(expect.stringContaining('hết hạn'), expect.any(Object))
  })

  it('requires explicit confirmation and supports cancel', async () => {
    const action = vi.fn()
    function Fixture() {
      const { ask, confirmation } = useConfirmation()
      return <><button onClick={() => { ask({ title: 'Xóa mục?', description: 'Kiểm tra trước khi xóa.', label: 'Xác nhận xóa', action }) }}>Xóa</button>{confirmation}</>
    }
    render(<Fixture />)
    await userEvent.click(screen.getByRole('button', { name: 'Xóa' }))
    expect(action).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    expect(action).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Xóa' }))
    await userEvent.click(screen.getByRole('button', { name: 'Xác nhận xóa' }))
    expect(action).toHaveBeenCalledTimes(1)
  })

  it('shows an offline banner and announces restored connectivity', async () => {
    const info = vi.spyOn(toast, 'info')
    render(<ConnectionNotice />)
    fireEvent(window, new Event('offline'))
    expect(screen.getByRole('status')).toHaveTextContent('Đang mất kết nối mạng')
    fireEvent(window, new Event('online'))
    await waitFor(() => { expect(screen.queryByRole('status')).not.toBeInTheDocument() })
    expect(info).toHaveBeenCalledTimes(1)
  })
})

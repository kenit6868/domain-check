import { Toaster } from 'sonner'
import { useContext } from 'react'
import { ThemeContext } from '../../app/providers/theme-context'

export function AppToaster() {
  const { appearance } = useContext(ThemeContext)
  return <Toaster theme={appearance} position="top-right" richColors closeButton
    duration={4_000} visibleToasts={3} containerAriaLabel="Thông báo"
    mobileOffset={16} offset={24} toastOptions={{ closeButtonAriaLabel: 'Đóng thông báo' }} />
}

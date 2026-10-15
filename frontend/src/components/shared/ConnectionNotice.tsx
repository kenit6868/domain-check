import { useEffect, useState } from 'react'
import { toast } from '../../lib/notifications'

export function ConnectionNotice() {
  const [offline, setOffline] = useState(() => !navigator.onLine)
  useEffect(() => {
    const lost = () => { setOffline(true) }
    const restored = () => { setOffline(false); toast.info('Đã có kết nối mạng trở lại.', { id: 'connection-restored' }) }
    window.addEventListener('offline', lost)
    window.addEventListener('online', restored)
    return () => { window.removeEventListener('offline', lost); window.removeEventListener('online', restored) }
  }, [])
  return offline ? <div className="sticky top-0 z-50 bg-amber-100 px-4 py-3 text-center text-sm text-amber-950 dark:bg-amber-950 dark:text-amber-100" role="status">Đang mất kết nối mạng. Kiểm tra kết nối trước khi tiếp tục thao tác.</div> : null
}

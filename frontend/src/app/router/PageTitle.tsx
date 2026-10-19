import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

const pageTitles: Record<string, string> = {
  '/': 'Dashboard',
  '/login': 'Đăng nhập',
  '/app': 'Dashboard',
  '/pending': 'Dashboard',
  '/quick-report': 'Quick Report',
  '/domain-check': 'Check Domain',
  '/domain-worker': 'Domain Worker',
  '/cloaking-review': 'Cloaking Review',
  '/domain-evidence-review': 'Domain Evidence Review',
  '/cloudflare-worker': 'Cloudflare Worker',
  '/link-status': 'Check Link Status',
  '/provider-replies': 'Phản hồi NCC',
  '/settings/mail': 'Cấu hình mail',
  '/audit-log': 'Audit Log',
  '/admin/users': 'Người dùng',
  '/admin/roles': 'Role & quyền',
}

export function PageTitle() {
  const { pathname } = useLocation()

  useEffect(() => {
    const path = pathname.replace(/\/+$/, '').toLowerCase() || '/'
    document.title = `${pageTitles[path] ?? '404 · Không tìm thấy trang'} | Domain Check Tool`
  }, [pathname])

  return null
}

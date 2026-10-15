import { Button } from '../../components/shared/Button'
import { ThemeToggle } from '../../components/shared/ThemeToggle'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Suspense } from 'react'
import { ChevronRight, FileSearch, Layers3, LogOut, Mail, ShieldCheck, UserCog, Users, Wifi } from 'lucide-react'
import { Navigate, NavLink, Outlet } from 'react-router-dom'

import { logoutCurrentUser } from '../../features/auth/api/authApi'
import { useCurrentUser } from '../../features/auth/hooks/useCurrentUser'
import type { Me } from '../../features/auth/types'
import { ApiError } from '../../lib/http/errors'
import { QueryError } from '../../components/shared/QueryError'
import { toast } from '../../lib/notifications'

export function AppShell() {
  const queryClient = useQueryClient()
  const me = useCurrentUser()
  const logout = useMutation({ mutationFn: logoutCurrentUser, onSuccess: () => {
    sessionStorage.clear()
    toast.dismiss()
    queryClient.clear()
    window.location.assign('/')
  } })
  if (me.isPending) return <FullPageMessage title="Đang xác thực phiên…" />
  if (me.isError) {
    if (!(me.error instanceof ApiError) || me.error.status !== 401) return <FullPageMessage title="Chưa thể tải phiên đăng nhập" action={<QueryError message="Không thể xác minh phiên. Kiểm tra kết nối rồi thử lại." onRetry={() => { void me.refetch() }} />} />
    return <FullPageMessage title="Bạn chưa đăng nhập" action={<a className="primary-button" href="/api/v1/auth/telegram/start?return_path=/app">Đăng nhập bằng Telegram</a>} />
  }
  if (me.data.status === 'pending') {
    return <FullPageMessage title="Tài khoản đang chờ duyệt" detail="Quản trị viên cần kích hoạt tài khoản trước khi bạn có thể dùng công cụ." />
  }
  const permissions = me.data.permissions
  return (
    <div className="app-surface min-h-screen">
      <aside className="sidebar-panel fixed inset-y-0 left-0 z-20 hidden w-72 p-5 lg:block">
        <div className="shell-brand flex items-center gap-3 px-2 pb-6 pt-2"><span className="brand-mark"><ShieldCheck size={21} /></span><div><strong className="block text-sm">Domain Check Tool</strong><span className="font-mono text-[10px] uppercase tracking-[0.2em] workspace-kicker">Operator Console</span></div></div>
        <Navigation permissions={permissions} />
        <div className="session-panel absolute inset-x-5 bottom-5 rounded-xl p-3"><span className="flex items-center gap-2 text-xs text-emerald-700 dark:text-emerald-300"><Wifi size={14} /> Session secured</span><span className="mt-1 block truncate text-[11px] text-slate-500">@{me.data.telegram_username ?? me.data.display_name}</span></div>
      </aside>
      <div className="lg:pl-72">
        <header className="app-topbar sticky top-0 z-10 flex min-h-18 flex-wrap items-center justify-between gap-3 px-5 py-3 backdrop-blur-xl sm:px-8">
          <div className="flex min-w-0 items-center gap-3"><span className="workspace-icon hidden sm:flex"><Layers3 size={18} /></span><div><p className="workspace-title text-sm font-semibold">Khu vực</p><p className="mt-1 text-xs text-slate-500">{me.data.timezone}</p></div></div>
          <div className="flex items-center gap-3"><ThemeToggle /><div className="profile-pill hidden sm:flex"><span className="profile-avatar" aria-hidden="true">{me.data.display_name.trim().slice(0, 1).toUpperCase()}</span><span className="max-w-48 truncate text-xs font-medium">{me.data.display_name}</span></div><Button className="icon-button" aria-label="Đăng xuất" loading={logout.isPending} onClick={() => { logout.mutate() }}><LogOut size={18} /></Button></div>
        </header>
        <div className="mobile-navigation overflow-x-auto px-3 py-2 lg:hidden"><Navigation permissions={permissions} mobile /></div>
        <main className="workspace-main mx-auto max-w-7xl p-5 sm:p-8"><Suspense fallback={<div className="data-panel p-8" role="status">Đang tải trang…</div>}><Outlet context={{ me: me.data }} /></Suspense></main>
      </div>
    </div>
  )
}

export function AuthorizedLanding({ me }: { me: Me }) {
  if (me.permissions.includes('quick_report.view')) return <Navigate to="/quick-report" replace />
  if (me.permissions.includes('mail_accounts.view_own')) return <Navigate to="/settings/mail" replace />
  if (me.permissions.includes('users.view')) return <Navigate to="/admin/users" replace />
  if (me.permissions.includes('roles.view')) return <Navigate to="/admin/roles" replace />
  return <FullPageMessage title="Không có trang được cấp quyền" />
}

function Navigation({ permissions, mobile = false }: { permissions: string[]; mobile?: boolean }) {
  const items = [
    { permission: 'quick_report.view', to: '/quick-report', icon: <FileSearch size={18} />, label: 'Quick Report' },
    { permission: 'mail_accounts.view_own', to: '/settings/mail', icon: <Mail size={18} />, label: 'Tài khoản gửi mail' },
    { permission: 'users.view', to: '/admin/users', icon: <Users size={18} />, label: 'Người dùng' },
    { permission: 'roles.view', to: '/admin/roles', icon: <UserCog size={18} />, label: 'Role & quyền' },
  ].filter((item) => permissions.includes(item.permission))
  return <nav className={mobile ? 'flex min-w-max gap-2' : 'mt-6 space-y-1'} aria-label="Điều hướng chính">{!mobile && <p className="mb-3 px-3 font-mono text-[10px] uppercase tracking-[0.2em] nav-heading">Workspace</p>}{items.map((item) => <NavItem key={item.to} {...item} />)}</nav>
}

function NavItem({ to, icon, label }: { to: string; icon: React.ReactNode; label: string }) {
  return <NavLink to={to} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}><span className="nav-icon">{icon}</span><span>{label}</span><ChevronRight size={14} className="nav-chevron" aria-hidden="true" /></NavLink>
}

export function FullPageMessage({ title, detail, action }: { title: string; detail?: string; action?: React.ReactNode }) {
  return <main className="login-surface flex min-h-screen items-center justify-center p-6"><section className="console-panel max-w-lg p-9 text-center"><div className="mb-6 flex justify-end"><ThemeToggle /></div><span className="brand-mark mx-auto mb-5"><ShieldCheck size={28} /></span><h1 className="text-2xl font-semibold text-white">{title}</h1>{detail && <p className="mt-3 text-slate-400">{detail}</p>}{action && <div className="mt-6">{action}</div>}</section></main>
}

import { Button } from '../../components/shared/Button'
import { ThemeToggle } from '../../components/shared/ThemeToggle'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Suspense } from 'react'
import { ChevronRight, CloudCog, FileSearch, Images, LayoutDashboard, Layers3, Link2, LogOut, Mail, Reply, ScanEye, SearchCheck, ScrollText, ShieldCheck, UserCog, Users, Wifi } from 'lucide-react'
import { Navigate, NavLink, Outlet } from 'react-router-dom'

import { logoutCurrentUser } from '../../features/auth/api/authApi'
import { useCurrentUser } from '../../features/auth/hooks/useCurrentUser'
import { ApiError } from '../../lib/http/errors'
import { StatusPage } from '../../components/shared/StatusPage'
import { RefreshCw } from 'lucide-react'
import { toast } from '../../lib/notifications'
import { WorkerHealthNotice } from '../../components/shared/WorkerHealthNotice'

export function AppShell() {
  const queryClient = useQueryClient()
  const me = useCurrentUser()
  const logout = useMutation({ mutationFn: logoutCurrentUser, onSuccess: () => {
    sessionStorage.clear()
    toast.dismiss()
    queryClient.clear()
    window.location.assign('/login')
  } })
  if (me.isPending) return <FullPageMessage title="Đang xác thực phiên…" />
  if (me.isError) {
    if (!(me.error instanceof ApiError) || me.error.status !== 401) return <FullPageMessage title="Chưa thể tải phiên đăng nhập" detail="Không thể kết nối để xác minh phiên đăng nhập. Hãy kiểm tra kết nối mạng và thử lại." action={<Button className="primary-button" loading={me.isFetching} onClick={() => { void me.refetch() }}><RefreshCw size={16} />Thử tải lại</Button>} />
    return <Navigate to={me.data ? '/login?session_expired=1' : '/login'} replace />
  }
  if (me.data.status === 'pending') {
    return <FullPageMessage title="Tài khoản đang chờ duyệt" detail="Quản trị viên cần kích hoạt tài khoản trước khi bạn có thể dùng công cụ." />
  }
  const permissions = me.data.permissions
  const usesWorkers = permissions.some((code) => ['quick_report.view', 'check_domain.view', 'domain_worker.view', 'cloudflare_worker.view'].includes(code))
  return (
    <div className="app-surface min-h-screen">
      <aside className="sidebar-panel fixed inset-y-0 left-0 z-20 hidden w-60 p-3 lg:block">
        <div className="shell-brand flex items-center gap-3 px-2 pb-3 pt-1"><span className="brand-mark"><ShieldCheck size={21} /></span><div><strong className="block text-sm">Domain Check Tool</strong><span className="font-mono text-[10px] uppercase tracking-[0.2em] workspace-kicker">Operator Console</span></div></div>
        <Navigation permissions={permissions} />
        <div className="session-panel absolute inset-x-3 bottom-3 rounded-xl p-3"><span className="flex items-center gap-2 text-xs text-emerald-700 dark:text-emerald-300"><Wifi size={14} /> Session secured</span><span className="mt-1 block truncate text-[11px] text-slate-500">@{me.data.telegram_username ?? me.data.display_name}</span></div>
      </aside>
      <div className="lg:pl-60">
        <header className="app-topbar sticky top-0 z-10 flex min-h-14 flex-wrap items-center justify-between gap-3 px-3 py-2 backdrop-blur-xl sm:px-4">
          <div className="flex min-w-0 items-center gap-3"><span className="workspace-icon hidden sm:flex"><Layers3 size={18} /></span><div><p className="workspace-title text-sm font-semibold">Timezone</p><p className="mt-1 text-xs text-slate-500">{me.data.timezone}</p></div></div>
          <div className="header-actions flex items-center gap-3"><ThemeToggle /><div className="profile-pill flex min-w-0"><span className="profile-avatar" aria-hidden="true">{me.data.display_name.trim().slice(0, 1).toUpperCase()}</span><div className="min-w-0 max-w-36 sm:max-w-52"><span className="block truncate text-xs font-medium">{me.data.display_name}</span>{me.data.telegram_username && <span className="block truncate text-[11px] text-slate-500">@{me.data.telegram_username.replace(/^@+/, '')}</span>}</div>{Boolean(me.data.roles?.length) && <div className="flex max-w-32 flex-wrap items-center gap-1 sm:max-w-48" aria-label="Role">{me.data.roles?.map((role) => <span key={role} className="inline-flex max-w-full items-center rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-[10px] font-semibold leading-4 text-indigo-700 dark:border-indigo-400/25 dark:bg-indigo-400/10 dark:text-indigo-300" title={role}><span className="truncate">{role}</span></span>)}</div>}</div><Button className="icon-button" aria-label="Đăng xuất" loading={logout.isPending} onClick={() => { logout.mutate() }}><LogOut size={18} /></Button></div>
        </header>
        <div className="mobile-navigation overflow-x-auto px-3 py-2 lg:hidden"><Navigation permissions={permissions} mobile /></div>
        <main className="workspace-main mx-auto max-w-[1600px] p-3 sm:p-4">{usesWorkers && <WorkerHealthNotice scope={`${me.data.id}:${[...permissions].sort().join(',')}`} />}<Suspense fallback={<div className="data-panel p-4" role="status">Đang tải trang…</div>}><Outlet context={{ me: me.data }} /></Suspense></main>
      </div>
    </div>
  )
}


function Navigation({ permissions, mobile = false }: { permissions: string[]; mobile?: boolean }) {
  const items = [
    { permission: 'check_domain.view', to: '/domain-check', icon: <SearchCheck size={18} />, label: 'Check Domain' },
    { permission: 'quick_report.view', to: '/quick-report', icon: <FileSearch size={18} />, label: 'Quick Report' },
    { permission: 'domain_worker.view', to: '/domain-worker', icon: <Layers3 size={18} />, label: 'Domain Worker' },
    { permission: 'cloaking_review.view', to: '/cloaking-review', icon: <ScanEye size={18} />, label: 'Cloaking Review' },
    { permission: 'domain_evidence_review.view', to: '/domain-evidence-review', icon: <Images size={18} />, label: 'Domain Evidence Review' },
    { permission: 'cloudflare_worker.view', to: '/cloudflare-worker', icon: <CloudCog size={18} />, label: 'Cloudflare Worker' },
    { permission: 'link_status.view', to: '/link-status', icon: <Link2 size={18} />, label: 'Check Link Status' },
    { permission: 'provider_replies.view', to: '/provider-replies', icon: <Reply size={18} />, label: 'Phản hồi NCC' },
    { permission: 'mail_accounts.view_own', to: '/settings/mail', icon: <Mail size={18} />, label: 'Cấu hình mail' },
    { permission: 'audit_logs.view', to: '/audit-log', icon: <ScrollText size={18} />, label: 'Audit Log' },
    { permission: 'users.view', to: '/admin/users', icon: <Users size={18} />, label: 'Người dùng' },
    { permission: 'roles.view', to: '/admin/roles', icon: <UserCog size={18} />, label: 'Role & quyền' },
  ].filter((item) => permissions.includes(item.permission))
  return <nav className={mobile ? 'flex min-w-max gap-2' : 'mt-3 max-h-[calc(100dvh-12rem)] space-y-1 overflow-y-auto'} aria-label="Điều hướng chính">{!mobile && <p className="mb-3 px-3 font-mono text-[10px] uppercase tracking-[0.2em] nav-heading">Workspace</p>}<NavItem to="/" icon={<LayoutDashboard size={18} />} label="Dashboard" />{items.map((item) => <NavItem key={item.to} {...item} />)}</nav>
}

function NavItem({ to, icon, label }: { to: string; icon: React.ReactNode; label: string }) {
  return <NavLink to={to} end={to === '/'} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}><span className="nav-icon">{icon}</span><span>{label}</span><ChevronRight size={14} className="nav-chevron" aria-hidden="true" /></NavLink>
}

export function FullPageMessage({ title, detail, action }: { title: string; detail?: string; action?: React.ReactNode }) {
  return <StatusPage standalone title={title} detail={detail} action={action} />
}

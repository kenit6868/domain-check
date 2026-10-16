import { ArrowUpRight, FileSearch, Mail, ShieldCheck, UserCog, Users } from 'lucide-react'
import { Link, useOutletContext } from 'react-router-dom'
import type { Me } from '../../auth/types'

export function DashboardPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const features = [
    { permission: 'quick_report.view', to: '/quick-report', label: 'Quick Report', description: 'Kiểm tra URL và mở các kênh báo cáo phù hợp.', icon: <FileSearch size={22} /> },
    { permission: 'mail_accounts.view_own', to: '/settings/mail', label: 'Tài khoản gửi mail', description: 'Quản lý hồ sơ liên hệ và cấu hình tài khoản mail.', icon: <Mail size={22} /> },
    { permission: 'users.view', to: '/admin/users', label: 'Người dùng', description: 'Duyệt tài khoản và quản lý quyền truy cập.', icon: <Users size={22} /> },
    { permission: 'roles.view', to: '/admin/roles', label: 'Role & quyền', description: 'Cấu hình vai trò và quyền sử dụng chức năng.', icon: <UserCog size={22} /> },
  ].filter((feature) => me.permissions.includes(feature.permission))
  return <section><div className="data-panel p-5"><p className="eyebrow">Dashboard</p><h1 className="mt-2 text-2xl font-semibold">Chào {me.display_name}</h1><p className="mt-2 text-sm text-slate-500">Chọn công cụ để bắt đầu công việc hôm nay.</p><span className="mt-4 inline-flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-300"><ShieldCheck size={15} />Tài khoản đang hoạt động</span></div>
    <div className="mt-4 grid gap-4 sm:grid-cols-2">{features.map((feature) => <Link key={feature.to} to={feature.to} className="data-panel group p-5 transition-colors hover:border-sky-400"><div className="flex items-start justify-between"><span className="workspace-icon">{feature.icon}</span><ArrowUpRight size={18} className="text-slate-400 group-hover:text-sky-500" /></div><h2 className="mt-4 font-semibold">{feature.label}</h2><p className="mt-2 text-sm text-slate-500">{feature.description}</p></Link>)}</div>
    {features.length === 0 && <p className="mt-4 text-sm text-slate-500">Chưa có chức năng được cấp quyền. Vui lòng liên hệ quản trị viên.</p>}
  </section>
}

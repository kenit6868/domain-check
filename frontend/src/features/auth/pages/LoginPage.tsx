import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Check, Globe2, LockKeyhole, Send, ShieldCheck } from 'lucide-react'
import { Navigate, useLocation } from 'react-router-dom'
import { ThemeToggle } from '../../../components/shared/ThemeToggle'
import { Button } from '../../../components/shared/Button'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { getLiveness, getTelegramAuthStatus } from '../../foundation/api/systemApi'
import { ApiError } from '../../../lib/http/errors'

export function LoginPage() {
  const me = useCurrentUser()
  const location = useLocation()
  const anonymous = me.isError && me.error instanceof ApiError && me.error.status === 401
  const health = useQuery({ queryKey: ['public', 'health', 'live'], queryFn: ({ signal }) => getLiveness(signal), enabled: anonymous, retry: 1 })
  const telegram = useQuery({ queryKey: ['public', 'auth', 'telegram'], queryFn: ({ signal }) => getTelegramAuthStatus(signal), enabled: anonymous, retry: 1 })
  if (me.isSuccess) return <Navigate to="/" replace />
  const ready = anonymous && health.isSuccess && telegram.data?.configured === true
  const errorCode = new URLSearchParams(location.search).get('login_error')
  const loginError = errorCode === 'telegram_denied' ? 'Bạn đã huỷ đăng nhập Telegram. Có thể thử lại khi sẵn sàng.'
    : errorCode === 'telegram_expired' ? 'Phiên đăng nhập đã hết hạn. Vui lòng thử lại.'
      : errorCode ? 'Không hoàn tất được đăng nhập Telegram. Vui lòng thử lại.' : null
  return <main className="auth-page">
    <header className="auth-topbar"><a href="/login" className="flex items-center gap-3"><span className="auth-brand-icon"><ShieldCheck size={22} /></span><span className="font-semibold tracking-tight">Domain Check Tool</span></a><ThemeToggle /></header>
    <div className="auth-layout">
      <section className="auth-intro">
        <span className="auth-eyebrow"><span className="size-1.5 rounded-full bg-sky-400" /> DOMAIN INVESTIGATION WORKSPACE</span>
        <h1>Không gian điều tra.<br /><span>Gọn gàng. Tập trung.</span></h1>
        <p>Kiểm tra domain, đối chiếu bằng chứng và quản lý báo cáo trong cùng một không gian làm việc.</p>
        <div className="auth-preview" aria-hidden="true"><div className="auth-preview-bar"><span /><span /><span /><i /></div><div className="auth-preview-row"><Globe2 size={20} /><div><strong>Domain investigation</strong><small>Quan sát · Đối chiếu · Báo cáo</small></div><ShieldCheck size={18} /></div><div className="auth-preview-lines"><i /><i /><i /></div><div className="auth-preview-tags"><span>Quick Report</span><span>Evidence</span><span>Provider contacts</span></div></div>
        <div className="auth-benefits">{['Dữ liệu riêng theo tài khoản', 'Quản lý quyền truy cập', 'Theo dõi kết quả trong ngày'].map((label) => <span key={label}><Check size={14} />{label}</span>)}</div>
      </section>
      <section className="auth-card" aria-label="Đăng nhập">
        <span className="auth-telegram-icon"><Send size={27} /></span><p className="auth-card-kicker">CHÀO MỪNG TRỞ LẠI</p><h2>Đăng nhập tài khoản</h2><p className="auth-card-description">Tiếp tục với Telegram để vào dashboard của bạn.</p>
        {me.isPending ? <p className="auth-message" role="status">Đang kiểm tra phiên đăng nhập…</p> : ready ? <a className="auth-login-button" href="/api/v1/auth/telegram/start?return_path=/"><Send size={18} /><span>Đăng nhập bằng Telegram</span><ArrowRight size={18} /></a> : <Button className="auth-login-button" disabled><Send size={18} />{health.isPending || telegram.isPending ? 'Đang kiểm tra kết nối…' : 'Telegram chưa sẵn sàng'}</Button>}
        {loginError && <p className="auth-error" role="alert">{loginError}</p>}
        {anonymous && new URLSearchParams(location.search).get('session_expired') === '1' && <p className="auth-message" role="status">Phiên đăng nhập đã hết hạn hoặc quyền truy cập đã thay đổi. Vui lòng đăng nhập lại để sử dụng quyền hiện tại.</p>}
        {me.isError && !anonymous && <div className="auth-error" role="alert">Chưa thể xác minh phiên đăng nhập.<Button className="secondary-button mt-2" onClick={() => { void me.refetch() }}>Thử lại</Button></div>}
        {anonymous && (health.isError || telegram.isError) && <div className="auth-error" role="alert">Không thể kết nối dịch vụ đăng nhập.<Button className="secondary-button mt-2" onClick={() => { void health.refetch(); void telegram.refetch() }}>Thử lại kết nối</Button></div>}
        {anonymous && telegram.isSuccess && !telegram.data.configured && <p className="auth-error" role="alert">Đăng nhập Telegram chưa được cấu hình. Vui lòng liên hệ quản trị viên.</p>}
        <div className="auth-card-divider" /><p className="auth-privacy"><LockKeyhole size={15} /><span>Xác thực qua Telegram.<br />Ứng dụng không yêu cầu mật khẩu Telegram.</span></p>
        <p className="auth-help">Tài khoản mới cần được quản trị viên duyệt trước khi sử dụng.</p>
      </section>
    </div>
    <footer className="auth-footer">Domain Check Tool <span>·</span> Investigation workspace</footer>
  </main>
}

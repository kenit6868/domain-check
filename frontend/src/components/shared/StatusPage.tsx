import type { ReactNode } from 'react'
import { ArrowLeft, ArrowRight, FileQuestion, Home, LockKeyhole, ShieldCheck, WifiOff } from 'lucide-react'
import { Link } from 'react-router-dom'
import { ThemeToggle } from './ThemeToggle'
import { Button } from './Button'

export function StatusPage({ title, detail, code = 'SESSION', action, standalone = false }: { title: string; detail?: string | undefined; code?: string; action?: ReactNode; standalone?: boolean }) {
  const Icon = code === '403' ? LockKeyhole : code === '404' ? FileQuestion : WifiOff
  const content = <section className="status-card" aria-labelledby="status-title">
    <div className="status-art" aria-hidden="true"><span className="status-orbit" /><span className="status-symbol"><Icon size={36} strokeWidth={1.5} /></span></div>
    <span className="status-code">{code === '403' ? '403 · TRUY CẬP HẠN CHẾ' : code === '404' ? '404 · KHÔNG TÌM THẤY' : 'PHIÊN LÀM VIỆC'}</span>
    <h1 id="status-title">{title}</h1>
    {detail && <p className="status-description" role={code === '403' ? 'alert' : undefined}>{detail}</p>}
    <div className="status-actions">{action ?? <><Link className="status-primary" to="/"><Home size={16} />Về Dashboard<ArrowRight size={15} /></Link><Button onClick={() => { window.history.back() }}><ArrowLeft size={16} />Quay lại</Button></>}</div>
    <p className="status-help">{code === '403' ? 'Nếu cần quyền truy cập, hãy liên hệ quản trị viên của bạn.' : code === '404' ? 'Kiểm tra lại đường dẫn hoặc trở về Dashboard để tiếp tục.' : 'Nếu tình trạng này tiếp diễn, hãy liên hệ quản trị viên để được hỗ trợ.'}</p>
  </section>
  return standalone ? <div className="auth-page"><header className="auth-topbar"><Link to="/" className="flex items-center gap-3"><span className="auth-brand-icon"><ShieldCheck size={23} /></span><span className="text-sm font-semibold">Domain Check Tool</span></Link><ThemeToggle /></header><main className="status-stage">{content}</main><footer className="auth-footer">Domain Check Tool / Operator Console</footer></div> : <div className="status-stage status-inline">{content}</div>
}

export function ForbiddenPage({ message }: { message: string }) {
  return <StatusPage code="403" title="Truy cập bị giới hạn" detail={message} />
}

export function NotFoundPage() {
  return <StatusPage code="404" title="Trang này không tồn tại" detail="Đường dẫn có thể đã thay đổi hoặc trang bạn đang tìm không còn khả dụng." />
}

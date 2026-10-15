import { ThemeToggle } from '../../../components/shared/ThemeToggle'
import { Button } from '../../../components/shared/Button'
import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowRight, Database, LockKeyhole, Radar, Send, ServerCog, ShieldCheck } from 'lucide-react'
import { useLocation } from 'react-router-dom'

import { getLiveness, getTelegramAuthStatus } from '../api/systemApi'

export function FoundationStatusPage() {
  const location = useLocation()
  const health = useQuery({ queryKey: ['public', 'health', 'live'], queryFn: ({ signal }) => getLiveness(signal), retry: 1, staleTime: 15_000 })
  const telegram = useQuery({ queryKey: ['public', 'auth', 'telegram'], queryFn: ({ signal }) => getTelegramAuthStatus(signal), retry: 1, staleTime: 30_000 })
  const loginError = new URLSearchParams(location.search).get('login_error')
  const ready = health.isSuccess && telegram.data?.configured === true

  return (
    <main className="login-surface min-h-screen overflow-hidden px-5 py-8 sm:px-8 lg:px-12">
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-7xl flex-col">
        <header className="flex items-center justify-between border-b border-white/10 pb-6">
          <div className="flex items-center gap-3"><span className="brand-mark"><ShieldCheck size={22} /></span><div><strong className="block tracking-tight text-white">Domain Check Tool</strong><span className="text-xs uppercase tracking-[0.22em] text-cyan-300/70">Investigation Console</span></div></div>
          <div className="flex items-center gap-4"><ThemeToggle /><span className="hidden items-center gap-2 font-mono text-xs text-slate-400 sm:flex"><span className={`size-2 rounded-full ${health.isSuccess ? 'bg-emerald-400 shadow-[0_0_12px_#34d399]' : 'bg-amber-400'}`} /> API {health.isSuccess ? 'ONLINE' : 'CHECKING'}</span></div>
        </header>

        <section className="grid flex-1 items-center gap-12 py-12 lg:grid-cols-[1.15fr_0.85fr] lg:py-16">
          <div>
            <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-cyan-400/20 bg-cyan-400/5 px-3 py-1.5 font-mono text-xs text-cyan-300"><Radar size={14} /> MULTI-TENANT SECURITY WORKSPACE</div>
            <h1 className="max-w-3xl text-4xl font-semibold leading-[1.08] tracking-[-0.04em] text-white sm:text-6xl">Điều tra domain.<br /><span className="text-gradient">Kiểm soát evidence.</span><br />Hành động có kiểm chứng.</h1>
            <p className="mt-7 max-w-2xl text-base leading-7 text-slate-400 sm:text-lg">Mỗi URL, job, draft và delivery được cô lập theo tài khoản. Mọi thao tác bên ngoài đều đi qua permission, preview và ledger bền vững.</p>
            <div className="mt-9 flex flex-wrap gap-3">
              {ready ? <a className="login-button" href="/api/v1/auth/telegram/start?return_path=/app"><Send size={17} /> Đăng nhập bằng Telegram <ArrowRight size={17} /></a> : <Button className="login-button opacity-50" type="button" disabled><LockKeyhole size={17} /> Telegram chưa sẵn sàng</Button>}
              <span className="flex items-center gap-2 px-3 font-mono text-xs text-slate-500"><LockKeyhole size={14} /> OIDC + PKCE · session HttpOnly</span>
            </div>
            {loginError && <div className="mt-5 max-w-2xl rounded-xl border border-rose-400/25 bg-rose-400/10 p-4 text-sm text-rose-700 dark:text-rose-200" role="alert">{loginError === 'telegram_denied' ? 'Đăng nhập Telegram đã bị hủy hoặc callback thiếu dữ liệu.' : 'Telegram callback không hoàn tất. Kiểm tra redirect URI trong BotFather và cấu hình backend.'}</div>}
            {health.isError && <div className="mt-5 max-w-2xl rounded-xl border border-rose-400/25 bg-rose-400/10 p-4 text-sm text-rose-700 dark:text-rose-200" role="alert">Backend chưa phản hồi. Không có thao tác điều tra hoặc gửi báo cáo nào được chạy.</div>}
            {telegram.data && !telegram.data.configured && <div className="mt-5 max-w-2xl rounded-xl border border-amber-400/20 bg-amber-400/5 p-4 text-sm text-amber-800 dark:text-amber-100" role="alert"><strong>Backend chưa có Telegram Client ID/Secret.</strong><span className="mt-1 block text-amber-800 dark:text-amber-100/70">Đăng ký chính xác callback sau trong BotFather: <code className="break-all text-cyan-300">{telegram.data.redirect_uri}</code></span></div>}
          </div>

          <aside className="console-panel">
            <div className="flex items-center justify-between border-b border-white/10 px-5 py-4"><span className="font-mono text-xs uppercase tracking-[0.18em] text-slate-400">System readiness</span><span className="flex gap-1.5"><i className="size-2 rounded-full bg-rose-400/70" /><i className="size-2 rounded-full bg-amber-400/70" /><i className="size-2 rounded-full bg-emerald-400/70" /></span></div>
            <div className="space-y-3 p-5">
              <StatusRow icon={<Activity />} label="FastAPI gateway" value={health.isSuccess ? 'Operational' : health.isError ? 'Unavailable' : 'Connecting'} tone={health.isSuccess ? 'ready' : health.isError ? 'error' : 'pending'} />
              <StatusRow icon={<Send />} label="Telegram OIDC" value={telegram.isPending ? 'Checking' : telegram.data?.configured ? 'Configured' : 'Needs configuration'} tone={telegram.data?.configured ? 'ready' : 'pending'} />
              <StatusRow icon={<Database />} label="Durable state" value="PostgreSQL + Redis" tone="neutral" />
              <StatusRow icon={<ServerCog />} label="Worker boundary" value="Isolated operations" tone="neutral" />
            </div>
            <div className="border-t border-white/10 px-5 py-4 font-mono text-[11px] leading-5 text-slate-500">AUTH GATE: Features remain inaccessible until Telegram session is valid and the account is activated.</div>
          </aside>
        </section>
      </div>
    </main>
  )
}

function StatusRow({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: string; tone: 'ready' | 'error' | 'pending' | 'neutral' }) {
  const color = { ready: 'bg-emerald-400 shadow-[0_0_10px_#34d399]', error: 'bg-rose-400', pending: 'bg-amber-400', neutral: 'bg-cyan-400' }[tone]
  return <div className="login-status flex items-center gap-4 rounded-xl p-4"><span className="flex size-10 items-center justify-center rounded-lg border border-cyan-400/15 bg-cyan-400/5 text-cyan-300">{icon}</span><span className="min-w-0 flex-1"><span className="block text-xs text-slate-500">{label}</span><strong className="mt-1 block truncate text-sm font-medium text-slate-200">{value}</strong></span><span className={`size-2 rounded-full ${color}`} /></div>
}

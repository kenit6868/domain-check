import { useQuery } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'
import { request } from '../../lib/http/client'

interface WorkerHealth { services: { name: string; state: 'ready' | 'draining' | 'offline' | 'unknown'; slots: number }[] }
const labels: Record<string, string> = {
  quick_report: 'Quick Report', check_domain: 'Check Domain', domain_worker_precheck: 'Precheck',
  domain_worker_send: 'Gửi mail theo batch', browser_evidence: 'Chụp evidence', cloudflare_runner: 'Cloudflare',
}

export function WorkerHealthNotice({ scope = '' }: { scope?: string }) {
  const health = useQuery({
    queryKey: ['worker-health', scope],
    queryFn: async ({ signal }) => {
      const data = await request<WorkerHealth>({ url: '/health/workers', signal })
      if (!Array.isArray(data.services)) throw new Error('Invalid worker status')
      return data
    },
    refetchInterval: 15_000, retry: false,
  })
  const affected = health.data?.services.filter((item) => item.state !== 'ready') ?? []
  if (!health.isError && affected.length === 0) return null
  return <div role="status" className="mb-3 flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
    <AlertTriangle size={18} className="mt-0.5 shrink-0" aria-hidden="true" />
    <div><strong>{health.isError ? 'Chưa thể kiểm tra dịch vụ xử lý' : 'Một số dịch vụ xử lý chưa sẵn sàng'}</strong>
      <p className="mt-1 text-xs">{affected.length > 0 ? `${affected.map((item) => labels[item.name] ?? item.name).join(', ')}. ` : ''}Job có thể phải chờ. Liên hệ quản trị viên kiểm tra dịch vụ; không tạo lại job chỉ vì đang chờ.</p>
    </div>
  </div>
}

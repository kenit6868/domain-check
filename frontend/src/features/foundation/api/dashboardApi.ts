import { request } from '../../../lib/http/client'

export interface Dashboard {
  scope: 'own' | 'all'
  days: number
  timezone: string
  start: string
  end: string
  updated_at: string
  features: string[]
  metrics: Record<string, number>
  trend: Array<{ date: string; checked_urls?: number; sent_emails?: number; cloudflare_submitted?: number }>
  pending: Array<{ feature: string; count: number; href: string }>
  workers: Array<{ id: string; owner_name: string; feature: string; state: string; total: number; completed: number; stale: boolean; href: string | null }>
  users: Array<{ id: string; name: string; status: string; checked_urls?: number; sent_emails?: number; attention?: number }>
  user_total: number | null
  pending_users: number | null
  users_offset: number
  last_mail_sync: string | null
  events: Array<{ id: string; action: string; outcome: string; at: string }>
}

export function getDashboard(scope: 'own' | 'all', days: number, feature: string, user: string, offset: number, signal: AbortSignal) {
  const params = new URLSearchParams({ scope, days: String(days), offset: String(offset) })
  if (feature) params.set('feature', feature)
  if (scope === 'all' && user) params.set('user_id', user)
  return request<Dashboard>({ url: `/dashboard?${params}`, signal })
}

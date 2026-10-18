import { ForbiddenPage } from '../../../components/shared/StatusPage'
import { Button } from '../../../components/shared/Button'
import { Link, useOutletContext } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { request } from '../../../lib/http/client'

import { PageHeader } from '../../../components/shared/PageHeader'
import type { Me } from '../../auth/types'
import { QuickReportInput } from '../components/QuickReportInput'
import { ExtensionConnectionStatus } from '../components/ExtensionConnectionStatus'
import { QuickReportOperationView } from '../components/QuickReportOperationView'
import { useExtensionConnection } from '../hooks/useExtensionConnection'
import { useQuickReportOperation } from '../hooks/useQuickReportOperation'
import { QuickReportCacheControl } from '../components/QuickReportCacheControl'
import { getDailyQuickReports } from '../api/quickReportApi'

export function QuickReportPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const canView = me.permissions.includes('quick_report.view')
  const readiness = useQuery({
    queryKey: ['quick-report', me.id, 'readiness'], enabled: canView,
    staleTime: 0,
    queryFn: async ({ signal }) => z.object({ ready: z.boolean(), missing: z.array(z.enum(['brand_name', 'contact_name', 'contact_email'])) }).parse(
      await request<unknown>({ url: '/quick-reports/readiness', signal })),
  })
  if (!canView) return <ForbiddenPage message="Bạn không có quyền xem Quick Report." />
  if (readiness.isPending) return <div className="data-panel p-6" role="status">Đang kiểm tra cấu hình…</div>
  if (readiness.isError) return <div className="error-notice" role="alert">{readiness.error.message}<Button className="secondary-button ml-3" onClick={() => { void readiness.refetch() }}>Thử lại</Button></div>
  if (!readiness.data.ready) {
    const labels = { brand_name: 'Thương hiệu', contact_name: 'Người liên hệ', contact_email: 'Email liên hệ hợp lệ' }
    return <section><PageHeader eyebrow="Thiết lập tài khoản" title="Hoàn tất cấu hình trước khi sử dụng" description="Quick Report cần hồ sơ tổ chức để tạo nội dung báo cáo đúng danh tính của bạn." />
      <div className="data-panel p-6" role="alert"><p className="font-semibold">Thông tin còn thiếu hoặc chưa hợp lệ:</p>
        <ul className="my-4 list-disc space-y-2 pl-5">{readiness.data.missing.map((field) => <li key={field}>{labels[field]}</li>)}</ul>
        {me.permissions.includes('mail_accounts.view_own') && me.permissions.includes('mail_accounts.manage_own') ? <Link className="primary-button inline-flex" to="/settings/mail">Đi tới cấu hình hồ sơ</Link> : <p>Liên hệ quản trị viên để được cấp quyền cấu hình hồ sơ.</p>}
        <Button className="secondary-button ml-3" onClick={() => { void readiness.refetch() }}>Kiểm tra lại</Button>
      </div></section>
  }
  return <ConfiguredQuickReportPage />
}

function ConfiguredQuickReportPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const canView = me.permissions.includes('quick_report.view')
  const canCheck = me.permissions.includes('quick_report.check')
  const canOpenForm = me.permissions.includes('quick_report.open_form')
  const extension = useExtensionConnection(canOpenForm)
  const workflow = useQuickReportOperation(me.id)
  const operation = workflow.operation.data ?? workflow.create.data
  const active = Boolean(operation && ['queued', 'running', 'stopping'].includes(operation.state))
  const daily = useQuery({ queryKey: ['quick-report', me.id, 'daily', operation?.id, operation?.state],
    queryFn: ({ signal }) => getDailyQuickReports(signal), refetchInterval: active ? 2_000 : 30_000,
    placeholderData: (previous, previousQuery) => previousQuery?.queryKey[1] === me.id ? previous : undefined })
  if (!canView) return <ForbiddenPage message="Bạn không có quyền xem Quick Report." />
  const error = workflow.create.error ?? workflow.operation.error
  return (
    <section className="quick-report-workspace">
      <header className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1"><h1 className="text-xl font-semibold">Quick Report</h1><p className="text-xs text-slate-500">Kiểm tra URL và báo cáo theo từng kênh.</p></header>
      {canOpenForm && <ExtensionConnectionStatus extension={extension} />}
      {me.permissions.includes('quick_report.clear_cache') && <QuickReportCacheControl onCleared={workflow.reset} busy={workflow.create.isPending || Boolean(workflow.operationId && !operation) || Boolean(operation && ['queued', 'running', 'stopping'].includes(operation.state))} />}
      <QuickReportInput canCheck={canCheck} busy={workflow.create.isPending || active} onCheck={(targets) => { workflow.create.mutate(targets) }} />
      {daily.isError && <div className="error-notice text-xs" role="alert">Không tải được danh sách trong ngày. Kết quả lượt hiện tại vẫn được giữ.<Button className="secondary-button ml-2" onClick={() => { void daily.refetch() }}>Tải lại danh sách hôm nay</Button></div>}
      {error && <div className="error-notice flex flex-wrap items-center justify-between gap-3" role="alert"><span>{error.message}</span>{workflow.operationId && <Button type="button" className="secondary-button" onClick={workflow.reset}>Bỏ operation lỗi</Button>}</div>}
      {(operation || daily.data) && <QuickReportOperationView key={operation?.id ?? 'daily'} operation={operation ?? { id: '00000000-0000-4000-8000-000000000000', state: 'completed', stage: 'completed', business_day: daily.data?.business_day ?? '', checks: [], duplicates: [] }} refreshing={workflow.operation.isFetching} onReset={workflow.reset} canOpenForm={canOpenForm} extension={extension} {...(daily.data ? { daily: daily.data } : {})} />}
    </section>
  )
}


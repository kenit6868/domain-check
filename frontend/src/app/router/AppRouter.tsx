import { Navigate, Route, Routes } from 'react-router-dom'
import { lazy, Suspense } from 'react'
import { PageTitle } from './PageTitle'

import { NotFoundPage } from '../../components/shared/StatusPage'
import { AppShell } from '../layouts/AppShell'
import { DashboardPage } from '../../features/foundation/pages/DashboardPage'
import { LoginPage } from '../../features/auth/pages/LoginPage'
const RolesPage = lazy(async () => ({ default: (await import('../../features/admin/pages/RolesPage')).RolesPage }))
const UsersPage = lazy(async () => ({ default: (await import('../../features/admin/pages/UsersPage')).UsersPage }))
const MailSettingsPage = lazy(async () => ({ default: (await import('../../features/mail-settings/pages/MailSettingsPage')).MailSettingsPage }))
const QuickReportPage = lazy(async () => ({ default: (await import('../../features/quick-report/pages/QuickReportPage')).QuickReportPage }))
const DomainCheckPage = lazy(async () => ({ default: (await import('../../features/domain-check/pages/DomainCheckPage')).DomainCheckPage }))
const DomainWorkerPage = lazy(async () => ({ default: (await import('../../features/domain-worker/DomainWorkerPage')).DomainWorkerPage }))
const ReviewQueuePage = lazy(async () => ({ default: (await import('../../features/domain-worker/ReviewQueuePage')).ReviewQueuePage }))
const ProviderRepliesPage = lazy(async () => ({ default: (await import('../../features/provider-replies/ProviderRepliesPage')).ProviderRepliesPage }))


const CloudflareWorkerPage = lazy(async () => ({ default: (await import('../../features/cloudflare-worker/CloudflareWorkerPage')).CloudflareWorkerPage }))
const LinkStatusPage = lazy(async () => ({ default: (await import('../../features/link-status/LinkStatusPage')).LinkStatusPage }))
const AuditLogPage = lazy(async () => ({ default: (await import('../../features/audit-log/AuditLogPage')).AuditLogPage }))

export function AppRouter() {
  return (
    <><PageTitle />
    <Suspense fallback={<div className="app-surface min-h-screen p-8" role="status">Đang tải giao diện…</div>}><Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AppShell />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/audit-log" element={<AuditLogPage />} />
        <Route path="/pending" element={<Navigate to="/" replace />} />
        <Route path="/app" element={<Navigate to="/" replace />} />
        <Route path="/quick-report" element={<QuickReportPage />} />
        <Route path="/domain-check" element={<DomainCheckPage />} />
        <Route path="/domain-worker" element={<DomainWorkerPage />} />
        <Route path="/cloaking-review" element={<ReviewQueuePage kind="cloaking" />} />
        <Route path="/domain-evidence-review" element={<ReviewQueuePage kind="evidence" />} />
        <Route path="/cloudflare-worker" element={<CloudflareWorkerPage />} />
        <Route path="/link-status" element={<LinkStatusPage />} />
        <Route path="/provider-replies" element={<ProviderRepliesPage />} />
        <Route path="/admin/users" element={<UsersPage />} />
        <Route path="/admin/roles" element={<RolesPage />} />
        <Route path="/settings/mail" element={<MailSettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes></Suspense></>
  )
}

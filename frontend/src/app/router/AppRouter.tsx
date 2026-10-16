import { Navigate, Route, Routes } from 'react-router-dom'
import { lazy, Suspense } from 'react'

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


export function AppRouter() {
  return (
    <Suspense fallback={<div className="app-surface min-h-screen p-8" role="status">Đang tải giao diện…</div>}><Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AppShell />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/pending" element={<Navigate to="/" replace />} />
        <Route path="/app" element={<Navigate to="/" replace />} />
        <Route path="/quick-report" element={<QuickReportPage />} />
        <Route path="/domain-check" element={<DomainCheckPage />} />
        <Route path="/domain-worker" element={<DomainWorkerPage />} />
        <Route path="/cloaking-review" element={<ReviewQueuePage kind="cloaking" />} />
        <Route path="/domain-evidence-review" element={<ReviewQueuePage kind="evidence" />} />
        <Route path="/admin/users" element={<UsersPage />} />
        <Route path="/admin/roles" element={<RolesPage />} />
        <Route path="/settings/mail" element={<MailSettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes></Suspense>
  )
}

import { Navigate, Route, Routes, useOutletContext } from 'react-router-dom'
import { lazy, Suspense } from 'react'

import { AppShell, AuthorizedLanding } from '../layouts/AppShell'
import type { Me } from '../../features/auth/types'
import { FoundationStatusPage } from '../../features/foundation/pages/FoundationStatusPage'
const RolesPage = lazy(async () => ({ default: (await import('../../features/admin/pages/RolesPage')).RolesPage }))
const UsersPage = lazy(async () => ({ default: (await import('../../features/admin/pages/UsersPage')).UsersPage }))
const MailSettingsPage = lazy(async () => ({ default: (await import('../../features/mail-settings/pages/MailSettingsPage')).MailSettingsPage }))
const QuickReportPage = lazy(async () => ({ default: (await import('../../features/quick-report/pages/QuickReportPage')).QuickReportPage }))

function LandingRoute() {
  const { me } = useOutletContext<{ me: Me }>()
  return <AuthorizedLanding me={me} />
}

export function AppRouter() {
  return (
    <Suspense fallback={<div className="app-surface min-h-screen p-8" role="status">Đang tải giao diện…</div>}><Routes>
      <Route path="/" element={<FoundationStatusPage />} />
      <Route element={<AppShell />}>
        <Route path="/pending" element={<LandingRoute />} />
        <Route path="/app" element={<LandingRoute />} />
        <Route path="/quick-report" element={<QuickReportPage />} />
        <Route path="/admin/users" element={<UsersPage />} />
        <Route path="/admin/roles" element={<RolesPage />} />
        <Route path="/settings/mail" element={<MailSettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes></Suspense>
  )
}

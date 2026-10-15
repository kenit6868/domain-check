import { QueryClientProvider } from '@tanstack/react-query'
import { useState, type PropsWithChildren } from 'react'
import { BrowserRouter } from 'react-router-dom'

import { createQueryClient } from '../../lib/query/client'
import { AppearanceProvider } from './AppearanceProvider'
import { AppToaster } from '../../components/shared/AppToaster'
import { AppErrorBoundary } from '../../components/shared/AppErrorBoundary'
import { ConnectionNotice } from '../../components/shared/ConnectionNotice'

export function AppProviders({ children }: PropsWithChildren) {
  const [queryClient] = useState(createQueryClient)
  return (
    <QueryClientProvider client={queryClient}>
      <AppearanceProvider><AppErrorBoundary><ConnectionNotice /><BrowserRouter>{children}</BrowserRouter></AppErrorBoundary><AppToaster /></AppearanceProvider>
    </QueryClientProvider>
  )
}

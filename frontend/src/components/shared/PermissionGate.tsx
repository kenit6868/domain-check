import type { PropsWithChildren, ReactNode } from 'react'

interface PermissionGateProps extends PropsWithChildren {
  permissions: string[]
  require: string
  fallback?: ReactNode
}

export function PermissionGate({ permissions, require, fallback = null, children }: PermissionGateProps) {
  return permissions.includes(require) ? children : fallback
}

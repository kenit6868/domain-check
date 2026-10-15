export interface RoleSummary {
  id: string
  key: string
  name: string
  system: boolean
}

export interface UserItem {
  id: string
  display_name: string
  telegram_username: string | null
  identity_provider: string | null
  status: 'pending' | 'active' | 'disabled'
  timezone: string
  last_login_at: string | null
  roles: RoleSummary[]
}

export interface UserPage {
  items: UserItem[]
  total: number
  offset: number
  limit: number
}

export interface RoleDetail extends RoleSummary {
  description: string
  permissions: string[]
  version: number
}

export interface PermissionItem {
  code: string
  description: string
  safe_for_default_user: boolean
}

export interface RoleInput {
  key?: string
  name: string
  description: string
  permissions: string[]
}

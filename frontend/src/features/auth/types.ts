export interface Me {
  id: string
  display_name: string
  telegram_username: string | null
  status: 'pending' | 'active' | 'disabled'
  timezone: string
  permissions: string[]
  roles?: string[]
}

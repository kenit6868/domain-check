export interface MailAccount {
  id: string
  label: string
  smtp_host: string
  smtp_port: number
  smtp_username: string
  smtp_transport: 'implicit_tls' | 'starttls' | 'plain'
  enabled: boolean
  is_default: boolean
  imap: { reply_mailboxes?: string[] | null; host: string; port: number; username: string; mailbox: string; sent_mailbox: string; junk_mailbox: string; cloudflare_mailbox: string; evidence_mailbox: string }
  proxy: { scheme: 'socks5' | 'socks4' | 'http' | null; host: string; port: number | null }
  signature: { contact_name: string; contact_email: string; signature_role: string }
  has_smtp_password: boolean
  has_imap_password: boolean
  has_proxy_credentials: boolean
  version: number
}

export interface MailAccountInput extends Omit<MailAccount, 'id' | 'has_smtp_password' | 'has_imap_password' | 'has_proxy_credentials' | 'version'> {
  smtp_password?: string | null
  imap_password?: string | null
  proxy_username?: string | null
  proxy_password?: string | null
}

export interface ProfileSettings {
  brand_name: string
  contact_name: string
  contact_email: string
  company_name: string
  business_registration_no: string
  signature_role: string
  logo_artifact_id: string | null
  version: number
}

export interface MailConnectionResult {
  status: 'success' | 'failed'
  protocol: 'smtp' | 'imap'
  transport: string
  code: string
  message: string
  elapsed_ms: number
}

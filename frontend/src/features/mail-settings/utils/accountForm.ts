import type { MailAccountInput } from '../types'

export function accountFormError(input: MailAccountInput): string | null {
  if (!input.smtp_username.trim()) return 'Vui lòng nhập SMTP username (email đăng nhập).'
  if (!input.smtp_host.trim()) return 'Vui lòng nhập SMTP host.'
  if (!Number.isInteger(input.smtp_port) || input.smtp_port < 1 || input.smtp_port > 65535) return 'SMTP port phải từ 1 đến 65535.'
  if (!Number.isInteger(input.imap.port) || input.imap.port < 1 || input.imap.port > 65535) return 'IMAP port phải từ 1 đến 65535.'
  if (input.proxy.host.trim() && (!input.proxy.scheme || !input.proxy.port || !Number.isInteger(input.proxy.port) || input.proxy.port < 1 || input.proxy.port > 65535)) return 'Khi dùng proxy, cần chọn scheme và port hợp lệ.'
  return null
}

export function prepareMailAccount(input: MailAccountInput): MailAccountInput {
  return { ...input, label: input.label.trim() || input.smtp_username.trim(),
    smtp_host: input.smtp_host.trim(), smtp_username: input.smtp_username.trim(),
    imap: { ...input.imap, host: input.imap.host.trim(), username: input.imap.username.trim() },
    proxy: { ...input.proxy, host: input.proxy.host.trim() } }
}

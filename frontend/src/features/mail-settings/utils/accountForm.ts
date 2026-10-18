import type { MailAccountInput } from '../types'

function hasControlCharacter(name: string): boolean {
  for (const char of name) if (char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) return true
  return false
}

export function accountFormError(input: MailAccountInput): string | null {
  const folders = input.imap.reply_mailboxes
  if (folders && (folders.length > 30 || folders.some((name) => !name.trim() || name.trim().length > 500 || hasControlCharacter(name)))) return 'Nhập tên thư mục hợp lệ, tối đa 30 thư mục và 500 ký tự mỗi tên.'
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
    imap: { ...input.imap, host: input.imap.host.trim(), username: input.imap.username.trim(), reply_mailboxes: input.imap.reply_mailboxes == null ? null : [...new Set(input.imap.reply_mailboxes.map((name) => name.trim()))] },
    proxy: { ...input.proxy, host: input.proxy.host.trim() } }
}

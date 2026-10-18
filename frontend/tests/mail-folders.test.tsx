import { Theme } from '@radix-ui/themes'
import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it } from 'vitest'
import { MailSettingsPage } from '../src/features/mail-settings/pages/MailSettingsPage'
import { createQueryClient } from '../src/lib/query/client'
import { installApiMock } from './httpMock'

afterEach(cleanup)
it('edits legacy folders as a dynamic list and saves exact mixed-provider folder names', async () => {
  const account = { id: '11111111-1111-4111-8111-111111111111', label: 'Sender', smtp_host: 'mail.example.com', smtp_port: 465,
    smtp_username: 'sender@example.com', smtp_transport: 'implicit_tls', enabled: true, is_default: false,
    imap: { host: 'mail.example.com', port: 993, username: '', mailbox: 'INBOX', sent_mailbox: 'Sent', junk_mailbox: 'Junk', cloudflare_mailbox: '2-Cloudflare', evidence_mailbox: '3 - Cần bằng chứng' },
    proxy: { scheme: null, host: '', port: null }, signature: { contact_name: '', contact_email: '', signature_role: '' }, version: 1 }
  let saved: { imap: { reply_mailboxes: string[] } } | undefined
  const restore = installApiMock((config) => {
    if (config.url === '/mail-accounts') return [account]
    if (config.url === '/settings/profile') return null
    if (config.url === `/mail-accounts/${account.id}`) { saved = JSON.parse(String(config.data)) as typeof saved; return account }
    throw new Error(`Unexpected request ${String(config.url)}`)
  })
  try {
    const me = { permissions: ['mail_accounts.view_own', 'mail_accounts.manage_own'] }
    render(<QueryClientProvider client={createQueryClient()}><Theme><MemoryRouter><Routes><Route element={<Outlet context={{ me }} />}><Route index element={<MailSettingsPage />} /></Route></Routes></MemoryRouter></Theme></QueryClientProvider>)
    await userEvent.click(await screen.findByRole('button', { name: 'Sửa' }))
    expect(screen.queryByLabelText('Cloudflare mailbox')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Thư mục 4')).toHaveValue('3 - Cần bằng chứng')
    await userEvent.click(screen.getByRole('button', { name: 'Xóa thư mục 3' }))
    await userEvent.click(screen.getByRole('button', { name: 'Thêm thư mục' }))
    await userEvent.click(screen.getByRole('button', { name: 'Lưu tài khoản' }))
    expect(saved).toBeUndefined()
    await userEvent.type(screen.getByLabelText('Thư mục 4'), ' Other providers ')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu tài khoản' }))
    await waitFor(() => { expect(saved?.imap.reply_mailboxes).toEqual(['INBOX', 'Junk', '3 - Cần bằng chứng', 'Other providers']) })
  } finally { restore() }
})

import { request } from '../../../lib/http/client'
import type { MailAccount, MailAccountInput, MailConnectionResult, ProfileSettings } from '../types'

export const getMailAccounts = (signal?: AbortSignal) => request<MailAccount[]>({ url: '/mail-accounts', ...(signal ? { signal } : {}) })
export const createMailAccount = (input: MailAccountInput) => request<MailAccount>({ url: '/mail-accounts', method: 'POST', data: input })
export const updateMailAccount = (id: string, input: MailAccountInput) => request<MailAccount>({ url: `/mail-accounts/${id}`, method: 'PATCH', data: input })
export const disableMailAccount = (id: string) => request<undefined>({ url: `/mail-accounts/${id}`, method: 'DELETE' })
export const testSmtpConnection = (id: string) => request<MailConnectionResult>({ url: `/mail-accounts/${id}/test-smtp`, method: 'POST' })
export const testImapConnection = (id: string) => request<MailConnectionResult>({ url: `/mail-accounts/${id}/test-imap`, method: 'POST' })
export const getProfile = (signal?: AbortSignal) => request<ProfileSettings>({ url: '/settings/profile', ...(signal ? { signal } : {}) })
export const applyDefaultLogo = () => request<{ logo_artifact_id: string }>({ url: '/settings/profile/logo/default', method: 'POST' })
export const uploadProfileLogo = (file: File) => { const data = new FormData(); data.append('file', file); return request<{ logo_artifact_id: string }>({ url: '/settings/profile/logo', method: 'PUT', data }) }
export const resetProfileLogo = () => request({ url: '/settings/profile/logo', method: 'DELETE' })
export type MailConnectivity = { enabled: boolean; accounts: { id: string; smtp_allowed: boolean; imap_allowed: boolean; proxy_allowed: boolean }[] }
export const getMailConnectivity = (signal?: AbortSignal) => request<MailConnectivity>({ url: '/settings/mail-connectivity', ...(signal ? { signal } : {}) })
export const updateProfile = (input: Omit<ProfileSettings, 'logo_artifact_id' | 'version'>) => request<ProfileSettings>({ url: '/settings/profile', method: 'PATCH', data: input })

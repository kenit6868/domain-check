import { Button } from '../../../components/shared/Button'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Building2, Cloud, Globe2, Leaf, Puzzle, Search, Shield, ShieldAlert } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { queryKeys } from '../../../lib/query/keys'
import { createFormTask, getFormTask, registerExtensionDevice } from '../api/formTaskApi'
import { openExtensionForm } from '../extension/extensionBridge'
import type { FormProvider } from '../schemas/formTaskSchema'
import type { QuickReportCheck } from '../schemas/quickReportSchema'
import { NetcraftAction } from './QuickReportActions'
import type { ReturnTypeOfUseExtension } from './types'
import { request } from '../../../lib/http/client'
import { z } from 'zod'
import { QuickReportDraftPanel } from './QuickReportDraftPanel'

const ACTIVE_STATES = new Set(['CREATED', 'EXCHANGED', 'WORKING'])
const ORDER: FormProvider[] = ['google_gsb', 'microsoft_smartscreen', 'chongluadao', 'coccoc_safe', 'cloudflare']
const ICONS: Partial<Record<FormProvider, ReactNode>> = {
  google_gsb: <Search size={16} />, microsoft_smartscreen: <Shield size={16} />,
  chongluadao: <ShieldAlert size={16} />, coccoc_safe: <Leaf size={16} />, cloudflare: <Cloud size={16} />,
}

export function QuickReportProviderActions({ check, canOpenForm, extension }: { check: QuickReportCheck; canOpenForm: boolean; extension: ReturnTypeOfUseExtension }) {
  const [taskId, setTaskId] = useState<string | null>(null)
  const drafts = useQuery({ queryKey: ['quick-report', check.id, 'all-form-drafts'], enabled: canOpenForm,
    queryFn: async ({ signal }) => z.array(z.object({ channel: z.string(), label: z.string(), form_url: z.string(), draft: z.string(), note: z.string(), autofill_provider: z.string().nullable() })).parse(await request<unknown>({ url: `/quick-reports/checks/${check.id}/form-drafts?include_providers=true`, signal })) })
  const task = useQuery({
    queryKey: queryKeys.formTask(taskId ?? 'none'),
    queryFn: ({ signal }) => getFormTask(taskId ?? '', signal), enabled: taskId !== null,
    refetchInterval: (query) => ACTIVE_STATES.has(query.state.data?.state ?? '') ? 1_500 : false,
  })
  const open = useMutation({
    meta: { pendingMessage: 'Đang mở form qua extension…', successMessage: 'Đã mở form. Kiểm tra thông tin và gửi thủ công.' },
    mutationFn: async (provider: FormProvider) => {
      if (!extension.connection) throw new Error('Extension chưa kết nối.')
      await registerExtensionDevice(extension.connection.deviceId, extension.connection.extensionVersion)
      const created = await createFormTask(check.id, provider, extension.connection.deviceId)
      setTaskId(created.id)
      const acknowledgement = await openExtensionForm(created.capability_token)
      return { taskId: acknowledgement.taskId }
    },
    onSuccess: (result) => { setTaskId(result.taskId) },
  })
  const providers = check.providers ?? [
    { id: 'google_gsb' as const, label: 'Google', form_url: '' },
    { id: 'microsoft_smartscreen' as const, label: 'Microsoft', form_url: '' },
  ]
  const connected = extension.state === 'connected'
  const groups = (['general', 'infrastructure', 'registrar', 'registry'] as const).filter((group) =>
    group === 'general' || (group === 'infrastructure' ? providers.some((provider) => provider.id === 'cloudflare') : check.report_forms?.some((form) => form.channel === group)))
  const autoButton = (provider: typeof providers[number], label: string, icon: ReactNode) => <Button key={provider.id}
    className="primary-button report-action inline-flex items-center gap-2"
    title={connected ? 'Tự điền qua extension; bạn kiểm tra và gửi thủ công' : 'Kết nối Web Form Assistant để tự điền'}
    aria-label={`${label} — Mở và tự điền`} disabled={!connected || open.isPending}
    loading={open.isPending && open.variables === provider.id} onClick={() => { open.mutate(provider.id) }}>
    {icon}<span className="text-left"><span className="block max-w-52 truncate">{label}</span><span className="block text-[10px] font-normal opacity-70">Mở và tự điền</span></span><Puzzle size={12} className="opacity-50" />
  </Button>
  return <section className="border-t border-slate-100 px-3 py-2 dark:border-slate-800">
    <div className="grid items-start gap-2 md:grid-cols-[repeat(var(--report-columns),minmax(0,1fr))]" style={{ '--report-columns': Math.max(1, groups.length - 1) } as React.CSSProperties} aria-label="Kênh báo cáo">
      {canOpenForm && groups.map((group) => <div key={group} className={`min-w-0 rounded-lg border border-slate-200 p-2 dark:border-slate-800 ${group === 'general' ? 'md:col-span-full' : ''}`}>
        <h3 className="mb-2 text-xs font-semibold">{group === 'general' ? 'Báo cáo chung' : group === 'infrastructure' ? 'CDN' : group === 'registrar' ? 'Registrar' : 'Registry'}</h3>
        <div className="flex flex-wrap content-start items-start gap-1.5">
        {ORDER.filter((id) => group === 'general' ? id !== 'cloudflare' : group === 'infrastructure' && id === 'cloudflare').map((id) => {
          const provider = providers.find((item) => item.id === id)
          return provider ? autoButton(provider, provider.label, ICONS[id]) : null
        })}
        {(group === 'registrar' || group === 'registry') && ([group] as const).flatMap((channel) => (check.report_forms ?? []).filter((form) => form.channel === channel).map((form) => {
          const role = channel === 'registrar' ? 'Registrar' : 'Registry'
          const icon = channel === 'registrar' ? <Building2 size={16} /> : <Globe2 size={16} />
          const label = `${role} · ${form.label}`
          const provider = providers.find((item) => item.id === form.autofill_provider)
          return provider ? autoButton(provider, label, icon) : <Button asChild key={`${channel}:${form.form_url}`} className="primary-button report-action inline-flex items-center gap-2 text-xs"><a
            title="Mở form thủ công" href={form.form_url} target="_blank" rel="noopener noreferrer">{icon}<span className="max-w-52 truncate">{label}</span></a></Button>
        }))}
        {group === 'general' && <NetcraftAction checkId={check.id} compact />}
        </div>
        <QuickReportDraftPanel wide={group === 'general'} drafts={(drafts.data ?? []).filter((item) => item.channel === group)} loading={drafts.isPending} {...(drafts.error ? { error: drafts.error.message } : {})} />
      </div>)}
      {!canOpenForm && <NetcraftAction checkId={check.id} compact />}
    </div>
    {canOpenForm && !connected && <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">Kết nối extension để dùng các nút tự điền. Form thủ công vẫn có thể xem nội dung.</p>}
    {open.error && <p className="error-notice mt-3" role="alert">{open.error.message}</p>}
    {task.error && <p className="error-notice mt-3" role="alert">{task.error.message}</p>}
    {task.data && <p className="mt-3 text-xs text-slate-500" role="status">Form: <strong>{task.data.state}</strong>{typeof task.data.status?.result === 'string' ? ` · ${task.data.status.result}` : ''}</p>}
  </section>
}


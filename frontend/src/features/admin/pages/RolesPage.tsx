import { QueryError } from '../../../components/shared/QueryError'
import { AppDialog } from '../../../components/shared/AppDialog'
import { Button } from '../../../components/shared/Button'
import { useConfirmation } from '../../../components/shared/useConfirmation'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Shield } from 'lucide-react'
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'

import type { Me } from '../../auth/types'
import { createRole, deleteRole, getPermissions, getRoles, updateRole } from '../api/adminApi'
import type { PermissionItem, RoleDetail } from '../types'

export function RolesPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const canView = me.permissions.includes('roles.view')
  const client = useQueryClient()
  const [editing, setEditing] = useState<RoleDetail | 'new' | null>(null)
  const roles = useQuery({ queryKey: ['admin', 'roles'], queryFn: ({ signal }) => getRoles(signal), enabled: canView })
  const permissions = useQuery({ queryKey: ['admin', 'permissions'], queryFn: ({ signal }) => getPermissions(signal), enabled: canView })
  const refresh = () => { void client.invalidateQueries({ queryKey: ['admin', 'roles'] }) }
  const save = useMutation({ meta: { successMessage: 'Đã lưu role và bộ quyền.' }, mutationFn: (input: { id?: string; key: string; name: string; description: string; permissions: string[] }) => input.id ? updateRole(input.id, input) : createRole(input), onSuccess: () => { refresh(); setEditing(null) } })
  const remove = useMutation({ meta: { successMessage: 'Đã xóa role.' }, mutationFn: deleteRole, onSuccess: refresh })
  const { ask, confirmation } = useConfirmation()
  const confirmRemove = (role: RoleDetail) => { ask({ title: 'Xóa role?', description: `Role ${role.name} sẽ bị xóa. Hãy kiểm tra ảnh hưởng đến quyền truy cập của thành viên trước khi tiếp tục.`, label: 'Xác nhận xóa', action: () => { remove.mutate(role.id) } }) }
  const error = save.error ?? remove.error
  const canManage = me.permissions.includes('roles.manage')
  if (!canView) return <div className="error-notice" role="alert">Bạn không có quyền xem trang role.</div>
  return <section>{confirmation}{roles.isError && <QueryError message={roles.error.message} onRetry={() => { void roles.refetch() }} />}{permissions.isError && <QueryError message={permissions.error.message} onRetry={() => { void permissions.refetch() }} />}<div className="page-heading flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div><p className="eyebrow">Access policy</p><h1 className="page-title">Role & quyền</h1><p className="page-description">Admin luôn có toàn bộ quyền. Role user và các role tùy chỉnh chỉ có những permission được chọn tại đây.</p></div>{canManage && <Button className="primary-button flex items-center gap-2" onClick={() => { setEditing('new') }}><Plus size={17} />Tạo role</Button>}</div>{error && <div className="error-notice" role="alert">{error.message}</div>}<div className="mt-7 grid gap-4 xl:grid-cols-2">{roles.isPending ? <div className="data-panel p-8">Đang tải role…</div> : roles.data?.map((role) => <article className="data-panel p-6" key={role.id}><div className="flex items-start justify-between gap-4"><div><div className="flex items-center gap-2"><Shield size={18} className="text-indigo-600" /><h2 className="font-semibold">{role.name}</h2>{role.system && <span className="role-chip">Hệ thống</span>}</div><p className="mt-1 font-mono text-xs text-slate-500">{role.key}</p></div>{canManage && <div className="flex gap-2"><Button className="secondary-button" disabled={role.key === 'admin'} onClick={() => { setEditing(role) }}>Sửa quyền</Button><Button className="danger-button" disabled={role.system || remove.isPending} onClick={() => { confirmRemove(role) }}>Xóa</Button></div>}</div><p className="mt-4 text-sm text-slate-600 dark:text-slate-300">{role.description || 'Không có mô tả.'}</p><p className="mt-4 text-xs font-medium text-slate-500">{role.permissions.length} quyền</p><div className="mt-2 flex max-h-28 flex-wrap gap-1 overflow-y-auto">{role.permissions.map((code) => <code className="permission-chip" key={code}>{code}</code>)}</div></article>)}</div>{editing && permissions.data && <RoleEditor role={editing} permissions={permissions.data} busy={save.isPending} onClose={() => { setEditing(null) }} onSave={(value) => { save.mutate(value) }} />}</section>
}

function RoleEditor({ role, permissions, busy, onClose, onSave }: { role: RoleDetail | 'new'; permissions: Awaited<ReturnType<typeof getPermissions>>; busy: boolean; onClose: () => void; onSave: (input: { id?: string; key: string; name: string; description: string; permissions: string[] }) => void }) {
  const current = role === 'new' ? null : role
  const [key, setKey] = useState(current?.key ?? '')
  const [name, setName] = useState(current?.name ?? '')
  const [description, setDescription] = useState(current?.description ?? '')
  const [selected, setSelected] = useState(() => new Set(current?.permissions ?? []))
  const groups = permissions.reduce<Map<string, PermissionItem[]>>((result, item) => {
    const group = item.code.split('.')[0] ?? 'other'
    result.set(group, [...(result.get(group) ?? []), item])
    return result
  }, new Map())
  return <AppDialog title={current ? `Sửa quyền ${current.name}` : 'Tạo role'} onClose={onClose} busy={busy} maxWidth="840px">{current?.key === 'user' && <p className="mt-2 text-sm text-slate-400">Mọi tài khoản được kích hoạt đều nhận role này. Chỉ các quyền được tick bên dưới mới có hiệu lực.</p>}<div className="mt-5 grid gap-4 sm:grid-cols-2"><label className="field">Khóa role<input disabled={Boolean(current)} value={key} onChange={(e) => { setKey(e.target.value) }} placeholder="reviewer" /></label><label className="field">Tên role<input disabled={Boolean(current?.system)} value={name} onChange={(e) => { setName(e.target.value) }} /></label><label className="field sm:col-span-2">Mô tả<textarea disabled={Boolean(current?.system)} value={description} onChange={(e) => { setDescription(e.target.value) }} rows={2} /></label></div><div className="mt-5 max-h-80 overflow-y-auto rounded-xl border border-slate-200 p-4 dark:border-slate-700">{[...groups].map(([group, items]) => <fieldset className="mb-5" key={group}><legend className="mb-2 font-mono text-xs font-semibold uppercase text-indigo-600">{group}</legend><div className="grid gap-2 sm:grid-cols-2">{items.map((permission) => <label className="check-row" key={permission.code}><input type="checkbox" checked={selected.has(permission.code)} onChange={(event) => { const next = new Set(selected); if (event.target.checked) next.add(permission.code); else next.delete(permission.code); setSelected(next) }} /><span><strong>{permission.code}</strong><small>{permission.description}</small></span></label>)}</div></fieldset>)}</div><div className="mt-6 flex justify-end gap-2"><Button className="secondary-button" onClick={onClose}>Hủy</Button><Button className="primary-button" loading={busy} disabled={busy || !key.trim() || !name.trim()} onClick={() => { const value = { key, name, description, permissions: [...selected] }; onSave(current ? { ...value, id: current.id } : value) }}>Lưu role</Button></div></AppDialog>
}

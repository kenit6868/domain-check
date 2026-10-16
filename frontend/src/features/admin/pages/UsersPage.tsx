import { QueryError } from '../../../components/shared/QueryError'
import { AppDialog } from '../../../components/shared/AppDialog'
import { Button } from '../../../components/shared/Button'
import { useConfirmation } from '../../../components/shared/useConfirmation'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Search, ShieldAlert } from 'lucide-react'
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'

import type { Me } from '../../auth/types'
import { getRoles, getUsers, replaceUserRoles, revokeUserSessions, updateUserStatus } from '../api/adminApi'
import type { UserItem } from '../types'

/* Event handlers intentionally dispatch TanStack mutations without awaiting them. */

export function UsersPage() {
  const { me } = useOutletContext<{ me: Me }>()
  const canView = me.permissions.includes('users.view')
  const client = useQueryClient()
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<UserItem | null>(null)
  const users = useQuery({ queryKey: ['admin', 'users', search], queryFn: ({ signal }) => getUsers(search, signal), enabled: canView })
  const roles = useQuery({ queryKey: ['admin', 'roles'], queryFn: ({ signal }) => getRoles(signal), enabled: canView && me.permissions.includes('users.assign_roles') })
  const refresh = () => { void client.invalidateQueries({ queryKey: ['admin', 'users'] }) }
  const statusMutation = useMutation({ meta: { successMessage: 'Đã cập nhật trạng thái người dùng.' }, mutationFn: ({ id, status }: { id: string; status: UserItem['status'] }) => updateUserStatus(id, status), onSuccess: refresh })
  const roleMutation = useMutation({ meta: { successMessage: 'Đã lưu role và cập nhật quyền người dùng.' }, mutationFn: ({ id, ids }: { id: string; ids: string[] }) => replaceUserRoles(id, ids), onSuccess: () => { refresh(); setSelected(null) } })
  const revokeMutation = useMutation({ meta: { successMessage: 'Đã thu hồi các phiên đăng nhập.' }, mutationFn: revokeUserSessions, onSuccess: refresh })
  const { ask, confirmation } = useConfirmation()
  const confirmLock = (user: UserItem) => { ask({ title: 'Khóa người dùng?', description: `${user.display_name} sẽ mất quyền truy cập và phải được kích hoạt lại để tiếp tục.`, label: 'Xác nhận khóa', action: () => { statusMutation.mutate({ id: user.id, status: 'disabled' }) } }) }
  const confirmRevoke = (user: UserItem) => { ask({ title: 'Thu hồi phiên đăng nhập?', description: `Các phiên của ${user.display_name} sẽ hết hiệu lực. Người dùng cần đăng nhập lại.`, label: 'Thu hồi phiên', action: () => { revokeMutation.mutate(user.id) } }) }
  const error = statusMutation.error ?? roleMutation.error ?? revokeMutation.error
  const canManage = me.permissions.includes('users.manage')
  const canAssign = me.permissions.includes('users.assign_roles')

  if (!canView) return <State text="Bạn không có quyền xem trang người dùng." error />

  return <section>
    {confirmation}
    <div className="page-heading flex flex-col justify-between gap-3 sm:flex-row sm:items-end"><div><p className="eyebrow">Identity & access</p><h1 className="page-title">Người dùng</h1><p className="page-description">Kích hoạt tài khoản Telegram, gán role và thu hồi phiên đang hoạt động.</p></div><label className="search-box"><Search size={18} /><span className="sr-only">Tìm người dùng</span><input value={search} onChange={(event) => { setSearch(event.target.value) }} placeholder="Tên hoặc username" /></label></div>
    {error && <div className="error-notice" role="alert">{error.message}</div>}
    <div className="data-panel mt-3 overflow-x-auto">
      {users.isPending ? <State text="Đang tải người dùng…" /> : users.isError ? <QueryError message={users.error.message} onRetry={() => { void users.refetch() }} /> : users.data.items.length === 0 ? <State text="Không có người dùng phù hợp." /> :
      <table className="data-table"><thead><tr><th>Người dùng</th><th>Trạng thái</th><th>Role</th><th>Lần đăng nhập cuối</th><th className="text-right">Thao tác</th></tr></thead><tbody>{users.data.items.map((user) => <tr key={user.id}><td><strong>{user.display_name}</strong><span>@{user.telegram_username ?? 'chưa có username'} · {user.identity_provider ?? 'unknown'}</span></td><td><Status status={user.status} /></td><td><div className="flex flex-wrap gap-1">{user.roles.map((role) => <span className="role-chip" key={role.id}>{role.name}</span>)}</div></td><td>{user.last_login_at ? new Date(user.last_login_at).toLocaleString('vi-VN') : '—'}</td><td><div className="flex justify-end gap-2">{canAssign && <Button className="secondary-button" onClick={() => { setSelected(user) }}>Gán role</Button>}{canManage && (user.status !== 'active' ? <Button className="primary-button" disabled={statusMutation.isPending} onClick={() => { statusMutation.mutate({ id: user.id, status: 'active' }) }}>Kích hoạt</Button> : <Button className="danger-button" disabled={statusMutation.isPending} onClick={() => { confirmLock(user) }}>Khóa</Button>)}{canManage && <Button className="secondary-button" disabled={revokeMutation.isPending} onClick={() => { confirmRevoke(user) }}>Thu hồi phiên</Button>}</div></td></tr>)}</tbody></table>}
    </div>
    {selected && roles.data && <RoleDialog user={selected} roles={roles.data} busy={roleMutation.isPending} onClose={() => { setSelected(null) }} onSave={(ids) => { roleMutation.mutate({ id: selected.id, ids }) }} />}
  </section>
}

function RoleDialog({ user, roles, busy, onClose, onSave }: { user: UserItem; roles: Awaited<ReturnType<typeof getRoles>>; busy: boolean; onClose: () => void; onSave: (ids: string[]) => void }) {
  const [ids, setIds] = useState(() => new Set(user.roles.map((role) => role.id)))
  return <AppDialog title={`Role của ${user.display_name}`} onClose={onClose} busy={busy} maxWidth="640px"><p className="mt-2 text-sm text-slate-500">API sẽ từ chối nếu thao tác làm mất admin hoạt động cuối cùng hoặc vượt quyền của bạn.</p><div className="mt-3 max-h-80 space-y-2 overflow-y-auto">{roles.map((role) => <label className="check-row" key={role.id}><input type="checkbox" checked={ids.has(role.id)} onChange={(event) => { const next = new Set(ids); if (event.target.checked) next.add(role.id); else next.delete(role.id); setIds(next) }} /><span><strong>{role.name}</strong><small>{role.description}</small></span></label>)}</div><div className="mt-3 flex justify-end gap-2"><Button className="secondary-button" onClick={onClose}>Hủy</Button><Button className="primary-button" loading={busy} onClick={() => { onSave([...ids]) }}>Lưu role</Button></div></AppDialog>
}

function Status({ status }: { status: UserItem['status'] }) { return <span className={`status status-${status}`}>{status === 'active' ? 'Hoạt động' : status === 'pending' ? 'Chờ duyệt' : 'Đã khóa'}</span> }
function State({ text, error = false }: { text: string; error?: boolean }) { return <div className={`p-10 text-center ${error ? 'text-rose-600' : 'text-slate-500'}`}>{error && <ShieldAlert className="mx-auto mb-2" />}{text}</div> }

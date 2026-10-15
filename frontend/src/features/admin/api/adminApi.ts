import { request } from '../../../lib/http/client'
import type { PermissionItem, RoleDetail, RoleInput, UserItem, UserPage } from '../types'

export function getUsers(search = '', signal?: AbortSignal) {
  const query = new URLSearchParams({ limit: '100' })
  if (search.trim()) query.set('search', search.trim())
  return request<UserPage>({ url: `/users?${query}`, ...(signal ? { signal } : {}) })
}

export const updateUserStatus = (id: string, status: UserItem['status']) =>
  request<UserItem>({ url: `/users/${id}/status`, method: 'PATCH', data: { status } })
export const replaceUserRoles = (id: string, roleIds: string[]) =>
  request<undefined>({ url: `/users/${id}/roles`, method: 'PUT', data: { role_ids: roleIds } })
export const revokeUserSessions = (id: string) =>
  request<undefined>({ url: `/users/${id}/revoke-sessions`, method: 'POST' })
export const getRoles = (signal?: AbortSignal) =>
  request<RoleDetail[]>({ url: '/roles', ...(signal ? { signal } : {}) })
export const getPermissions = (signal?: AbortSignal) =>
  request<PermissionItem[]>({ url: '/permissions', ...(signal ? { signal } : {}) })
export const createRole = (input: RoleInput & { key: string }) =>
  request<RoleDetail>({ url: '/roles', method: 'POST', data: input })
export const updateRole = (id: string, input: RoleInput) =>
  request<RoleDetail>({ url: `/roles/${id}`, method: 'PATCH', data: input })
export const deleteRole = (id: string) =>
  request<undefined>({ url: `/roles/${id}`, method: 'DELETE' })

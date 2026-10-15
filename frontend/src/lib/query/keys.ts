export const queryKeys = {
  me: ['auth', 'me'] as const,
  quickReport: (userId: string, operationId: string) =>
    ['quick-report', userId, operationId] as const,
  formTask: (taskId: string) => ['form-task', taskId] as const,
  users: (search: string) => ['admin', 'users', search] as const,
  roles: ['admin', 'roles'] as const,
  permissions: ['admin', 'permissions'] as const,
  mail: ['mail'] as const,
}

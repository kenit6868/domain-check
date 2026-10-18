import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query'
import { notifyError, toast } from '../notifications'
import { ApiError } from '../http/errors'
import { queryKeys } from './keys'

export function createQueryClient(): QueryClient {
  const pending = new WeakMap<object, string | number>()
  const expireSession = (error: unknown) => {
    if (error instanceof ApiError && error.status === 403) {
      // Another administrator may have removed a permission since /me was read.
      void client.invalidateQueries({ queryKey: queryKeys.me })
      return
    }
    if (!(error instanceof ApiError) || error.status !== 401) return
    const me = client.getQueryCache().find({ queryKey: queryKeys.me, exact: true })
    // A protected request has already verified that this session is invalid.
    // Keep its previous identity only to explain the redirect to the login page.
    if (me?.state.data) me.setState({ status: 'error', error, fetchStatus: 'idle' })
  }
  const client = new QueryClient({
    queryCache: new QueryCache({ onError: expireSession }),
    mutationCache: new MutationCache({
      onMutate: (_variables, mutation) => {
        if (mutation.meta?.pendingMessage) pending.set(mutation, toast.loading(mutation.meta.pendingMessage))
      },
      onSuccess: (_data, _variables, _context, mutation) => {
        const id = pending.get(mutation)
        if (mutation.meta?.successMessage) toast.success(mutation.meta.successMessage, id === undefined ? {} : { id })
        else if (id !== undefined) toast.dismiss(id)
      },
      onError: (error, _variables, _context, mutation) => {
        expireSession(error)
        const id = pending.get(mutation)
        if (!mutation.meta?.silentError) notifyError(error, id)
        else if (id !== undefined) toast.dismiss(id)
      },
      onSettled: (_data, _error, _variables, _context, mutation) => { pending.delete(mutation) },
    }),
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => !(error instanceof ApiError && error.status === 401) && failureCount < 1,
        staleTime: 15_000,
      },
      mutations: { retry: false },
    },
  })
  return client
}

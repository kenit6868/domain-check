import { MutationCache, QueryClient } from '@tanstack/react-query'
import { notifyError, toast } from '../notifications'

export function createQueryClient(): QueryClient {
  const pending = new WeakMap<object, string | number>()
  return new QueryClient({
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
        const id = pending.get(mutation)
        if (!mutation.meta?.silentError) notifyError(error, id)
        else if (id !== undefined) toast.dismiss(id)
      },
      onSettled: (_data, _error, _variables, _context, mutation) => { pending.delete(mutation) },
    }),
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        retry: 1,
        staleTime: 15_000,
      },
      mutations: { retry: false },
    },
  })
}

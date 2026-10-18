import { useQuery } from '@tanstack/react-query'

import { queryKeys } from '../../../lib/query/keys'
import { getCurrentUser } from '../api/authApi'

export function useCurrentUser() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => getCurrentUser(signal),
    retry: false,
    staleTime: 30_000,
    refetchOnWindowFocus: true,
    refetchInterval: (query) => query.state.data && query.state.status !== 'error' ? 30_000 : false,
  })
}

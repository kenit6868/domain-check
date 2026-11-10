import type { QueryClient } from '@tanstack/react-query'
import { getNetcraftStatuses, type NetcraftSubmission } from './actionApi'

type Snapshot = NetcraftSubmission | null
type FetchBatch = (ids: string[]) => Promise<Record<string, Snapshot>>
type Pending = { id: string; signal?: AbortSignal | undefined; resolve: (value: Snapshot) => void; reject: (error: unknown) => void; abort: () => void }

// Coalesce independent React Query readers without sharing caches between users.
// One request at a time, at most 50 check IDs; mutations never pass this queue.
export function createStatusBatcher(fetchBatch: FetchBatch) {
  const pending: Pending[] = []
  let scheduled = false
  const flush = async () => {
    try {
      while (pending.length) {
        const entries = pending.splice(0, 50)
        const active = entries.filter(entry => !entry.signal?.aborted)
        try {
          if (active.length) {
            const result = await fetchBatch([...new Set(active.map(entry => entry.id))])
            for (const entry of active) {
              if (!Object.hasOwn(result, entry.id)) entry.reject(new Error('Thiếu trạng thái Netcraft trong phản hồi.'))
              else entry.resolve(result[entry.id] ?? null)
            }
          }
        } catch (error) {
          for (const entry of active) entry.reject(error)
        } finally {
          for (const entry of entries) entry.signal?.removeEventListener('abort', entry.abort)
        }
      }
    } finally { scheduled = false }
  }
  return (id: string, signal?: AbortSignal): Promise<Snapshot> => new Promise((resolve, reject) => {
    const abort = () => { reject(new DOMException('Request cancelled', 'AbortError')) }
    if (signal?.aborted) { abort(); return }
    signal?.addEventListener('abort', abort, { once: true })
    pending.push({ id, signal, resolve, reject, abort })
    if (!scheduled) { scheduled = true; setTimeout(() => { void flush() }, 0) }
  })
}

const clients = new WeakMap<QueryClient, Map<string, ReturnType<typeof createStatusBatcher>>>()
export function getBatchedNetcraft(client: QueryClient, ownerId: string, checkId: string, signal: AbortSignal) {
  let owners = clients.get(client)
  if (!owners) { owners = new Map(); clients.set(client, owners) }
  let batcher = owners.get(ownerId)
  if (!batcher) { batcher = createStatusBatcher(getNetcraftStatuses); owners.set(ownerId, batcher) }
  return batcher(checkId, signal)
}

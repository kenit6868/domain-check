// Preserve automatic draft display without flooding the API on long lists.
export function createRequestLimiter(limit: number) {
  let active = 0
  const queue: Array<() => void> = []
  const pump = () => { while (active < limit && queue.length) queue.shift()?.() }
  return <T>(work: () => Promise<T>, signal: AbortSignal): Promise<T> => new Promise((resolve, reject) => {
    const abort = () => { reject(new DOMException('Request cancelled', 'AbortError')) }
    if (signal.aborted) { abort(); return }
    const start = () => {
      if (signal.aborted) { signal.removeEventListener('abort', abort); return }
      active += 1
      void Promise.resolve().then(work).then(resolve, reject).finally(() => {
        signal.removeEventListener('abort', abort)
        active -= 1
        pump()
      })
    }
    signal.addEventListener('abort', abort, { once: true })
    queue.push(start)
    pump()
  })
}

export const limitDraftRequest = createRequestLimiter(2)

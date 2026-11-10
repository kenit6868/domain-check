import { expect, it, vi } from 'vitest'
import { createRequestLimiter } from '../src/features/quick-report/api/draftRequestQueue'

it('automatically loads 100 drafts with at most two requests running', async () => {
  const run = createRequestLimiter(2)
  let active = 0
  let peak = 0
  const work = vi.fn(async () => {
    peak = Math.max(peak, ++active)
    await new Promise(resolve => setTimeout(resolve, 1))
    active -= 1
    return 'draft'
  })
  const result = await Promise.all(Array.from({ length: 100 }, () => run(work, new AbortController().signal)))
  expect(result).toHaveLength(100)
  expect(work).toHaveBeenCalledTimes(100)
  expect(peak).toBe(2)
})

it('skips cancelled queued requests and releases slots after failure', async () => {
  const run = createRequestLimiter(1)
  let release: () => void = () => {}
  const first = run(() => new Promise<void>(resolve => { release = resolve }), new AbortController().signal)
  await Promise.resolve()
  const cancelled = new AbortController()
  const work = vi.fn(() => Promise.resolve('unexpected'))
  const rejected = expect(run(work, cancelled.signal)).rejects.toThrow('Request cancelled')
  cancelled.abort()
  await rejected
  release()
  await first
  await expect(run(() => Promise.reject(new Error('fixture error')), new AbortController().signal)).rejects.toThrow('fixture error')
  await expect(run(() => Promise.resolve('recovered'), new AbortController().signal)).resolves.toBe('recovered')
  expect(work).not.toHaveBeenCalled()
})

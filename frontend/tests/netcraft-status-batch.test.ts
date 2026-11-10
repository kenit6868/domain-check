import { expect, it, vi } from 'vitest'
import { createStatusBatcher } from '../src/features/quick-report/api/netcraftStatusBatch'

it('coalesces 100 readers into two sequential requests of at most 50', async () => {
  let active = 0
  let peak = 0
  const fetcher = vi.fn(async (ids: string[]) => {
    active += 1
    peak = Math.max(peak, active)
    await new Promise(resolve => setTimeout(resolve, 1))
    active -= 1
    return Object.fromEntries(ids.map(id => [id, null]))
  })
  const read = createStatusBatcher(fetcher)
  const results = await Promise.all(Array.from({ length: 100 }, (_, index) => read(String(index))))
  expect(results).toEqual(Array.from({ length: 100 }, () => null))
  expect(fetcher.mock.calls.map(([ids]) => ids.length)).toEqual([50, 50])
  expect(peak).toBe(1)
})

it('omits cancelled readers and fails closed when a status is missing', async () => {
  const fetcher = vi.fn((ids: string[]) => Promise.resolve(Object.fromEntries(ids.filter(id => id !== 'missing').map(id => [id, null]))))
  const read = createStatusBatcher(fetcher)
  const controller = new AbortController()
  const cancelled = expect(read('cancelled', controller.signal)).rejects.toThrow('Request cancelled')
  controller.abort()
  await cancelled
  const [valid, missing] = await Promise.allSettled([read('valid'), read('missing')])
  expect(valid).toEqual({ status: 'fulfilled', value: null })
  expect(missing.status).toBe('rejected')
  expect(fetcher).toHaveBeenCalledWith(['valid', 'missing'])
})

it('rejects failed batches and accepts new reads after recovery', async () => {
  const fetcher = vi.fn((ids: string[]) => Promise.resolve(Object.fromEntries(ids.map(id => [id, null]))))
    .mockRejectedValueOnce(new Error('offline'))
  const read = createStatusBatcher(fetcher)
  const results = await Promise.allSettled([read('first'), read('second')])
  expect(results.every(result => result.status === 'rejected')).toBe(true)
  await expect(read('third')).resolves.toBeNull()
  expect(fetcher).toHaveBeenCalledTimes(2)
})

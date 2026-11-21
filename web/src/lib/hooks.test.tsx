import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useQuery } from './hooks'
import { apiFetch } from './http'
import { clearQueryCache } from './queryCache'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  clearQueryCache()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useQuery cache', () => {
  it('serves a fresh cache entry to a remount without refetching', async () => {
    const fetcher = vi.fn(async () => 'first')
    const first = renderHook(() => useQuery('project:one', fetcher))
    await waitFor(() => expect(first.result.current.data).toBe('first'))
    first.unmount()

    const second = renderHook(() => useQuery('project:one', fetcher))
    await waitFor(() => expect(second.result.current.data).toBe('first'))
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(second.result.current.loading).toBe(false)
  })

  it('deduplicates concurrent mounts with the same key', async () => {
    const request = deferred<string>()
    const fetcher = vi.fn(() => request.promise)
    const one = renderHook(() => useQuery('outline:one', fetcher))
    const two = renderHook(() => useQuery('outline:one', fetcher))

    expect(fetcher).toHaveBeenCalledTimes(1)

    await act(async () => {
      request.resolve('chapters')
    })
    await waitFor(() => {
      expect(one.result.current.data).toBe('chapters')
      expect(two.result.current.data).toBe('chapters')
    })
  })

  it('keeps locally set data in the cache for the next mount', async () => {
    const fetcher = vi.fn(async () => 'from-server')
    const first = renderHook(() => useQuery('ledger:one', fetcher))
    await waitFor(() => expect(first.result.current.data).toBe('from-server'))

    act(() => {
      first.result.current.setData('edited-locally')
    })
    expect(first.result.current.data).toBe('edited-locally')
    first.unmount()

    const second = renderHook(() => useQuery('ledger:one', fetcher))
    await waitFor(() => expect(second.result.current.data).toBe('edited-locally'))
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})

describe('useQuery request lifecycle', () => {
  it('ignores a late response after the key changed', async () => {
    const first = deferred<string>()
    const second = deferred<string>()
    let currentKey = 'a'
    const fetcher = vi.fn(() => (currentKey === 'a' ? first.promise : second.promise))

    const { result, rerender } = renderHook(
      ({ queryKey }: { queryKey: string }) => useQuery(queryKey, fetcher),
      { initialProps: { queryKey: 'project:a' } },
    )

    currentKey = 'b'
    rerender({ queryKey: 'project:b' })
    await act(async () => {
      second.resolve('B')
    })
    await waitFor(() => expect(result.current.data).toBe('B'))

    await act(async () => {
      first.resolve('A')
    })
    expect(result.current.data).toBe('B')
  })

  it('ignores an older refresh response that lands after a newer one', async () => {
    const older = deferred<string>()
    const newer = deferred<string>()
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockImplementationOnce(() => older.promise)
      .mockImplementationOnce(() => newer.promise)

    const { result } = renderHook(() => useQuery('jobs:one', fetcher))
    act(() => {
      result.current.refresh()
    })
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))

    await act(async () => {
      newer.resolve('newer')
    })
    await waitFor(() => expect(result.current.data).toBe('newer'))
    expect(result.current.refreshing).toBe(false)

    await act(async () => {
      older.resolve('older')
    })
    expect(result.current.data).toBe('newer')
  })

  it('surfaces an error, then clears it on refresh', async () => {
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new Error('后端不可用'))
      .mockResolvedValueOnce('recovered')

    const { result } = renderHook(() => useQuery('issues:one', fetcher))
    await waitFor(() => expect(result.current.error).toBe('后端不可用'))
    expect(result.current.loading).toBe(false)

    act(() => {
      result.current.refresh()
    })
    await waitFor(() => expect(result.current.data).toBe('recovered'))
    expect(result.current.error).toBeNull()
  })

  it('does not fetch while disabled', () => {
    const fetcher = vi.fn(async () => 'never')
    const { result } = renderHook(() => useQuery('boq:waiting', fetcher, { enabled: false }))
    expect(fetcher).not.toHaveBeenCalled()
    expect(result.current.loading).toBe(true)
  })
})

describe('useQuery cancellation', () => {
  it('passes its signal to the fetcher and aborts it on unmount', async () => {
    let received: AbortSignal | undefined
    const fetcher = vi.fn((signal?: AbortSignal) => {
      received = signal
      return new Promise<string>(() => {
        /* never resolves: the hook must be able to cancel it */
      })
    })

    const { unmount } = renderHook(() => useQuery('cancel:one', fetcher))
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
    expect(received).toBeDefined()
    expect(received?.aborted).toBe(false)

    unmount()
    expect(received?.aborted).toBe(true)
  })

  it('forwards the signal through apiFetch and aborts the underlying fetch', async () => {
    const signals: AbortSignal[] = []
    const fetchMock = vi.fn((_path: RequestInfo | URL, init?: RequestInit) => {
      const signal = init?.signal
      if (!signal) throw new Error('missing abort signal')
      signals.push(signal)
      return new Promise<Response>((_resolve, reject) => {
        signal.addEventListener('abort', () => {
          reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
        })
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const { unmount } = renderHook(() =>
      useQuery('cancel:fetch', (signal) => apiFetch('/slow', undefined, signal)),
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(signals[0].aborted).toBe(false)

    unmount()
    expect(signals[0].aborted).toBe(true)
    // The abort rejection must not leak as an unhandled error.
    await act(async () => {})
  })
})

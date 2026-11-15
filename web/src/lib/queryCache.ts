/**
 * Module-level cache shared by every `useQuery` hook.
 *
 * Kept in its own module so non-React code (for example the auth provider that
 * clears per-user data on login/logout) can reset the cache without importing
 * the hook layer.
 */

export const QUERY_STALE_TIME = 30_000

const MAX_ENTRIES = 200

export interface QueryCacheEntry<T = unknown> {
  data: T | undefined
  /** Timestamp of the last successful fetch or local `setData`. */
  ts: number
  promise: Promise<unknown> | null
  controller: AbortController | null
  waiters: number
  /** Monotonic id so a replaced request can never overwrite newer data. */
  requestId: number
}

const cache = new Map<string, QueryCacheEntry>()
const subscribers = new Map<string, Set<(data: unknown) => void>>()

/** Read an entry and mark it as most recently used. */
export function peekQueryEntry<T>(key: string): QueryCacheEntry<T> | undefined {
  const entry = cache.get(key)
  if (!entry) return undefined
  cache.delete(key)
  cache.set(key, entry)
  return entry as QueryCacheEntry<T>
}

export function ensureQueryEntry(key: string): QueryCacheEntry {
  const existing = peekQueryEntry(key)
  if (existing) return existing
  const entry: QueryCacheEntry = {
    data: undefined,
    ts: 0,
    promise: null,
    controller: null,
    waiters: 0,
    requestId: 0,
  }
  cache.set(key, entry)
  trim()
  return entry
}

/** Store fresh data and wake every mounted hook that shares the key. */
export function storeQueryData(key: string, data: unknown): void {
  const entry = ensureQueryEntry(key)
  entry.data = data
  entry.ts = Date.now()
  const listeners = subscribers.get(key)
  if (!listeners) return
  for (const listener of Array.from(listeners)) listener(data)
}

export function subscribeQuery(
  key: string,
  listener: (data: unknown) => void,
): () => void {
  let listeners = subscribers.get(key)
  if (!listeners) {
    listeners = new Set()
    subscribers.set(key, listeners)
  }
  listeners.add(listener)
  return () => {
    const current = subscribers.get(key)
    if (!current) return
    current.delete(listener)
    if (current.size === 0) subscribers.delete(key)
  }
}

/**
 * Drop one waiter. When nobody is listening any more, abort the in-flight
 * request so a key switch or unmount does not keep the network busy.
 */
export function releaseQuery(key: string): void {
  const entry = cache.get(key)
  if (!entry) return
  entry.waiters = Math.max(0, entry.waiters - 1)
  if (entry.waiters > 0) return
  entry.controller?.abort()
  entry.controller = null
  entry.promise = null
  trim()
}

/** Clear everything (logout, account switch, tests). */
export function clearQueryCache(): void {
  for (const entry of cache.values()) entry.controller?.abort()
  cache.clear()
  subscribers.clear()
}

function trim(): void {
  if (cache.size <= MAX_ENTRIES) return
  for (const [key, entry] of cache) {
    if (cache.size <= MAX_ENTRIES) break
    if (entry.waiters > 0 || entry.promise) continue
    cache.delete(key)
  }
}

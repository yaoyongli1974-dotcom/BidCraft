import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react'
import { useBlocker } from 'react-router-dom'
import { getPackages, messageOf } from '../lib/api'
import { readStorage, writeStorage } from '../lib/format'
import type { AuditEntry, AuditPage, PackageInfo } from '../lib/types'
import { useConfirm } from '../context/ConfirmContext'

interface QueryState<T> {
  key: string
  data: T | undefined
  error: string | null
  pending: boolean
}

export interface QueryResult<T> {
  data: T | undefined
  error: string | null
  loading: boolean
  refreshing: boolean
  refresh: () => void
  setData: Dispatch<SetStateAction<T | undefined>>
}

/**
 * Small request hook. `key` must uniquely describe the request parameters;
 * changing it clears stale data so views never show another project's content.
 */
export function useQuery<T>(
  key: string,
  fetcher: () => Promise<T>,
  options: { enabled?: boolean } = {},
): QueryResult<T> {
  const { enabled = true } = options
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const [nonce, setNonce] = useState(0)
  const [state, setState] = useState<QueryState<T>>({
    key,
    data: undefined,
    error: null,
    pending: true,
  })

  useEffect(() => {
    // Some views must wait for a prerequisite request (for example the BOQ
    // package list) before the key has a valid value. Keep the loading state
    // until `enabled` turns on, without firing a request with a half-built key.
    if (!enabled) {
      setState((current) => ({
        key,
        data: current.key === key ? current.data : undefined,
        error: null,
        pending: true,
      }))
      return
    }
    let active = true
    setState((current) => ({
      key,
      data: current.key === key ? current.data : undefined,
      error: null,
      pending: true,
    }))
    fetcherRef
      .current()
      .then((data) => {
        if (!active) return
        setState({ key, data, error: null, pending: false })
      })
      .catch((error: unknown) => {
        if (!active) return
        setState((current) => ({
          key,
          data: current.key === key ? current.data : undefined,
          error: messageOf(error),
          pending: false,
        }))
      })
    return () => {
      active = false
    }
  }, [key, nonce, enabled])

  const refresh = useCallback(() => setNonce((value) => value + 1), [])
  const setData = useCallback<Dispatch<SetStateAction<T | undefined>>>((value) => {
    setState((current) => {
      const next =
        typeof value === 'function'
          ? (value as (previous: T | undefined) => T | undefined)(current.data)
          : value
      return { ...current, data: next, pending: false, error: null }
    })
  }, [])

  return {
    data: state.key === key ? state.data : undefined,
    error: state.key === key ? state.error : null,
    loading: state.pending && state.data === undefined,
    refreshing: state.pending && state.data !== undefined,
    refresh,
    setData,
  }
}

// ------------------------------------------------------------------ packages

let packageCache: PackageInfo[] | null = null
let packagePromise: Promise<PackageInfo[]> | null = null

function fetchPackages(force = false): Promise<PackageInfo[]> {
  if (!force && packageCache) return Promise.resolve(packageCache)
  if (!force && packagePromise) return packagePromise
  packagePromise = getPackages()
    .then((items) => {
      packageCache = items
      return items
    })
    .finally(() => {
      packagePromise = null
    })
  return packagePromise
}

/**
 * Drop the module-level package cache. Call after an administrator changes rule
 * packages so the next new-project dialog reads the new list from the server.
 */
export function invalidatePackageCache(): void {
  packageCache = null
}

export function usePackages() {
  const [packages, setPackages] = useState<PackageInfo[]>(packageCache || [])
  const [loading, setLoading] = useState(!packageCache)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let active = true
    setLoading(!packageCache)
    fetchPackages(nonce > 0)
      .then((items) => {
        if (!active) return
        setPackages(items)
        setError(null)
      })
      .catch((err) => {
        if (!active) return
        setError(messageOf(err))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [nonce])

  return {
    packages,
    loading,
    error,
    refresh: useCallback(() => setNonce((value) => value + 1), []),
  }
}

// ------------------------------------------------------------------ guards

export function useUnsavedGuard(
  dirty: boolean,
  options: {
    title?: string
    description?: string
    confirmLabel?: string
  } = {},
) {
  const confirm = useConfirm()
  const blocker = useBlocker(dirty)
  const prompted = useRef(false)
  const { state, proceed, reset } = blocker

  useEffect(() => {
    if (state !== 'blocked') {
      prompted.current = false
      return
    }
    if (prompted.current) return
    prompted.current = true
    void confirm({
      title: options.title || '有未保存的修改',
      description:
        options.description ||
        '离开当前页面会丢失尚未保存的内容。可以先留在本页保存，再继续离开。',
      confirmLabel: options.confirmLabel || '放弃修改并离开',
      cancelLabel: '留在本页',
      tone: 'danger',
    }).then((accepted) => {
      if (accepted) proceed()
      else reset()
    })
  }, [state, proceed, reset, confirm, options.title, options.description, options.confirmLabel])

  useEffect(() => {
    if (!dirty) return
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])
}

export function useSaveShortcut(handler: () => void, enabled = true) {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => {
    if (!enabled) return
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        ref.current()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [enabled])
}

export function useDocumentTitle(title: string | null | undefined) {
  useEffect(() => {
    document.title = title ? `${title} · BidCraft 标书工坊` : 'BidCraft 标书工坊'
    return () => {
      document.title = 'BidCraft 标书工坊'
    }
  }, [title])
}

// ------------------------------------------------------------------ audit feed

export interface AuditFeed {
  items: AuditEntry[]
  next: number | null
  loading: boolean
  loadingMore: boolean
  error: string | null
  reload: () => void
  loadMore: () => void
}

/**
 * Cursor-paged audit feed. The first page follows `key` (project ref or
 * `admin`); later pages append with `before=next_before` from the server.
 */
export function useAuditFeed(
  key: string,
  fetcher: (before: number | null) => Promise<AuditPage>,
): AuditFeed {
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const [items, setItems] = useState<AuditEntry[]>([])
  const [next, setNext] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestId = useRef(0)
  const nextRef = useRef<number | null>(null)
  const loadingMoreRef = useRef(false)

  const loadFirst = useCallback(async () => {
    const id = requestId.current + 1
    requestId.current = id
    loadingMoreRef.current = false
    nextRef.current = null
    setLoading(true)
    setLoadingMore(false)
    setNext(null)
    setError(null)
    try {
      const page = await fetcherRef.current(null)
      if (requestId.current !== id) return
      setItems(page.items)
      setNext(page.next_before)
      nextRef.current = page.next_before
      setLoading(false)
    } catch (err) {
      if (requestId.current !== id) return
      setItems([])
      setNext(null)
      nextRef.current = null
      setError(messageOf(err))
      setLoading(false)
    }
    // `key` changing must restart the feed even when the fetcher is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  useEffect(() => {
    void loadFirst()
  }, [loadFirst])

  const loadMore = useCallback(async () => {
    const before = nextRef.current
    if (before === null || loadingMoreRef.current) return
    const id = requestId.current
    loadingMoreRef.current = true
    setLoadingMore(true)
    setError(null)
    try {
      const page = await fetcherRef.current(before)
      if (requestId.current !== id) return
      setItems((current) => [...current, ...page.items])
      setNext(page.next_before)
      nextRef.current = page.next_before
    } catch (err) {
      if (requestId.current !== id) return
      setError(messageOf(err))
    } finally {
      if (requestId.current === id) {
        loadingMoreRef.current = false
        setLoadingMore(false)
      }
    }
  }, [])

  return {
    items,
    next,
    loading,
    loadingMore,
    error,
    reload: loadFirst,
    loadMore: () => void loadMore(),
  }
}

// ------------------------------------------------------------------ local UI memory

export function useLocalStorageState<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => readStorage<T>(key, initial))
  const set = useCallback(
    (next: SetStateAction<T>) => {
      setValue((current) => {
        const resolved =
          typeof next === 'function' ? (next as (previous: T) => T)(current) : next
        writeStorage(key, resolved)
        return resolved
      })
    },
    [key],
  )
  return [value, set] as const
}

export function useDebouncedValue<T>(value: T, delay = 220): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return debounced
}

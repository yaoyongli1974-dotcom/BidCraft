import type { ChapterConflict } from './types'

// ---------------------------------------------------------------- http domain
export const UNAUTHORIZED_EVENT = 'bidcraft:unauthorized'

export const AUTH_SKIP_401 = ['/auth/login', '/auth/me', '/auth/logout']

export function notifyUnauthorized(path: string): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT, { detail: { path } }))
}

export class ApiError extends Error {
  status: number
  detail: unknown
  /** Full parsed response body, when the server sent one. */
  payload: unknown
  missingRequired: string[]

  constructor(status: number, detail: unknown, message?: string, payload?: unknown) {
    super(message || errorDetailText(detail) || `请求失败（${status || '网络错误'}）`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.payload = payload ?? null
    const missing =
      detail && typeof detail === 'object' && 'missing_required' in detail
        ? (detail as { missing_required?: unknown }).missing_required
        : undefined
    this.missingRequired = Array.isArray(missing)
      ? missing.filter((item): item is string => typeof item === 'string')
      : []
  }
}

export function errorDetailText(detail: unknown): string {
  if (!detail) return ''
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg: unknown }).msg)
        }
        return JSON.stringify(item)
      })
      .join('；')
  }
  if (typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    if (Array.isArray(record.missing_required)) {
      return `还缺 ${record.missing_required.length} 项必填事实：${record.missing_required
        .slice(0, 4)
        .join('、')}${record.missing_required.length > 4 ? '…' : ''}`
    }
    if (typeof record.message === 'string') return record.message
    if (typeof record.detail === 'string') return record.detail
    try {
      return JSON.stringify(detail)
    } catch {
      return '请求失败'
    }
  }
  return String(detail)
}

export function messageOf(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return '发生未知错误，请重试'
}

/**
 * Extract the optimistic-lock payload from a chapter save 409. The server nests
 * the conflict fields next to `detail`; older proxies may only expose `detail`,
 * so both shapes are accepted.
 */

export function chapterConflict(error: unknown): ChapterConflict | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null
  const candidates: unknown[] = [error.payload, error.detail]
  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== 'object') continue
    const record = candidate as Record<string, unknown>
    if (typeof record.current_revision !== 'number') continue
    return {
      current_revision: record.current_revision,
      current_body:
        typeof record.current_body === 'string' ? record.current_body : '',
      current_words:
        typeof record.current_words === 'number' ? record.current_words : 0,
      message: typeof record.detail === 'string' ? record.detail : undefined,
    }
  }
  return null
}

export function asArray<T>(value: unknown, key: string): T[] {
  if (Array.isArray(value)) return value as T[]
  if (value && typeof value === 'object') {
    const nested = (value as Record<string, unknown>)[key]
    if (Array.isArray(nested)) return nested as T[]
  }
  return []
}

export async function readError(response: Response): Promise<ApiError> {
  let detail: unknown = response.statusText
  let payload: unknown = null
  const text = await response.text().catch(() => '')
  if (text) {
    try {
      const parsed = JSON.parse(text) as { detail?: unknown; message?: unknown }
      payload = parsed
      detail = parsed.detail ?? parsed.message ?? text
    } catch {
      detail = text
      payload = text
    }
  }
  return new ApiError(response.status, detail, undefined, payload)
}

/** Cancellation must stay distinguishable from a real network failure. */

export function isAbortError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  return (error as { name?: unknown }).name === 'AbortError'
}

export function parseJsonOrNull(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

export interface DesktopBridge {
  api_request(
    method: string,
    path: string,
    bodyB64?: string,
    contentType?: string,
  ): Promise<{
    status: number
    content_type: string
    body: string
    offline: boolean
    cached_at?: number
  }>
  pick_and_upload(
    kind?: string,
    project?: string,
  ): Promise<{
    ok: boolean
    cancelled?: boolean
    error?: string
    payload?: unknown
    path?: string
  }>
  info(): Promise<{ server: string; version: string }>
  choose_server?(): Promise<{ changed: boolean; server: string }>
}

export function desktopBridge(): DesktopBridge | null {
  if (typeof window === 'undefined') return null
  const host = window as unknown as { pywebview?: { api?: DesktopBridge } }
  const api = host.pywebview?.api
  return api && typeof api.api_request === 'function' ? api : null
}

export let bridgeReady: Promise<DesktopBridge | null> | null = null

/** pywebview 在 `pywebviewready` 后才注入 api，启动早期的桩对象不可用。 */

export function awaitDesktopBridge(timeoutMs = 5000): Promise<DesktopBridge | null> {
  const ready = desktopBridge()
  if (ready) return Promise.resolve(ready)
  if (typeof window === 'undefined') return Promise.resolve(null)
  const host = window as unknown as { pywebview?: unknown }
  if (!host.pywebview) return Promise.resolve(null)
  if (!bridgeReady) {
    bridgeReady = new Promise((resolve) => {
      const finish = (value: DesktopBridge | null) => {
        clearInterval(poll)
        clearTimeout(deadline)
        window.removeEventListener('pywebviewready', onReady)
        bridgeReady = null
        resolve(value)
      }
      const onReady = () => {
        const found = desktopBridge()
        if (found) finish(found)
      }
      const poll = setInterval(onReady, 50)
      const deadline = setTimeout(() => finish(desktopBridge()), timeoutMs)
      window.addEventListener('pywebviewready', onReady)
    })
  }
  return bridgeReady
}

export function announceOffline(offline: boolean, cachedAt?: number): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(
    new CustomEvent(offline ? 'bidcraft-offline' : 'bidcraft-online', { detail: { cachedAt } }),
  )
}

export function base64Utf8(text: string): string {
  const bytes = new TextEncoder().encode(text)
  let binary = ''
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte)
  })
  return btoa(binary)
}

export function decodeBase64(body: string): string {
  const binary = atob(body)
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
  return new TextDecoder().decode(bytes)
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  signal?: AbortSignal,
): Promise<T> {
  const requestSignal = signal ?? init?.signal ?? undefined
  if (requestSignal?.aborted) {
    throw Object.assign(new Error('aborted'), { name: 'AbortError' })
  }
  const bridge = await awaitDesktopBridge()
  if (requestSignal?.aborted) {
    throw Object.assign(new Error('aborted'), { name: 'AbortError' })
  }
  if (bridge) {
    const bodyText = typeof init?.body === 'string' ? init.body : undefined
    const contentType = String(
      (init?.headers as Record<string, string> | undefined)?.['Content-Type'] ?? '',
    )
    const result = await bridge.api_request(
      init?.method ?? 'GET',
      path,
      bodyText ? base64Utf8(bodyText) : '',
      contentType,
    )
    announceOffline(result.offline, result.cached_at)
    if (result.status === 401 && !AUTH_SKIP_401.some((item) => path.startsWith(item))) {
      notifyUnauthorized(path)
    }
    const text = decodeBase64(result.body)
    if (result.status >= 400) {
      const detail = parseJsonOrNull(text)
      throw new ApiError(result.status, detail, messageOf({ status: result.status, detail } as never))
    }
    if (result.status === 204 || !text) return undefined as T
    return JSON.parse(text) as T
  }
  let response: Response
  try {
    response = await fetch(path, {
      credentials: 'same-origin',
      ...init,
      ...(requestSignal ? { signal: requestSignal } : {}),
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init?.headers || {}),
      },
    })
  } catch (error) {
    // An aborted request is not a server failure: let `useQuery` and callers
    // recognise it and skip the error state.
    if (isAbortError(error)) throw error
    throw new ApiError(
      0,
      null,
      '无法连接本地服务。请确认 BidCraft 后端已启动，且当前页面由它托管。',
    )
  }
  if (!response.ok) {
    if (response.status === 401 && !AUTH_SKIP_401.some((item) => path.startsWith(item))) {
      notifyUnauthorized(path)
    }
    throw await readError(response)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const request = apiFetch

export const jsonBody = (data: unknown): RequestInit => ({
  method: 'POST',
  body: JSON.stringify(data),
})

export interface UploadOptions {
  onProgress?: (percent: number) => void
  /** Friendly message used when the request never reaches the server. */
  networkMessage?: string
  /** Cancels the upload; the promise rejects with a cancellation ApiError. */
  signal?: AbortSignal
}

/**
 * Shared multipart upload transport. Uploads cannot use `fetch` (no upload
 * progress), so every multipart call goes through this helper instead of
 * hand-rolling XHR: 401 still triggers the global re-auth event, 403 and other
 * failures surface as the same `ApiError` shape as JSON requests, and progress
 * reporting stays consistent.
 */

export function uploadForm<T>(
  path: string,
  form: FormData,
  options: UploadOptions = {},
): Promise<T> {
  const { onProgress, networkMessage, signal } = options
  if (signal?.aborted) {
    return Promise.reject(new ApiError(0, null, '上传已取消。'))
  }
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const cancelled = () => new ApiError(0, null, '上传已取消。')
    let settled = false
    const handleAbort = () => xhr.abort()
    const cleanup = () => {
      if (signal) signal.removeEventListener('abort', handleAbort)
    }
    const finish = (fn: () => void) => {
      if (settled) return
      settled = true
      cleanup()
      fn()
    }
    if (signal) signal.addEventListener('abort', handleAbort)
    xhr.open('POST', path)
    xhr.responseType = 'text'
    xhr.withCredentials = true
    if (onProgress) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress(Math.round((event.loaded / event.total) * 100))
        }
      }
    }
    xhr.onload = () => {
      const text = xhr.responseText || ''
      const body: unknown = (() => {
        try {
          return text ? JSON.parse(text) : null
        } catch {
          return text
        }
      })()
      if (xhr.status >= 200 && xhr.status < 300) {
        finish(() => resolve((body || {}) as T))
        return
      }
      if (xhr.status === 401) notifyUnauthorized(path)
      const fallback =
        xhr.status === 403 ? '当前账号没有执行此操作的权限。' : xhr.statusText
      const detail =
        body && typeof body === 'object' && 'detail' in body
          ? (body as { detail: unknown }).detail
          : text || fallback
      finish(() => reject(new ApiError(xhr.status, detail)))
    }
    xhr.onerror = () =>
      finish(() =>
        reject(
          new ApiError(
            0,
            null,
            networkMessage || '上传失败：无法连接本地服务，请确认后端已启动。',
          ),
        ),
      )
    xhr.onabort = () => finish(() => reject(cancelled()))
    xhr.send(form)
  })
}

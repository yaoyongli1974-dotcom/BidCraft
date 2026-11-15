import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  DEFAULT_IMAGE_POLICY,
  DEFAULT_PRICE_POLICY,
  UNAUTHORIZED_EVENT,
  apiFetch,
  errorDetailText,
  messageOf,
  normalizeImagePolicy,
  normalizePricePolicy,
  reindexKnowledgeBase,
  roundTo,
  uploadForm,
} from './api'

class FakeXhr {
  static instances: FakeXhr[] = []
  upload: { onprogress?: (event: ProgressEvent) => void } = {}
  status = 0
  statusText = ''
  responseText = ''
  responseType = ''
  withCredentials = false
  aborted = false
  method = ''
  url = ''
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null

  constructor() {
    FakeXhr.instances.push(this)
  }

  open(method: string, url: string) {
    this.method = method
    this.url = url
  }

  send() {}

  abort() {
    this.aborted = true
    this.onabort?.()
  }

  progress(loaded: number, total: number) {
    this.upload.onprogress?.({
      lengthComputable: true,
      loaded,
      total,
    } as ProgressEvent)
  }

  respond(status: number, text: string) {
    this.status = status
    this.responseText = text
    this.onload?.()
  }
}

function form() {
  const data = new FormData()
  data.append('file', new Blob(['hello']), 'a.txt')
  return data
}


describe('normalizePricePolicy', () => {
  it('uses the defaults for an empty payload', () => {
    expect(normalizePricePolicy(undefined)).toEqual(DEFAULT_PRICE_POLICY)
    expect(normalizePricePolicy({})).toEqual(DEFAULT_PRICE_POLICY)
  })

  it('reads the nested price_policy wrapper used by the API', () => {
    expect(
      normalizePricePolicy({ price_policy: { enabled: 'true', factor: 1.15, round_digits: 1 } }),
    ).toEqual({ enabled: true, factor: 1.15, round_digits: 1 })
  })

  it('clamps the factor and rounding digits into the contract range', () => {
    expect(normalizePricePolicy({ factor: 99 }).factor).toBe(10)
    expect(normalizePricePolicy({ factor: 0 }).factor).toBe(0.1)
    expect(normalizePricePolicy({ round_digits: 12 }).round_digits).toBe(4)
    expect(normalizePricePolicy({ round_digits: -3 }).round_digits).toBe(0)
  })

  it('treats explicit falsey strings as disabled', () => {
    expect(normalizePricePolicy({ enabled: 'off' }).enabled).toBe(false)
    expect(normalizePricePolicy({ enabled: 0 }).enabled).toBe(false)
  })
})

describe('normalizeImagePolicy', () => {
  it('fills missing fields from the default policy', () => {
    expect(normalizeImagePolicy(null)).toEqual(DEFAULT_IMAGE_POLICY)
  })

  it('clamps total, width and caption text', () => {
    const policy = normalizeImagePolicy({
      max_total: 999,
      width_cm: 2,
      caption_prefix: '这是一个过长的图片前缀',
      size: 'not-a-size',
    })
    expect(policy.max_total).toBe(60)
    expect(policy.width_cm).toBe(8)
    expect(policy.caption_prefix).toBe('这是一个过长的图片前')
    expect(policy.size).toBe('auto')
    expect(policy.captions).toBe(true)
  })

  it('accepts explicit pixel sizes and loose booleans', () => {
    const policy = normalizeImagePolicy({ size: '1200x800', captions: 'false' })
    expect(policy.size).toBe('1200x800')
    expect(policy.captions).toBe(false)
  })
})

describe('roundTo', () => {
  it('rounds with a bounded number of decimals', () => {
    expect(roundTo(1.005, 2)).toBe(1.01)
    expect(roundTo(12.3456, 9)).toBe(12.3456)
    expect(roundTo(12.6, -2)).toBe(13)
  })
})

describe('error helpers', () => {
  it('renders a readable summary for missing required facts', () => {
    expect(errorDetailText({ missing_required: ['a', 'b', 'c', 'd', 'e'] })).toBe(
      '还缺 5 项必填事实：a、b、c、d…',
    )
  })

  it('joins validation arrays and falls back to JSON', () => {
    expect(errorDetailText([{ msg: '名称不能为空' }, '规则包重复'])).toBe(
      '名称不能为空；规则包重复',
    )
    expect(errorDetailText({ foo: 'bar' })).toBe('{"foo":"bar"}')
    expect(messageOf(new Error('网络异常'))).toBe('网络异常')
    expect(messageOf(null)).toBe('发生未知错误，请重试')
  })
})

describe('uploadForm', () => {
  beforeEach(() => {
    FakeXhr.instances = []
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reports upload progress percentages', async () => {
    const seen: number[] = []
    const pending = uploadForm('/projects/a/sources', form(), {
      onProgress: (percent) => seen.push(percent),
    })
    const xhr = FakeXhr.instances[0]
    xhr.progress(3, 4)
    xhr.respond(200, '{"ok":true}')
    await expect(pending).resolves.toEqual({ ok: true })
    expect(seen).toEqual([75])
  })

  it('fires the global unauthorized event on 401 and rejects with the detail', async () => {
    const onUnauthorized = vi.fn()
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    const pending = uploadForm('/projects/a/sources', form())
    const xhr = FakeXhr.instances[0]
    xhr.respond(401, '{"detail":"登录已失效"}')

    await expect(pending).rejects.toMatchObject({
      name: 'ApiError',
      status: 401,
      message: '登录已失效',
    })
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
    expect((onUnauthorized.mock.calls[0][0] as CustomEvent).detail).toEqual({
      path: '/projects/a/sources',
    })
    window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  })

  it('aborts the XHR when the signal is aborted', async () => {
    const controller = new AbortController()
    const pending = uploadForm('/projects/a/sources', form(), {
      signal: controller.signal,
    })
    const xhr = FakeXhr.instances[0]
    controller.abort()

    expect(xhr.aborted).toBe(true)
    await expect(pending).rejects.toBeInstanceOf(ApiError)
    await expect(pending).rejects.toMatchObject({ message: '上传已取消。' })
  })

  it('rejects immediately when the signal is already aborted', async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(
      uploadForm('/projects/a/sources', form(), { signal: controller.signal }),
    ).rejects.toMatchObject({ message: '上传已取消。' })
    expect(FakeXhr.instances).toHaveLength(0)
  })
})

describe('request cancellation', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('rethrows an abort instead of masking it as a network failure', async () => {
    const controller = new AbortController()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_path: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () => {
              reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
            })
          }),
      ),
    )
    const pending = apiFetch('/slow', undefined, controller.signal)
    controller.abort()
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('returns a job id for the async reindex endpoint', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ job_id: 'job-9' }), {
          status: 202,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    await expect(reindexKnowledgeBase('kb-1')).resolves.toEqual({
      job_id: 'job-9',
      legacy: null,
    })
  })

  it('falls back to the legacy synchronous reindex payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            indexed: 4,
            vector: { embedded: 2, total: 4, model: 'bge', dim: 8 },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )
    const result = await reindexKnowledgeBase('kb-1')
    expect(result.job_id).toBeNull()
    expect(result.legacy?.indexed).toBe(4)
    expect(result.legacy?.vector?.embedded).toBe(2)
    expect(result.legacy?.vector?.model).toBe('bge')
  })
})

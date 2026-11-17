import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  DEFAULT_IMAGE_POLICY,
  DEFAULT_PRICE_POLICY,
  UNAUTHORIZED_EVENT,
  apiFetch,
  cnyTotalWithTax,
  createBoqVersion,
  createTerm,
  deleteBoqVersion,
  errorDetailText,
  getBoqVersion,
  getTerms,
  listBoqVersions,
  messageOf,
  normalizeBoqQuoteVersion,
  normalizeBoqSummary,
  normalizeImagePolicy,
  normalizePricePolicy,
  normalizeTerm,
  reindexKnowledgeBase,
  restoreBoqVersion,
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
    ).toEqual({
      enabled: true,
      factor: 1.15,
      round_digits: 1,
      tax_rate: 0,
      currency: 'CNY',
      exchange_rate: 1,
    })
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

  it('clamps the tax rate and normalises the currency enum', () => {
    expect(normalizePricePolicy({ tax_rate: 0.09 }).tax_rate).toBe(0.09)
    expect(normalizePricePolicy({ tax_rate: 0.6 }).tax_rate).toBe(0.5)
    expect(normalizePricePolicy({ tax_rate: -1 }).tax_rate).toBe(0)
    expect(normalizePricePolicy({ tax_rate: '0.13' }).tax_rate).toBe(0.13)
    expect(normalizePricePolicy({ tax_rate: 'not-a-number' }).tax_rate).toBe(0)
    expect(normalizePricePolicy({ currency: 'usd' }).currency).toBe('USD')
    expect(normalizePricePolicy({ currency: 'GBP' }).currency).toBe('CNY')
    expect(normalizePricePolicy({ currency: null }).currency).toBe('CNY')
  })

  it('clamps the exchange rate and defaults to 1', () => {
    expect(normalizePricePolicy({ currency: 'USD' }).exchange_rate).toBe(1)
    expect(normalizePricePolicy({ currency: 'USD', exchange_rate: 7.2361 }).exchange_rate).toBe(
      7.2361,
    )
    expect(normalizePricePolicy({ currency: 'USD', exchange_rate: 0 }).exchange_rate).toBe(
      0.0001,
    )
    expect(normalizePricePolicy({ currency: 'USD', exchange_rate: -2 }).exchange_rate).toBe(
      0.0001,
    )
    expect(normalizePricePolicy({ currency: 'USD', exchange_rate: 99999 }).exchange_rate).toBe(
      10000,
    )
    expect(
      normalizePricePolicy({ currency: 'USD', exchange_rate: 'not-a-number' }).exchange_rate,
    ).toBe(1)
    expect(normalizePricePolicy({ currency: 'USD', exchange_rate: '7.12' }).exchange_rate).toBe(
      7.12,
    )
  })

  it('forces the exchange rate to 1 for CNY', () => {
    expect(normalizePricePolicy({ currency: 'CNY', exchange_rate: 7.2 }).exchange_rate).toBe(1)
    expect(normalizePricePolicy({ exchange_rate: 7.2 }).exchange_rate).toBe(1)
  })
})

describe('normalizeBoqSummary', () => {
  it('keeps the tax fields from the backend summary', () => {
    const summary = normalizeBoqSummary({
      total_amount: 1000,
      tax_amount: 90,
      total_with_tax: 1090,
      cny_total_with_tax: 7850.5,
    })
    expect(summary.total_amount).toBe(1000)
    expect(summary.tax_amount).toBe(90)
    expect(summary.total_with_tax).toBe(1090)
    expect(summary.cny_total_with_tax).toBe(7850.5)
  })

  it('returns null tax fields when the backend omits them', () => {
    const summary = normalizeBoqSummary({ total_amount: 1000 })
    expect(summary.tax_amount).toBeNull()
    expect(summary.total_with_tax).toBeNull()
    expect(summary.cny_total_with_tax).toBeNull()
  })

  it('treats non-numeric tax fields as missing', () => {
    const summary = normalizeBoqSummary({
      tax_amount: '90',
      total_with_tax: Number.NaN,
      cny_total_with_tax: '7850',
    })
    expect(summary.tax_amount).toBeNull()
    expect(summary.total_with_tax).toBeNull()
    expect(summary.cny_total_with_tax).toBeNull()
  })
})

describe('cnyTotalWithTax', () => {
  it('prefers the backend cny total when present', () => {
    expect(cnyTotalWithTax(7236.1, 1000, 7.2361)).toBe(7236.1)
  })

  it('falls back to total with tax times the exchange rate for older backends', () => {
    expect(cnyTotalWithTax(null, 1090, 7.2361)).toBe(7887.35)
    expect(cnyTotalWithTax(undefined, 100, 1)).toBe(100)
  })

  it('rounds the fallback to two decimals', () => {
    expect(cnyTotalWithTax(null, 100.005, 1)).toBe(100.01)
  })

  it('returns null without a total and defaults an invalid rate to 1', () => {
    expect(cnyTotalWithTax(null, null, 7.2)).toBeNull()
    expect(cnyTotalWithTax(undefined, 250, Number.NaN)).toBe(250)
    expect(cnyTotalWithTax(undefined, 250, 0)).toBe(0.03)
  })
})

describe('boq quote versions', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })

  it('normalizes a version payload and filters invalid prices', () => {
    const version = normalizeBoqQuoteVersion({
      id: 'v-1',
      name: '版本 1',
      note: '含税报价',
      package_id: 'pkg-1',
      summary: { total_amount: 100, tax_amount: 9, total_with_tax: 109 },
      created_at: '2026-11-16T08:30:00+08:00',
      price_count: 3,
      prices: { 'sheet!2': 12.5, 'sheet!3': 'oops', 'sheet!4': null },
    })
    expect(version).toMatchObject({
      id: 'v-1',
      name: '版本 1',
      note: '含税报价',
      package_id: 'pkg-1',
      created_at: '2026-11-16T08:30:00+08:00',
      price_count: 3,
    })
    expect(version?.summary.total_with_tax).toBe(109)
    expect(version?.prices).toEqual({ 'sheet!2': 12.5 })
    expect(normalizeBoqQuoteVersion({ name: '缺少 id' })).toBeNull()
    expect(normalizeBoqQuoteVersion(null)).toBeNull()
  })

  it('lists versions for the current package and fills missing fields', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse([
        {
          id: 'v-2',
          name: '版本 2',
          summary: { total_amount: 88 },
          price_count: 2,
        },
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)
    const versions = await listBoqVersions('p/1', 'pkg-1')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/projects/p%2F1/boq/versions?package_id=pkg-1',
    )
    expect(versions).toHaveLength(1)
    expect(versions[0]).toMatchObject({
      id: 'v-2',
      name: '版本 2',
      note: '',
      package_id: null,
      created_at: null,
      price_count: 2,
    })
    expect(versions[0].summary.total_with_tax).toBeNull()
  })

  it('posts a new version with the package id', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ id: 'v-3', name: '版本 3', note: '下浮', price_count: 0 }, 201),
    )
    vi.stubGlobal('fetch', fetchMock)
    const created = await createBoqVersion('proj-1', {
      name: ' 版本 3 ',
      note: '下浮',
      package_id: 'pkg-9',
    })
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/proj-1/boq/versions')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({
      name: '版本 3',
      note: '下浮',
      package_id: 'pkg-9',
    })
    expect(created.id).toBe('v-3')
    expect(created.created_at).toBeNull()
  })

  it('reads version detail and restores it into a normalized BOQ payload', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ id: 'v-4', name: '版本 4', prices: { 'sheet!2': 9 } }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          snapshot: { id: 's1', filename: 'a.xlsx', sheet: 'Sheet1', header_row: 2, item_count: 1 },
          rows: [{ key: 'sheet!2', name: '管材', price: { unit_price: 9 } }],
          summary: { total_amount: 90, tax_amount: 8.1, total_with_tax: 98.1 },
        }),
      )
    vi.stubGlobal('fetch', fetchMock)

    const detail = await getBoqVersion('proj-1', 'v/4')
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/proj-1/boq/versions/v%2F4')
    expect(detail.prices).toEqual({ 'sheet!2': 9 })

    const payload = await restoreBoqVersion('proj-1', 'v/4')
    const init = fetchMock.mock.calls[1][1] as RequestInit
    expect(fetchMock.mock.calls[1][0]).toBe(
      '/projects/proj-1/boq/versions/v%2F4/restore',
    )
    expect(init.method).toBe('POST')
    expect(payload.rows[0].price?.unit_price).toBe(9)
    expect(payload.summary.total_with_tax).toBe(98.1)
  })

  it('normalizes the delete response', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ deleted: true, id: 'v-5' }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await expect(deleteBoqVersion('proj-1', 'v-5')).resolves.toEqual({
      deleted: true,
      id: 'v-5',
    })
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('DELETE')
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

describe('enterprise terms', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })

  it('normalizes kind, enabled and column widths', () => {
    const term = normalizeTerm({
      id: 't-1',
      kind: 'unknown',
      source: '甲'.repeat(130),
      target: '乙'.repeat(130),
      note: '备'.repeat(210),
    })
    expect(term.kind).toBe('replace')
    expect(term.enabled).toBe(true)
    expect(term.source).toHaveLength(120)
    expect(term.target).toHaveLength(120)
    expect(term.note).toHaveLength(200)

    const forbidden = normalizeTerm({
      id: 't-2',
      kind: 'forbidden',
      source: '绝对领先',
      enabled: 'false',
    })
    expect(forbidden.kind).toBe('forbidden')
    expect(forbidden.enabled).toBe(false)
    expect(forbidden.target).toBe('')
    expect(forbidden.created_at).toBeNull()
  })

  it('builds the query string for the flat list endpoint', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse([
        { id: 't-1', kind: 'forbidden', source: '绝对领先', enabled: true },
        { id: 't-2', kind: 'replace', source: '甲方', target: '招标人' },
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const terms = await getTerms({
      search: ' 甲方 ',
      kind: 'forbidden',
      limit: 50,
      offset: 100,
    })

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/admin/terms?search=%E7%94%B2%E6%96%B9&kind=forbidden&limit=50&offset=100',
    )
    expect(terms).toHaveLength(2)
    expect(terms[1]).toMatchObject({
      id: 't-2',
      kind: 'replace',
      source: '甲方',
      target: '招标人',
      enabled: true,
    })
  })

  it('posts a normalized create payload', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse(
        { id: 't-3', kind: 'replace', source: '甲方', target: '招标人', enabled: false },
        201,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const created = await createTerm({
      kind: 'replace',
      source: ' 甲方 ',
      target: ' 招标人 ',
      note: ' 统一称呼 ',
      enabled: false,
    })

    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(fetchMock.mock.calls[0][0]).toBe('/admin/terms')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({
      kind: 'replace',
      source: '甲方',
      target: '招标人',
      note: '统一称呼',
      enabled: false,
    })
    expect(created.id).toBe('t-3')
    expect(created.enabled).toBe(false)
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

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  DEFAULT_IMAGE_POLICY,
  DEFAULT_PRICE_POLICY,
  PLUGIN_CPU_SECONDS,
  PLUGIN_MEM_LIMIT,
  UNAUTHORIZED_EVENT,
  apiFetch,
  applicableStages,
  cnyTotalWithTax,
  createBoqVersion,
  createPlugin,
  createTerm,
  deleteBoqVersion,
  duplicateProject,
  errorDetailText,
  exportPluginPackage,
  getBoqVersion,
  getPendingApprovals,
  getProject,
  getTerms,
  importPluginPackage,
  listBoqVersions,
  messageOf,
  normalizeApprovalChain,
  normalizeBoqQuoteVersion,
  normalizeBoqSummary,
  normalizeImagePolicy,
  normalizePlugin,
  normalizePricePolicy,
  normalizeTerm,
  reindexKnowledgeBase,
  restoreBoqVersion,
  reviewBoqVersion,
  roundTo,
  submitBoqVersion,
  unapproveBoqVersion,
  updatePlugin,
  updateProject,
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

  it('defaults an unknown status to draft and clamps audit text', () => {
    const version = normalizeBoqQuoteVersion({
      id: 'v-6',
      status: 'bogus',
      submit_note: 'x'.repeat(240),
      submitted_by: 'y'.repeat(100),
      submitted_at: '2026-11-18T09:00:00+08:00',
      review_note: 'z'.repeat(240),
      reviewed_by: 'w'.repeat(100),
      reviewed_at: 42,
    })
    expect(version?.status).toBe('draft')
    expect(version?.submit_note).toHaveLength(200)
    expect(version?.submitted_by).toHaveLength(80)
    expect(version?.review_note).toHaveLength(200)
    expect(version?.reviewed_by).toHaveLength(80)
    expect(version?.submitted_at).toBe('2026-11-18T09:00:00+08:00')
    expect(version?.reviewed_at).toBeNull()
    expect(normalizeBoqQuoteVersion({ id: 'v-7', status: 'approved' })?.status).toBe(
      'approved',
    )
  })

  it('posts submit, review and unapprove actions with the documented bodies', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ id: 'v-8', name: '版本 8', status: 'submitted' }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const submitted = await submitBoqVersion('proj-1', 'v/8', ' 请审批 ')
    const submitInit = fetchMock.mock.calls[0][1] as RequestInit
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/projects/proj-1/boq/versions/v%2F8/submit',
    )
    expect(submitInit.method).toBe('POST')
    expect(JSON.parse(String(submitInit.body))).toEqual({ note: '请审批' })
    expect(submitted.status).toBe('submitted')

    const reviewed = await reviewBoqVersion('proj-1', 'v/8', 'approve', ' 同意 ')
    const reviewInit = fetchMock.mock.calls[1][1] as RequestInit
    expect(fetchMock.mock.calls[1][0]).toBe(
      '/projects/proj-1/boq/versions/v%2F8/review',
    )
    expect(reviewInit.method).toBe('POST')
    expect(JSON.parse(String(reviewInit.body))).toEqual({
      decision: 'approve',
      note: '同意',
    })
    expect(reviewed.id).toBe('v-8')

    await unapproveBoqVersion('proj-1', 'v/8')
    const unapproveInit = fetchMock.mock.calls[2][1] as RequestInit
    expect(fetchMock.mock.calls[2][0]).toBe(
      '/projects/proj-1/boq/versions/v%2F8/unapprove',
    )
    expect(unapproveInit.method).toBe('POST')
    expect(unapproveInit.body).toBeUndefined()
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

  it('defaults approvals and approval_stage for older backends', () => {
    const version = normalizeBoqQuoteVersion({ id: 'v-9', status: 'submitted' })
    expect(version?.approvals).toEqual([])
    expect(version?.approval_stage).toBe(0)
    expect(normalizeBoqQuoteVersion({ id: 'v-9', approval_stage: -3 })?.approval_stage).toBe(0)
  })

  it('normalizes stage approval records and filters invalid decisions', () => {
    const version = normalizeBoqQuoteVersion({
      id: 'v-10',
      status: 'submitted',
      approval_stage: 1,
      approvals: [
        {
          stage: 0,
          user_id: 'u-1',
          name: '甲',
          decision: 'approve',
          note: '同意',
          at: '2026-11-18T09:00:00+08:00',
        },
        { stage: 1, user_id: 'u-2', name: '乙', decision: 'reject', note: '', at: null },
        { stage: 2, user_id: 'u-3', decision: 'bogus' },
        null,
      ],
    })
    expect(version?.approval_stage).toBe(1)
    expect(version?.approvals).toEqual([
      {
        stage: 0,
        user_id: 'u-1',
        name: '甲',
        decision: 'approve',
        note: '同意',
        at: '2026-11-18T09:00:00+08:00',
      },
      { stage: 1, user_id: 'u-2', name: '乙', decision: 'reject', note: '', at: null },
    ])
  })

  it('patches the approval chain as v2 stages with per-stage modes', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({
        id: 'p-1',
        name: '项目',
        approval_chain: [
          {
            approvers: [
              { id: 'u-1', name: '甲' },
              { id: 'u-2', name: '乙' },
            ],
            mode: 'all',
          },
          { approvers: [{ id: 'u-3', name: '丙' }], mode: 'any' },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const updated = await updateProject('p/1', {
      approval_chain: [
        { approvers: ['u-1', 'u-2'], mode: 'all' },
        { approvers: ['u-3'], mode: 'any' },
      ],
    })
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/p%2F1')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(String(init.body))).toEqual({
      approval_chain: [
        { approvers: ['u-1', 'u-2'], mode: 'all' },
        { approvers: ['u-3'], mode: 'any' },
      ],
    })
    expect(updated.approval_chain).toEqual([
      {
        approvers: [
          { id: 'u-1', name: '甲' },
          { id: 'u-2', name: '乙' },
        ],
        mode: 'all',
      },
      { approvers: [{ id: 'u-3', name: '丙' }], mode: 'any' },
    ])

    // 空列表 = 清空审批链，回到负责人单级审批；body 必须原样透传。
    await updateProject('p/1', { approval_chain: [] })
    const clearInit = fetchMock.mock.calls[1][1] as RequestInit
    expect(JSON.parse(String(clearInit.body))).toEqual({ approval_chain: [] })
  })

  it('normalizes a legacy flat PATCH echo into v2 stages', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({
        id: 'p-1',
        name: '项目',
        approval_chain: [{ id: 'u-1', name: '甲' }],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const updated = await updateProject('p/1', {
      approval_chain: [{ approvers: ['u-1'], mode: 'all' }],
    })
    expect(updated.approval_chain).toEqual([
      { approvers: [{ id: 'u-1', name: '甲' }], mode: 'all' },
    ])
  })

  it('normalizes the legacy flat project chain into single-approver all stages', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({
        id: 'p-2',
        name: '项目',
        approval_chain: [{ id: 'u-1', name: '甲' }, { name: '无 id' }, null, { id: 'u-2' }],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const project = await getProject('p-2')
    expect(project.approval_chain).toEqual([
      { approvers: [{ id: 'u-1', name: '甲' }], mode: 'all' },
      { approvers: [{ id: 'u-2', name: '' }], mode: 'all' },
    ])
  })

  it('normalizes v2 stages with mode whitelist fallback and approver filtering', () => {
    expect(
      normalizeApprovalChain([
        {
          approvers: [
            { id: 'u-1', name: '甲' },
            { name: '无 id' },
            null,
            { id: 'u-1', name: '重复' },
            { id: 'u-2' },
          ],
          mode: 'any',
        },
        { approvers: [{ id: 'u-3', name: '丙' }] },
        { approvers: [{ id: 'u-4', name: '丁' }], mode: 'bogus' },
      ]),
    ).toEqual([
      {
        approvers: [
          { id: 'u-1', name: '甲' },
          { id: 'u-2', name: '' },
        ],
        mode: 'any',
      },
      { approvers: [{ id: 'u-3', name: '丙' }], mode: 'all' },
      { approvers: [{ id: 'u-4', name: '丁' }], mode: 'all' },
    ])
  })

  it('drops stages without valid approvers and non-array chain input', () => {
    expect(
      normalizeApprovalChain([
        { approvers: [], mode: 'any' },
        { approvers: [{ name: '无 id' }], mode: 'all' },
        { approvers: 'not-a-list' },
        null,
        'u-1',
        { approvers: [{ id: 'u-1', name: '甲' }], mode: 'any' },
      ]),
    ).toEqual([{ approvers: [{ id: 'u-1', name: '甲' }], mode: 'any' }])
    expect(normalizeApprovalChain(null)).toEqual([])
    expect(normalizeApprovalChain(undefined)).toEqual([])
    expect(normalizeApprovalChain('u-1')).toEqual([])
    expect(normalizeApprovalChain([])).toEqual([])
  })
})

describe('project duplicate (R22)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })

  it('posts to the duplicate endpoint with both include flags defaulting to true', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse(
        {
          id: 'p-new',
          name: '项目 副本',
          package: 'pkg-1',
          status: 'draft',
          my_role: 'owner',
        },
        201,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const created = await duplicateProject('p/1')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/p%2F1/duplicate')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    // name 缺省不发送，由后端默认「源名 副本」。
    expect(JSON.parse(String(init.body))).toEqual({
      include_boq: true,
      include_images: true,
    })
    expect(created).toMatchObject({ id: 'p-new', name: '项目 副本', my_role: 'owner' })
  })

  it('sends a custom name and explicit false for both include flags', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ id: 'p-new', name: '复用模板', package: 'pkg-1', status: 'draft' }, 201),
    )
    vi.stubGlobal('fetch', fetchMock)

    await duplicateProject('proj-1', {
      name: '复用模板',
      includeBoq: false,
      includeImages: false,
    })
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/proj-1/duplicate')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({
      name: '复用模板',
      include_boq: false,
      include_images: false,
    })
  })

  it('keeps the other flag true when only one include flag is disabled', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ id: 'p-new', name: '项目 副本', package: 'pkg-1', status: 'draft' }, 201),
    )
    vi.stubGlobal('fetch', fetchMock)

    await duplicateProject('proj-1', { includeImages: false })
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(init.body))).toEqual({
      include_boq: true,
      include_images: false,
    })
  })

  it('surfaces 400/403 Chinese detail through ApiError', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: '项目名称不能为空' }, 400))
      .mockResolvedValueOnce(jsonResponse({ detail: '只读成员不能复制项目' }, 403))
    vi.stubGlobal('fetch', fetchMock)

    await expect(duplicateProject('proj-1', { name: '  ' })).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      message: '项目名称不能为空',
    })
    await expect(duplicateProject('proj-1')).rejects.toMatchObject({
      name: 'ApiError',
      status: 403,
      message: '只读成员不能复制项目',
    })
  })
})

describe('R21 conditional approval stages (min_amount)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })

  const stage = (id: string, extra: Record<string, unknown> = {}) => ({
    approvers: [{ id, name: id }],
    mode: 'all',
    ...extra,
  })

  it('keeps valid min_amount in the chain echo and drops invalid values', () => {
    const stages = normalizeApprovalChain([
      stage('u-1', { min_amount: 0 }),
      stage('u-2', { min_amount: 50000.5 }),
      stage('u-3', { min_amount: -1 }),
      stage('u-4', { min_amount: '100' }),
      stage('u-5', { min_amount: true }),
      stage('u-6', { min_amount: Number.NaN }),
      stage('u-7', { min_amount: null }),
      stage('u-8'),
    ])
    expect(stages).toHaveLength(8)
    expect(stages[0].min_amount).toBe(0)
    expect(stages[1].min_amount).toBe(50000.5)
    for (const index of [2, 3, 4, 5, 6, 7]) {
      expect('min_amount' in stages[index]).toBe(false)
    }
  })

  it('normalizes approval_stage_count with a safe default for older backends', () => {
    expect(
      normalizeBoqQuoteVersion({ id: 'v-1', approval_stage_count: 2 })?.approval_stage_count,
    ).toBe(2)
    expect(normalizeBoqQuoteVersion({ id: 'v-1' })?.approval_stage_count).toBe(1)
    expect(
      normalizeBoqQuoteVersion({ id: 'v-1', approval_stage_count: 0 })?.approval_stage_count,
    ).toBe(1)
    expect(
      normalizeBoqQuoteVersion({ id: 'v-1', approval_stage_count: -3 })?.approval_stage_count,
    ).toBe(1)
    expect(
      normalizeBoqQuoteVersion({ id: 'v-1', approval_stage_count: 'bogus' })?.approval_stage_count,
    ).toBe(1)
    expect(
      normalizeBoqQuoteVersion({ id: 'v-1', approval_stage_count: 2.6 })?.approval_stage_count,
    ).toBe(3)
  })

  it('patches the approval chain with per-stage min_amount and omits it when unset', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({
        id: 'p-1',
        name: '项目',
        approval_chain: [
          { approvers: [{ id: 'u-1', name: '甲' }], mode: 'all', min_amount: 100000 },
          { approvers: [{ id: 'u-2', name: '乙' }], mode: 'any' },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const updated = await updateProject('p/1', {
      approval_chain: [
        { approvers: ['u-1'], mode: 'all', min_amount: 100000 },
        { approvers: ['u-2'], mode: 'any' },
      ],
    })
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/p%2F1')
    expect(init.method).toBe('PATCH')
    const body = JSON.parse(String(init.body)) as {
      approval_chain: Array<Record<string, unknown>>
    }
    expect(body.approval_chain[0]).toEqual({
      approvers: ['u-1'],
      mode: 'all',
      min_amount: 100000,
    })
    expect(body.approval_chain[1]).toEqual({ approvers: ['u-2'], mode: 'any' })
    expect('min_amount' in body.approval_chain[1]).toBe(false)
    expect(updated.approval_chain?.[0].min_amount).toBe(100000)
    expect(updated.approval_chain && 'min_amount' in updated.approval_chain[1]).toBe(false)
  })

  it('mirrors applicable_stages: thresholds filter by amount, unknown amount keeps all', () => {
    const chain = normalizeApprovalChain([
      stage('u-1'),
      stage('u-2', { min_amount: 100000 }),
      stage('u-3', { min_amount: 0 }),
    ])
    // 低金额跳过门槛级，保留原链索引。
    expect(applicableStages(chain, 99999.99).map((item) => item.index)).toEqual([0, 2])
    // 恰好等于门槛视为生效。
    expect(applicableStages(chain, 100000).map((item) => item.index)).toEqual([0, 1, 2])
    expect(applicableStages(chain, 200000).map((item) => item.index)).toEqual([0, 1, 2])
    // 金额未知 → 保守全部生效。
    expect(applicableStages(chain, null).map((item) => item.index)).toEqual([0, 1, 2])
    expect(applicableStages(chain, undefined).map((item) => item.index)).toEqual([0, 1, 2])
    expect(applicableStages(chain, Number.NaN).map((item) => item.index)).toEqual([0, 1, 2])
    // 0 元仍激活 min_amount=0 的级。
    expect(applicableStages(chain, 0).map((item) => item.index)).toEqual([0, 2])
    expect(applicableStages([], 100)).toEqual([])
  })

  it('returns original indices with their stage objects in the applicable list', () => {
    const chain = normalizeApprovalChain([
      stage('u-1', { min_amount: 500 }),
      stage('u-2', { mode: 'any' }),
    ])
    expect(applicableStages(chain, 100)).toEqual([{ index: 1, stage: chain[1] }])
    expect(applicableStages(chain, 500)).toEqual([
      { index: 0, stage: chain[0] },
      { index: 1, stage: chain[1] },
    ])
  })
})

describe('pending approvals', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })

  it('normalizes the flat list with truncation, number fallback and null-safe time', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse([
        {
          project_id: 'p-1',
          project_name: '项目甲',
          version_id: 'v-1',
          version_name: '版本 1',
          package_id: 'pkg-1',
          stage: 1,
          stage_count: 2,
          mode: 'all',
          submitted_by: '张三',
          submitted_at: '2026-11-18T09:00:00+08:00',
          total_amount: 1234.5,
        },
        {
          project_id: 'p-2',
          project_name: '名'.repeat(130),
          version_id: 'v-2',
          version_name: '版'.repeat(130),
          stage: '2',
          mode: 'bogus',
          submitted_by: '李'.repeat(100),
          submitted_at: null,
          total_amount: '88.5',
        },
        { project_id: 'p-3', version_id: 'v-3', stage: 0, stage_count: 9, mode: 'any', total_amount: null },
        { version_id: 'v-x', project_name: '缺项目 id' },
        { project_id: 'p-y', project_name: '缺版本 id' },
        null,
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const items = await getPendingApprovals()
    expect(fetchMock.mock.calls[0][0]).toBe('/approvals/pending')
    expect(items).toHaveLength(3)
    expect(items[0]).toEqual({
      project_id: 'p-1',
      project_name: '项目甲',
      version_id: 'v-1',
      version_name: '版本 1',
      package_id: 'pkg-1',
      stage: 1,
      stage_count: 2,
      mode: 'all',
      submitted_by: '张三',
      submitted_at: '2026-11-18T09:00:00+08:00',
      total_amount: 1234.5,
    })
    expect(items[1].project_name).toHaveLength(120)
    expect(items[1].version_name).toHaveLength(120)
    expect(items[1].submitted_by).toHaveLength(80)
    expect(items[1].stage).toBe(2)
    expect(items[1].stage_count).toBe(2)
    expect(items[1].mode).toBe('')
    expect(items[1].submitted_at).toBeNull()
    expect(items[1].total_amount).toBe(88.5)
    expect(items[1].package_id).toBeNull()
    expect(items[2].stage).toBe(1)
    expect(items[2].mode).toBe('any')
    expect(items[2].total_amount).toBeNull()
    expect(items[2].project_name).toBe('未命名项目')
    expect(items[2].version_name).toBe('未命名版本')
  })

  it('returns an empty list when the backend has no such endpoint (404)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: 'Not Found' }, 404)),
    )
    await expect(getPendingApprovals()).resolves.toEqual([])
  })

  it('degrades to an empty list when the SPA fallback returns HTML', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response('<!doctype html><html></html>', {
          status: 200,
          headers: { 'Content-Type': 'text/html' },
        }),
      ),
    )
    await expect(getPendingApprovals()).resolves.toEqual([])
  })

  it('propagates non-404 failures', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: '服务器错误' }, 500)),
    )
    await expect(getPendingApprovals()).rejects.toMatchObject({
      status: 500,
      message: '服务器错误',
    })
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

describe('plugin resource limits', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })

  it('defaults missing limits and clamps out-of-range values', () => {
    const fallback = normalizePlugin({ id: 'p-0', name: 'filesystem', transport: 'stdio' })
    expect(fallback.mem_limit_mb).toBe(PLUGIN_MEM_LIMIT.default)
    expect(fallback.cpu_seconds).toBe(PLUGIN_CPU_SECONDS.default)

    const parsed = normalizePlugin({
      id: 'p-1',
      name: 'filesystem',
      mem_limit_mb: '1024',
      cpu_seconds: '30',
    })
    expect(parsed.mem_limit_mb).toBe(1024)
    expect(parsed.cpu_seconds).toBe(30)

    const low = normalizePlugin({ id: 'p-2', name: 'low', mem_limit_mb: 1, cpu_seconds: 0 })
    expect(low.mem_limit_mb).toBe(PLUGIN_MEM_LIMIT.min)
    expect(low.cpu_seconds).toBe(PLUGIN_CPU_SECONDS.min)

    const high = normalizePlugin({
      id: 'p-3',
      name: 'high',
      mem_limit_mb: 99999,
      cpu_seconds: 999,
    })
    expect(high.mem_limit_mb).toBe(PLUGIN_MEM_LIMIT.max)
    expect(high.cpu_seconds).toBe(PLUGIN_CPU_SECONDS.max)

    const invalid = normalizePlugin({
      id: 'p-4',
      name: 'invalid',
      mem_limit_mb: 'lots',
      cpu_seconds: null,
    })
    expect(invalid.mem_limit_mb).toBe(PLUGIN_MEM_LIMIT.default)
    expect(invalid.cpu_seconds).toBe(PLUGIN_CPU_SECONDS.default)
  })

  it('sends both limits when creating and updating a plugin', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({
        id: 'p-5',
        name: 'memory',
        transport: 'stdio',
        mem_limit_mb: 2048,
        cpu_seconds: 45,
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await createPlugin({
      name: 'memory',
      transport: 'stdio',
      command: 'npx',
      mem_limit_mb: 2048,
      cpu_seconds: 45,
    })
    const createInit = fetchMock.mock.calls[0][1] as RequestInit
    expect(fetchMock.mock.calls[0][0]).toBe('/admin/plugins')
    expect(createInit.method).toBe('POST')
    expect(JSON.parse(String(createInit.body))).toMatchObject({
      name: 'memory',
      mem_limit_mb: 2048,
      cpu_seconds: 45,
    })

    await updatePlugin('p/5', { mem_limit_mb: 256, cpu_seconds: 5 })
    const updateInit = fetchMock.mock.calls[1][1] as RequestInit
    expect(fetchMock.mock.calls[1][0]).toBe('/admin/plugins/p%2F5')
    expect(updateInit.method).toBe('PATCH')
    expect(JSON.parse(String(updateInit.body))).toEqual({
      mem_limit_mb: 256,
      cpu_seconds: 5,
    })
  })
})

describe('plugin packages', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })

  const zipResponse = (disposition?: string) =>
    new Response('zip-bytes', {
      status: 200,
      headers: {
        'Content-Type': 'application/zip',
        ...(disposition ? { 'Content-Disposition': disposition } : {}),
      },
    })

  it('downloads the package with GET and the attachment filename', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      zipResponse('attachment; filename="filesystem.bidplugin.zip"'),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { blob, filename } = await exportPluginPackage('p/1', 'filesystem')
    expect(fetchMock.mock.calls[0][0]).toBe('/admin/plugins/p%2F1/package')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method ?? 'GET').toBe('GET')
    expect(init.body).toBeUndefined()
    expect(filename).toBe('filesystem.bidplugin.zip')
    expect(await blob.text()).toBe('zip-bytes')
  })

  it('falls back to a safe <name>.bidplugin.zip filename', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) => zipResponse()),
    )
    expect((await exportPluginPackage('p-2', 'my/plugin')).filename).toBe(
      'my_plugin.bidplugin.zip',
    )
    expect((await exportPluginPackage('p-2')).filename).toBe('plugin.bidplugin.zip')

    vi.stubGlobal(
      'fetch',
      vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
        zipResponse("attachment; filename*=UTF-8''%E8%AE%B0%E5%BF%86.bidplugin.zip"),
      ),
    )
    expect((await exportPluginPackage('p-3', '记忆')).filename).toBe('记忆.bidplugin.zip')
  })

  it('imports as multipart file with the on_conflict query', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ id: 'p-9', name: 'memory', transport: 'stdio', mem_limit_mb: 99999 }, 201),
    )
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['zip'], 'memory.bidplugin.zip', { type: 'application/zip' })

    const created = await importPluginPackage(file)
    expect(fetchMock.mock.calls[0][0]).toBe('/admin/plugins/import?on_conflict=replace')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    const form = init.body as FormData
    expect(form).toBeInstanceOf(FormData)
    const sent = form.get('file') as File
    expect(sent.name).toBe('memory.bidplugin.zip')
    expect(await sent.text()).toBe('zip')
    expect(created.created).toBe(true)
    expect(created.plugin.name).toBe('memory')
    // 归一化仍然生效：越界的资源上限被 clamp。
    expect(created.plugin.mem_limit_mb).toBe(PLUGIN_MEM_LIMIT.max)

    await importPluginPackage(file, 'skip')
    expect(fetchMock.mock.calls[1][0]).toBe('/admin/plugins/import?on_conflict=skip')
  })

  it('reports an upgrade on 200 and surfaces the backend detail on failure', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ id: 'p-9', name: 'memory', transport: 'stdio' }, 200),
    )
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['zip'], 'memory.bidplugin.zip', { type: 'application/zip' })
    const result = await importPluginPackage(file)
    expect(result.created).toBe(false)
    expect(result.plugin.id).toBe('p-9')

    vi.stubGlobal(
      'fetch',
      vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse({ detail: '已存在同名插件，且选择了跳过' }, 409),
      ),
    )
    await expect(importPluginPackage(file, 'skip')).rejects.toMatchObject({
      status: 409,
      message: '已存在同名插件，且选择了跳过',
    })

    vi.stubGlobal(
      'fetch',
      vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse({ detail: '插件包过大' }, 400),
      ),
    )
    await expect(importPluginPackage(file)).rejects.toBeInstanceOf(ApiError)
  })
})

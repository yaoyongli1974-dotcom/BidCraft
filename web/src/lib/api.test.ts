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
  createBillingOrder,
  createBoqVersion,
  createPlugin,
  createQualification,
  createSubAccount,
  createTerm,
  createUserDoc,
  createVipOrder,
  deleteBoqVersion,
  deleteQualification,
  deleteSubAccount,
  deleteUserDoc,
  duplicateProject,
  errorDetailText,
  exportPluginPackage,
  getBoqVersion,
  getPendingApprovals,
  getPreflight,
  getProject,
  getProjectTemplates,
  getQualifications,
  getSubAccounts,
  getTerms,
  getUserDocs,
  grantUserVip,
  importPluginPackage,
  instantiateTemplate,
  listBoqVersions,
  messageOf,
  normalizeApprovalChain,
  normalizeBoqQuoteVersion,
  normalizeBoqSummary,
  normalizeImagePolicy,
  normalizePlugin,
  normalizePreflight,
  normalizePricePolicy,
  normalizeQualification,
  normalizeTerm,
  reindexKnowledgeBase,
  restoreBoqVersion,
  reviewBoqVersion,
  roundTo,
  submitBoqVersion,
  unapproveBoqVersion,
  updatePlugin,
  updateProject,
  updateQualification,
  updateSubAccount,
  uploadForm,
  uploadQualificationEvidence,
  userDocFileUrl,
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

  body: unknown = null

  send(body?: unknown) {
    this.body = body
  }

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

describe('project templates (R23)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })

  it('reads the flat template list from GET /projects/templates and normalizes items', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse([
        {
          id: 'p-tpl',
          name: '市政模板',
          package: 'pkg-1',
          is_template: true,
          updated_at: '2026-11-18T09:00:00',
          owner_name: '张三',
        },
        { id: 'p-tpl-2', name: '货物模板', package: 'pkg-2', is_template: true },
        { name: '缺 id 的脏数据' },
        null,
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const items = await getProjectTemplates()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/templates')
    const init = fetchMock.mock.calls[0][1] as RequestInit | undefined
    expect(init?.method ?? 'GET').toBe('GET')
    expect(items).toEqual([
      {
        id: 'p-tpl',
        name: '市政模板',
        package: 'pkg-1',
        is_template: true,
        updated_at: '2026-11-18T09:00:00',
        owner_name: '张三',
      },
      {
        id: 'p-tpl-2',
        name: '货物模板',
        package: 'pkg-2',
        is_template: true,
        updated_at: null,
        owner_name: null,
      },
    ])
  })

  it('returns an empty list when the backend has no template endpoint (404)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: 'Not Found' }, 404)),
    )
    await expect(getProjectTemplates()).resolves.toEqual([])
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
    await expect(getProjectTemplates()).resolves.toEqual([])
  })

  it('propagates non-404 template list failures', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: '服务器错误' }, 500)),
    )
    await expect(getProjectTemplates()).rejects.toMatchObject({
      status: 500,
      message: '服务器错误',
    })
  })

  it('instantiates with both include flags defaulting to true and no name', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse(
        {
          id: 'p-new',
          name: '市政模板 副本',
          package: 'pkg-1',
          status: 'draft',
          my_role: 'owner',
          is_template: false,
        },
        201,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const created = await instantiateTemplate('tpl/1')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/templates/tpl%2F1/instantiate')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    // name 缺省不发送，由后端默认「模板名 副本」。
    expect(JSON.parse(String(init.body))).toEqual({
      include_boq: true,
      include_images: true,
    })
    expect(created).toMatchObject({ id: 'p-new', name: '市政模板 副本', my_role: 'owner' })
  })

  it('instantiates with a custom name and explicit false for both include flags', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ id: 'p-new', name: '新项目', package: 'pkg-1', status: 'draft' }, 201),
    )
    vi.stubGlobal('fetch', fetchMock)

    await instantiateTemplate('tpl-1', {
      name: '新项目',
      includeBoq: false,
      includeImages: false,
    })
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/templates/tpl-1/instantiate')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({
      name: '新项目',
      include_boq: false,
      include_images: false,
    })
  })

  it('surfaces 404 (not a template) and 400 (bad name) through ApiError', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: '模板不存在' }, 404))
      .mockResolvedValueOnce(jsonResponse({ detail: '项目名称不能为空' }, 400))
    vi.stubGlobal('fetch', fetchMock)

    await expect(instantiateTemplate('tpl-x')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      message: '模板不存在',
    })
    await expect(instantiateTemplate('tpl-1', { name: '  ' })).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      message: '项目名称不能为空',
    })
  })

  it('passes is_template through the project PATCH body', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ id: 'p-1', name: '项目', is_template: true }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const updated = await updateProject('p/1', { is_template: true })
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/p%2F1')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(String(init.body))).toEqual({ is_template: true })
    expect(updated.is_template).toBe(true)
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

describe('R25 conditional approval stages (packages & currencies)', () => {
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

  it('keeps valid package and currency lists, dedupes in order and drops invalid entries', () => {
    const chain = normalizeApprovalChain([
      stage('u-1', {
        packages: ['pkg-1', ' pkg-1 ', 'pkg-2', '', 'x'.repeat(41), 3, null],
        currencies: ['USD', 'usd', 'USD', '', 'y'.repeat(41), false],
      }),
      stage('u-2', { packages: [], currencies: [] }),
      stage('u-3'),
    ])
    expect(chain[0].packages).toEqual(['pkg-1', 'pkg-2'])
    expect(chain[0].currencies).toEqual(['USD', 'usd'])
    expect('packages' in chain[1]).toBe(false)
    expect('currencies' in chain[1]).toBe(false)
    expect('packages' in chain[2]).toBe(false)
    expect('currencies' in chain[2]).toBe(false)
  })

  it('filters stages by package whitelist and falls back to all when none match', () => {
    const chain = normalizeApprovalChain([
      stage('u-1'),
      stage('u-2', { packages: ['pkg-1', 'pkg-2'] }),
    ])
    expect(
      applicableStages(chain, null, { packageId: 'pkg-1' }).map((item) => item.index),
    ).toEqual([0, 1])
    expect(
      applicableStages(chain, null, { packageId: 'pkg-2' }).map((item) => item.index),
    ).toEqual([0, 1])
    expect(
      applicableStages(chain, null, { packageId: 'pkg-3' }).map((item) => item.index),
    ).toEqual([0])
    expect(applicableStages(chain, null, { packageId: null }).map((item) => item.index)).toEqual([
      0,
    ])
    // 旧调用只传金额：签名兼容；没有包件上下文时带包件条件的级按不匹配跳过。
    expect(applicableStages(chain, null).map((item) => item.index)).toEqual([0])
    // 没有任何条件的老链仍照常返回全部阶段。
    const legacy = normalizeApprovalChain([stage('u-7'), stage('u-8')])
    expect(applicableStages(legacy, null).map((item) => item.index)).toEqual([0, 1])
    // 所有级都被包件条件过滤时回退全部阶段，审批链不会被架空。
    const onlyPackage = normalizeApprovalChain([stage('u-9', { packages: ['pkg-1'] })])
    expect(
      applicableStages(onlyPackage, null, { packageId: 'pkg-3' }).map((item) => item.index),
    ).toEqual([0])
  })

  it('filters stages by currency whitelist with normalized comparison and fallback', () => {
    const chain = normalizeApprovalChain([
      stage('u-1'),
      stage('u-2', { currencies: ['USD', 'JPY'] }),
    ])
    expect(
      applicableStages(chain, null, { currency: 'USD' }).map((item) => item.index),
    ).toEqual([0, 1])
    expect(
      applicableStages(chain, null, { currency: 'usd' }).map((item) => item.index),
    ).toEqual([0, 1])
    expect(
      applicableStages(chain, null, { currency: 'JPY' }).map((item) => item.index),
    ).toEqual([0, 1])
    expect(
      applicableStages(chain, null, { currency: 'EUR' }).map((item) => item.index),
    ).toEqual([0])
    expect(applicableStages(chain, null, { currency: null }).map((item) => item.index)).toEqual([
      0,
    ])
    const onlyCurrency = normalizeApprovalChain([stage('u-9', { currencies: ['USD'] })])
    expect(
      applicableStages(onlyCurrency, null, { currency: 'EUR' }).map((item) => item.index),
    ).toEqual([0])
  })

  it('requires amount, package and currency conditions to pass together', () => {
    const chain = normalizeApprovalChain([
      stage('u-1', { min_amount: 100, packages: ['pkg-1'], currencies: ['USD'] }),
      stage('u-2'),
    ])
    const context = { packageId: 'pkg-1', currency: 'USD' }
    expect(applicableStages(chain, 100, context).map((item) => item.index)).toEqual([0, 1])
    expect(applicableStages(chain, 99, context).map((item) => item.index)).toEqual([1])
    expect(
      applicableStages(chain, 100, { packageId: 'pkg-2', currency: 'USD' }).map(
        (item) => item.index,
      ),
    ).toEqual([1])
    expect(
      applicableStages(chain, 100, { packageId: 'pkg-1', currency: 'CNY' }).map(
        (item) => item.index,
      ),
    ).toEqual([1])
  })

  it('patches packages and currencies per stage and keeps them in the echo', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({
        id: 'p-1',
        name: '项目',
        approval_chain: [
          {
            approvers: [{ id: 'u-1', name: '甲' }],
            mode: 'all',
            packages: ['pkg-1', 'pkg-2'],
            currencies: ['USD'],
          },
          { approvers: [{ id: 'u-2', name: '乙' }], mode: 'any' },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const updated = await updateProject('p/1', {
      approval_chain: [
        { approvers: ['u-1'], mode: 'all', packages: ['pkg-1', 'pkg-2'], currencies: ['USD'] },
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
      packages: ['pkg-1', 'pkg-2'],
      currencies: ['USD'],
    })
    expect(body.approval_chain[1]).toEqual({ approvers: ['u-2'], mode: 'any' })
    expect('packages' in body.approval_chain[1]).toBe(false)
    expect('currencies' in body.approval_chain[1]).toBe(false)
    expect(updated.approval_chain?.[0].packages).toEqual(['pkg-1', 'pkg-2'])
    expect(updated.approval_chain?.[0].currencies).toEqual(['USD'])
    expect(updated.approval_chain && 'packages' in updated.approval_chain[1]).toBe(false)
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

describe('R24 VIP & sub-accounts', () => {
  afterEach(() => vi.unstubAllGlobals())
  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

  it('grantUserVip posts days/note to the admin vip route', async () => {
    const fetchMock = vi.fn(async (_p: unknown, _init?: RequestInit) => jsonResponse({ user: { id: 'u1', is_vip: true } }))
    vi.stubGlobal('fetch', fetchMock)
    const res = await grantUserVip('u/1', 30, '开通')
    expect(fetchMock.mock.calls[0][0]).toBe('/admin/users/u%2F1/vip')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ days: 30, note: '开通' })
    expect(res.user).toMatchObject({ id: 'u1', is_vip: true })
  })

  it('createVipOrder posts package_id', async () => {
    const fetchMock = vi.fn(async (_p: unknown, _init?: RequestInit) => jsonResponse({ id: 'o1', credits: 0 }))
    vi.stubGlobal('fetch', fetchMock)
    await createVipOrder('vip-month')
    expect(fetchMock.mock.calls[0][0]).toBe('/billing/vip/orders')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ package_id: 'vip-month' })
  })

  it('getSubAccounts GETs the account list', async () => {
    const fetchMock = vi.fn(async (_p: unknown, _init?: RequestInit) => jsonResponse([{ id: 's1', username: 'sub1' }]))
    vi.stubGlobal('fetch', fetchMock)
    const list = await getSubAccounts()
    expect(fetchMock.mock.calls[0][0]).toBe('/account/sub-accounts')
    const init = fetchMock.mock.calls[0][1] as RequestInit | undefined
    expect(init?.method ?? 'GET').toBe('GET')
    expect(list).toEqual([{ id: 's1', username: 'sub1' }])
  })

  it('createSubAccount posts the input body', async () => {
    const fetchMock = vi.fn(async (_p: unknown, _init?: RequestInit) => jsonResponse({ id: 's1' }, 201))
    vi.stubGlobal('fetch', fetchMock)
    await createSubAccount({ username: 'worker', password: 'Passw0rd!1', display_name: '员工' })
    expect(fetchMock.mock.calls[0][0]).toBe('/account/sub-accounts')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ username: 'worker', password: 'Passw0rd!1', display_name: '员工' })
  })

  it('updateSubAccount PATCHes only the provided fields', async () => {
    const fetchMock = vi.fn(async (_p: unknown, _init?: RequestInit) => jsonResponse({ id: 's/1', active: false }))
    vi.stubGlobal('fetch', fetchMock)
    await updateSubAccount('s/1', { active: false })
    expect(fetchMock.mock.calls[0][0]).toBe('/account/sub-accounts/s%2F1')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(String(init.body))).toEqual({ active: false })
  })

  it('deleteSubAccount DELETEs by id', async () => {
    const fetchMock = vi.fn(async (_p: unknown, _init?: RequestInit) => jsonResponse({ deleted: true, id: 's1' }))
    vi.stubGlobal('fetch', fetchMock)
    const res = await deleteSubAccount('s1')
    expect(fetchMock.mock.calls[0][0]).toBe('/account/sub-accounts/s1')
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe('DELETE')
    expect(res).toEqual({ deleted: true, id: 's1' })
  })

  it('surfaces a 403 when a sub-account tries to recharge', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: '子账号费用由主账号承担，无需单独充值' }, 403)))
    await expect(createBillingOrder('starter')).rejects.toBeInstanceOf(ApiError)
  })
})

describe('my docs', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })

  it('lists the current user docs and normalizes defaults', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse([
        {
          id: 'd-1',
          filename: '资质证书.pdf',
          size: '2048',
          mime: 'application/pdf',
          status: 'Indexing',
          kb_id: 'kb-1',
          created_at: '2026-11-01T08:00:00',
        },
        { id: '', filename: 'ignored.txt' },
        { id: 'd-2', filename: '产品参数.xlsx', size: 12 },
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const docs = await getUserDocs()
    expect(fetchMock.mock.calls[0][0]).toBe('/my/docs')
    expect((fetchMock.mock.calls[0][1] as RequestInit | undefined)?.method).toBeUndefined()
    expect(docs).toHaveLength(2)
    expect(docs[0]).toMatchObject({
      id: 'd-1',
      filename: '资质证书.pdf',
      size: 2048,
      status: 'indexing',
      error: '',
      kb_id: 'kb-1',
    })
    expect(docs[1]).toMatchObject({
      id: 'd-2',
      filename: '产品参数.xlsx',
      size: 12,
      mime: '',
      status: 'ready',
      created_at: null,
      updated_at: null,
    })
  })

  it('unwraps a docs/items envelope when an older server nests the list', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({ items: [{ id: 'd-3', filename: '说明.md', size: 10 }] }),
      ),
    )
    const docs = await getUserDocs()
    expect(docs.map((doc) => doc.id)).toEqual(['d-3'])
  })

  it('uploads a multipart file to /my/docs and returns the 202 job', async () => {
    FakeXhr.instances = []
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const file = new File(['hello'], '产品彩页.pdf', { type: 'application/pdf' })

    const pending = createUserDoc(file)
    const xhr = FakeXhr.instances[0]
    expect(xhr.method).toBe('POST')
    expect(xhr.url).toBe('/my/docs')
    const form = xhr.body as FormData
    expect(form).toBeInstanceOf(FormData)
    const sent = form.get('file') as File
    expect(sent.name).toBe('产品彩页.pdf')
    expect(await sent.text()).toBe('hello')

    xhr.respond(
      202,
      JSON.stringify({
        doc: {
          id: 'd-4',
          filename: '产品彩页.pdf',
          size: 5,
          mime: 'application/pdf',
          status: 'ready',
          kb_id: 'kb-9',
          created_at: '2026-11-19T09:00:00',
        },
        job_id: 'job-7',
      }),
    )
    await expect(pending).resolves.toMatchObject({
      job_id: 'job-7',
      doc: { id: 'd-4', filename: '产品彩页.pdf', kb_id: 'kb-9' },
    })
  })

  it('rejects an unrecognizable upload payload', async () => {
    FakeXhr.instances = []
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const pending = createUserDoc(new File(['x'], 'a.txt'))
    FakeXhr.instances[0].respond(202, '{"job_id":"job-1"}')
    await expect(pending).rejects.toMatchObject({
      name: 'ApiError',
      message: '服务端返回的文档信息无法识别，请刷新后重试。',
    })
  })

  it('deletes by encoded id and keeps the server echo', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ deleted: true, id: 'd/1' }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await deleteUserDoc('d/1')
    expect(fetchMock.mock.calls[0][0]).toBe('/my/docs/d%2F1')
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe('DELETE')
    expect(result).toEqual({ deleted: true, id: 'd/1' })
  })

  it('builds the download URL for a doc', () => {
    expect(userDocFileUrl('d/1')).toBe('/my/docs/d%2F1/file')
  })
})

describe('qualifications & preflight (R32)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })

  it('归一化资质：kind/status 白名单、日期 null 与证据过滤', () => {
    const parsed = normalizeQualification({
      id: 'q-1',
      kind: 'unknown-kind',
      name: '   ',
      holder: '某公司',
      cert_no: 'D123',
      issuer: '省住建厅',
      valid_from: null,
      valid_to: '2027-01-05',
      note: '备注',
      status: 'weird',
      days_left: '12',
      evidence: [
        {
          filename: '信用中国.png',
          size: '2048',
          mime: 'image/png',
          uploaded_at: '2026-11-01T08:00:00',
        },
        { size: 12, mime: 'image/png' },
      ],
      created_at: '2026-11-01T08:00:00',
      updated_at: null,
    })

    expect(parsed).toMatchObject({
      id: 'q-1',
      kind: 'other',
      name: '未命名资质',
      holder: '某公司',
      cert_no: 'D123',
      issuer: '省住建厅',
      valid_from: null,
      valid_to: '2027-01-05',
      status: 'none',
      days_left: 12,
      updated_at: null,
    })
    expect(parsed?.evidence).toEqual([
      {
        filename: '信用中国.png',
        size: 2048,
        mime: 'image/png',
        uploaded_at: '2026-11-01T08:00:00',
      },
    ])
    expect(normalizeQualification({ id: '', name: '无 id' })).toBeNull()
    expect(
      normalizeQualification({
        id: 'q-2',
        kind: 'safety',
        name: '安全生产许可证',
        status: 'expiring',
        days_left: -3,
      }),
    ).toMatchObject({ kind: 'safety', status: 'expiring', days_left: -3 })
  })

  it('getQualifications 拼接 kind/status 查询并归一化平铺数组', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse([
        {
          id: 'q-1',
          kind: 'license',
          name: '营业执照',
          status: 'valid',
          days_left: 200,
          evidence: [],
        },
        { id: '', name: '应被忽略' },
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const list = await getQualifications({ kind: 'license', status: ' valid ' })
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/my/qualifications?kind=license&status=valid',
    )
    expect(
      (fetchMock.mock.calls[0][1] as RequestInit | undefined)?.method,
    ).toBeUndefined()
    expect(list).toHaveLength(1)
    expect(list[0]).toMatchObject({
      id: 'q-1',
      kind: 'license',
      name: '营业执照',
      status: 'valid',
      days_left: 200,
    })

    await getQualifications()
    expect(fetchMock.mock.calls[1][0]).toBe('/my/qualifications')
  })

  it('create/update/delete 使用正确的方法、路径与 body', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, init?: RequestInit) =>
      jsonResponse(
        { id: 'q/1', kind: 'license', name: '营业执照', status: 'valid', evidence: [] },
        init?.method === 'POST' ? 201 : 200,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await createQualification({
      kind: 'license',
      name: '营业执照',
      valid_from: '2024-01-01',
      valid_to: '2028-01-01',
    })
    expect(fetchMock.mock.calls[0][0]).toBe('/my/qualifications')
    const createInit = fetchMock.mock.calls[0][1] as RequestInit
    expect(createInit.method).toBe('POST')
    expect(JSON.parse(String(createInit.body))).toMatchObject({
      kind: 'license',
      name: '营业执照',
      valid_from: '2024-01-01',
      valid_to: '2028-01-01',
    })

    await updateQualification('q/1', { name: '更新后的名称', valid_to: null })
    expect(fetchMock.mock.calls[1][0]).toBe('/my/qualifications/q%2F1')
    const updateInit = fetchMock.mock.calls[1][1] as RequestInit
    expect(updateInit.method).toBe('PATCH')
    expect(JSON.parse(String(updateInit.body))).toEqual({
      name: '更新后的名称',
      valid_to: null,
    })

    await deleteQualification('q/1')
    expect(fetchMock.mock.calls[2][0]).toBe('/my/qualifications/q%2F1')
    expect((fetchMock.mock.calls[2][1] as RequestInit).method).toBe('DELETE')
  })

  it('上传证据走 multipart 的 file 字段并读回归一化资质', async () => {
    FakeXhr.instances = []
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const file = new File(['png-bytes'], '信用中国截图.png', { type: 'image/png' })

    const pending = uploadQualificationEvidence('q/1', file)
    const xhr = FakeXhr.instances[0]
    expect(xhr.method).toBe('POST')
    expect(xhr.url).toBe('/my/qualifications/q%2F1/evidence')
    const form = xhr.body as FormData
    expect(form).toBeInstanceOf(FormData)
    const sent = form.get('file') as File
    expect(sent.name).toBe('信用中国截图.png')
    expect(await sent.text()).toBe('png-bytes')

    xhr.respond(
      201,
      JSON.stringify({
        id: 'q/1',
        kind: 'credit',
        name: '信用记录',
        status: 'valid',
        evidence: [
          {
            filename: '信用中国截图.png',
            size: 9,
            mime: 'image/png',
            uploaded_at: '2026-11-19T09:00:00',
          },
        ],
      }),
    )
    await expect(pending).resolves.toMatchObject({
      id: 'q/1',
      kind: 'credit',
      name: '信用记录',
      evidence: [{ filename: '信用中国截图.png', size: 9 }],
    })
  })

  it('preflight 透传 deadline，并回算缺失的 summary', async () => {
    const fetchMock = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({
        deadline: '2026-12-01',
        items: [
          {
            key: 'business_license',
            label: '营业执照',
            kinds: ['license', 'not-a-kind'],
            hint: '需在有效期内',
            status: 'ok',
            qualification_id: 'q-1',
            name: '营业执照',
            valid_to: '2028-01-01',
            evidence_count: '2',
          },
          {
            key: 'credit',
            label: '信用记录',
            kinds: ['credit'],
            hint: '开标当日查询',
            status: 'not-a-status',
            qualification_id: null,
            name: '',
            valid_to: null,
            evidence_count: 0,
          },
        ],
        summary: { ok: 1, warn: '0', missing: 1, total: 2 },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const report = await getPreflight('2026-12-01')
    expect(fetchMock.mock.calls[0][0]).toBe('/my/preflight?deadline=2026-12-01')
    expect(report.items[0]).toMatchObject({
      key: 'business_license',
      kinds: ['license'],
      evidence_count: 2,
    })
    expect(report.items[1]).toMatchObject({
      key: 'credit',
      status: 'missing',
      qualification_id: null,
      valid_to: null,
    })
    expect(report.summary).toEqual({ ok: 1, warn: 0, missing: 1, total: 2 })

    await getPreflight()
    expect(fetchMock.mock.calls[1][0]).toBe('/my/preflight')

    const fallback = normalizePreflight(
      {
        items: [
          { key: 'a', status: 'ok' },
          { key: 'b', status: 'expiring' },
          { key: 'c', status: 'expired' },
        ],
      },
      '2026-12-02',
    )
    expect(fallback.deadline).toBe('2026-12-02')
    expect(fallback.summary).toEqual({ ok: 1, warn: 1, missing: 1, total: 3 })
  })
})

describe('desktop bridge readiness (R29)', () => {
  afterEach(() => {
    delete (window as unknown as { pywebview?: unknown }).pywebview
    vi.resetModules()
  })

  it('浏览器环境没有桥时立即返回 null', async () => {
    const mod = await import('./api')
    expect(mod.desktopBridge()).toBeNull()
    await expect(mod.awaitDesktopBridge(50)).resolves.toBeNull()
  })

  it('pywebview 注入完成前的桩对象不算就绪，ready 后可用', async () => {
    const mod = await import('./api')
    const stub: Record<string, unknown> = {}
    ;(window as unknown as { pywebview: { api: unknown } }).pywebview = { api: stub }
    expect(mod.desktopBridge()).toBeNull()
    const pending = mod.awaitDesktopBridge(2000)
    setTimeout(() => {
      stub.api_request = () => Promise.resolve({ status: 200, body: '', offline: false })
      window.dispatchEvent(new Event('pywebviewready'))
    }, 30)
    const bridge = await pending
    expect(bridge).not.toBeNull()
    expect(typeof bridge?.api_request).toBe('function')
  })
})

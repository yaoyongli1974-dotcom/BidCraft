import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../lib/http'
import {
  extractKnowledgeAsset,
  listKnowledgeAssetDuplicates,
  listKnowledgeAssets,
  normalizeKnowledgeAsset,
  normalizeKnowledgeAssetDuplicateGroup,
  resolveKnowledgeAssetDuplicate,
  reviewKnowledgeAsset,
} from './knowledge'
import {
  extractEnterpriseKnowledgeAsset,
  listEnterpriseKnowledgeAssetDuplicates,
  listEnterpriseKnowledgeAssets,
  normalizeEnterprise,
  resolveEnterpriseKnowledgeAssetDuplicate,
  reviewEnterpriseKnowledgeAsset,
} from './enterprise'

const ASSET = {
  id: 'a1',
  document_id: 'd1',
  kb_id: 'kb-1',
  source: '资质证书.pdf',
  sha256: 'abc123',
  review_status: 'PUBLISHED',
  fields: { cert_no: 'C-1', nested: { owner: '示例公司' } },
  chunks: [
    { id: 'c1', page: '3', heading: '资质证书', token: 'keep' },
    { bogus: true },
  ],
  source_file_id: 'sf-1',
  reviewed_by: 'u1',
  reviewed_at: '2026-11-01T00:00:00Z',
  published_at: '2026-11-02T00:00:00Z',
  rejected_reason: null,
  ok: 'true',
  reason: '',
  custom_flag: 'keep-me',
}

const GROUP = {
  id: 'g1',
  kb_id: 'kb-1',
  fingerprint: 'fp-1',
  similarity: '0.92',
  status: 'pending',
  reason: '字段高度相似',
  members: [
    { id: 'm1', document_id: 'd1', score: '0.92', role: 'primary', source: '资质证书.pdf', note: 'raw' },
    { junk: true },
  ],
  custom_group: 'keep-me',
}

function mockFetch(payload: unknown, status = 200) {
  const fetch = vi.fn(
    async (_input: string, _init?: RequestInit) =>
      new Response(JSON.stringify(payload), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
  )
  vi.stubGlobal('fetch', fetch)
  return fetch
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('知识资产归一化', () => {
  it('资产保留未知字段、片段与溯源，状态转小写，ok/可空字段不伪造', () => {
    const asset = normalizeKnowledgeAsset(ASSET)
    expect(asset).toMatchObject({
      id: 'a1',
      document_id: 'd1',
      kb_id: 'kb-1',
      source: '资质证书.pdf',
      sha256: 'abc123',
      review_status: 'published',
      source_file_id: 'sf-1',
      reviewed_by: 'u1',
      reviewed_at: '2026-11-01T00:00:00Z',
      published_at: '2026-11-02T00:00:00Z',
      rejected_reason: null,
      ok: true,
      reason: '',
      custom_flag: 'keep-me',
    })
    expect(asset.fields).toEqual(ASSET.fields)
    expect(asset.chunks).toHaveLength(1)
    expect(asset.chunks?.[0]).toMatchObject({
      id: 'c1',
      page: 3,
      heading: '资质证书',
      token: 'keep',
    })

    // 旧后端只发 document_id：用真实字段作为行标识，不伪造额外来源。
    const legacy = normalizeKnowledgeAsset({ document_id: 'd9', source: '旧版.pdf' })
    expect(legacy).toMatchObject({ id: 'd9', document_id: 'd9', source: '旧版.pdf' })

    const sparse = normalizeKnowledgeAsset({ id: 'a2', review_status: null })
    expect(sparse).toMatchObject({
      document_id: null,
      kb_id: null,
      source: '',
      sha256: null,
      review_status: null,
      fields: {},
      chunks: [],
      source_file_id: null,
      ok: undefined,
    })
  })

  it('无法识别的资产与列表载荷抛 ApiError，不回退为空列表', () => {
    expect(() => normalizeKnowledgeAsset(null)).toThrow(ApiError)
    expect(() => normalizeKnowledgeAsset([])).toThrow(ApiError)
    expect(() => normalizeKnowledgeAsset({ source: 'a.pdf' })).toThrow(ApiError)
    expect(() => normalizeKnowledgeAsset(ASSET)).not.toThrow()
  })

  it('重复组归一化保留成员与未知字段，非法条目丢弃', async () => {
    const group = normalizeKnowledgeAssetDuplicateGroup(GROUP)
    expect(group).toMatchObject({
      id: 'g1',
      fingerprint: 'fp-1',
      similarity: 0.92,
      status: 'pending',
      reason: '字段高度相似',
      custom_group: 'keep-me',
    })
    expect(group.members).toHaveLength(1)
    expect(group.members?.[0]).toMatchObject({
      document_id: 'd1',
      score: 0.92,
      role: 'primary',
      note: 'raw',
    })
    expect(() => normalizeKnowledgeAssetDuplicateGroup({})).toThrow(ApiError)

    mockFetch({ unexpected: true })
    await expect(listKnowledgeAssets('kb-1')).rejects.toBeInstanceOf(ApiError)
    await expect(listKnowledgeAssetDuplicates('kb-1')).rejects.toThrow('无法识别')
  })
})

describe('admin 知识资产接口', () => {
  it('list / extract / review 使用契约路径、方法与请求体', async () => {
    const listFetch = mockFetch({ items: [ASSET] })
    const assets = await listKnowledgeAssets('kb 1')
    expect(listFetch.mock.calls[0][0]).toBe('/admin/kbs/kb%201/assets')
    expect(assets).toHaveLength(1)
    expect(assets[0].review_status).toBe('published')

    const extractFetch = mockFetch(ASSET)
    const extracted = await extractKnowledgeAsset('kb-1', 'doc 1')
    const [extractUrl, extractInit] = extractFetch.mock.calls[0]
    expect(extractUrl).toBe('/admin/kbs/kb-1/assets/doc%201/extract')
    expect(extractInit?.method).toBe('POST')
    expect(extracted.id).toBe('a1')

    const reviewFetch = mockFetch(ASSET)
    const reviewed = await reviewKnowledgeAsset('kb-1', 'd1', {
      decision: 'approve',
      note: '  复核通过  ',
    })
    const [reviewUrl, reviewInit] = reviewFetch.mock.calls[0]
    expect(reviewUrl).toBe('/admin/kbs/kb-1/assets/d1/review')
    expect(reviewInit?.method).toBe('POST')
    expect(JSON.parse(String(reviewInit?.body))).toEqual({
      decision: 'approve',
      note: '复核通过',
    })
    expect(reviewed.id).toBe('a1')
  })

  it('duplicates / resolve 使用契约路径、方法与请求体', async () => {
    const listFetch = mockFetch({ items: [GROUP] })
    const groups = await listKnowledgeAssetDuplicates('kb-1')
    expect(listFetch.mock.calls[0][0]).toBe('/admin/kbs/kb-1/assets/duplicates')
    expect(groups[0].members).toHaveLength(1)

    const resolveFetch = mockFetch(GROUP)
    const resolved = await resolveKnowledgeAssetDuplicate('kb-1', 'g 1', {
      decision: 'dismiss',
      note: '误报',
    })
    const [url, init] = resolveFetch.mock.calls[0]
    expect(url).toBe('/admin/kbs/kb-1/assets/duplicates/g%201/resolve')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ decision: 'dismiss', note: '误报' })
    expect(resolved.id).toBe('g1')
  })

  it('单个动作响应无法识别时抛错，不伪造成功', async () => {
    mockFetch({ ok: true })
    await expect(reviewKnowledgeAsset('kb-1', 'd1', { decision: 'review' })).rejects.toBeInstanceOf(
      ApiError,
    )
    mockFetch({ unexpected: true })
    await expect(
      resolveKnowledgeAssetDuplicate('kb-1', 'g1', { decision: 'resolve' }),
    ).rejects.toBeInstanceOf(ApiError)
  })
})

describe('enterprise 知识资产接口与 owner 标记', () => {
  it('五个端点全部留在 /enterprise 命名空间下', async () => {
    const listFetch = mockFetch({ items: [ASSET] })
    await listEnterpriseKnowledgeAssets('e 1')
    expect(listFetch.mock.calls[0][0]).toBe('/enterprise/e%201/assets')

    const extractFetch = mockFetch(ASSET)
    await extractEnterpriseKnowledgeAsset('e-1', 'd1')
    expect(extractFetch.mock.calls[0][0]).toBe('/enterprise/e-1/assets/d1/extract')
    expect(extractFetch.mock.calls[0][1]?.method).toBe('POST')

    const reviewFetch = mockFetch(ASSET)
    await reviewEnterpriseKnowledgeAsset('e-1', 'd1', { decision: 'publish', note: '可发布' })
    expect(reviewFetch.mock.calls[0][0]).toBe('/enterprise/e-1/assets/d1/review')
    expect(reviewFetch.mock.calls[0][1]?.method).toBe('POST')
    expect(JSON.parse(String(reviewFetch.mock.calls[0][1]?.body))).toEqual({
      decision: 'publish',
      note: '可发布',
    })

    const duplicateFetch = mockFetch({ items: [GROUP] })
    await listEnterpriseKnowledgeAssetDuplicates('e-1')
    expect(duplicateFetch.mock.calls[0][0]).toBe('/enterprise/e-1/assets/duplicates')

    const resolveFetch = mockFetch(GROUP)
    await resolveEnterpriseKnowledgeAssetDuplicate('e-1', 'g1', {
      decision: 'resolve',
      note: '已确认',
    })
    expect(resolveFetch.mock.calls[0][0]).toBe('/enterprise/e-1/assets/duplicates/g1/resolve')
    expect(resolveFetch.mock.calls[0][1]?.method).toBe('POST')
    expect(JSON.parse(String(resolveFetch.mock.calls[0][1]?.body))).toEqual({
      decision: 'resolve',
      note: '已确认',
    })
  })

  it('normalizeEnterprise 保留服务端 is_owner，缺失时保持 undefined', () => {
    expect(normalizeEnterprise({ id: 'e1', name: '示例', is_owner: false }).is_owner).toBe(false)
    expect(normalizeEnterprise({ id: 'e1', name: '示例', is_owner: true }).is_owner).toBe(true)
    expect(normalizeEnterprise({ id: 'e1', name: '示例' }).is_owner).toBeUndefined()
  })
})

import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../lib/http'
import {
  getRetrievalPreview,
  previewProjectRetrieval,
  previewRetrievalPassages,
} from './retrieval'

function fetchMock(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    statusText: '',
    json: async () => body,
    text: async () => JSON.stringify(body),
  })
}

const PASSAGE = {
  kb_id: 'kb-1',
  source: 'cert.pdf',
  text: '证书编号 XYZ-001',
  score: 0.9,
  rerank_score: 0.82,
  trust_tier: 'excerpt',
  degraded: true,
  chunk_id: 'chunk-7',
  chunk_index: 2,
  document_id: 'doc-1',
  page: '3',
  heading: '资质证书',
  diagnostics: { searched: ['kb-1'] },
  custom_field: 'keep-me',
}

describe('项目级检索预览客户端', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('请求 /projects/{ref}/retrieval/preview?q=&limit= 并透传溯源字段', async () => {
    const fetch = fetchMock({
      query: '证书',
      items: [PASSAGE],
      diagnostics: { searched: ['kb-1'], failed: [] },
      degraded: true,
    })
    vi.stubGlobal('fetch', fetch)

    const result = await previewProjectRetrieval('p 1', '  证书  ', 3)
    expect(fetch.mock.calls[0][0]).toBe(
      `/projects/p%201/retrieval/preview?q=${encodeURIComponent('证书')}&limit=3`,
    )
    expect(result.query).toBe('证书')
    expect(result.degraded).toBe(true)
    expect(result.diagnostics).toEqual({ searched: ['kb-1'], failed: [] })
    expect(result.items[0]).toMatchObject({
      kb_id: 'kb-1',
      source: 'cert.pdf',
      text: '证书编号 XYZ-001',
      score: 0.9,
      rerank_score: 0.82,
      trust_tier: 'excerpt',
      degraded: true,
      chunk_id: 'chunk-7',
      chunk_index: 2,
      document_id: 'doc-1',
      page: 3,
      heading: '资质证书',
      custom_field: 'keep-me',
    })
  })

  it('旧后端没有溯源字段时全部归一为 null', async () => {
    vi.stubGlobal(
      'fetch',
      fetchMock({
        passages: [{ kb_id: 'kb-1', source: 'a.pdf', text: '片段', score: '0.7' }],
        diagnostics: ['降级 A'],
        downgrades: 1,
      }),
    )
    const result = await previewProjectRetrieval('p1', '片段')
    expect(result.items[0]).toMatchObject({
      rerank_score: null,
      trust_tier: null,
      degraded: null,
      chunk_id: null,
      chunk_index: null,
      diagnostics: null,
    })
    expect(result.degraded).toBeNull()
    expect(result.diagnostics).toEqual(['降级 A'])
  })

  it('接受裸数组响应；空查询与无法识别的响应明确失败', async () => {
    const fetch = fetchMock([{ source: 'a.pdf', text: '片段', score: 0.6 }])
    vi.stubGlobal('fetch', fetch)
    expect(await previewRetrievalPassages('p1', '片段')).toHaveLength(1)

    await expect(previewProjectRetrieval('p1', '   ')).rejects.toThrow('检索内容不能为空')
    expect(fetch).toHaveBeenCalledTimes(1)

    vi.stubGlobal('fetch', fetchMock({ unexpected: true }))
    await expect(previewProjectRetrieval('p1', '片段')).rejects.toBeInstanceOf(ApiError)
    expect(getRetrievalPreview).toBe(previewProjectRetrieval)
  })
})

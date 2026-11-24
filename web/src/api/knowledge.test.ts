import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../lib/http'
import {
  listKbDocuments,
  normalizeKbDocument,
  normalizeKbPassage,
  previewKbRetrieval,
} from './knowledge'

function fetchMock(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    statusText: '',
    json: async () => body,
    text: async () => JSON.stringify(body),
  })
}

describe('KB 文档实体 / 检索预览归一化', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('文档实体保留后端原字段、状态统一小写，并补出 filename / chunk_count', () => {
    const row = normalizeKbDocument({
      id: 'doc-1',
      kb_id: 'kb-1',
      source: '/data/资质证书.pdf',
      sha256: 'abc123',
      size: '2048',
      mime: 'application/pdf',
      pages: '6',
      status: 'READY',
      version: '2',
      kind: 'qualification',
      tags: ['资质', 42],
      valid_to: '2027-01-31',
      chunk_count: '12',
      error: '',
      created_at: '2026-11-01T00:00:00Z',
      updated_at: null,
      custom_flag: 'keep-me',
    })
    expect(row).not.toBeNull()
    expect(row).toMatchObject({
      id: 'doc-1',
      kb_id: 'kb-1',
      source: '/data/资质证书.pdf',
      filename: '/data/资质证书.pdf',
      sha256: 'abc123',
      size: 2048,
      mime: 'application/pdf',
      pages: 6,
      status: 'ready',
      version: 2,
      kind: 'qualification',
      tags: ['资质'],
      valid_to: '2027-01-31',
      chunk_count: 12,
      error: '',
      created_at: '2026-11-01T00:00:00Z',
      updated_at: null,
      custom_flag: 'keep-me',
    })
  })

  it('文档实体容忍缺省/异常字段：状态回退 ready，计数回退 0，非法条目丢弃', () => {
    const row = normalizeKbDocument({ id: 'doc-2', chunks: 3, status: '   ' })
    expect(row).toMatchObject({
      id: 'doc-2',
      filename: 'doc-2',
      status: 'ready',
      kind: 'other',
      size: 0,
      pages: 0,
      version: 1,
      valid_to: null,
      tags: [],
      chunk_count: 3,
    })
    expect(normalizeKbDocument({ source: 'a.pdf', status: 'EXPIRED' })?.status).toBe(
      'expired',
    )
    expect(normalizeKbDocument({ id: 'doc-3', pages: 'x', version: 'x', tags: 'x' })).toMatchObject({
      pages: 0,
      version: 1,
      tags: [],
      id: 'doc-3',
      filename: 'doc-3',
    })
    expect(normalizeKbDocument(null)).toBeNull()
    expect(normalizeKbDocument([{ id: 'doc' }])).toBeNull()
    expect(normalizeKbDocument({ status: 'ready' })).toBeNull()
  })

  it('检索片段保留溯源字段与未知字段，分值/页码容错', () => {
    const passage = normalizeKbPassage({
      kb_id: 'kb-1',
      source: '证书.pdf',
      text: '证书编号 XYZ-001',
      score: '0.86',
      document_id: 'doc-9',
      page: '7',
      heading: '资质证书',
      rank: 1,
    })
    expect(passage).toMatchObject({
      kb_id: 'kb-1',
      source: '证书.pdf',
      text: '证书编号 XYZ-001',
      score: 0.86,
      document_id: 'doc-9',
      page: 7,
      heading: '资质证书',
      rank: 1,
    })
    const sparse = normalizeKbPassage({ source: 'a.pdf', score: 'x', page: null })
    expect(sparse).toMatchObject({
      text: '',
      score: 0,
      document_id: null,
      page: null,
      heading: '',
    })
    expect(normalizeKbPassage({ page: 'bad', text: '片段' })?.page).toBeNull()
    expect(normalizeKbPassage(null)).toBeNull()
    expect(normalizeKbPassage({})).toBeNull()
  })

  it('listKbDocuments 请求 documents 端点并归一化 items', async () => {
    const fetch = fetchMock({
      items: [
        { id: 'd1', source: 'a.pdf', status: 'READY', chunk_count: 4, extra: 'raw' },
        { id: 'd2', source: 'b.pdf', status: 'expired' },
      ],
    })
    vi.stubGlobal('fetch', fetch)
    const rows = await listKbDocuments('kb 1')
    expect(fetch.mock.calls[0][0]).toBe('/admin/kbs/kb%201/documents')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toMatchObject({
      id: 'd1',
      filename: 'a.pdf',
      status: 'ready',
      chunk_count: 4,
      extra: 'raw',
    })
    expect(rows[1].status).toBe('expired')
  })

  it('previewKbRetrieval 携带 q/limit，空查询与异常响应用中文报错', async () => {
    const fetch = fetchMock({
      kb_id: 'kb-1',
      query: '证书',
      items: [
        {
          kb_id: 'kb-1',
          source: 'a.pdf',
          text: '编号 XYZ-001',
          score: '0.9',
          document_id: 'doc-1',
          page: '2',
          heading: '资质',
        },
      ],
    })
    vi.stubGlobal('fetch', fetch)
    const passages = await previewKbRetrieval('kb-1', '  证书  ')
    expect(fetch.mock.calls[0][0]).toBe(
      `/admin/kbs/kb-1/preview?q=${encodeURIComponent('证书')}&limit=5`,
    )
    expect(passages[0]).toMatchObject({
      document_id: 'doc-1',
      page: 2,
      heading: '资质',
      score: 0.9,
    })

    await expect(previewKbRetrieval('kb-1', '   ')).rejects.toThrow('检索内容不能为空')
    expect(fetch).toHaveBeenCalledTimes(1)

    vi.stubGlobal('fetch', fetchMock({ unexpected: true }))
    await expect(listKbDocuments('kb-1')).rejects.toBeInstanceOf(ApiError)
    await expect(listKbDocuments('kb-1')).rejects.toThrow(
      '服务端返回的文档清单无法识别，请刷新后重试。',
    )
    await expect(previewKbRetrieval('kb-1', '证书')).rejects.toThrow(
      '服务端返回的检索预览无法识别，请刷新后重试。',
    )
  })
})

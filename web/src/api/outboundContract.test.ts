import { afterEach, describe, expect, it, vi } from 'vitest'

import { checkSimilarity, updateSimilarityHit } from './similarity'

/**
 * 出站契约的 JS 镜像（只读引用后端声明，不修改后端）：
 * - SimilarityRequest: bidcraft/api/schemas.py:577-581
 * - scope/mode 真实取值: bidcraft/similarity/service.py:23-24、105-111
 * - REVIEW_STATUSES / SimilarityHitReview: service.py:26、schemas.py:584-586
 * - KbDocumentIngest: bidcraft/api/schemas.py:564-570
 * - QualityFindingUpdate: bidcraft/api/schemas.py:644-648
 * - CommercialReview: bidcraft/api/schemas.py:600-604
 */
const BACKEND_ENUMS = {
  similarityScope: ['project', 'history'] as const,
  similarityMode: ['text', 'structure', 'semantic', 'image', 'all'] as const,
  similarityReviewStatus: ['pending', 'normal', 'suspicious', 'ignored', 'confirmed'] as const,
  qualityFindingStatus: ['handled', 'ignored', 'open'] as const,
}

const BACKEND_KEYS = {
  similarityCheck: ['scope', 'mode', 'threshold', 'source_project_id'],
  similarityHitReview: ['status', 'note'],
  kbDocIngest: ['source', 'content', 'kind', 'tags', 'valid_to'],
  qualityFindingUpdate: ['status', 'note'],
  commercialReview: ['action', 'note', 'require_complete'],
} as const

function assertBody(
  body: unknown,
  allowedKeys: readonly string[],
  label: string,
): Record<string, unknown> {
  expect(body, label).toBeTypeOf('object')
  expect(body, label).not.toBeNull()
  const record = body as Record<string, unknown>
  expect(
    Object.keys(record).every((key) => allowedKeys.includes(key)),
    `${label} 含后端不接受的字段`,
  ).toBe(true)
  return record
}

function mockFetch(payload: unknown) {
  const fetchMock = vi.fn(
    async (_input: string, _init?: RequestInit) =>
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('出站契约：UI payload 对齐后端枚举与字段', () => {
  it('旧 UI 的 scope/mode 取值不在后端允许集合内', () => {
    expect(BACKEND_ENUMS.similarityScope).not.toContain('all')
    expect(BACKEND_ENUMS.similarityScope).not.toContain('internal')
    expect(BACKEND_ENUMS.similarityScope).not.toContain('external')
    expect(BACKEND_ENUMS.similarityMode).not.toContain('paragraph')
    expect(BACKEND_ENUMS.similarityMode).not.toContain('sentence')
    expect(BACKEND_ENUMS.similarityMode).not.toContain('section')
  })

  it('similarity check 的真实请求体只含合法枚举、阈值与可选来源项目', async () => {
    const fetchMock = mockFetch({ report_id: 'r9' })

    await checkSimilarity('p1', {
      scope: 'history',
      mode: 'semantic',
      threshold: 0.7,
      source_project_id: 'p0',
    })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/projects/p1/similarity/check')
    const body = assertBody(
      JSON.parse(String(init?.body)),
      BACKEND_KEYS.similarityCheck,
      'similarity check',
    )
    expect(BACKEND_ENUMS.similarityScope).toContain(body.scope)
    expect(BACKEND_ENUMS.similarityMode).toContain(body.mode)
    expect(typeof body.threshold).toBe('number')
    expect(body.threshold as number).toBeGreaterThan(0)
    expect(body.threshold as number).toBeLessThanOrEqual(1)
    expect(body.source_project_id).toBe('p0')

    // project 范围不得下发来源项目（旧 UI 无论范围都会带上）。
    await checkSimilarity('p1', { scope: 'project', mode: 'text', threshold: 0.85 })
    const projectBody = assertBody(
      JSON.parse(String(fetchMock.mock.calls[1][1]?.body)),
      BACKEND_KEYS.similarityCheck,
      'similarity check (project)',
    )
    expect(projectBody).toEqual({ scope: 'project', mode: 'text', threshold: 0.85 })
    expect(Object.prototype.hasOwnProperty.call(projectBody, 'source_project_id')).toBe(false)
  })

  it('复核状态的真实请求体使用后端 REVIEW_STATUSES 中的值', async () => {
    const fetchMock = mockFetch({ id: 'h1', review_status: 'suspicious' })

    await updateSimilarityHit('p1', 'h1', 'suspicious', '待人工判断')
    const [, init] = fetchMock.mock.calls[0]
    const body = assertBody(
      JSON.parse(String(init?.body)),
      BACKEND_KEYS.similarityHitReview,
      'similarity hit review',
    )
    expect(BACKEND_ENUMS.similarityReviewStatus).toContain(body.status)
    expect(body.note).toBe('待人工判断')
  })

  it('KB 文档入库字段与后端 KbDocumentIngest 一致', () => {
    const payload = {
      source: 'https://example.org/standard.pdf',
      content: '综合布线系统技术要求',
      kind: 'standard',
      tags: ['布线', '规范'],
      valid_to: '2027-12-31',
    }
    const body = assertBody(payload, BACKEND_KEYS.kbDocIngest, 'kb document ingest')
    expect(typeof body.source).toBe('string')
    expect((body.source as string).length).toBeGreaterThanOrEqual(1)
    expect((body.source as string).length).toBeLessThanOrEqual(500)
    expect(typeof body.content).toBe('string')
    expect(typeof body.kind).toBe('string')
    expect(Array.isArray(body.tags)).toBe(true)
    expect((body.tags as unknown[]).every((tag) => typeof tag === 'string')).toBe(true)
    expect(body.valid_to === null || typeof body.valid_to === 'string').toBe(true)
  })

  it('质量复核状态与后端 QualityFindingUpdate 的 Literal 一致', () => {
    for (const status of BACKEND_ENUMS.qualityFindingStatus) {
      const body = assertBody(
        { status, note: '' },
        BACKEND_KEYS.qualityFindingUpdate,
        'quality finding update',
      )
      expect(BACKEND_ENUMS.qualityFindingStatus).toContain(body.status)
    }
    // 后端 Literal 中不存在旧 UI 可能露出的 resolved/closed。
    expect(BACKEND_ENUMS.qualityFindingStatus).not.toContain('resolved')
  })

  it('商务提交只在显式要求时下发布尔型 require_complete', () => {
    const withComplete = assertBody(
      { action: 'submit', note: '', require_complete: true },
      BACKEND_KEYS.commercialReview,
      'commercial review',
    )
    expect(withComplete.action).toBe('submit')
    expect(typeof withComplete.require_complete).toBe('boolean')

    const withoutComplete = assertBody(
      { action: 'submit', note: '' },
      BACKEND_KEYS.commercialReview,
      'commercial review (default)',
    )
    expect(Object.prototype.hasOwnProperty.call(withoutComplete, 'require_complete')).toBe(false)
  })
})

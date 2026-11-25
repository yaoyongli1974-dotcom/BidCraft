import { afterEach, describe, expect, it, vi } from 'vitest'

import { generateChapterPlan, updateChapterPlan } from './generation'
import { reviewEnterpriseKnowledgeAsset, resolveEnterpriseKnowledgeAssetDuplicate } from './enterprise'
import { previewProjectRetrieval } from './retrieval'
import { resolveKnowledgeAssetDuplicate, reviewKnowledgeAsset } from './knowledge'
import { checkSimilarity, updateSimilarityHit } from './similarity'
import {
  getAgentTools,
  normalizeToolCallTrace,
  normalizeWorkflowRun,
  normalizeWorkflowTask,
} from './orchestration'

/**
 * 出站契约的 JS 镜像（只读引用后端声明，不修改后端）：
 * - SimilarityRequest: bidcraft/api/schemas.py:577-581
 * - scope/mode 真实取值: bidcraft/similarity/service.py:23-24、105-111
 * - REVIEW_STATUSES / SimilarityHitReview: service.py:26、schemas.py:584-586
 * - KbDocumentIngest: bidcraft/api/schemas.py:564-570
 * - QualityFindingUpdate: bidcraft/api/schemas.py:644-648
 * - CommercialReview: bidcraft/api/schemas.py:600-604
 * - ChapterPlanGenerateRequest / ChapterPlanUpdate: schemas.py:652-684
 * - KbAssetReview / KbDuplicateGroupResolve: schemas.py:691-702
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
  chapterPlanGenerate: ['user_instruction', 'package_id', 'regenerate', 'note'],
  chapterPlanUpdate: [
    'note',
    'package_id',
    'updates',
    'objective',
    'user_instruction',
    'score_points',
    'requirements',
    'conditions',
    'challenges',
    'strategy',
    'measures',
    'knowledge_refs',
    'fact_refs',
    'planned_tables',
    'planned_diagrams',
    'forbidden_assumptions',
    'quality_criteria',
    'user_adjustments',
  ],
  kbAssetReview: ['decision', 'note'],
  duplicateResolve: ['decision', 'note'],
  retrievalPreviewQuery: ['q', 'limit'],
  agentTool: ['name', 'description', 'input_schema', 'side_effect', 'readonly'],
  // 只读投影：轨迹字段严格来自后端，前端不得合成 ok / verified。
  toolCallTrace: ['name', 'args', 'result', 'verification', 'audit_id', 'ts', 'is_error', 'ms'],
} as const

const ASSET_DECISIONS = ['review', 'approve', 'publish', 'reject', 'archive'] as const
const DUPLICATE_DECISIONS = ['resolve', 'dismiss'] as const

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

  it('章节规划生成只提交后端接受的输入字段', async () => {
    const fetchMock = mockFetch({ id: 'plan-1', status: 'DRAFT' })

    await generateChapterPlan('p1', 'n1', {
      user_instruction: '突出交付能力',
      package_id: 'pkg-1',
    })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/projects/p1/chapters/n1/plan/generate')
    expect(init?.method).toBe('POST')
    const body = assertBody(
      JSON.parse(String(init?.body)),
      BACKEND_KEYS.chapterPlanGenerate,
      'chapter plan generate',
    )
    expect(body).toEqual({ user_instruction: '突出交付能力', package_id: 'pkg-1' })
    expect(Object.prototype.hasOwnProperty.call(body, 'regenerate')).toBe(false)
  })

  it('章节规划更新的扁平字段与 updates 字典都在后端允许集合内', async () => {
    const fetchMock = mockFetch({ id: 'plan-1', status: 'UPDATED' })

    await updateChapterPlan('p1', 'n1', {
      updates: { objective: '新目标', strategy: ['突出交付能力'] },
      package_id: 'pkg-2',
    })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/projects/p1/chapters/n1/plan')
    expect(init?.method).toBe('PUT')
    const body = assertBody(
      JSON.parse(String(init?.body)),
      BACKEND_KEYS.chapterPlanUpdate,
      'chapter plan update',
    )
    expect(body.updates).toEqual({ objective: '新目标', strategy: ['突出交付能力'] })
    expect(body.objective).toBe('新目标')
    expect(body.package_id).toBe('pkg-2')
    expect(Object.prototype.hasOwnProperty.call(body, 'regenerate')).toBe(false)
  })

  it('知识资产审核与重复组裁决只提交 decision / note', async () => {
    const fetchMock = mockFetch({ id: 'g-1', status: 'pending' })

    await reviewKnowledgeAsset('kb-1', 'd1', { decision: 'approve', note: '材料有效' })
    const [adminUrl, adminInit] = fetchMock.mock.calls[0]
    expect(adminUrl).toBe('/admin/kbs/kb-1/assets/d1/review')
    const reviewBody = assertBody(
      JSON.parse(String(adminInit?.body)),
      BACKEND_KEYS.kbAssetReview,
      'kb asset review',
    )
    expect(ASSET_DECISIONS).toContain(reviewBody.decision)
    expect(reviewBody.note).toBe('材料有效')

    await reviewEnterpriseKnowledgeAsset('e-1', 'd1', { decision: 'publish', note: '' })
    const [enterpriseUrl, enterpriseInit] = fetchMock.mock.calls[1]
    expect(enterpriseUrl).toBe('/enterprise/e-1/assets/d1/review')
    assertBody(
      JSON.parse(String(enterpriseInit?.body)),
      BACKEND_KEYS.kbAssetReview,
      'enterprise asset review',
    )

    await resolveKnowledgeAssetDuplicate('kb-1', 'g-1', { decision: 'dismiss', note: '忽略' })
    const draftResolve = assertBody(
      JSON.parse(String(fetchMock.mock.calls[2][1]?.body)),
      BACKEND_KEYS.duplicateResolve,
      'duplicate resolve',
    )
    expect(DUPLICATE_DECISIONS).toContain(draftResolve.decision)

    await resolveEnterpriseKnowledgeAssetDuplicate('e-1', 'g-1', {
      decision: 'resolve',
      note: '',
    })
    const enterpriseResolve = assertBody(
      JSON.parse(String(fetchMock.mock.calls[3][1]?.body)),
      BACKEND_KEYS.duplicateResolve,
      'enterprise duplicate resolve',
    )
    expect(enterpriseResolve.note).toBe('')
  })

  it('检索预览请求只带 q / limit 两个查询参数', async () => {
    const fetchMock = mockFetch({ items: [] })

    await previewProjectRetrieval('p 1', '  布线规范  ', 3)

    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe(
      `/projects/p%201/retrieval/preview?q=${encodeURIComponent('布线规范')}&limit=3`,
    )
    const params = new URL(url, 'http://localhost').searchParams
    expect(Array.from(params.keys()).every((key) => BACKEND_KEYS.retrievalPreviewQuery.includes(key as (typeof BACKEND_KEYS.retrievalPreviewQuery)[number]))).toBe(true)
    expect(params.get('q')).toBe('布线规范')
    expect(params.get('limit')).toBe('3')

    await expect(previewProjectRetrieval('p1', '   ')).rejects.toThrow('检索内容不能为空')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('工具目录只发 GET，带项目 ref 时按项目上下文读取', async () => {
    const fetchMock = mockFetch({
      agent_type: 'technical',
      tools: [
        {
          name: 'get_ledger',
          description: '读取台账',
          input_schema: { type: 'object' },
          side_effect: 'none',
          readonly: true,
        },
      ],
    })

    const tools = await getAgentTools('p 1', 'technical')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/agents/technical/tools?ref=p%201')
    expect(init?.method).toBeUndefined()
    expect(Object.keys(tools[0]).every((key) => BACKEND_KEYS.agentTool.includes(key as never))).toBe(true)
    expect(tools[0].readonly).toBe(true)

    await getAgentTools('technical')
    expect(fetchMock.mock.calls[1][0]).toBe('/agents/technical/tools')
  })

  it('工具轨迹严格使用后端字段，缺失 verification 不推断成功', () => {
    const raw = {
      name: 'get_project',
      args: { ref: 'p1' },
      result: { id: 'p1' },
      verification: null,
      audit_id: null,
      ts: null,
      is_error: false,
      ms: 4,
      injected: 'must-not-leak',
      ok: true,
    }
    const call = normalizeToolCallTrace(raw)
    expect(call).not.toBeNull()
    expect(Object.keys(call!).every((key) => BACKEND_KEYS.toolCallTrace.includes(key as never))).toBe(true)
    expect(call!.verification).toBeNull()
    expect(call!.audit_id).toBeNull()
    expect('ok' in call!).toBe(false)
    expect('injected' in call!).toBe(false)

    expect(normalizeToolCallTrace({ name: 'x', verification: 'failed' })!.verification).toBe('failed')
    expect(normalizeToolCallTrace('not-an-object')).toBeNull()
    expect(normalizeToolCallTrace({ args: {} })).toBeNull()
  })

  it('工作流与任务归一化都携带 tool_calls，脏值回退空数组', () => {
    const trace = { name: 'run_check', args: {}, verification: 'verified', audit_id: 'a1' }
    const run = normalizeWorkflowRun({
      id: 'run-1',
      status: 'done',
      tool_calls: [trace],
    })!
    const task = normalizeWorkflowTask({
      id: 't1',
      seq: 1,
      task_key: 'check',
      agent_type: 'compliance',
      output: { tool_calls: [trace] },
    })!
    expect(Object.keys(run.tool_calls![0]).every((key) => BACKEND_KEYS.toolCallTrace.includes(key as never))).toBe(true)
    expect(run.tool_calls![0].verification).toBe('verified')
    expect(task.tool_calls![0].audit_id).toBe('a1')
    expect(normalizeWorkflowTask({ id: 't2', output: { tool_calls: 'bad' } })!.tool_calls).toEqual([])
    expect(normalizeWorkflowRun({ id: 'run-2', result: { tool_calls: 42 } })!.tool_calls).toEqual([])
  })
})

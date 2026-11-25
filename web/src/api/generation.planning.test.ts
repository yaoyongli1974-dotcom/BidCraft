import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, request } from '../lib/http'
import {
  confirmChapterPlan,
  generateChapterPlan,
  getChapterPlan,
  listChapterPlanHistory,
  normalizeChapterPlan,
  regenerateChapterPlan,
  updateChapterPlan,
} from './generation'

vi.mock('../lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/http')>()
  return { ...actual, request: vi.fn() }
})

const rawPlan = {
  id: 'plan-1',
  project_id: 'p1',
  node_id: 'n1',
  version: 2,
  status: 'DRAFT',
  目标: '提升技术响应',
  评分点: [{ text: '交付及时', 分值: 5 }],
  策略: ['突出交付能力'],
  校验错误: ['缺少工期依据'],
}

const planPath = '/projects/p1/chapters/n1/plan'

describe('chapter planning client', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(request).mockResolvedValue({ plan: rawPlan, mode: 'review' })
  })

  it('normalizes Chinese aliases and keeps real values', () => {
    const plan = normalizeChapterPlan(rawPlan)!
    expect(plan.id).toBe('plan-1')
    expect(plan.objective).toBe('提升技术响应')
    expect(plan.status).toBe('DRAFT')
    expect(plan.version).toBe(2)
    expect(plan.score_points).toEqual([{ text: '交付及时', 分值: 5 }])
    expect(plan.strategy).toEqual(['突出交付能力'])
    expect(plan.validation_errors).toEqual(['缺少工期依据'])
    // 后端没给的字段保持 undefined，不补造内容。
    expect(plan.measures).toBeUndefined()
    expect(plan.confirmed_at).toBeUndefined()
  })

  it('returns null for an empty plan without inventing a draft', () => {
    expect(normalizeChapterPlan({})).toBeNull()
    expect(normalizeChapterPlan(null)).toBeNull()
  })

  it('getChapterPlan reads the exact contract path and mode', async () => {
    const result = await getChapterPlan('p1', 'n1')
    expect(request).toHaveBeenCalledWith(planPath)
    expect(result.mode).toBe('review')
    expect(result.plan?.node_id).toBe('n1')

    vi.mocked(request).mockResolvedValueOnce({ plan: null, mode: 'auto' })
    const empty = await getChapterPlan('p1', 'n1')
    expect(empty).toEqual({ plan: null, mode: 'auto' })
  })

  it('generateChapterPlan posts instruction and package id', async () => {
    const plan = await generateChapterPlan('p1', 'n1', {
      user_instruction: '突出业绩',
      package_id: 'pkg-1',
    })
    expect(request).toHaveBeenCalledWith(`${planPath}/generate`, {
      method: 'POST',
      body: JSON.stringify({ user_instruction: '突出业绩', package_id: 'pkg-1' }),
    })
    expect(plan.objective).toBe('提升技术响应')
  })

  it('generateChapterPlan falls back to POST /plan when /generate is unavailable', async () => {
    vi.mocked(request)
      .mockRejectedValueOnce(new ApiError(404, null, 'Not Found'))
      .mockResolvedValueOnce({ plan: rawPlan })
    const plan = await generateChapterPlan('p1', 'n1')
    expect(request).toHaveBeenNthCalledWith(2, planPath, { method: 'POST', body: '{}' })
    expect(plan.id).toBe('plan-1')
  })

  it('regenerateChapterPlan posts an empty body when no options are given', async () => {
    await regenerateChapterPlan('p1', 'n1')
    expect(request).toHaveBeenCalledWith(`${planPath}/regenerate`, {
      method: 'POST',
      body: '{}',
    })
  })

  it('updateChapterPlan PUTs the updates dictionary', async () => {
    const updates = { strategy: ['突出交付能力'], objective: '新的目标' }
    await updateChapterPlan('p1', 'n1', { updates, package_id: 'pkg-2' })
    expect(request).toHaveBeenCalledWith(planPath, {
      method: 'PUT',
      body: JSON.stringify({
        updates,
        package_id: 'pkg-2',
        strategy: updates.strategy,
        objective: updates.objective,
      }),
    })
  })

  it('confirmChapterPlan posts an optional note', async () => {
    await confirmChapterPlan('p1', 'n1', { note: '已复核' })
    expect(request).toHaveBeenCalledWith(`${planPath}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ note: '已复核' }),
    })
    vi.mocked(request).mockClear()
    await confirmChapterPlan('p1', 'n1')
    expect(request).toHaveBeenCalledWith(`${planPath}/confirm`, {
      method: 'POST',
      body: '{}',
    })
  })

  it('listChapterPlanHistory normalizes every valid plan and drops malformed entries', async () => {
    vi.mocked(request).mockResolvedValueOnce({
      plans: [rawPlan, {}, { id: 'plan-2', status: 'CONFIRMED' }],
    })
    const plans = await listChapterPlanHistory('p1', 'n1')
    expect(request).toHaveBeenCalledWith(`${planPath}/history`)
    expect(plans).toHaveLength(2)
    expect(plans[0].version).toBe(2)
    expect(plans[1].status).toBe('CONFIRMED')
  })

  it('rejects a mutation response without a usable plan', async () => {
    vi.mocked(request).mockResolvedValueOnce({})
    await expect(generateChapterPlan('p1', 'n1')).rejects.toThrow('章节规划响应缺少有效数据')
  })
})

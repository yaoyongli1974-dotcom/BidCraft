import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/generation', () => ({
  getChapterPlan: vi.fn(),
  generateChapterPlan: vi.fn(),
  confirmChapterPlan: vi.fn(),
}))

import {
  batchConfirmPlans,
  loadChapterPlans,
  missingPlanNodeIds,
} from './useChapterPlans'
import { getChapterPlan, confirmChapterPlan } from '../api/generation'

const confirmedPlan = {
  id: 'plan-1',
  project_id: 'p1',
  node_id: 'n1',
  version: 1,
  status: 'CONFIRMED' as const,
}

describe('chapter plan batch helpers', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('keeps successful entries when one chapter lookup fails', async () => {
    vi.mocked(getChapterPlan)
      .mockResolvedValueOnce({ plan: confirmedPlan, mode: 'review' })
      .mockRejectedValueOnce(new Error('网络中断'))
      .mockResolvedValueOnce({ plan: null, mode: 'review' })

    const entries = await loadChapterPlans('p1', ['n1', 'n2', 'n3'])
    expect(entries.map((item) => item.node_id)).toEqual(['n1', 'n3'])
    expect(entries[0].plan?.status).toBe('CONFIRMED')
  })

  it('treats a confirm reply that is still not confirmed as a failure', async () => {
    vi.mocked(confirmChapterPlan).mockResolvedValueOnce({
      ...confirmedPlan,
      status: 'DRAFT',
    })

    const outcome = await batchConfirmPlans('p1', ['n1'], '', vi.fn())
    expect(outcome.succeeded).toEqual([])
    expect(outcome.failed).toHaveLength(1)
    expect(outcome.failed[0].message).toContain('尚未确认')
  })

  it('includes chapters with no active plan in batch generate targets', () => {
    const targets = missingPlanNodeIds(
      [{ id: 'n1' }, { id: 'n2' }],
      [{ node_id: 'n1', plan: confirmedPlan }],
    )
    expect(targets).toEqual(['n2'])
  })
})

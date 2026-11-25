import { describe, expect, it } from 'vitest'
import {
  KNOWLEDGE_ASSET_DECISIONS,
  KNOWLEDGE_ASSET_DECISION_META,
  KNOWLEDGE_ASSET_TRANSITIONS,
  allowedNextStatuses,
  isDecisionAllowed,
  knowledgeAssetStatus,
  knowledgeAssetStatusMeta,
} from './knowledgeAssetMeta'
import type { KnowledgeAssetReviewStatus } from './types'

describe('知识资产状态元数据', () => {
  it('状态机与后端 VALID_TRANSITIONS 一致', () => {
    expect(KNOWLEDGE_ASSET_TRANSITIONS.draft).toEqual(['review', 'rejected'])
    expect(KNOWLEDGE_ASSET_TRANSITIONS.review).toEqual(['approved', 'rejected', 'draft'])
    expect(KNOWLEDGE_ASSET_TRANSITIONS.approved).toEqual(['published', 'rejected'])
    expect(KNOWLEDGE_ASSET_TRANSITIONS.published).toEqual(['archived'])
    expect(KNOWLEDGE_ASSET_TRANSITIONS.rejected).toEqual(['draft'])
    expect(KNOWLEDGE_ASSET_TRANSITIONS.archived).toEqual(['draft'])
  })

  it('每个动作的目标状态都在当前状态允许的下一状态集合内', () => {
    const statusList: KnowledgeAssetReviewStatus[] = [
      'draft',
      'review',
      'approved',
      'published',
      'rejected',
      'archived',
    ]
    for (const status of statusList) {
      for (const decision of KNOWLEDGE_ASSET_DECISIONS) {
        const meta = KNOWLEDGE_ASSET_DECISION_META[decision]
        expect(
          isDecisionAllowed(status, decision),
          `${status} -> ${decision}`,
        ).toBe(KNOWLEDGE_ASSET_TRANSITIONS[status].includes(meta.target))
      }
    }
  })

  it('非法流转禁用，null 按后端口径落到草稿的流转集合', () => {
    expect(isDecisionAllowed('draft', 'review')).toBe(true)
    expect(isDecisionAllowed('draft', 'approve')).toBe(false)
    expect(isDecisionAllowed('published', 'archive')).toBe(true)
    expect(isDecisionAllowed('published', 'review')).toBe(false)
    expect(isDecisionAllowed('rejected', 'review')).toBe(false)
    expect(isDecisionAllowed(null, 'review')).toBe(true)
    expect(allowedNextStatuses(null)).toEqual(KNOWLEDGE_ASSET_TRANSITIONS.draft)
    expect(isDecisionAllowed('draft', undefined)).toBe(false)
  })

  it('未知/缺失状态不冒充已审核状态', () => {
    expect(knowledgeAssetStatus(null)).toBeNull()
    expect(knowledgeAssetStatus('surprise')).toBeNull()
    expect(knowledgeAssetStatus(' PUBLISHED ')).toBe('published')
    expect(knowledgeAssetStatusMeta(null)).toMatchObject({ label: '未审核', tone: 'neutral' })
    expect(knowledgeAssetStatusMeta('published').tone).toBe('success')
    expect(knowledgeAssetStatusMeta('review').label).toBe('待审核')
    expect(knowledgeAssetStatusMeta('surprise')).toMatchObject({
      label: 'surprise',
      tone: 'neutral',
    })
  })
})

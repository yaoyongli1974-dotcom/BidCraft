import { describe, expect, it } from 'vitest'
import {
  actionLabel,
  formatMoney,
  formatWords,
  normalizeProjectType,
  percent,
  projectTypeLabel,
  projectTypeMeta,
  kbVectorMeta,
} from './format'
import type { KnowledgeBase } from './types'

describe('project type helpers', () => {
  it('falls back to the "other" package for unknown or empty values', () => {
    expect(normalizeProjectType(undefined)).toBe('other')
    expect(normalizeProjectType('  UNKNOWN ')).toBe('other')
    expect(projectTypeMeta('weakcurrent').label).toBe('弱电智能化')
    expect(projectTypeMeta('nope').id).toBe('other')
  })

  it('keeps a server-provided label only for the generic "other" type', () => {
    expect(projectTypeLabel('other', '智慧园区')).toBe('智慧园区')
    expect(projectTypeLabel('fire', '不该出现')).toBe('消防工程')
    expect(projectTypeLabel(null)).toBe('其他')
  })
})

describe('actionLabel', () => {
  it('maps known audit keys and preserves unknown ones', () => {
    expect(actionLabel('project.create')).toBe('新建项目')
    expect(actionLabel('boq.quota_fill')).toBe('套用企业定额')
    expect(actionLabel('future.action')).toBe('future.action')
    expect(actionLabel()).toBe('操作')
  })
})

describe('formatMoney', () => {
  it('formats yuan with grouping and fixed decimals', () => {
    expect(formatMoney(1234.5)).toBe('1,234.50')
    expect(formatMoney(0)).toBe('0.00')
    expect(formatMoney(12.3456, 3)).toBe('12.346')
    expect(formatMoney(12.3456, 9)).toBe('12.3456')
  })

  it('returns a dash for missing or non-finite values', () => {
    expect(formatMoney(null)).toBe('—')
    expect(formatMoney(Number.NaN)).toBe('—')
    expect(formatMoney(undefined)).toBe('—')
  })
})

describe('formatWords and percent', () => {
  it('switches to 万字 above ten thousand characters', () => {
    expect(formatWords(999)).toBe('999 字')
    expect(formatWords(10_000)).toBe('1 万字')
    expect(formatWords(12_500)).toBe('1.3 万字')
    expect(formatWords(undefined)).toBe('—')
  })

  it('clamps percentages into 0–100 and survives a zero total', () => {
    expect(percent(1, 4)).toBe(25)
    expect(percent(9, 4)).toBe(100)
    expect(percent(2, 0)).toBe(0)
  })
})

describe('kbVectorMeta', () => {
  const kb = {
    id: 'kb-1',
    name: '知识库',
    kind: 'local',
    provider: 'local',
    location: '',
    enabled: true,
    is_default: false,
    has_api_key: false,
    chunk_count: 4,
    vector_enabled: true,
    vector_model: 'emb-test',
    vector_embedded: 2,
    vector_total: 2,
    vector_excluded: 2,
    vector_ready: true,
    vector_stale: false,
  } as KnowledgeBase

  it('使用可发布块口径，并标注被排除的草稿/驳回块', () => {
    expect(kbVectorMeta(kb)).toEqual({
      label: '语义就绪（未发布 2 不计入）',
      tone: 'success',
    })
  })
})

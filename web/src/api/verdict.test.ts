import { describe, expect, it } from 'vitest'

import { normalizeRequirement } from './shared'

describe('五层判定字段透传', () => {
  const base = {
    id: 'r1',
    text: '★ 传输带宽不小于1000Mbps',
    marker: '★',
    mandatory: true,
    source_quote: '★ …',
    response_status: 'pending',
  }

  it('透传四态判定、页码与条款编号', () => {
    const row = normalizeRequirement({
      ...base,
      page: 21,
      clause_no: '4.1.2',
      verdict: 'unsatisfied',
      verdict_label: '不满足',
      verdict_reason: '数值/等级/否定语义不符合要求：至少 1000Mbps',
      verdict_level: 'blocking',
      verdict_layers: { layer1: { page: 21 }, layer5: { state: 'unsatisfied' } },
    })
    expect(row).not.toBeNull()
    expect(row!.page).toBe(21)
    expect(row!.clause_no).toBe('4.1.2')
    expect(row!.verdict).toBe('unsatisfied')
    expect(row!.verdict_label).toBe('不满足')
    expect(row!.verdict_reason).toContain('1000')
    expect(row!.verdict_layers?.layer5).toEqual({ state: 'unsatisfied' })
  })

  it('未知判定值按 undefined 处理，不破坏原状态展示', () => {
    const row = normalizeRequirement({ ...base, verdict: 'weird-value' })
    expect(row!.verdict).toBeUndefined()
    expect(row!.response_status).toBe('pending')
  })

  it('旧后端缺字段时保持兼容', () => {
    const row = normalizeRequirement(base)
    expect(row!.verdict).toBeUndefined()
    expect(row!.page).toBe(0)
    expect(row!.clause_no).toBe('')
  })
})

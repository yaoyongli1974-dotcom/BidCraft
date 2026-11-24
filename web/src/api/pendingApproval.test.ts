import { describe, expect, it } from 'vitest'

import { normalizePendingApproval } from './shared'

describe('normalizePendingApproval', () => {
  it('保留商务标待办标识以便跳转商务标页签', () => {
    const item = normalizePendingApproval({
      project_id: 'p1',
      project_name: '项目甲',
      version_id: 'c1',
      version_name: '商务标 · 终版',
      doc_id: 'c1',
      doc_name: '终版',
      stage: 1,
      stage_count: 1,
      mode: '',
      submitted_by: '张三',
      submitted_at: '2026-11-24T09:00:00+08:00',
      total_amount: 100,
    })

    expect(item?.doc_id).toBe('c1')
    expect(item?.doc_name).toBe('终版')
  })

  it('旧报价待办没有商务标字段时保持 undefined', () => {
    const item = normalizePendingApproval({
      project_id: 'p1',
      version_id: 'v1',
    })

    expect(item?.doc_id).toBeUndefined()
    expect(item?.doc_name).toBeUndefined()
  })
})

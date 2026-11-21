import { describe, expect, it } from 'vitest'

import { normalizeRequirement } from './shared'

describe('normalizeRequirement 条款类型透传', () => {
  it('保留服务端下发的条款类型与中文标签', () => {
    const item = normalizeRequirement({
      id: 'r1',
      text: '★ 需一级建造师资质证书',
      marker: '★',
      requirement_type: 'qualification',
      requirement_type_label: '资质资格',
      response_status: 'pending',
    })
    expect(item?.requirement_type).toBe('qualification')
    expect(item?.requirement_type_label).toBe('资质资格')
  })

  it('旧后端无该字段时保持为空，不影响解析', () => {
    const item = normalizeRequirement({ id: 'r2', text: '某条款', response_status: 'na' })
    expect(item?.requirement_type).toBeUndefined()
    expect(item?.response_status).toBe('na')
  })
})

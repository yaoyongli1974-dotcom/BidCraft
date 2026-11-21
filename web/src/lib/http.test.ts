import { describe, expect, it } from 'vitest'

import { ApiError, chapterConflict, errorDetailText, messageOf } from './http'

describe('unified error handling', () => {
  it('ApiError 从 detail 提取消息并保留 payload / missing_required', () => {
    const err = new ApiError(
      400,
      { missing_required: ['工期', '质保金', '项目经理', '业绩', '报价'] },
      undefined,
      { extra: 1 },
    )
    expect(err.message).toContain('还缺 5 项必填事实')
    expect(err.message).toContain('工期、质保金、项目经理、业绩')
    expect(err.missingRequired).toEqual(['工期', '质保金', '项目经理', '业绩', '报价'])
    expect(err.payload).toEqual({ extra: 1 })
  })

  it('errorDetailText 兼容字符串 / FastAPI 校验数组 / 未知对象', () => {
    expect(errorDetailText('直接消息')).toBe('直接消息')
    expect(errorDetailText([{ msg: '字段必填' }, '另一条'])).toBe('字段必填；另一条')
    expect(errorDetailText({ detail: '嵌套详情' })).toBe('嵌套详情')
    expect(errorDetailText(null)).toBe('')
  })

  it('messageOf 对 ApiError / Error / 未知值给统一文案', () => {
    expect(messageOf(new ApiError(409, '冲突'))).toBe('冲突')
    expect(messageOf(new Error('网络错误'))).toBe('网络错误')
    expect(messageOf('奇怪的东西')).toBe('发生未知错误，请重试')
  })
})

describe('chapterConflict（并发编辑冲突解析）', () => {
  it('从 payload 提取乐观锁字段', () => {
    const err = new ApiError(
      409,
      '章节已被他人修改',
      undefined,
      { current_revision: 7, current_body: '服务器正文', current_words: 12, detail: '已更新' },
    )
    expect(chapterConflict(err)).toEqual({
      current_revision: 7,
      current_body: '服务器正文',
      current_words: 12,
      message: '已更新',
    })
  })

  it('老代理只透传 detail 时也能解析', () => {
    const err = new ApiError(409, { current_revision: 3, current_body: '旧正文' })
    expect(chapterConflict(err)?.current_revision).toBe(3)
    expect(chapterConflict(err)?.current_body).toBe('旧正文')
  })

  it('非 409 / 非 ApiError / 无 rev 字段都不算冲突', () => {
    expect(chapterConflict(new ApiError(500, '服务器错误'))).toBeNull()
    expect(chapterConflict(new Error('普通错误'))).toBeNull()
    expect(chapterConflict(new ApiError(409, '没有 rev'))).toBeNull()
  })
})

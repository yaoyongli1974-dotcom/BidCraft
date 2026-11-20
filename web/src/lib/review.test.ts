import { describe, expect, it } from 'vitest'

import {
  isChapterExportable,
  isChapterFrozen,
  reviewDoneMessage,
  reviewMeta,
  reviewStatus,
  reviewSteps,
} from './review'

describe('chapter review status', () => {
  it('未知或缺失状态一律按草稿处理', () => {
    expect(reviewStatus(undefined)).toBe('draft')
    expect(reviewStatus('')).toBe('draft')
    expect(reviewStatus('whatever')).toBe('draft')
    expect(reviewMeta('locked').label).toBe('已锁定')
  })

  it('已确认与已锁定才算冻结、才算可导出', () => {
    expect(isChapterFrozen('draft')).toBe(false)
    expect(isChapterFrozen('review')).toBe(false)
    expect(isChapterFrozen('confirmed')).toBe(true)
    expect(isChapterFrozen('locked')).toBe(true)
    expect(isChapterExportable('review')).toBe(false)
    expect(isChapterExportable('locked')).toBe(true)
  })

  it('未确认状态的提示语说明「改动会退回草稿」', () => {
    expect(reviewMeta('review').hint).toContain('草稿')
  })

  it('草稿只有提交复核一个动作', () => {
    expect(reviewSteps('draft', false).map((step) => step.action)).toEqual(['submit'])
    expect(reviewSteps('draft', true).map((step) => step.action)).toEqual(['submit'])
  })

  it('待复核时编辑只能撤回，负责人可以确认', () => {
    expect(reviewSteps('review', false).map((step) => step.action)).toEqual(['revert'])
    const owner = reviewSteps('review', true)
    expect(owner.map((step) => step.action)).toEqual(['confirm', 'revert'])
    expect(owner.find((step) => step.action === 'revert')?.needsNote).toBe(true)
  })

  it('已确认时负责人可锁定或撤回，编辑只能等负责人', () => {
    expect(reviewSteps('confirmed', false).map((step) => step.action)).toEqual(['revert'])
    const owner = reviewSteps('confirmed', true)
    expect(owner.map((step) => step.action)).toEqual(['lock', 'revert'])
    expect(owner.find((step) => step.action === 'revert')?.needsNote).toBe(true)
  })

  it('已锁定后只有负责人能解锁', () => {
    expect(reviewSteps('locked', false)).toEqual([])
    const owner = reviewSteps('locked', true)
    expect(owner.map((step) => step.action)).toEqual(['unlock'])
    expect(owner[0].needsNote).toBe(true)
  })

  it('流转完成提示与动作用语一致', () => {
    expect(reviewDoneMessage('confirm')).toBe('已确认本章')
    expect(reviewDoneMessage('unlock')).toContain('已确认')
  })
})

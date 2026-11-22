import { describe, expect, it } from 'vitest'

import { normalizeCommercialDoc, normalizeCommercialValidation } from './commercial'
import { normalizeSimilarityReport } from './similarity'

/**
 * 对接真实后端形状：第二批后端 lane 的返回结构与前端契约有若干差异，
 * 这些测试锁住前端 normalizer 能正确消费后端实际输出（而不只是前端假设的形状）。
 */
describe('backend shape reconciliation', () => {
  it('commercial: 字典形式的 sections 转为章节数组并生成内容摘要', () => {
    const doc = normalizeCommercialDoc({
      id: 'd1',
      name: '商务标',
      status: 'draft',
      sections: {
        bid_letter: {
          title: '投标函',
          fields: [
            { label: '项目编号', value: 'ZB-001' },
            { label: '投标报价', value: '100.00' },
          ],
          rows: [],
        },
        opening_sheet: { title: '开标一览表', fields: [], rows: [{ item: '分项A' }] },
      },
    })
    expect(doc.sections).toHaveLength(2)
    expect(doc.sections[0].title).toBe('投标函')
    expect(doc.sections[0].content).toContain('ZB-001')
    expect(doc.sections[0].content).toContain('100.00')
    expect(doc.sections[1].content).toContain('分项A')
  })

  it('commercial: 兼容数组形式的 sections（原前端契约）', () => {
    const doc = normalizeCommercialDoc({
      id: 'd2',
      sections: [{ key: 'a', title: '投标函', content: '内容', status: 'ok' }],
    })
    expect(doc.sections).toHaveLength(1)
    expect(doc.sections[0].title).toBe('投标函')
  })

  it('commercial: validation.consistency.errors 映射为校验问题并 ok=false', () => {
    const validation = normalizeCommercialValidation({
      consistency: { ok: false, errors: ['报价与版本不一致'], warnings: ['请复核税率'] },
      completeness: { ok: false, missing: ['工期'], checked: 8 },
      quote: {},
    })
    expect(validation.ok).toBe(false)
    expect(validation.issues).toHaveLength(3)
    expect(validation.issues[0].message).toBe('报价与版本不一致')
    expect(validation.issues[0].severity).toBe('error')
    expect(validation.issues.some((i) => i.message.includes('工期'))).toBe(true)
  })

  it('commercial: 即时 payload 的 {ok, errors} 形状仍兼容', () => {
    const validation = normalizeCommercialValidation({ ok: false, errors: ['字段缺失'] })
    expect(validation.ok).toBe(false)
    expect(validation.issues[0].message).toBe('字段缺失')
  })

  it('similarity: skipped 中的 {mode, reason} 对象转为可读文本', () => {
    const report = normalizeSimilarityReport({
      id: 'r1',
      status: 'done',
      skipped: [
        { mode: 'semantic', reason: '模型未配置' },
        { mode: 'image', path: 'p1', reason: '无可查重图片' },
      ],
      stats: { hits: 3, by_kind: { text: 3 } },
    })
    expect(Array.isArray(report.skipped)).toBe(true)
    const skipped = report.skipped as string[]
    expect(skipped[0]).toContain('semantic')
    expect(skipped[0]).toContain('模型未配置')
    expect(skipped[1]).toContain('无可查重图片')
    expect(report.stats.hits).toBe(3)
  })

  it('similarity: skipped 为数字或字符串时仍兼容', () => {
    expect(normalizeSimilarityReport({ skipped: 5 }).skipped).toBe(5)
    expect(normalizeSimilarityReport({ skipped: ['空文档'] }).skipped).toEqual(['空文档'])
  })
})

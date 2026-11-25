import { describe, expect, it } from 'vitest'
import {
  normalizeKnowledgeRef,
  normalizeKnowledgeRefs,
  normalizeRetrievalTrace,
  traceBool,
  traceDiagnostics,
  traceNumber,
  traceText,
  trustTierLabel,
  trustTierTone,
} from './retrievalTrace'

describe('检索溯源归一化', () => {
  it('可空数值 / 布尔 / 文本保留真实值，null 与 undefined 不伪装成 0 / trusted', () => {
    expect(traceNumber(0)).toBe(0)
    expect(traceNumber('0.42')).toBe(0.42)
    expect(traceNumber(Number.NaN)).toBeNull()
    expect(traceNumber(null)).toBeNull()
    expect(traceNumber(undefined)).toBeNull()
    expect(traceBool(true)).toBe(true)
    expect(traceBool(false)).toBe(false)
    expect(traceBool('true')).toBe(true)
    expect(traceBool(null)).toBeNull()
    expect(traceBool(undefined)).toBeNull()
    expect(traceText('  完整  ')).toBe('  完整  ')
    expect(traceText('   ')).toBeNull()
    expect(traceText(null)).toBeNull()
    expect(traceDiagnostics({ searched: ['kb-1'] })).toEqual({ searched: ['kb-1'] })
    expect(traceDiagnostics(['降级说明'])).toEqual(['降级说明'])
    expect(traceDiagnostics(null)).toBeNull()
    expect(traceDiagnostics('bad')).toBeNull()
  })

  it('缺失的溯源字段全部为 null，重排分 0 与降级 false 是真实信号', () => {
    expect(normalizeRetrievalTrace({})).toEqual({
      rerank_score: null,
      trust_tier: null,
      degraded: null,
      chunk_id: null,
      chunk_index: null,
      diagnostics: null,
      score_source: null,
    })
    const real = normalizeRetrievalTrace({
      rerank_score: 0,
      trust_tier: 'excerpt',
      degraded: false,
      chunk_id: 'chunk-1',
      chunk_index: 0,
      diagnostics: { failed: [] },
    })
    expect(real.rerank_score).toBe(0)
    expect(real.degraded).toBe(false)
    expect(real.chunk_index).toBe(0)
    expect(real.trust_tier).toBe('excerpt')
  })

  it('兼容 tier / downgraded 别名，未知档位不回退成 trusted', () => {
    const trace = normalizeRetrievalTrace({ tier: 'suppressed', downgraded: true })
    expect(trace.trust_tier).toBe('suppressed')
    expect(trace.degraded).toBe(true)
    expect(trustTierLabel(null)).toBe('信任档位未知')
    expect(trustTierLabel('full')).toBe('全文注入')
    expect(trustTierLabel('custom')).toBe('custom')
    expect(trustTierTone(null)).toBe('neutral')
    expect(trustTierTone('suppressed')).toBe('danger')
  })

  it('知识引用保留真实 id 与块溯源；空引用与纯空白字符串被丢弃', () => {
    const refs = normalizeKnowledgeRefs([
      '知识库A',
      {
        id: 'chunk-9',
        kind: 'chunk',
        heading: '资质证书',
        source: 'cert.pdf',
        snippet: '证书编号 XYZ',
        rerank_score: 0.87,
        trust_tier: 'full',
        degraded: true,
        chunk_id: 'chunk-9',
        chunk_index: 3,
        diagnostics: { searched: ['kb-1'] },
      },
      { heading: '   ' },
      null,
      '   ',
    ])
    expect(refs).toHaveLength(2)
    expect(refs[0]).toBe('知识库A')
    expect(refs[1]).toMatchObject({
      id: 'chunk-9',
      kind: 'chunk',
      heading: '资质证书',
      rerank_score: 0.87,
      trust_tier: 'full',
      degraded: true,
      chunk_id: 'chunk-9',
      chunk_index: 3,
      diagnostics: { searched: ['kb-1'] },
    })
    expect(normalizeKnowledgeRef({ heading: '   ', source: null })).toBeNull()
    expect(normalizeKnowledgeRefs(undefined)).toEqual([])
  })
})

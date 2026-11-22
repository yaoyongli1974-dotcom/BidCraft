import { describe, expect, it } from 'vitest'
import { normalizeQualityFinding, normalizeQualityReport } from './evaluation'
import type { QualityReport } from '../lib/types'

const base: QualityReport = {
  id: 'r1', project_id: 'p1', report_version: 1, overall_score: 80, grade: 'B',
  verdict: 'pass', dimensions: [], summary: {}, risk_summary: {}, created_by: 'owner',
  created_at: null, findings: [],
}

describe('normalizeQualityReport', () => {
  it('normalizes full backend report', () => {
    const report = normalizeQualityReport({
      ...base,
      dimensions: [{ key: 'substantive', label: '实质性响应', score: 90, weight: 25, weighted: 22.5 }],
      summary: { coverage: { chapter: { ratio: 1 } }, recommendations: ['建议A'], note: 'n' },
      findings: [{ id: 'f1', category: '实质性条款', level: 'blocking', status: 'open' }],
    })
    expect(report).not.toBeNull()
    expect(report!.dimensions).toHaveLength(1)
    expect(report!.summary.recommendations).toEqual(['建议A'])
    expect(report!.findings[0].status).toBe('open')
  })

  it('handles missing/legacy fields without throwing', () => {
    const report = normalizeQualityReport({ id: 'r2', verdict: 'warn' })
    expect(report!.grade).toBe('C')
    expect(report!.dimensions).toEqual([])
    expect(report!.summary.recommendations).toEqual([])
  })

  it('rejects invalid response', () => {
    expect(normalizeQualityReport({})).toBeNull()
  })
})

describe('normalizeQualityFinding', () => {
  it('keeps evidence and nullable anchors', () => {
    const f = normalizeQualityFinding({ id: 'f1', evidence: { code: 'X' }, node_id: null })
    expect(f.evidence).toEqual({ code: 'X' })
    expect(f.node_id).toBeNull()
    expect(f.level).toBe('major')
  })
})

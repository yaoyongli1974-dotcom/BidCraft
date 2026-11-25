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

  it('keeps v0.31 quality metrics and marks real-tender data as unmeasured', () => {
    const report = normalizeQualityReport({
      ...base,
      summary: {
        metrics: {
          manual_edit_rate: 0.5,
          issue_density: 23.9234,
          boq_match_rate: '0.75',
          notes: ['word count note'],
        },
        tender_quality: {
          status: '未测量',
          reason: '真实招标文件数据集尚未就绪',
          clause_recall: null,
          value_parse_rate: null,
          verdict_agreement: null,
          files: [],
        },
      },
    })
    expect(report!.summary.metrics?.manual_edit_rate).toBe(0.5)
    expect(report!.summary.metrics?.boq_match_rate).toBe('0.75')
    expect(report!.summary.tender_quality?.status).toBe('未测量')
    expect(report!.summary.tender_quality?.clause_recall).toBeNull()
    expect(report!.summary.tender_quality?.reason).toContain('数据集尚未就绪')
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

  it('reads resolved_at from the finding or its evidence without inventing a time', () => {
    const topLevel = normalizeQualityFinding({
      id: 'f1',
      status: 'handled',
      resolved_by: 'alice',
      resolved_at: '2026-11-25T10:00:00Z',
    })
    expect(topLevel.resolved_at).toBe('2026-11-25T10:00:00Z')
    expect(topLevel.resolved_by).toBe('alice')

    const nested = normalizeQualityFinding({
      id: 'f2',
      status: 'ignored',
      evidence: { resolved_by: 'bob', resolved_at: '2026-11-24T09:00:00Z' },
    })
    expect(nested.resolved_by).toBe('bob')
    expect(nested.resolved_at).toBe('2026-11-24T09:00:00Z')

    const open = normalizeQualityFinding({ id: 'f3', status: 'open', resolved_at: null })
    expect(open.resolved_at).toBeNull()
  })
})

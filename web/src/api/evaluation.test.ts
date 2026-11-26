import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  exportEvaluationReport,
  getEvaluationReportDetail,
  getEvaluationSummary,
  normalizeEvaluationSummary,
  normalizeEvaluationSummaryItem,
  normalizeQualityFinding,
  normalizeQualityReport,
} from './evaluation'
import type { QualityReport } from '../lib/types'

const base: QualityReport = {
  id: 'r1', project_id: 'p1', report_version: 1, overall_score: 80, grade: 'B',
  verdict: 'pass', dimensions: [], summary: {}, risk_summary: {}, created_by: 'owner',
  created_at: null, findings: [],
}

function jsonFetch(payload: unknown) {
  const mock = vi.fn(
    async (_input: string, _init?: RequestInit) =>
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

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

  it('marks zero-denominator metrics unmeasured instead of showing 0.0%', () => {
    const report = normalizeQualityReport({
      ...base,
      summary: {
        metrics: {
          manual_edit_rate: 0,
          issue_density: 0,
          boq_match_rate: 0,
          measured: {
            manual_edit_rate: false,
            issue_density: false,
            boq_match_rate: false,
          },
        },
      },
    })
    expect(report!.summary.metrics?.manual_edit_rate).toBeNull()
    expect(report!.summary.metrics?.issue_density).toBeNull()
    expect(report!.summary.metrics?.boq_match_rate).toBeNull()
    expect(report!.summary.metrics?.measured).toEqual({
      manual_edit_rate: false,
      issue_density: false,
      boq_match_rate: false,
    })

    const measured = normalizeQualityReport({
      ...base,
      summary: { metrics: { manual_edit_rate: 0.75, measured: { manual_edit_rate: true } } },
    })
    expect(measured!.summary.metrics?.manual_edit_rate).toBe(0.75)
  })

  it('normalizes coverage/samples/mismatches details null-safe', () => {
    const report = normalizeQualityReport({
      ...base,
      summary: {
        metrics: {
          manual_edit_rate: 0.5,
          coverage: {
            manual_edit_rate: { numerator: 2, denominator: 4, sample_count: 2 },
            broken: null,
          },
          samples: { manual_edit_rate: [{ section_id: 's1' }, null], broken: 'x' },
          mismatches: [{ metric: 'issue_density', finding_id: 'f1' }, null],
        },
        tender_quality: {
          status: '未测量',
          coverage: null,
          samples: null,
          mismatches: null,
          files: [{ path: 'a.pdf', coverage: null, samples: null, mismatches: null }],
        },
      },
    })
    expect(report!.summary.metrics!.coverage!.manual_edit_rate).toEqual({
      numerator: 2, denominator: 4, sample_count: 2,
    })
    expect(report!.summary.metrics!.coverage!.broken).toEqual({})
    expect(report!.summary.metrics!.samples!.manual_edit_rate).toEqual([{ section_id: 's1' }])
    expect(report!.summary.metrics!.samples!.broken).toEqual([])
    expect(report!.summary.metrics!.mismatches).toEqual([
      { metric: 'issue_density', finding_id: 'f1' },
    ])
    expect(report!.summary.tender_quality!.coverage).toEqual({})
    expect(report!.summary.tender_quality!.samples).toEqual({})
    expect(report!.summary.tender_quality!.mismatches).toEqual([])
    expect(report!.summary.tender_quality!.files![0].coverage).toEqual({})
    expect(report!.summary.tender_quality!.files![0].samples).toEqual({})
    expect(report!.summary.tender_quality!.files![0].mismatches).toEqual([])
  })

  it('missing grade/verdict stay null instead of fake C/warn defaults', () => {
    const partial = normalizeQualityReport({ id: 'r2', verdict: 'warn' })
    expect(partial!.grade).toBeNull()
    expect(partial!.verdict).toBe('warn')
    expect(partial!.dimensions).toEqual([])
    expect(partial!.summary.recommendations).toEqual([])

    const bare = normalizeQualityReport({ id: 'r3' })
    expect(bare!.grade).toBeNull()
    expect(bare!.verdict).toBeNull()
  })

  it('rejects invalid response', () => {
    expect(normalizeQualityReport({})).toBeNull()
  })
})

describe('normalizeEvaluationSummaryItem', () => {
  it('defaults to unmeasured unless the backend explicitly says measured=true', () => {
    const item = normalizeEvaluationSummaryItem({
      ref: 'p1', project_id: 'p1', name: '项目一', overall_score: 99, grade: 'A', verdict: 'pass',
      report_id: 'r1', report_version: 3, created_at: '2026-11-25T10:00:00Z',
    })
    expect(item).toMatchObject({
      measured: false, report_id: 'r1', overall_score: null, grade: null,
      verdict: null, report_version: null,
    })

    const missingFlag = normalizeEvaluationSummaryItem({
      ref: 'p1', measured: undefined, report_id: null, overall_score: 88,
    })
    expect(missingFlag!.measured).toBe(false)
    expect(missingFlag!.overall_score).toBeNull()
  })

  it('keeps identity fields from the backend summary', () => {
    const item = normalizeEvaluationSummaryItem({
      project_id: 'p1', ref: ' p1 ', name: '项目一', measured: true, report_id: 'r9',
      report_version: 3, overall_score: 88.5, grade: 'B', verdict: 'pass',
      created_at: '2026-11-25T10:00:00Z',
    })
    expect(item).toMatchObject({
      ref: 'p1', name: '项目一', report_version: 3,
      created_at: '2026-11-25T10:00:00Z', measured: true,
    })
  })
})

describe('normalizeQualityFinding', () => {
  it('keeps evidence and nullable anchors', () => {
    const f = normalizeQualityFinding({ id: 'f1', evidence: { code: 'X' }, node_id: null })
    expect(f.evidence).toEqual({ code: 'X' })
    expect(f.node_id).toBeNull()
    expect(f.level).toBe('major')
  })

  it('reads stable keys and source row ids from the finding or evidence', () => {
    const topLevel = normalizeQualityFinding({
      id: 'f1', stable_key: 'sk-1', source_row_id: 'row-7',
      evidence: { code: 'C1', page: 3 },
    })
    expect(topLevel.stable_key).toBe('sk-1')
    expect(topLevel.source_row_id).toBe('row-7')
    expect(topLevel.evidence).toEqual({ code: 'C1', page: 3 })

    const nested = normalizeQualityFinding({
      id: 'f2', evidence: { fingerprint: 'fp-2', row_id: 9 },
    })
    expect(nested.stable_key).toBe('fp-2')
    expect(nested.source_row_id).toBe('9')

    const backendShape = normalizeQualityFinding({
      id: 'f3', evidence: { stable_key: 'issue:i1', issue_id: 'i1' },
    })
    expect(backendShape.stable_key).toBe('issue:i1')
    expect(backendShape.source_row_id).toBe('i1')

    const missing = normalizeQualityFinding({ id: 'f4' })
    expect(missing.stable_key).toBeNull()
    expect(missing.source_row_id).toBeNull()
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

describe('evaluation API URLs', () => {
  it('getEvaluationReportDetail uses the exact report id, not latest', async () => {
    const fetchMock = jsonFetch(base)
    const report = await getEvaluationReportDetail('p 1', 'r/1')
    expect(report.id).toBe('r1')
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/p%201/evaluation/reports/r%2F1')
    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined()
  })

  it('exportEvaluationReport downloads the Markdown attachment with its disposition name', async () => {
    const fetchMock = vi.fn(
      async (_input: string, _init?: RequestInit) =>
        new Response('markdown bytes', {
          status: 200,
          headers: { 'Content-Disposition': 'attachment; filename="quality-report.md"' },
        }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const result = await exportEvaluationReport('p1', 'r1')
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/p1/evaluation/reports/r1/export')
    expect((fetchMock.mock.calls[0][1]?.headers as Record<string, string>).Accept).toBe('*/*')
    expect(result.filename).toBe('quality-report.md')
    expect(await result.blob.text()).toBe('markdown bytes')
  })

  it('getEvaluationSummary keeps real scores and marks projects without a report unmeasured', async () => {
    const fetchMock = jsonFetch({
      items: [
        {
          project_id: 'p1', ref: 'p1', name: '项目一', measured: true, report_id: 'r9',
          report_version: 3, overall_score: 88.5, grade: 'B', verdict: 'pass',
          created_at: '2026-11-25T10:00:00Z',
        },
        {
          project_id: 'p2', ref: 'p2', name: '项目二', measured: false,
          overall_score: 99, grade: 'C', verdict: 'pass',
        },
      ],
    })
    const summary = await getEvaluationSummary('p1', ['p 1', 'p2'])
    const [url] = fetchMock.mock.calls[0]
    expect(url.startsWith('/projects/p1/evaluation/summary?')).toBe(true)
    expect(new URL(url, 'http://localhost').searchParams.get('refs')).toBe('p 1,p2')
    expect(summary.items).toHaveLength(2)
    expect(summary.items[0]).toMatchObject({
      measured: true, report_id: 'r9', report_version: 3, overall_score: 88.5,
      grade: 'B', verdict: 'pass',
    })
    expect(summary.items[1]).toMatchObject({
      measured: false, report_id: null, report_version: null, overall_score: null,
      grade: null, verdict: null,
    })
  })

  it('getEvaluationSummary omits the query when refs is absent', async () => {
    const fetchMock = jsonFetch({ items: [] })
    await getEvaluationSummary('p1')
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/p1/evaluation/summary')
    expect(normalizeEvaluationSummary(null).items).toEqual([])
  })
})

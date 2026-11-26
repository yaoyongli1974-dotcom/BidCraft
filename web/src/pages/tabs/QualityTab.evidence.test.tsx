import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QualityTab } from './QualityTab'
import {
  exportEvaluationReport,
  getEvaluationFindings,
  getEvaluationReportDetail,
  getLatestEvaluation,
  listEvaluationReports,
  runEvaluation,
  runEvaluationAsync,
  updateQualityFinding,
} from '../../api/evaluation'
import { downloadBlob } from '../../api/generation'
import { watchJob, type WatchJobOptions } from '../../hooks/watchJob'
import type { Job, QualityFinding, QualityReport } from '../../lib/types'
import { clearQueryCache } from '../../lib/queryCache'

const workspace = vi.hoisted(() => ({
  ref: 'p1',
  basePath: '/app/projects/p1',
  project: { id: 'p1', name: '测试', my_role: 'owner' as string | undefined },
  reloadProject: vi.fn(async () => ({})),
  reloadSources: vi.fn(async () => []),
  readOnly: false,
  canWrite: true,
  setUnsaved: vi.fn(),
}))
const toast = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(),
}))

vi.mock('../useWorkspace', () => ({ useWorkspace: () => workspace }))
vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../hooks/watchJob', () => ({ watchJob: vi.fn() }))
vi.mock('../../api/generation', () => ({ downloadBlob: vi.fn() }))
vi.mock('../../api/evaluation', () => ({
  getLatestEvaluation: vi.fn(),
  listEvaluationReports: vi.fn(),
  getEvaluationFindings: vi.fn(),
  getEvaluationReportDetail: vi.fn(),
  exportEvaluationReport: vi.fn(),
  getEvaluationSummary: vi.fn(),
  runEvaluation: vi.fn(),
  runEvaluationAsync: vi.fn(),
  updateQualityFinding: vi.fn(),
}))

function finding(overrides: Partial<QualityFinding> = {}): QualityFinding {
  return {
    id: 'f1', report_id: 'r1', category: '核对项', level: 'major', node_id: null,
    scoring_item_id: null, requirement_id: null, title: '待处置问题', description: '',
    evidence: {}, recommendation: '', status: 'open', ...overrides,
  }
}

function report(overrides: Partial<QualityReport> = {}): QualityReport {
  return {
    id: 'r1', project_id: 'p1', report_version: 1, overall_score: 82.5, grade: 'B',
    verdict: 'warn', dimensions: [], summary: {}, risk_summary: {}, created_by: 'owner',
    created_at: null, findings: [], ...overrides,
  }
}

function lastWatchOptions(): WatchJobOptions {
  const calls = vi.mocked(watchJob).mock.calls
  return calls[calls.length - 1][1]
}

describe('QualityTab 证据与明细', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    workspace.canWrite = true
    vi.mocked(getLatestEvaluation).mockResolvedValue(report())
    vi.mocked(listEvaluationReports).mockResolvedValue([report()])
    vi.mocked(getEvaluationFindings).mockResolvedValue([])
    vi.mocked(getEvaluationReportDetail).mockResolvedValue(report())
    vi.mocked(exportEvaluationReport).mockResolvedValue({
      blob: new Blob(['markdown']),
      filename: 'quality-report.md',
    })
    vi.mocked(runEvaluation).mockResolvedValue(report())
    vi.mocked(runEvaluationAsync).mockResolvedValue({ job_id: 'job-1' })
    vi.mocked(watchJob).mockReturnValue(vi.fn())
    vi.mocked(updateQualityFinding).mockResolvedValue(finding())
  })

  function renderTab() {
    return render(<MemoryRouter><QualityTab /></MemoryRouter>)
  }

  it('展示 finding 的来源/证据行、处置人与处置时间，缺值显示 —', async () => {
    vi.mocked(getLatestEvaluation).mockResolvedValue(report({
      findings: [
        finding({
          evidence: {
            code: 'CHK-7', page: 12, location: '第3章', clause_no: '5.2.1',
            stable_key: 'sk-1', source_row_id: 'row-9',
          },
          node_id: 'n7', requirement_id: 'req-2', scoring_item_id: 'score-3',
          status: 'handled', resolved_by: 'alice', resolved_at: '2026-11-20T10:00:00Z',
          resolution_note: '已确认',
        }),
        finding({ id: 'f2', title: '无来源问题' }),
      ],
    }))
    renderTab()
    expect(await screen.findByText('待处置问题')).toBeTruthy()
    for (const part of [
      /核对码：CHK-7/, /页码：12/, /位置：第3章/, /条款：5.2.1/,
      /章节：n7/, /要求：req-2/, /评分点：score-3/,
      /稳定键：sk-1/, /源行：row-9/,
    ]) {
      expect(screen.getByText(part)).toBeTruthy()
    }
    expect(screen.getByText(/处置人：alice/).textContent).toContain('已确认')
    expect(screen.getByText(/处置人：alice/).textContent).toContain('处置时间')
    const emptyCard = screen.getByText('无来源问题').closest('article')
    expect(emptyCard?.textContent).toContain('核对码：—')
    expect(emptyCard?.textContent).toContain('页码：—')
  })

  it('指标明细面板展示分子/分母/样本，未测量项不伪造数值', async () => {
    vi.mocked(getLatestEvaluation).mockResolvedValue(report({
      summary: {
        metrics: {
          manual_edit_rate: 0.5,
          coverage: { manual_edit_rate: { numerator: 2, denominator: 4, sample_count: 2 } },
          samples: { manual_edit_rate: [{ section_id: 's1' }, { section_id: 's2' }] },
          mismatches: [{ metric: 'issue_density', finding_id: 'f1' }],
        },
        tender_quality: {
          status: '未测量', clause_recall: null, value_parse_rate: null,
          verdict_agreement: null, coverage: {}, samples: {}, mismatches: [],
        },
      },
    }))
    renderTab()
    fireEvent.click(await screen.findByText(/查看指标计算明细/))
    expect(screen.getByText(/分子 2 \/ 分母 4 \/ 样本 2/)).toBeTruthy()
    expect(screen.getByText('可抽查样本 2 条')).toBeTruthy()
    expect(screen.getByText('不匹配明细 1 条')).toBeTruthy()
    expect(screen.getAllByText('未测量').length).toBeGreaterThan(0)
  })

  it('后端 measured=false 的指标显示未测量，绝不显示 0.0%', async () => {
    vi.mocked(getLatestEvaluation).mockResolvedValue(report({
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
    }))
    renderTab()
    expect((await screen.findAllByText('未测量')).length).toBeGreaterThanOrEqual(6)
    expect(screen.queryByText('0.0%')).toBeNull()
    expect(screen.queryByText('0.0 /千词')).toBeNull()
    fireEvent.click(await screen.findByText(/查看指标计算明细/))
    const details = screen.getByText(/查看指标计算明细/).closest('details')!
    expect(details.textContent).toContain('未测量')
    expect(details.textContent).not.toContain('分子 0 / 分母 0')
  })

  it('判定明细以期望/实测/一致的可读形式展示', async () => {
    vi.mocked(getLatestEvaluation).mockResolvedValue(report({
      summary: {
        tender_quality: {
          status: '已测量',
          clause_recall: 1,
          value_parse_rate: 1,
          verdict_agreement: 1,
          coverage: {}, samples: {
            verdict_checks: [
              { clause: '★资质', expected: 'satisfied', actual: 'satisfied', agree: true },
              { clause: '★业绩', expected: 'satisfied', actual: 'review', agree: false },
            ],
          },
          mismatches: [],
        },
      },
    }))
    renderTab()
    fireEvent.click(await screen.findByText(/查看指标计算明细/))
    expect(screen.getByText(/判定：期望 satisfied \/ 实测 satisfied \/ 一致/)).toBeTruthy()
    expect(screen.getByText(/判定：期望 satisfied \/ 实测 review \/ 不一致/)).toBeTruthy()
  })

  it('忽略必须填写处置意见，校验通过后才提交 PATCH', async () => {
    vi.mocked(getLatestEvaluation).mockResolvedValue(report({ findings: [finding()] }))
    vi.mocked(updateQualityFinding).mockResolvedValue(
      finding({ status: 'ignored', resolution_note: '确认无需处置' }),
    )
    renderTab()
    expect(await screen.findByText('待处置问题')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '忽略' }))
    expect(updateQualityFinding).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toContain('忽略时必须填写处置意见')

    fireEvent.change(screen.getByLabelText('处置意见：待处置问题'), {
      target: { value: ' 确认无需处置 ' },
    })
    fireEvent.click(screen.getByRole('button', { name: '忽略' }))
    await waitFor(() =>
      expect(updateQualityFinding).toHaveBeenCalledWith('p1', 'f1', {
        status: 'ignored', note: '确认无需处置',
      }),
    )
  })

  it('历史报告行提供下载按钮，按该报告 ID 导出 Markdown', async () => {
    const older = report({ id: 'r-old', report_version: 1 })
    vi.mocked(listEvaluationReports).mockResolvedValue([report({ id: 'r-new', report_version: 2 }), older])
    renderTab()
    const download = await screen.findByRole('button', { name: '下载报告 r-old' })
    fireEvent.click(download)
    await waitFor(() => expect(exportEvaluationReport).toHaveBeenCalledWith('p1', 'r-old'))
    await waitFor(() =>
      expect(downloadBlob).toHaveBeenCalledWith(expect.any(Blob), 'quality-report.md'),
    )
  })

  it('后台任务无 report_id 时不读最新报告冒充本次结果', async () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: /后台评估/ }))
    await waitFor(() => expect(watchJob).toHaveBeenCalled())
    await act(async () => {
      await lastWatchOptions().onDone?.({ id: 'job-1' } as Job)
    })
    expect(getEvaluationReportDetail).not.toHaveBeenCalled()
    expect(toast.success).toHaveBeenCalledWith('后台质量评估已完成')
  })
})

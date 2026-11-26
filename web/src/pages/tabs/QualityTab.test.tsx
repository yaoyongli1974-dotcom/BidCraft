import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QualityTab } from './QualityTab'
import {
  getEvaluationFindings,
  getEvaluationSummary,
  getLatestEvaluation,
  listEvaluationReports,
  runEvaluation,
  updateQualityFinding,
} from '../../api/evaluation'
import type { QualityReport } from '../../lib/types'
import { clearQueryCache } from '../../lib/queryCache'

const workspace = vi.hoisted(() => ({
  ref: 'p1',
  basePath: '/app/projects/p1',
  project: { id: 'p1', name: '测试', package: 'pkg', status: 'draft', my_role: 'owner' as string | undefined },
  sources: [],
  sourcesLoading: false,
  packages: [],
  reloadProject: vi.fn(async () => workspace.project),
  reloadSources: vi.fn(async () => []),
  readOnly: false,
  canWrite: true,
  setUnsaved: vi.fn(),
}))

const toast = vi.hoisted(() => ({
  notify: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), dismiss: vi.fn(),
}))

vi.mock('../useWorkspace', () => ({ useWorkspace: () => workspace }))
vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../api/evaluation', () => ({
  getLatestEvaluation: vi.fn(),
  listEvaluationReports: vi.fn(),
  getEvaluationFindings: vi.fn(),
  getEvaluationReportDetail: vi.fn(),
  exportEvaluationReport: vi.fn(),
  getEvaluationSummary: vi.fn(),
  runEvaluation: vi.fn(),
  updateQualityFinding: vi.fn(),
}))

function makeReport(overrides: Partial<QualityReport> = {}): QualityReport {
  return {
    id: 'r1',
    project_id: 'p1',
    report_version: 1,
    overall_score: 82.5,
    grade: 'B',
    verdict: 'warn',
    dimensions: [
      { key: 'substantive', label: '实质性响应', score: 90, weight: 25, weighted: 22.5 },
      { key: 'delivery', label: '文档交付质量', score: 60, weight: 10, weighted: 6 },
    ],
    summary: { recommendations: ['先确认 1 条 ★ 条款'], note: '' },
    risk_summary: { has_hard_risk: false },
    created_by: 'owner',
    created_at: null,
    findings: [],
    ...overrides,
  }
}

describe('QualityTab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    workspace.canWrite = true
    vi.mocked(getLatestEvaluation).mockResolvedValue(makeReport())
    vi.mocked(listEvaluationReports).mockResolvedValue([makeReport()])
    vi.mocked(getEvaluationFindings).mockResolvedValue([])
    vi.mocked(getEvaluationSummary).mockResolvedValue({
      items: [{
        project_id: 'p1', ref: 'p1', name: '测试', measured: true, report_id: 'r1',
        report_version: 1, overall_score: 91.5, grade: 'A', verdict: 'pass', created_at: null,
      }],
    })
    vi.mocked(runEvaluation).mockResolvedValue(makeReport({ report_version: 2 }))
    vi.mocked(updateQualityFinding).mockImplementation(async (_ref, _id, input) => ({
      id: 'f1', report_id: 'r1', category: '核对项', level: 'major', node_id: null,
      scoring_item_id: null, requirement_id: null, title: '问题', description: '',
      evidence: {}, recommendation: '', status: input.status,
    }))
  })

  function renderTab() {
    return render(<MemoryRouter><QualityTab /></MemoryRouter>)
  }

  it('renders latest report overview and dimensions', async () => {
    renderTab()
    expect(await screen.findByText('质量评估', { selector: 'h1, h2, h3' })).toBeTruthy()
    await waitFor(() => expect(screen.getByText('实质性响应')).toBeTruthy())
    expect(screen.getByText('82.5')).toBeTruthy()
    expect(screen.getByText('先确认 1 条 ★ 条款')).toBeTruthy()
  })

  it('renders the accessible evaluation summary without fabricating missing scores', async () => {
    vi.mocked(getEvaluationSummary).mockResolvedValue({
      items: [
        {
          project_id: 'p1', ref: 'p1', name: '测试', measured: true, report_id: 'r1',
          report_version: 1, overall_score: 82.5, grade: 'B', verdict: 'warn', created_at: null,
        },
        {
          project_id: 'p2', ref: 'p2', name: '未评估项目', measured: false, report_id: null,
          report_version: null, overall_score: null, grade: null, verdict: null, created_at: null,
        },
      ],
    })
    renderTab()
    const summary = await screen.findByLabelText('质量评估概览')
    expect(summary.textContent).toContain('82.5')
    expect(summary.textContent).toContain('需关注')
    expect(summary.textContent).toContain('未测量')
    expect(summary.textContent).not.toContain('0.0')
  })

  it('runs a new evaluation and shows updated version', async () => {
    renderTab()
    await screen.findByText('实质性响应')
    fireEvent.change(screen.getByPlaceholderText('例如：交付前例行评估'), { target: { value: '交付前' } })
    fireEvent.click(screen.getByRole('button', { name: /运行评估/ }))
    await waitFor(() => expect(runEvaluation).toHaveBeenCalledWith('p1', { note: '交付前' }))
    await waitFor(() => expect(screen.getByText('v2')).toBeTruthy())
  })

  it('运行评估后即使响应缺 findings，也采用重新拉取的最新报告展示问题', async () => {
    const finding = {
      id: 'f1', report_id: 'r2', category: '核对项', level: 'major', node_id: null,
      scoring_item_id: null, requirement_id: null, title: '待补充盖章页', description: '',
      evidence: {}, recommendation: '', status: 'open',
    }
    // 初始加载 → 运行后重新拉取；runEvaluation 的响应故意不带 findings。
    vi.mocked(getLatestEvaluation)
      .mockResolvedValueOnce(makeReport())
      .mockResolvedValueOnce(makeReport({ id: 'r2', report_version: 2, findings: [finding] }))
    vi.mocked(runEvaluation).mockResolvedValue(makeReport({ id: 'r2', report_version: 2 }))
    renderTab()
    await screen.findByText('实质性响应')
    fireEvent.click(screen.getByRole('button', { name: /运行评估/ }))
    expect(await screen.findByText('待补充盖章页')).toBeTruthy()
    expect(screen.getByText('v2')).toBeTruthy()
    expect(vi.mocked(getLatestEvaluation).mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('disables run for read-only members', async () => {
    workspace.canWrite = false
    renderTab()
    await screen.findByText('实质性响应')
    const btn = screen.getByRole('button', { name: /运行评估/ }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })

  it('shows hard-risk banner when present', async () => {
    vi.mocked(getLatestEvaluation).mockResolvedValue(makeReport({ verdict: 'blocked', risk_summary: { has_hard_risk: true } }))
    renderTab()
    expect(await screen.findByText(/存在硬性风险/)).toBeTruthy()
  })

  it('renders empty state when no report', async () => {
    vi.mocked(getLatestEvaluation).mockResolvedValue(null)
    renderTab()
    expect(await screen.findByText('还没有质量报告')).toBeTruthy()
  })

  it('updates a finding status', async () => {
    vi.mocked(getLatestEvaluation).mockResolvedValue(makeReport({
      findings: [{
        id: 'f1', report_id: 'r1', category: '核对项', level: 'major', node_id: null,
        scoring_item_id: null, requirement_id: null, title: '待处置问题', description: '',
        evidence: {}, recommendation: '', status: 'open',
      }],
    }))
    renderTab()
    expect(await screen.findByText('待处置问题')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '标记已处置' }))
    await waitFor(() => expect(updateQualityFinding).toHaveBeenCalledWith('p1', 'f1', { status: 'handled' }))
  })
})

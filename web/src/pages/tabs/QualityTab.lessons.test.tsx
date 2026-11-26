import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QualityTab } from './QualityTab'
import { getProjectLessons, revokeProjectLesson } from '../../api/learning'
import {
  getEvaluationFindings,
  getLatestEvaluation,
  listEvaluationReports,
  runEvaluation,
  runEvaluationAsync,
  updateQualityFinding,
} from '../../api/evaluation'
import type { Lesson, QualityReport } from '../../lib/types'
import { clearQueryCache } from '../../lib/queryCache'

const workspace = vi.hoisted(() => ({
  ref: 'p1',
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
vi.mock('../../api/learning', () => ({
  getProjectLessons: vi.fn(),
  revokeProjectLesson: vi.fn(),
}))
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

function lesson(overrides: Partial<Lesson> = {}): Lesson {
  return {
    id: 'l1',
    source: 'finding',
    kind: 'quality',
    text: '建议补充盖章页并复核日期',
    node_id: 'n1',
    created_at: '2026-11-25T10:00:00Z',
    author: 'alice',
    status: '未审核建议经验',
    grade: 'A',
    trust: 'high',
    expires_at: '2026-12-25T10:00:00Z',
    revoked: false,
    ...overrides,
  }
}

function report(): QualityReport {
  return {
    id: 'r1', project_id: 'p1', report_version: 1, overall_score: 82.5, grade: 'B',
    verdict: 'warn', dimensions: [], summary: {}, risk_summary: {}, created_by: 'owner',
    created_at: null, findings: [],
  }
}

describe('QualityTab 建议经验面板', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    workspace.canWrite = true
    vi.mocked(getLatestEvaluation).mockResolvedValue(report())
    vi.mocked(listEvaluationReports).mockResolvedValue([report()])
    vi.mocked(getEvaluationFindings).mockResolvedValue([])
    vi.mocked(runEvaluation).mockResolvedValue(report())
    vi.mocked(runEvaluationAsync).mockResolvedValue({ job_id: 'j1' })
    vi.mocked(updateQualityFinding).mockResolvedValue({
      id: 'f1', report_id: 'r1', category: '核对项', level: 'major', node_id: null,
      scoring_item_id: null, requirement_id: null, title: '问题', description: '',
      evidence: {}, recommendation: '', status: 'handled',
    })
    vi.mocked(getProjectLessons).mockImplementation(async (_ref, params) => ({
      items: [lesson({ node_id: params?.node_id || 'n1' })],
      node_id: params?.node_id || 'n1',
      status: '未审核建议经验',
      policy: {
        grades: {},
        trust_levels: {},
        min_trust: null,
        ttl_days: 0,
        revoked_ids: [],
      },
    }))
    vi.mocked(revokeProjectLesson).mockResolvedValue({
      lesson_id: 'l1',
      revoked: true,
      policy: {
        grades: {},
        trust_levels: {},
        min_trust: null,
        ttl_days: 0,
        revoked_ids: ['l1'],
      },
    })
  })

  function renderTab() {
    return render(<MemoryRouter><QualityTab /></MemoryRouter>)
  }

  it('展示未审核状态、来源/类型/ID 徽标与建议文本，不冒充已验证事实', async () => {
    renderTab()
    const panel = await screen.findByRole('region', { name: '建议经验' })
    expect(panel.textContent).toContain('未审核建议经验')
    expect(panel.textContent).toContain('ID l1')
    expect(panel.textContent).toContain('来源 finding')
    expect(panel.textContent).toContain('类型 quality')
    expect(panel.textContent).toContain('建议补充盖章页并复核日期')
    expect(panel.textContent).toContain('仅作建议')
    expect(panel.textContent).toContain('不代表已验证事实')
    expect(panel.textContent).not.toContain('已确认事实')
  })

  it('按章节节点筛选并刷新都走真实只读接口', async () => {
    renderTab()
    await screen.findByText('建议补充盖章页并复核日期')
    expect(getProjectLessons).toHaveBeenCalledWith('p1', { limit: 50 })

    fireEvent.change(screen.getByLabelText('经验节点筛选'), { target: { value: ' n2 ' } })
    fireEvent.click(screen.getByRole('button', { name: '筛选' }))
    await waitFor(() =>
      expect(getProjectLessons).toHaveBeenCalledWith('p1', { node_id: 'n2', limit: 50 }),
    )
    expect(await screen.findByText('n2')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() =>
      expect(vi.mocked(getProjectLessons).mock.calls.length).toBeGreaterThanOrEqual(3),
    )
  })

  it('经验接口失败时显示错误而不是伪装成空建议', async () => {
    vi.mocked(getProjectLessons).mockRejectedValueOnce(new Error('经验接口不可用'))
    renderTab()
    expect(await screen.findByText(/建议经验加载失败：经验接口不可用/)).toBeTruthy()
  })

  it('已处置 finding 展示服务端记录的处置时间', async () => {
    vi.mocked(getLatestEvaluation).mockResolvedValue(report())
    vi.mocked(getLatestEvaluation).mockResolvedValue({
      ...report(),
      findings: [{
        id: 'f1', report_id: 'r1', category: '核对项', level: 'major', node_id: null,
        scoring_item_id: null, requirement_id: null, title: '待补充盖章页', description: '',
        evidence: {}, recommendation: '', status: 'handled', resolved_by: 'alice',
        resolved_at: '2026-11-25T10:00:00Z', resolution_note: '已核对',
      }],
    })
    renderTab()
    const resolution = await screen.findByText(/处置人：alice/)
    expect(resolution.textContent).toContain('处置时间')
    expect(resolution.textContent).toContain('意见：已核对')
  })

  it('展示等级/信任/有效期徽标与治理策略摘要', async () => {
    renderTab()
    const panel = await screen.findByRole('region', { name: '建议经验' })
    expect(panel.textContent).toContain('等级 A')
    expect(panel.textContent).toContain('信任 high')
    expect(panel.textContent).toContain('有效至')
    expect(panel.textContent).toContain('治理策略：最低信任 不限')
    expect(panel.textContent).toContain('已撤销 0 条')
  })

  it('owner 可撤销经验；viewer 看不到撤销控件', async () => {
    renderTab()
    await screen.findByText('建议补充盖章页并复核日期')
    const revokeButton = screen.getByRole('button', { name: '撤销' })
    fireEvent.click(revokeButton)
    await waitFor(() =>
      expect(revokeProjectLesson).toHaveBeenCalledWith('p1', 'l1'),
    )
    expect(toast.success).toHaveBeenCalled()

    workspace.project.my_role = 'viewer'
    try {
      renderTab()
      await screen.findAllByText('建议补充盖章页并复核日期')
      expect(screen.queryByRole('button', { name: '撤销' })).toBeNull()
    } finally {
      workspace.project.my_role = 'owner'
    }
  })

  it('已撤销开关切换 include_revoked 查询并展示撤销徽标', async () => {
    vi.mocked(getProjectLessons).mockImplementation(async (_ref, params) => ({
      items: [
        lesson({
          node_id: params?.node_id || 'n1',
          revoked: params?.include_revoked === true,
        }),
      ],
      node_id: params?.node_id || 'n1',
      status: '未审核建议经验',
      policy: {
        grades: {},
        trust_levels: {},
        min_trust: null,
        ttl_days: 0,
        revoked_ids: params?.include_revoked ? ['l1'] : [],
      },
    }))
    renderTab()
    await screen.findByText('建议补充盖章页并复核日期')
    expect(screen.queryByText('已撤销')).toBeNull()
    fireEvent.click(screen.getByLabelText('显示已撤销经验'))
    await waitFor(() =>
      expect(getProjectLessons).toHaveBeenCalledWith('p1', { limit: 50, include_revoked: true }),
    )
    expect(await screen.findByText('已撤销')).toBeTruthy()
  })
})

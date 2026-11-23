import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QualityTab } from './QualityTab'
import {
  getEvaluationFindings,
  getLatestEvaluation,
  listEvaluationReports,
  runEvaluation,
  runEvaluationAsync,
  updateQualityFinding,
} from '../../api/evaluation'
import { watchJob, type WatchJobOptions } from '../../hooks/watchJob'
import type { Job, QualityFinding, QualityReport } from '../../lib/types'
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
vi.mock('../../hooks/watchJob', () => ({ watchJob: vi.fn() }))
vi.mock('../../api/evaluation', () => ({
  getEvaluationFindings: vi.fn(),
  getLatestEvaluation: vi.fn(),
  listEvaluationReports: vi.fn(),
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

/** 捕获 watchJob 回调，模拟进度 / 完成 / 失败。 */
function lastWatchOptions(): WatchJobOptions {
  const calls = vi.mocked(watchJob).mock.calls
  return calls[calls.length - 1][1]
}

describe('QualityTab 交互修复', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    workspace.canWrite = true
    vi.mocked(getLatestEvaluation).mockResolvedValue(report())
    vi.mocked(listEvaluationReports).mockResolvedValue([report()])
    vi.mocked(getEvaluationFindings).mockResolvedValue([])
    vi.mocked(runEvaluation).mockResolvedValue(report({ report_version: 2 }))
    vi.mocked(runEvaluationAsync).mockResolvedValue({ job_id: 'job-1' })
    vi.mocked(watchJob).mockReturnValue(vi.fn())
    vi.mocked(updateQualityFinding).mockImplementation(async (_ref, _id, input) =>
      finding({ status: input.status, resolution_note: input.note }),
    )
  })

  function renderTab() {
    return render(<MemoryRouter><QualityTab /></MemoryRouter>)
  }

  it('后台评估展示进度，完成后刷新最新报告', async () => {
    vi.mocked(getLatestEvaluation)
      .mockResolvedValueOnce(report())
      .mockResolvedValueOnce(report({ id: 'r3', report_version: 3 }))
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: /后台评估/ }))

    await waitFor(() => expect(watchJob).toHaveBeenCalledWith('job-1', expect.any(Object)))
    expect(screen.getByRole('status').textContent).toContain('0%')
    await act(async () => {
      lastWatchOptions().onUpdate?.({ progress: 42 } as Job)
    })
    expect(screen.getByRole('status').textContent).toContain('42%')

    await act(async () => {
      await lastWatchOptions().onDone?.({ id: 'job-1' } as Job)
    })
    await waitFor(() => expect(screen.getByText('v3')).toBeTruthy())
    expect(screen.queryByRole('status')).toBeNull()
    expect(toast.success).toHaveBeenCalledWith('已生成质量报告 v3')
  })

  it('后台评估失败后清除进度并提示错误', async () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: /后台评估/ }))
    await waitFor(() => expect(watchJob).toHaveBeenCalled())
    await act(async () => {
      lastWatchOptions().onFailed?.({ error: '模型不可用' } as Job)
    })
    expect(toast.error).toHaveBeenCalledWith('模型不可用')
    expect(screen.queryByRole('status')).toBeNull()
    expect((screen.getByRole('button', { name: /后台评估/ }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('选择历史报告时按需加载 findings', async () => {
    const older = report({ id: 'r0', report_version: 1, overall_score: 60 })
    const newer = report({ id: 'r2', report_version: 2, overall_score: 91 })
    vi.mocked(getLatestEvaluation).mockResolvedValue(newer)
    vi.mocked(listEvaluationReports).mockResolvedValue([newer, older])
    vi.mocked(getEvaluationFindings).mockImplementation(async (_ref, id) =>
      id === 'r0' ? [finding({ id: 'f0', title: '历史问题' })] : [],
    )
    renderTab()
    const historyButton = await screen.findByRole('button', { name: /v1/ })
    fireEvent.click(historyButton)
    await waitFor(() => expect(getEvaluationFindings).toHaveBeenCalledWith('p1', 'r0'))
    expect(await screen.findByText('历史问题')).toBeTruthy()
    expect(screen.getByText('60.0')).toBeTruthy()
  })

  it('历史详情加载失败时显示错误而不是旧 findings', async () => {
    const older = report({ id: 'r0', report_version: 1 })
    vi.mocked(listEvaluationReports).mockResolvedValue([report({ id: 'r2', report_version: 2 }), older])
    vi.mocked(getEvaluationFindings).mockRejectedValue(new Error('详情读取失败'))
    renderTab()
    fireEvent.click(await screen.findByRole('button', { name: /v1/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('历史报告详情加载失败：详情读取失败')
  })

  it('提交处置意见并支持重新打开', async () => {
    vi.mocked(getLatestEvaluation).mockResolvedValue(report({ findings: [finding()] }))
    vi.mocked(updateQualityFinding).mockImplementation(async (_ref, _id, input) =>
      finding({
        status: input.status,
        evidence: { resolution_note: input.note || '', resolved_by: 'alice' },
        resolution_note: input.note || '',
        resolved_by: 'alice',
      }),
    )
    renderTab()
    expect(await screen.findByText('待处置问题')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('处置意见：待处置问题'), { target: { value: ' 已核对 ' } })
    fireEvent.click(screen.getByRole('button', { name: '标记已处置' }))
    await waitFor(() =>
      expect(updateQualityFinding).toHaveBeenCalledWith('p1', 'f1', { status: 'handled', note: '已核对' }),
    )
    expect(await screen.findByText(/处置人：alice/)).toHaveTextContent('意见：已核对')

    fireEvent.click(screen.getByRole('button', { name: '重新打开' }))
    await waitFor(() =>
      expect(updateQualityFinding).toHaveBeenCalledWith('p1', 'f1', { status: 'open' }),
    )
  })
})

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AgentsTab } from './AgentsTab'
import {
  cancelWorkflow,
  createWorkflow,
  getWorkflow,
  listAgents,
  listWorkflowApprovals,
  listWorkflowTasks,
  listWorkflows,
  resumeWorkflow,
  reviewWorkflowApproval,
} from '../../api/orchestration'
import { clearQueryCache } from '../../lib/queryCache'

const workspace = vi.hoisted(() => ({
  ref: 'p1',
  basePath: '/app/projects/p1',
  project: { id: 'p1', name: '测试项目', my_role: 'owner' as string | undefined },
  reloadProject: vi.fn(async () => workspace.project),
  reloadSources: vi.fn(async () => []),
  sources: [],
  sourcesLoading: false,
  packages: [],
  readOnly: false,
  canWrite: true,
  setUnsaved: vi.fn(),
}))
const toast = vi.hoisted(() => ({
  notify: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), dismiss: vi.fn(),
}))

vi.mock('../useWorkspace', () => ({ useWorkspace: () => workspace }))
vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../api/orchestration', () => ({
  listAgents: vi.fn(),
  getAgentTools: vi.fn(async () => []),
  listWorkflows: vi.fn(),
  createWorkflow: vi.fn(),
  getWorkflow: vi.fn(),
  listWorkflowTasks: vi.fn(),
  listWorkflowApprovals: vi.fn(),
  cancelWorkflow: vi.fn(),
  resumeWorkflow: vi.fn(),
  reviewWorkflowApproval: vi.fn(),
}))

const RUN = (status: string) => ({
  id: 'run-1', workflow_type: 'standard', status, goal: '编技术标',
  stats: { done: 3, total: 3 }, created_at: '2026-11-22T08:00:00Z',
})
const TASKS = [{ id: 't1', seq: 1, task_key: 'parse', agent_type: 'tender', status: 'done' }]
const APPROVAL = {
  id: 'ap-1', approval_type: 'delivery', status: 'pending', run_id: 'run-1', submitted_by: 'alice',
}

describe('AgentsTab 交互修复', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    workspace.readOnly = false
    vi.mocked(listAgents).mockResolvedValue([] as never)
    vi.mocked(listWorkflowApprovals).mockResolvedValue([] as never)
    vi.mocked(listWorkflowTasks).mockResolvedValue(TASKS as never)
    vi.mocked(getWorkflow).mockImplementation(async () => RUN('failed') as never)
    vi.mocked(createWorkflow).mockResolvedValue(RUN('queued') as never)
    vi.mocked(cancelWorkflow).mockResolvedValue(RUN('cancelled') as never)
    vi.mocked(resumeWorkflow).mockResolvedValue(RUN('running') as never)
    vi.mocked(reviewWorkflowApproval).mockResolvedValue({ ...APPROVAL, status: 'approved' } as never)
  })

  function withRun(status: string) {
    vi.mocked(listWorkflows).mockResolvedValue([RUN(status)] as never)
    render(<AgentsTab />)
  }

  it('仅对可恢复状态显示恢复按钮', async () => {
    withRun('failed')
    fireEvent.click(await screen.findByText('编技术标'))
    await screen.findByText('parse')
    const resume = screen.getByRole('button', { name: '恢复' })
    fireEvent.click(resume)
    await waitFor(() => expect(resumeWorkflow).toHaveBeenCalledWith('p1', 'run-1'))
  })

  it('排队中可取消但不提供恢复；已完成两者都不提供', async () => {
    withRun('queued')
    fireEvent.click(await screen.findByText('编技术标'))
    await screen.findByText('parse')
    expect(screen.queryByRole('button', { name: '恢复' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(cancelWorkflow).toHaveBeenCalledWith('p1', 'run-1'))

    cleanup()
    vi.clearAllMocks()
    clearQueryCache()
    withRun('done')
    fireEvent.click(await screen.findByText('编技术标'))
    await screen.findByText('parse')
    expect(screen.queryByRole('button', { name: '恢复' })).toBeNull()
    expect(screen.queryByRole('button', { name: '取消' })).toBeNull()
  })

  it('详情加载失败时显示错误卡片并可重试', async () => {
    vi.mocked(getWorkflow).mockRejectedValueOnce(new Error('网络异常'))
    withRun('failed')
    fireEvent.click(await screen.findByText('编技术标'))
    expect(await screen.findByRole('alert')).toHaveTextContent('运行详情加载失败：网络异常')
    expect(screen.queryByText('parse')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByText('parse')).toBeTruthy()
  })

  it('审批提交时带上意见并展示处置记录', async () => {
    vi.mocked(listWorkflowApprovals).mockResolvedValue([
      APPROVAL,
      {
        ...APPROVAL, id: 'ap-2', status: 'approved', review_note: '同意交付', reviewed_by: 'bob',
      },
    ] as never)
    withRun('failed')
    await screen.findByLabelText('审批意见：交付审批')
    fireEvent.change(screen.getByLabelText('审批意见：交付审批'), { target: { value: ' 同意 ' } })
    fireEvent.click(screen.getAllByRole('button', { name: '通过' })[0])
    await waitFor(() =>
      expect(reviewWorkflowApproval).toHaveBeenCalledWith('p1', 'ap-1', {
        decision: 'approve', note: '同意',
      }),
    )
    expect(await screen.findByText(/审批人：bob/)).toHaveTextContent('意见：同意交付')
  })

  it('工作流类型只提供内置选项', async () => {
    withRun('failed')
    const select = await screen.findByRole('combobox', { name: '工作流类型' })
    expect(Array.from(select.querySelectorAll('option')).map((option) => option.value)).toEqual([
      'standard', 'full', 'bid',
    ])
    fireEvent.change(select, { target: { value: 'bid' } })
    fireEvent.change(screen.getByPlaceholderText(/基于现有事实/), { target: { value: '投标编排' } })
    fireEvent.click(screen.getByRole('button', { name: '启动' }))
    await waitFor(() =>
      expect(createWorkflow).toHaveBeenCalledWith('p1', expect.objectContaining({ workflow_type: 'bid' })),
    )
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AgentsTab } from './AgentsTab'
import {
  cancelWorkflow,
  createWorkflow,
  getAgentTools,
  getWorkflow,
  listAgents,
  listWorkflowApprovals,
  listWorkflowTasks,
  listWorkflows,
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

const AGENTS = [
  { agent_type: 'technical', name: '技术标 Agent', description: '编制技术方案', enabled: true, capabilities: ['写章节'] },
  { agent_type: 'compliance', name: '合规 Agent', description: '执行核验', enabled: true },
]
const RUN = {
  id: 'run-1', workflow_type: 'standard', status: 'done', goal: '编技术标',
  stats: { done: 3, total: 3, tokens: 1200 }, created_at: '2026-11-22T08:00:00Z',
}
const TASKS = [
  { id: 't1', seq: 1, task_key: 'parse', agent_type: 'tender', status: 'done' },
  { id: 't2', seq: 2, task_key: 'write', agent_type: 'technical', status: 'done' },
]
const APPROVAL = {
  id: 'ap-1', approval_type: 'delivery', status: 'pending', run_id: 'run-1', submitted_by: 'u1',
}

describe('AgentsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    vi.mocked(listAgents).mockResolvedValue(AGENTS as never)
    vi.mocked(listWorkflows).mockResolvedValue([RUN] as never)
    vi.mocked(listWorkflowApprovals).mockResolvedValue([APPROVAL] as never)
    vi.mocked(getWorkflow).mockResolvedValue(RUN as never)
    vi.mocked(listWorkflowTasks).mockResolvedValue(TASKS as never)
    vi.mocked(createWorkflow).mockResolvedValue({ ...RUN, id: 'run-2', status: 'queued' } as never)
    vi.mocked(cancelWorkflow).mockResolvedValue({ ...RUN, status: 'cancelled' } as never)
    vi.mocked(reviewWorkflowApproval).mockResolvedValue({ ...APPROVAL, status: 'approved' } as never)
  })

  it('renders agent registry and workflow list', async () => {
    render(<AgentsTab />)
    expect(await screen.findByText('技术标 Agent')).toBeTruthy()
    expect(screen.getByText('编技术标')).toBeTruthy()
    expect(screen.getByText('已完成')).toBeTruthy()
  })

  it('creates a workflow from the goal form', async () => {
    render(<AgentsTab />)
    await screen.findByText('编技术标')
    const input = screen.getByPlaceholderText(/基于现有事实/)
    fireEvent.change(input, { target: { value: '重新编制商务标' } })
    fireEvent.click(screen.getByRole('button', { name: '启动' }))
    await waitFor(() => expect(createWorkflow).toHaveBeenCalled())
    expect(vi.mocked(createWorkflow).mock.calls[0][1].goal).toBe('重新编制商务标')
  })

  it('loads task timeline when a workflow is selected', async () => {
    render(<AgentsTab />)
    fireEvent.click(await screen.findByText('编技术标'))
    expect(await screen.findByText('parse')).toBeTruthy()
    expect(screen.getByText('write')).toBeTruthy()
  })

  it('reviews a pending approval', async () => {
    render(<AgentsTab />)
    await screen.findByText('交付审批')
    fireEvent.click(screen.getByRole('button', { name: '通过' }))
    await waitFor(() => expect(reviewWorkflowApproval).toHaveBeenCalled())
    expect(vi.mocked(reviewWorkflowApproval).mock.calls[0][2].decision).toBe('approve')
  })

  it('审批类型以中文标签展示而非英文代码', async () => {
    vi.mocked(listWorkflowApprovals).mockResolvedValue([
      { ...APPROVAL, id: 'ap-1', approval_type: 'technical' },
      { ...APPROVAL, id: 'ap-2', approval_type: 'commercial', status: 'approved' },
      { ...APPROVAL, id: 'ap-3', approval_type: 'asset', status: 'ignored' },
      { ...APPROVAL, id: 'ap-4', approval_type: 'unknown_type', status: 'approved' },
    ] as never)
    render(<AgentsTab />)
    expect(await screen.findByText('技术审批')).toBeTruthy()
    expect(screen.getByText('商务审批')).toBeTruthy()
    expect(screen.getByText('资质资产审批')).toBeTruthy()
    // 未知类型保留原码便于排查新闸门。
    expect(screen.getByText('unknown_type')).toBeTruthy()
    expect(screen.queryByText('technical')).toBeNull()
  })

  it('disables creation in read-only mode', async () => {
    workspace.readOnly = true
    render(<AgentsTab />)
    await screen.findByText('编技术标')
    expect((screen.getByRole('button', { name: '启动' }) as HTMLButtonElement).disabled).toBe(true)
    workspace.readOnly = false
  })

  it('shows empty states when no agents or workflows', async () => {
    vi.mocked(listAgents).mockResolvedValue([] as never)
    vi.mocked(listWorkflows).mockResolvedValue([] as never)
    vi.mocked(listWorkflowApprovals).mockResolvedValue([] as never)
    render(<AgentsTab />)
    expect(await screen.findByText('暂无 Agent')).toBeTruthy()
    expect(screen.getByText('暂无工作流')).toBeTruthy()
    expect(screen.getByText('无待审批项')).toBeTruthy()
  })

  it('渲染任务下的工具调用轨迹，缺失 verification 显示 unverified', async () => {
    vi.mocked(listWorkflowTasks).mockResolvedValue([
      {
        ...TASKS[1],
        tool_calls: [
          {
            name: 'get_ledger',
            args: { ref: 'p1' },
            result: { facts: 2 },
            verification: 'verified',
            audit_id: 'audit-1',
            ts: '2026-11-25T00:00:00Z',
            is_error: false,
            ms: 12,
          },
          { name: 'export_document', args: {}, result: 'boom', is_error: true },
        ],
      },
    ] as never)
    render(<AgentsTab />)
    fireEvent.click(await screen.findByText('编技术标'))
    await screen.findByText('write')
    fireEvent.click(screen.getByRole('button', { name: '工具调用轨迹：write' }))
    expect(await screen.findByText('get_ledger')).toBeTruthy()
    expect(screen.getByText('verified')).toBeTruthy()
    expect(screen.getByText('unverified')).toBeTruthy()
    expect(screen.getByText('audit-1')).toBeTruthy()
    expect(screen.getByText('2026-11-25T00:00:00Z')).toBeTruthy()
    expect(screen.getByText('is_error=true')).toBeTruthy()
    expect(screen.getByText('12 ms')).toBeTruthy()
    expect(screen.getByText(/\{"ref":"p1"\}/)).toBeTruthy()
    expect(screen.getByText('export_document')).toBeTruthy()
  })

  it('注册表卡片按 agent 类型懒加载只读工具目录', async () => {
    vi.mocked(getAgentTools).mockResolvedValue([
      {
        name: 'get_project',
        description: '读取项目',
        input_schema: { type: 'object' },
        side_effect: 'none',
        readonly: true,
      },
      {
        name: 'export_document',
        description: '导出',
        input_schema: { type: 'object' },
        side_effect: 'write',
        readonly: false,
      },
    ] as never)
    render(<AgentsTab />)
    await screen.findByText('技术标 Agent')
    expect(getAgentTools).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '工具目录：technical' }))
    expect(await screen.findByText('get_project')).toBeTruthy()
    expect(screen.getByText('只读')).toBeTruthy()
    expect(screen.getByText('write')).toBeTruthy()
    expect(vi.mocked(getAgentTools).mock.calls[0].slice(0, 2)).toEqual(['p1', 'technical'])
  })

  it('审批操作后回读任务列表', async () => {
    render(<AgentsTab />)
    await screen.findByText('交付审批')
    fireEvent.click(screen.getByRole('button', { name: '通过' }))
    await waitFor(() => expect(reviewWorkflowApproval).toHaveBeenCalled())
    await waitFor(() => expect(listWorkflowTasks).toHaveBeenCalledWith('p1', 'run-1'))
  })
})

import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getPendingApprovals } from '../api/jobs'
import { getPendingWorkflowApprovals } from '../api/orchestration'
import type { PendingApproval, PendingWorkflowApproval } from '../lib/types'
import { PendingApprovals } from './PendingApprovals'

const auth = vi.hoisted(() => ({
  user: { id: 'u1', username: 'me' },
  loading: false,
  error: null,
}))

vi.mock('../context/useAuth', () => ({ useAuth: () => auth }))
vi.mock('../api/jobs', () => ({ getPendingApprovals: vi.fn() }))
vi.mock('../api/orchestration', () => ({ getPendingWorkflowApprovals: vi.fn() }))

const QUOTE: PendingApproval = {
  project_id: 'p1',
  project_name: '项目甲',
  version_id: 'v1',
  version_name: '报价 · 终版',
  package_id: null,
  stage: 1,
  stage_count: 1,
  mode: '',
  submitted_by: '李四',
  submitted_at: '2026-11-24T09:00:00+08:00',
  total_amount: 100,
}

const DOC: PendingApproval = {
  ...QUOTE,
  project_id: 'p2',
  project_name: '项目乙',
  version_id: 'd1',
  version_name: '商务标 · 终版',
  doc_id: 'd1',
  doc_name: '终版',
}

const WORKFLOW: PendingWorkflowApproval = {
  id: 'a1',
  run_id: 'r1',
  task_id: null,
  approval_type: 'gate',
  status: 'pending',
  project_id: 'p3',
  project_name: '项目丙',
  workflow_type: 'bid',
  run_status: 'waiting_approval',
  goal: '复核报价',
  labels: ['关键'],
  submitted_by: '王五',
  submitted_at: '2026-11-24T10:00:00+08:00',
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderPanel() {
  return render(
    <MemoryRouter initialEntries={['/app']}>
      <LocationProbe />
      <PendingApprovals />
    </MemoryRouter>,
  )
}

/** Flush the promises behind `load` (Promise.all + mock resolutions). */
async function settle() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

function clickTrigger(container: HTMLElement) {
  const trigger = container.querySelector<HTMLButtonElement>('.pending-trigger')
  if (!trigger) throw new Error('pending trigger not found')
  fireEvent.click(trigger)
}

describe('PendingApprovals', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPendingApprovals).mockResolvedValue([])
    vi.mocked(getPendingWorkflowApprovals).mockResolvedValue([])
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('登录后只拉取一次，Badge 合计报价与工作流待办', async () => {
    vi.mocked(getPendingApprovals).mockResolvedValue([QUOTE])
    vi.mocked(getPendingWorkflowApprovals).mockResolvedValue([WORKFLOW])

    renderPanel()
    await settle()

    expect(screen.getByRole('button', { name: '待我审批，2 项' })).toBeInTheDocument()
    expect(getPendingApprovals).toHaveBeenCalledTimes(1)
    expect(getPendingWorkflowApprovals).toHaveBeenCalledTimes(1)
  })

  it('打开面板时刷新，并分别渲染报价与工作流两节', async () => {
    vi.mocked(getPendingApprovals).mockResolvedValue([QUOTE])
    vi.mocked(getPendingWorkflowApprovals).mockResolvedValue([WORKFLOW])

    const view = renderPanel()
    await settle()
    clickTrigger(view.container)
    await settle()

    expect(getPendingApprovals).toHaveBeenCalledTimes(2)
    expect(getPendingWorkflowApprovals).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('region', { name: '报价与商务标' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '工作流待审' })).toBeInTheDocument()
    expect(screen.getByText('工作流待审')).toBeInTheDocument()
    expect(screen.getByText(/复核报价/)).toBeInTheDocument()
  })

  it('渲染后端当前审批级解释，不编造审批人', async () => {
    vi.mocked(getPendingApprovals).mockResolvedValue([
      {
        ...QUOTE,
        approval_explain: {
          index: 0,
          applicable: true,
          conditions: [
            {
              field: 'min_amount',
              op: 'gte',
              value: 10,
              actual: 100,
              hit: true,
              missing: false,
            },
          ],
          missing_data: [],
        },
      },
    ])

    const view = renderPanel()
    await settle()
    clickTrigger(view.container)
    await settle()

    expect(screen.getByText('当前级命中 1/1 项条件')).toBeInTheDocument()
  })

  it('仅在面板打开时每 60 秒轮询', async () => {
    vi.useFakeTimers()
    const view = renderPanel()
    await settle()
    expect(getPendingApprovals).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(60_000)
    })
    expect(getPendingApprovals).toHaveBeenCalledTimes(1)

    clickTrigger(view.container)
    await settle()
    expect(getPendingApprovals).toHaveBeenCalledTimes(2)

    await act(async () => {
      vi.advanceTimersByTime(60_000)
    })
    expect(getPendingApprovals).toHaveBeenCalledTimes(3)
    expect(getPendingWorkflowApprovals).toHaveBeenCalledTimes(3)

    clickTrigger(view.container)
    await act(async () => {
      vi.advanceTimersByTime(60_000)
    })
    expect(getPendingApprovals).toHaveBeenCalledTimes(3)
    expect(getPendingWorkflowApprovals).toHaveBeenCalledTimes(3)
  })

  it('关闭面板与卸载时取消在途请求', async () => {
    const signals: AbortSignal[] = []
    const never = new Promise<PendingApproval[]>(() => {})
    vi.mocked(getPendingApprovals).mockImplementation((signal) => {
      signals.push(signal as AbortSignal)
      return never
    })
    vi.mocked(getPendingWorkflowApprovals).mockImplementation((signal) => {
      signals.push(signal as AbortSignal)
      return Promise.resolve([])
    })

    const view = renderPanel()
    await settle()
    clickTrigger(view.container)
    await settle()
    const openSignal = signals[signals.length - 1]
    expect(openSignal?.aborted).toBe(false)

    clickTrigger(view.container)
    expect(openSignal?.aborted).toBe(true)
    expect(signals[0]?.aborted).toBe(false)

    view.unmount()
    expect(signals[0]?.aborted).toBe(true)
  })

  it('请求失败时显示错误，重试成功后恢复待办', async () => {
    vi.mocked(getPendingApprovals)
      .mockRejectedValueOnce(new Error('服务暂时不可用'))
      .mockRejectedValueOnce(new Error('服务暂时不可用'))
      .mockResolvedValue([QUOTE])
    vi.mocked(getPendingWorkflowApprovals).mockResolvedValue([WORKFLOW])

    const view = renderPanel()
    await settle()
    clickTrigger(view.container)
    await settle()
    expect(screen.getByRole('alert')).toHaveTextContent('服务暂时不可用')

    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await settle()

    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByText(/报价 · 终版/)).toBeInTheDocument()
    expect(screen.getByText(/复核报价/)).toBeInTheDocument()
    expect(getPendingApprovals).toHaveBeenCalledTimes(3)
  })

  it('Escape 与外部点击收起面板，Escape 还把焦点还给触发器', async () => {
    const view = renderPanel()
    await settle()
    const trigger = view.container.querySelector<HTMLButtonElement>('.pending-trigger')!

    clickTrigger(view.container)
    await settle()
    expect(trigger).toHaveAttribute('aria-expanded', 'true')

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(trigger).toHaveFocus()

    clickTrigger(view.container)
    await settle()
    fireEvent.mouseDown(document.body)
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('混合待办分别跳转到商务标、清单对齐与 Agent 工作流', async () => {
    vi.mocked(getPendingApprovals).mockResolvedValue([DOC, QUOTE])
    vi.mocked(getPendingWorkflowApprovals).mockResolvedValue([WORKFLOW])

    const view = renderPanel()
    await settle()
    expect(screen.getByRole('button', { name: '待我审批，3 项' })).toBeInTheDocument()

    clickTrigger(view.container)
    await settle()
    fireEvent.click(screen.getByText(/商务标 · 终版/))
    expect(screen.getByTestId('location')).toHaveTextContent('/app/projects/p2/commercial')

    clickTrigger(view.container)
    await settle()
    fireEvent.click(screen.getByText(/报价 · 终版/))
    expect(screen.getByTestId('location')).toHaveTextContent('/app/projects/p1/boq')

    clickTrigger(view.container)
    await settle()
    fireEvent.click(screen.getByText(/复核报价/))
    expect(screen.getByTestId('location')).toHaveTextContent('/app/projects/p3/agents')
  })
})

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { OutlineTab } from './OutlineTab'
import {
  confirmChapterPlan,
  generateChapter,
  generateChapterPlan,
  getChapterPlan,
  getOutline,
  getScoringCoverage,
  listChapterPlanHistory,
  regenerateChapterPlan,
  saveOutline,
  updateChapterPlan,
} from '../../api/generation'
import type { ChapterPlan } from '../../lib/types'
import { clearQueryCache } from '../../lib/queryCache'

const workspace = vi.hoisted(() => ({
  ref: 'p1',
  basePath: '/app/projects/p1',
  project: {
    id: 'p1',
    name: '测试项目',
    package: 'pkg',
    status: 'draft',
    my_role: 'owner' as string | undefined,
  },
  reloadProject: vi.fn(async () => ({})),
  setUnsaved: vi.fn(),
  canWrite: true,
  readOnly: false,
}))

const toast = vi.hoisted(() => ({
  notify: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
  dismiss: vi.fn(),
}))

vi.mock('../useWorkspace', () => ({ useWorkspace: () => workspace }))
vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../lib/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/hooks')>()
  return { ...actual, useUnsavedGuard: vi.fn() }
})
vi.mock('../../context/useConfirm', () => ({ useConfirm: () => vi.fn(async () => true) }))
vi.mock('../../api/generation', () => ({
  getOutline: vi.fn(),
  getScoringCoverage: vi.fn(),
  saveOutline: vi.fn(),
  generateOutline: vi.fn(),
  generateChapter: vi.fn(),
  getChapterPlan: vi.fn(),
  generateChapterPlan: vi.fn(),
  regenerateChapterPlan: vi.fn(),
  updateChapterPlan: vi.fn(),
  confirmChapterPlan: vi.fn(),
  listChapterPlanHistory: vi.fn(),
}))

function makePlan(overrides: Partial<ChapterPlan> = {}): ChapterPlan {
  return {
    id: 'plan-1',
    project_id: 'p1',
    node_id: 'n1',
    version: 2,
    status: 'DRAFT',
    objective: '提升技术响应',
    score_points: ['技术方案 20 分'],
    requirements: ['满足工期要求'],
    conditions: ['现场具备施工条件'],
    challenges: ['工期紧张'],
    strategy: ['突出交付能力'],
    measures: ['提前备货'],
    knowledge_refs: ['知识库A'],
    fact_refs: ['工期 30 天'],
    planned_tables: ['进度计划表'],
    planned_diagrams: ['施工流程图'],
    forbidden_assumptions: ['不得假设预算'],
    quality_criteria: ['取值可追溯'],
    validation_errors: ['缺少工期依据'],
    ...overrides,
  }
}

describe('OutlineTab chapter planning panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    workspace.canWrite = true
    workspace.readOnly = false
    workspace.project.my_role = 'owner'
    vi.mocked(getOutline).mockResolvedValue([
      {
        id: 'n1',
        heading: '第一章 技术方案',
        intent: '',
        target_words: 1000,
        words: 0,
        has_body: false,
      },
    ])
    vi.mocked(getScoringCoverage).mockResolvedValue({
      project_id: 'p1',
      total_items: 0,
      total_score: 0,
      covered_score: 0,
      weak_score: 0,
      covered_ratio: 0,
      uncovered_count: 0,
      rows: [],
      issues: [],
    })
    vi.mocked(getChapterPlan).mockResolvedValue({ plan: makePlan(), mode: 'auto' })
    vi.mocked(generateChapterPlan).mockResolvedValue(makePlan({ version: 3 }))
    vi.mocked(regenerateChapterPlan).mockResolvedValue(makePlan({ version: 4 }))
    vi.mocked(updateChapterPlan).mockResolvedValue(
      makePlan({ version: 5, status: 'UPDATED' }),
    )
    vi.mocked(confirmChapterPlan).mockResolvedValue(
      makePlan({ version: 6, status: 'CONFIRMED' }),
    )
    vi.mocked(listChapterPlanHistory).mockResolvedValue([
      makePlan({ id: 'plan-0', version: 1, status: 'CONFIRMED' }),
    ])
    vi.mocked(generateChapter).mockResolvedValue({ written: 1, fallback: 0, failed: 0 })
    vi.mocked(saveOutline).mockResolvedValue([])
  })

  function renderTab() {
    return render(
      <MemoryRouter>
        <OutlineTab />
      </MemoryRouter>,
    )
  }

  function panel() {
    return within(screen.getByRole('region', { name: '章节规划' }))
  }

  async function waitForPlan() {
    await screen.findByRole('heading', { name: '逐章规划与确认' })
    await screen.findByRole('option', { name: '第一章 技术方案' })
    await waitFor(() => expect(getChapterPlan).toHaveBeenCalledWith('p1', 'n1'))
  }

  it('renders the plan with its fields, status, version and validation errors', async () => {
    renderTab()
    await waitForPlan()
    const view = panel()
    expect(getChapterPlan).toHaveBeenCalledWith('p1', 'n1')
    expect(view.getByText('提升技术响应')).toBeTruthy()
    expect(view.getByText('技术方案 20 分')).toBeTruthy()
    expect(view.getByText('提前备货')).toBeTruthy()
    expect(view.getByText('施工流程图')).toBeTruthy()
    expect(view.getByText(/v2/)).toBeTruthy()
    expect(view.getByText('未确认')).toBeTruthy()
    expect(view.getByRole('alert').textContent).toContain('缺少工期依据')
  })

  it('shows an explicit 未生成 state and generates a plan', async () => {
    vi.mocked(getChapterPlan).mockResolvedValue({ plan: null, mode: 'auto' })
    renderTab()
    await waitForPlan()
    expect(panel().getByText(/本章规划未生成/)).toBeTruthy()
    fireEvent.click(panel().getByRole('button', { name: '生成规划' }))
    await waitFor(() =>
      expect(generateChapterPlan).toHaveBeenCalledWith('p1', 'n1'),
    )
    expect(await panel().findByText('提升技术响应')).toBeTruthy()
  })

  it('generates and regenerates with an optional user instruction', async () => {
    renderTab()
    await waitForPlan()
    fireEvent.change(panel().getByLabelText('规划补充指令'), {
      target: { value: '突出业绩' },
    })
    fireEvent.click(panel().getByRole('button', { name: '生成规划' }))
    await waitFor(() =>
      expect(generateChapterPlan).toHaveBeenCalledWith('p1', 'n1', {
        user_instruction: '突出业绩',
      }),
    )
    fireEvent.click(panel().getByRole('button', { name: '重新生成' }))
    await waitFor(() =>
      expect(regenerateChapterPlan).toHaveBeenCalledWith('p1', 'n1', {
        user_instruction: '突出业绩',
      }),
    )
  })

  it('edits strategy and saves the updates dictionary', async () => {
    renderTab()
    await waitForPlan()
    fireEvent.click(panel().getByRole('button', { name: '编辑' }))
    fireEvent.change(panel().getByLabelText('章节规划策略'), {
      target: { value: '新策略一\n新策略二' },
    })
    fireEvent.click(panel().getByRole('button', { name: '保存修改' }))
    await waitFor(() =>
      expect(updateChapterPlan).toHaveBeenCalledWith('p1', 'n1', {
        updates: { strategy: ['新策略一', '新策略二'] },
      }),
    )
  })

  it('confirms the plan and respects reviewer permission', async () => {
    renderTab()
    await waitForPlan()
    fireEvent.click(panel().getByRole('button', { name: '确认规划' }))
    await waitFor(() => expect(confirmChapterPlan).toHaveBeenCalledWith('p1', 'n1'))
    expect((await panel().findAllByText('已确认')).length).toBeGreaterThan(0)
  })

  it('disables confirm for non-reviewers', async () => {
    workspace.project.my_role = 'editor'
    renderTab()
    await waitForPlan()
    expect((panel().getByRole('button', { name: '确认规划' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
  })

  it('disables all planning actions for read-only members', async () => {
    workspace.readOnly = true
    workspace.canWrite = false
    renderTab()
    await waitForPlan()
    for (const name of ['生成规划', '重新生成', '编辑', '查看历史', '刷新', '确认规划']) {
      const button = panel().getByRole('button', { name }) as HTMLButtonElement
      expect(button.disabled).toBe(true)
    }
  })

  it('loads and displays plan history', async () => {
    renderTab()
    await waitForPlan()
    fireEvent.click(panel().getByRole('button', { name: '查看历史' }))
    await waitFor(() => expect(listChapterPlanHistory).toHaveBeenCalledWith('p1', 'n1'))
    const history = await screen.findByRole('region', { name: '规划历史' })
    expect(within(history).getByText('v1')).toBeTruthy()
  })

  it('blocks chapter generation in review mode until the plan is confirmed', async () => {
    vi.mocked(getChapterPlan).mockResolvedValue({ plan: makePlan(), mode: 'review' })
    renderTab()
    await waitForPlan()
    const generateButton = screen.getByRole('button', { name: '生成本章' }) as HTMLButtonElement
    expect(generateButton.disabled).toBe(true)
    expect(screen.getAllByText(/章节规划尚未确认/).length).toBeGreaterThan(0)
  })

  it('warns but does not block in auto mode', async () => {
    renderTab()
    await waitForPlan()
    expect((screen.getByRole('button', { name: '生成本章' }) as HTMLButtonElement).disabled).toBe(
      false,
    )
    expect(screen.getAllByText(/自动规划模式，章节规划未经人工确认/).length).toBeGreaterThan(0)
  })

  it('batch generates only the missing plan and never auto-confirms it', async () => {
    vi.mocked(getOutline).mockResolvedValue([
      {
        id: 'n1',
        heading: '第一章 技术方案',
        intent: '',
        target_words: 1000,
        words: 0,
        has_body: false,
      },
      {
        id: 'n2',
        heading: '第二章 商务条款',
        intent: '',
        target_words: 1000,
        words: 0,
        has_body: false,
      },
    ])
    vi.mocked(getChapterPlan).mockImplementation(async (_ref, nodeId) => ({
      plan:
        nodeId === 'n1'
          ? null
          : makePlan({ node_id: 'n2', status: 'CONFIRMED' }),
      mode: 'auto',
    }))

    renderTab()
    await waitForPlan()
    fireEvent.click(panel().getByRole('button', { name: '批量生成缺失规划' }))

    await waitFor(() => expect(generateChapterPlan).toHaveBeenCalledWith('p1', 'n1'))
    expect(generateChapterPlan).toHaveBeenCalledTimes(1)
    expect(confirmChapterPlan).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('批量生成完成', expect.anything()),
    )
    await waitFor(() => expect(workspace.reloadProject).toHaveBeenCalled())
  })

  it('offers one-click missing plan generation without auto-confirming', async () => {
    vi.mocked(getChapterPlan).mockResolvedValue({ plan: null, mode: 'auto' })
    renderTab()
    await waitForPlan()

    fireEvent.click(screen.getByRole('button', { name: '一键生成缺失章节规划' }))

    await waitFor(() => expect(generateChapterPlan).toHaveBeenCalledWith('p1', 'n1'))
    expect(confirmChapterPlan).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('缺失章节规划已生成', expect.anything()),
    )
  })

  it('disables batch planning while the outline has unsaved changes', async () => {
    renderTab()
    await waitForPlan()
    fireEvent.change(screen.getByLabelText('第 1 章标题'), {
      target: { value: '改名后的章节' },
    })
    for (const name of ['一键生成缺失章节规划', '批量生成缺失规划', '确认全部待确认']) {
      expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true)
    }
  })

  it('batch confirms only pending plans using the shared note', async () => {
    vi.mocked(getOutline).mockResolvedValue([
      {
        id: 'n1',
        heading: '第一章 技术方案',
        intent: '',
        target_words: 1000,
        words: 0,
        has_body: false,
      },
      {
        id: 'n2',
        heading: '第二章 商务条款',
        intent: '',
        target_words: 1000,
        words: 0,
        has_body: false,
      },
    ])
    vi.mocked(getChapterPlan).mockImplementation(async (_ref, nodeId) => ({
      plan: makePlan({
        node_id: nodeId,
        status: nodeId === 'n1' ? 'DRAFT' : 'CONFIRMED',
      }),
      mode: 'review',
    }))

    renderTab()
    await waitForPlan()
    fireEvent.change(panel().getByLabelText('批量确认备注'), {
      target: { value: '批量复核通过' },
    })
    fireEvent.click(panel().getByRole('button', { name: '确认全部待确认' }))

    await waitFor(() =>
      expect(confirmChapterPlan).toHaveBeenCalledWith('p1', 'n1', { note: '批量复核通过' }),
    )
    expect(confirmChapterPlan).toHaveBeenCalledTimes(1)
    expect(generateChapterPlan).not.toHaveBeenCalled()
  })

  it('keeps review_strict mode from the plan response and blocks generation', async () => {
    vi.mocked(getChapterPlan).mockResolvedValue({ plan: makePlan(), mode: 'review_strict' })
    renderTab()
    await waitForPlan()
    expect(panel().getByText(/严格复核/)).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: '生成本章' }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })
})

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { confirmChapterPlan, generateChapterPlan } from '../api/generation'
import type { ChapterPlanEntry } from '../hooks/useChapterPlans'
import type { ChapterPlan, Project } from '../lib/types'
import { ChapterPlanningPanel } from './ChapterPlanningPanel'

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../api/generation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/generation')>()
  return {
    ...actual,
    generateChapterPlan: vi.fn(),
    regenerateChapterPlan: vi.fn(),
    updateChapterPlan: vi.fn(),
    confirmChapterPlan: vi.fn(),
  }
})

function makePlan(overrides: Partial<ChapterPlan> = {}): ChapterPlan {
  return {
    id: 'plan-1',
    project_id: 'p1',
    node_id: 'n1',
    version: 1,
    status: 'DRAFT',
    objective: '章节目标',
    ...overrides,
  }
}

function entry(nodeId: string, plan: ChapterPlan | null): ChapterPlanEntry {
  return { node_id: nodeId, plan, mode: 'auto' }
}

const chapters = [
  { id: 'n1', heading: '第一章' },
  { id: 'n2', heading: '第二章' },
  { id: 'n3', heading: '第三章' },
  { id: 'n4', heading: '第四章' },
  { heading: '尚未保存的新章节' },
]

interface RenderOptions {
  entries?: ChapterPlanEntry[]
  canWrite?: boolean
  readOnly?: boolean
  role?: string
  outlineDirty?: boolean
  onRefresh?: () => void
  onPlanChange?: (nodeId: string, plan: ChapterPlan) => void
  onBatchComplete?: () => void | Promise<void>
}

function renderPanel(options: RenderOptions = {}) {
  const props = {
    projectRef: 'p1',
    chapters,
    project: { id: 'p1', name: '测试项目', my_role: options.role ?? 'owner' } as Project,
    canWrite: options.canWrite ?? true,
    readOnly: options.readOnly ?? false,
    entries: options.entries ?? [],
    mode: 'auto' as const,
    loading: false,
    error: null,
    onRefresh: options.onRefresh ?? vi.fn(),
    onPlanChange: options.onPlanChange ?? vi.fn(),
    outlineDirty: options.outlineDirty ?? false,
    onBatchComplete: options.onBatchComplete,
  }
  const result = render(<ChapterPlanningPanel {...props} />)
  const view = within(result.container.querySelector('.chapter-planning') as HTMLElement)
  return { view, onRefresh: props.onRefresh, onPlanChange: props.onPlanChange }
}

describe('ChapterPlanningPanel 批量操作', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(generateChapterPlan).mockResolvedValue(makePlan({ version: 3 }))
    vi.mocked(confirmChapterPlan).mockResolvedValue(makePlan({ status: 'CONFIRMED', version: 4 }))
  })

  it('只为没有规划的真实章节顺序生成，跳过已确认 / 已使用的规划，且不自动确认', async () => {
    const generated = makePlan({ node_id: 'n1', version: 3 })
    vi.mocked(generateChapterPlan).mockResolvedValue(generated)
    const { view, onRefresh, onPlanChange } = renderPanel({
      entries: [
        entry('n1', null),
        entry('n2', makePlan({ status: 'CONFIRMED' })),
        entry('n3', makePlan({ status: 'USED_FOR_GENERATION' })),
        entry('n4', makePlan({ status: 'DRAFT' })),
      ],
    })

    fireEvent.click(view.getByRole('button', { name: '批量生成缺失规划' }))

    await waitFor(() => expect(generateChapterPlan).toHaveBeenCalledWith('p1', 'n1'))
    expect(generateChapterPlan).toHaveBeenCalledTimes(1)
    expect(generateChapterPlan).not.toHaveBeenCalledWith('p1', 'n2')
    expect(generateChapterPlan).not.toHaveBeenCalledWith('p1', 'n4')
    expect(confirmChapterPlan).not.toHaveBeenCalled()
    expect(onPlanChange).toHaveBeenCalledWith('n1', generated)
    await waitFor(() => expect(onRefresh).toHaveBeenCalled())
    expect(toast.success).toHaveBeenCalledWith('批量生成完成', expect.stringContaining('未自动确认'))
  })

  it('单章失败不中断批量，报告部分成功并回写成功的章节', async () => {
    const generated = makePlan({ node_id: 'n2', version: 5 })
    vi.mocked(generateChapterPlan)
      .mockRejectedValueOnce(new Error('第一章生成失败'))
      .mockResolvedValueOnce(generated)
    const { view, onRefresh, onPlanChange } = renderPanel({
      entries: [
        entry('n1', null),
        entry('n2', null),
        entry('n3', makePlan({ status: 'CONFIRMED' })),
        entry('n4', makePlan({ status: 'CONFIRMED' })),
      ],
    })

    fireEvent.click(view.getByRole('button', { name: '批量生成缺失规划' }))

    await waitFor(() => expect(generateChapterPlan).toHaveBeenCalledTimes(2))
    expect(onPlanChange).toHaveBeenCalledTimes(1)
    expect(onPlanChange).toHaveBeenCalledWith('n2', generated)
    expect(toast.warning).toHaveBeenCalledWith(
      '批量生成部分完成',
      expect.stringContaining('第一章'),
    )
    await waitFor(() => expect(onRefresh).toHaveBeenCalled())
  })

  it('显示批量进度，结束后刷新项目状态', async () => {
    const onBatchComplete = vi.fn(async () => {})
    let resolve!: (plan: ChapterPlan) => void
    vi.mocked(generateChapterPlan).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    const { view } = renderPanel({
      entries: [
        entry('n1', null),
        entry('n2', makePlan({ status: 'CONFIRMED' })),
        entry('n3', makePlan({ status: 'CONFIRMED' })),
        entry('n4', makePlan({ status: 'CONFIRMED' })),
      ],
      onBatchComplete,
    })

    fireEvent.click(view.getByRole('button', { name: '批量生成缺失规划' }))
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('批量进度 0/1'))
    await act(async () => {
      resolve(makePlan({ version: 3 }))
    })
    await waitFor(() => expect(onBatchComplete).toHaveBeenCalled())
    expect(toast.success).toHaveBeenCalled()
  })

  it('批量确认只处理草稿 / 已更新 / 待复核，并使用公共备注', async () => {
    const { view, onPlanChange } = renderPanel({
      entries: [
        entry('n1', makePlan({ status: 'DRAFT', node_id: 'n1' })),
        entry('n2', makePlan({ status: 'UPDATED', node_id: 'n2' })),
        entry('n3', makePlan({ status: 'REVIEW_REQUIRED', node_id: 'n3' })),
        entry('n4', makePlan({ status: 'CONFIRMED', node_id: 'n4' })),
      ],
    })

    fireEvent.change(view.getByLabelText('批量确认备注'), {
      target: { value: '  统一复核通过  ' },
    })
    fireEvent.click(view.getByRole('button', { name: '确认全部待确认' }))

    await waitFor(() => expect(confirmChapterPlan).toHaveBeenCalledTimes(3))
    expect(confirmChapterPlan).toHaveBeenCalledWith('p1', 'n1', { note: '统一复核通过' })
    expect(confirmChapterPlan).toHaveBeenCalledWith('p1', 'n2', { note: '统一复核通过' })
    expect(confirmChapterPlan).toHaveBeenCalledWith('p1', 'n3', { note: '统一复核通过' })
    expect(confirmChapterPlan).not.toHaveBeenCalledWith('p1', 'n4', expect.anything())
    expect(generateChapterPlan).not.toHaveBeenCalled()
    expect(onPlanChange).toHaveBeenCalledTimes(3)
    expect(toast.success).toHaveBeenCalledWith('批量确认完成', expect.anything())
  })

  it('部分确认失败时继续处理其余章节并给出警告', async () => {
    vi.mocked(confirmChapterPlan)
      .mockRejectedValueOnce(new Error('无权确认'))
      .mockResolvedValueOnce(makePlan({ status: 'CONFIRMED', node_id: 'n2' }))
    const { view, onPlanChange } = renderPanel({
      entries: [
        entry('n1', makePlan({ node_id: 'n1' })),
        entry('n2', makePlan({ node_id: 'n2' })),
      ],
    })

    fireEvent.click(view.getByRole('button', { name: '确认全部待确认' }))

    await waitFor(() => expect(confirmChapterPlan).toHaveBeenCalledTimes(2))
    expect(onPlanChange).toHaveBeenCalledTimes(1)
    expect(toast.warning).toHaveBeenCalledWith(
      '批量确认部分完成',
      expect.stringContaining('第一章'),
    )
  })

  it('只读、非复核人与未保存大纲修改都会禁用批量按钮', () => {
    const entries = [entry('n1', makePlan({ node_id: 'n1' }))]
    const readOnly = renderPanel({ entries, canWrite: false, readOnly: true })
    for (const name of ['批量生成缺失规划', '确认全部待确认']) {
      expect(
        (readOnly.view.getByRole('button', { name }) as HTMLButtonElement).disabled,
      ).toBe(true)
    }

    const nonReviewer = renderPanel({ entries, role: 'editor' })
    expect(
      (nonReviewer.view.getByRole('button', { name: '批量生成缺失规划' }) as HTMLButtonElement)
        .disabled,
    ).toBe(false)
    expect(
      (nonReviewer.view.getByRole('button', { name: '确认全部待确认' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true)

    const dirty = renderPanel({ entries, outlineDirty: true })
    for (const name of ['批量生成缺失规划', '确认全部待确认']) {
      expect((dirty.view.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true)
    }
  })
})

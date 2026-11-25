import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { normalizeChapterPlan } from '../api/generation'
import type { Project } from '../lib/types'
import type { ChapterPlanEntry } from '../hooks/useChapterPlans'
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
    listChapterPlanHistory: vi.fn(),
  }
})

const RAW_PLAN = {
  id: 'plan-1',
  node_id: 'n1',
  version: 2,
  status: 'DRAFT',
  objective: '提升技术响应',
  knowledge_refs: [
    '旧版纯文本引用',
    {
      id: 'chunk-9',
      kind: 'chunk',
      heading: '资质证书',
      source: 'cert.pdf',
      score: 0.88,
      rerank_score: 0.83,
      trust_tier: 'title_only',
      degraded: true,
      chunk_id: 'chunk-9',
      chunk_index: 1,
      page: 4,
    },
  ],
}

function entry(): ChapterPlanEntry {
  const plan = normalizeChapterPlan(RAW_PLAN)
  if (!plan) throw new Error('测试规划无法归一化')
  return { node_id: 'n1', plan, mode: 'auto' }
}

function renderPanel(canWrite = true, readOnly = false, role = 'owner') {
  return render(
    <ChapterPlanningPanel
      projectRef="p1"
      chapters={[{ id: 'n1', heading: '第一章 技术方案' }]}
      project={{ id: 'p1', name: '测试项目', my_role: role } as Project}
      canWrite={canWrite}
      readOnly={readOnly}
      entries={[entry()]}
      mode="auto"
      loading={false}
      error={null}
      onRefresh={vi.fn()}
      onPlanChange={vi.fn()}
    />,
  )
}

describe('章节规划的知识引用溯源', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('归一化后保留真实引用 id 与重排 / 信任 / 降级信号', () => {
    const plan = entry().plan!
    expect(plan.knowledge_refs?.[0]).toBe('旧版纯文本引用')
    expect(plan.knowledge_refs?.[1]).toMatchObject({
      id: 'chunk-9',
      chunk_id: 'chunk-9',
      chunk_index: 1,
      rerank_score: 0.83,
      trust_tier: 'title_only',
      degraded: true,
    })
  })

  it('面板展示引用的真实 id、块溯源与溯源标签', () => {
    renderPanel()
    const knowledgeField = screen.getByText('知识引用').closest('.plan-field')
    expect(knowledgeField).not.toBeNull()
    const view = within(knowledgeField as HTMLElement)
    expect(view.getByText('旧版纯文本引用')).toBeInTheDocument()
    expect(view.getAllByText('chunk-9').length).toBeGreaterThan(0)
    expect(view.getByText('重排 0.83')).toBeInTheDocument()
    expect(view.getByText('仅题录')).toBeInTheDocument()
    expect(view.getByText('降级运行')).toBeInTheDocument()
    expect(view.getByText('块序号')).toBeInTheDocument()
    expect(view.getByText('第 4 页')).toBeInTheDocument()
  })

  it('只读 viewer 仍可查看引用，但所有规划写操作禁用', () => {
    renderPanel(false, true, 'viewer')
    expect(screen.getByText('仅题录')).toBeInTheDocument()
    for (const name of ['生成规划', '重新生成', '编辑', '查看历史', '刷新', '确认规划']) {
      expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true)
    }
  })
})

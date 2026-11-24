import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SimilarityTab } from './SimilarityTab'
import {
  checkSimilarity,
  getSimilarityHits,
  getSimilarityReport,
  listSimilarityReports,
  updateSimilarityHit,
} from '../../api/similarity'
import type { SimilarityHit, SimilarityReport } from '../../lib/types'
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
  notify: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
  dismiss: vi.fn(),
}))

vi.mock('../useWorkspace', () => ({ useWorkspace: () => workspace }))
vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../api/similarity', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/similarity')>()
  return {
    ...actual,
    checkSimilarity: vi.fn(),
    listSimilarityReports: vi.fn(),
    getSimilarityReport: vi.fn(),
    getSimilarityHits: vi.fn(),
    updateSimilarityHit: vi.fn(),
    exportSimilarityReport: vi.fn(),
  }
})

// 与后端真实返回一致（bidcraft/similarity/service.py:308-320）。
const REPORT: SimilarityReport = {
  id: 'r1',
  scope: 'project',
  mode: 'text',
  threshold: 0.8,
  status: 'done',
  stats: {
    mode: 'text',
    scope: 'project',
    threshold: 0.8,
    source_project_id: '',
    project: '测试项目',
    modes: { text: { hits: 2 } },
    hits: 2,
    by_kind: { text: 2 },
    common_candidates: 0,
    degraded: false,
    models: [],
    truncated: false,
  },
  skipped: ['空文档'],
  created_at: '2026-11-20T08:00:00.000Z',
}

// 后端允许值，来源 bidcraft/similarity/service.py:23-24、105-109。
const BACKEND_SCOPES = ['project', 'history']
const BACKEND_MODES = ['text', 'structure', 'semantic', 'image', 'all']
const CHECK_BODY_KEYS = ['scope', 'mode', 'threshold', 'source_project_id']

const HITS: SimilarityHit[] = [
  {
    id: 'h1',
    kind: 'text',
    source_a: '本项目正文 · 技术方案',
    source_b: '历史标书 A · 第三章',
    loc_a: '3.2 节',
    loc_b: '第 12 页',
    excerpt_a: '投标人须提供完整的施工组织设计并覆盖全部关键工序。',
    excerpt_b: '投标人应提供完整的施工组织设计，覆盖全部关键工序。',
    similarity: 0.92,
    review_status: 'pending',
    review_note: '',
  },
  {
    id: 'h2',
    kind: 'logic',
    source_a: '本项目正文 · 服务承诺',
    source_b: '历史标书 B · 第五章',
    excerpt_a: '服务响应流程分为受理、派单、回访三步。',
    excerpt_b: '服务流程：受理—派单—回访。',
    similarity: 0.71,
    review_status: 'suspicious',
    review_note: '与历史表述高度接近，待人工判断',
  },
]

function renderTab() {
  return render(
    <MemoryRouter initialEntries={['/app/projects/p1/similarity']}>
      <SimilarityTab />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  clearQueryCache()
  vi.clearAllMocks()
  workspace.canWrite = true
  workspace.readOnly = false
  vi.mocked(listSimilarityReports).mockResolvedValue([REPORT])
  vi.mocked(getSimilarityReport).mockResolvedValue(REPORT)
  vi.mocked(getSimilarityHits).mockResolvedValue(HITS)
  vi.mocked(updateSimilarityHit).mockImplementation(async (_ref, id, status, note) => ({
    ...(HITS.find((hit) => hit.id === id) || HITS[0]),
    review_status: status,
    review_note: note || '',
  }))
})

describe('SimilarityTab', () => {
  it('shows the empty report state when no report exists', async () => {
    vi.mocked(listSimilarityReports).mockResolvedValue([])
    renderTab()

    expect(await screen.findByText('还没有查重报告')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '启动查重' })).toBeEnabled()
    expect(screen.getByLabelText(/相似度阈值/)).toHaveValue(0.85)
  })

  it('loads the selected report and renders dual-column hits with kind filters', async () => {
    renderTab()

    expect(await screen.findByText('已完成')).toBeInTheDocument()
    expect(await screen.findByText(HITS[0].excerpt_a)).toBeInTheDocument()
    expect(screen.getByText(HITS[0].excerpt_b)).toBeInTheDocument()
    expect(screen.getByText('92%')).toBeInTheDocument()

    const filter = screen.getByRole('group', { name: '按重复类型筛选' })
    expect(within(filter).getByRole('button', { name: /文本重合/ })).toBeInTheDocument()
    expect(within(filter).getByRole('button', { name: /逻辑结构/ })).toBeInTheDocument()

    fireEvent.click(within(filter).getByRole('button', { name: /逻辑结构/ }))
    await waitFor(() => {
      expect(screen.queryByText(HITS[0].excerpt_a)).not.toBeInTheDocument()
    })
    expect(screen.getByText(HITS[1].excerpt_a)).toBeInTheDocument()
  })

  it('starts a check with backend scope/mode enums and routes source_project_id for history', async () => {
    vi.mocked(listSimilarityReports).mockResolvedValue([])
    vi.mocked(checkSimilarity).mockResolvedValue({ report_id: 'r9' })
    vi.mocked(getSimilarityReport).mockResolvedValue({
      ...REPORT,
      id: 'r9',
      status: 'running',
      stats: {},
    })
    renderTab()

    fireEvent.change(screen.getByLabelText(/查重范围/), { target: { value: 'history' } })
    fireEvent.change(screen.getByLabelText(/比对粒度/), { target: { value: 'semantic' } })
    fireEvent.change(screen.getByLabelText(/相似度阈值/), { target: { value: '0.7' } })
    fireEvent.change(screen.getByLabelText(/来源项目/), {
      target: { value: 'p0' },
    })
    fireEvent.click(screen.getByRole('button', { name: '启动查重' }))

    await waitFor(() =>
      expect(checkSimilarity).toHaveBeenCalledWith('p1', {
        scope: 'history',
        mode: 'semantic',
        threshold: 0.7,
        source_project_id: 'p0',
      }),
    )
    // 真实请求体只含后端 SimilarityRequest 允许的键与枚举值。
    const payload = vi.mocked(checkSimilarity).mock.calls[0][1]
    expect(Object.keys(payload).every((key) => CHECK_BODY_KEYS.includes(key))).toBe(true)
    expect(BACKEND_SCOPES).toContain(payload.scope)
    expect(BACKEND_MODES).toContain(payload.mode)
    expect(typeof payload.threshold).toBe('number')
    expect(await screen.findByText(/查重进行中/)).toBeInTheDocument()
    expect(toast.success).toHaveBeenCalled()
  })

  it('omits source_project_id for project scope and disables history submit until a source is set', async () => {
    vi.mocked(listSimilarityReports).mockResolvedValue([])
    vi.mocked(checkSimilarity).mockResolvedValue({ report_id: 'r9' })
    vi.mocked(getSimilarityReport).mockResolvedValue({ ...REPORT, id: 'r9', status: 'running' })
    renderTab()

    expect(screen.getByLabelText(/查重范围/)).toHaveValue('project')
    expect(screen.getByLabelText(/比对粒度/)).toHaveValue('text')
    fireEvent.change(screen.getByLabelText(/来源项目/), { target: { value: 'ignored' } })
    fireEvent.click(screen.getByRole('button', { name: '启动查重' }))
    await waitFor(() =>
      expect(checkSimilarity).toHaveBeenCalledWith('p1', {
        scope: 'project',
        mode: 'text',
        threshold: 0.85,
      }),
    )

    fireEvent.change(screen.getByLabelText(/查重范围/), { target: { value: 'history' } })
    fireEvent.change(screen.getByLabelText(/来源项目/), { target: { value: '' } })
    const submit = screen.getByRole('button', { name: '启动查重' })
    expect(submit).toBeDisabled()
    fireEvent.click(submit)
    expect(checkSimilarity).toHaveBeenCalledTimes(1)
    // 绕过 disabled 直接提交表单，确认 start() 也有兜底校验。
    fireEvent.submit(submit.closest('form') as HTMLFormElement)
    expect(checkSimilarity).toHaveBeenCalledTimes(1)
    expect(await screen.findByRole('alert')).toHaveTextContent('历史项目比对需填写来源项目编号')
  })

  it('derives stat tiles from real backend stats and normalized hits', async () => {
    renderTab()

    expect(await screen.findByText('已完成')).toBeInTheDocument()
    expect(await screen.findByText(HITS[0].excerpt_a)).toBeInTheDocument()

    const tileValue = (label: string) =>
      screen.getByText(label).closest('.stat-tile')?.querySelector('.stat-value')?.textContent
    expect(tileValue('比对条目')).toBe('2')
    expect(tileValue('命中')).toBe('2')
    expect(tileValue('已复核')).toBe('1')
    expect(tileValue('跳过')).toContain('空文档')
    expect(screen.getByText('疑似重复')).toBeInTheDocument()
  })

  it('saves a manual review decision with the note', async () => {
    renderTab()

    const hit = (await screen.findByText(HITS[0].excerpt_a)).closest('article') as HTMLElement
    fireEvent.change(within(hit).getByLabelText('复核备注'), {
      target: { value: '已改写为自有表述' },
    })
    fireEvent.click(within(hit).getByRole('button', { name: '标记原创' }))

    await waitFor(() =>
      expect(updateSimilarityHit).toHaveBeenCalledWith(
        'p1',
        'h1',
        'ignored',
        '已改写为自有表述',
      ),
    )
    expect(toast.success).toHaveBeenCalledWith('复核状态已更新', expect.any(String))
  })

  it('disables starting and reviewing for read-only members', async () => {
    workspace.canWrite = false
    workspace.readOnly = true
    renderTab()

    expect(await screen.findByRole('button', { name: '启动查重' })).toBeDisabled()
    const hit = (await screen.findByText(HITS[0].excerpt_a)).closest('article') as HTMLElement
    expect(within(hit).getByRole('button', { name: '确认重复' })).toBeDisabled()
    expect(within(hit).getByRole('button', { name: '标记原创' })).toBeDisabled()
    expect(within(hit).getByLabelText('复核备注')).toBeDisabled()
  })
})

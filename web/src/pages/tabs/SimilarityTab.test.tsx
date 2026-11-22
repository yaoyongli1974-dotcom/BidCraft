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
vi.mock('../../api/similarity', () => ({
  checkSimilarity: vi.fn(),
  listSimilarityReports: vi.fn(),
  getSimilarityReport: vi.fn(),
  getSimilarityHits: vi.fn(),
  updateSimilarityHit: vi.fn(),
  exportSimilarityReport: vi.fn(),
}))

const REPORT: SimilarityReport = {
  id: 'r1',
  scope: 'internal',
  mode: 'paragraph',
  threshold: 0.8,
  status: 'done',
  stats: { checked: 12, hits: 2, reviewed: 0 },
  skipped: 1,
  created_at: '2026-11-20T08:00:00.000Z',
}

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
    review_status: 'pending',
    review_note: '',
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

  it('starts a check with the chosen scope, mode and threshold', async () => {
    vi.mocked(listSimilarityReports).mockResolvedValue([])
    vi.mocked(checkSimilarity).mockResolvedValue({ report_id: 'r9' })
    vi.mocked(getSimilarityReport).mockResolvedValue({
      ...REPORT,
      id: 'r9',
      status: 'running',
      stats: {},
    })
    renderTab()

    fireEvent.change(screen.getByLabelText(/查重范围/), { target: { value: 'external' } })
    fireEvent.change(screen.getByLabelText(/比对粒度/), { target: { value: 'sentence' } })
    fireEvent.change(screen.getByLabelText(/相似度阈值/), { target: { value: '0.7' } })
    fireEvent.change(screen.getByLabelText(/来源项目/), {
      target: { value: 'p0' },
    })
    fireEvent.click(screen.getByRole('button', { name: '启动查重' }))

    await waitFor(() =>
      expect(checkSimilarity).toHaveBeenCalledWith('p1', {
        scope: 'external',
        mode: 'sentence',
        threshold: 0.7,
        source_project_id: 'p0',
      }),
    )
    expect(await screen.findByText(/查重进行中/)).toBeInTheDocument()
    expect(toast.success).toHaveBeenCalled()
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

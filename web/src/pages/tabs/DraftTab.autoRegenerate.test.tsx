import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DraftTab } from './DraftTab'
import { getChapter, getChapterCitations, getOutline, getProjectJobs, startDocumentGeneration } from '../../api/generation'
import { batchRegeneratePlans, missingPlanNodeIds } from '../../hooks/useChapterPlans'
import { clearQueryCache } from '../../lib/queryCache'

const PROJECT_REF = '33333333-3333-3333-3333-333333333333'

const workspace = vi.hoisted(() => ({
  ref: '33333333-3333-3333-3333-333333333333',
  basePath: '/app/projects/33333333-3333-3333-3333-333333333333',
  project: {
    id: '33333333-3333-3333-3333-333333333333',
    name: '测试项目',
    my_role: 'owner' as string | undefined,
  },
  reloadProject: vi.fn(async () => ({})),
  setUnsaved: vi.fn(),
  canWrite: true,
}))

const toast = vi.hoisted(() => ({
  notify: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
  dismiss: vi.fn(),
}))

const planHook = vi.hoisted(() => ({
  entries: [] as Array<{ node_id: string; plan: unknown; mode: string }>,
  setPlan: vi.fn(),
  refresh: vi.fn(async () => {}),
}))

vi.mock('../useWorkspace', () => ({ useWorkspace: () => workspace }))
vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../context/useConfirm', () => ({ useConfirm: () => vi.fn(async () => true) }))
vi.mock('../../context/useAuth', () => ({
  useAuth: () => ({ syncUser: vi.fn(), user: { id: 'u1' } }),
}))
vi.mock('../../lib/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/hooks')>()
  return { ...actual, useUnsavedGuard: vi.fn(), useSaveShortcut: vi.fn() }
})
vi.mock('../../hooks/useChapterPlans', () => ({
  useChapterPlans: () => ({
    entries: planHook.entries,
    plans: new Map(planHook.entries.map((entry) => [entry.node_id, entry.plan])),
    mode: 'auto',
    loading: false,
    error: '',
    refresh: planHook.refresh,
    setPlan: planHook.setPlan,
  }),
  missingPlanNodeIds: vi.fn(() => ['n1']),
  batchRegeneratePlans: vi.fn(async () => ({ total: 1, succeeded: ['n1'], failed: [] })),
  batchGeneratePlans: vi.fn(),
  batchConfirmPlans: vi.fn(),
  pendingConfirmNodeIds: vi.fn(() => []),
}))
vi.mock('../../api/collaboration', () => ({
  acquireChapterLock: vi.fn(),
  getChapterDiff: vi.fn(),
  getChapterLock: vi.fn(async () => null),
  listChapterRevisions: vi.fn(async () => []),
  releaseChapterLock: vi.fn(),
  renewChapterLock: vi.fn(),
  restoreChapterRevision: vi.fn(),
}))
vi.mock('../../api/generation', () => ({
  generateChapter: vi.fn(),
  getChapter: vi.fn(),
  getChapterCitations: vi.fn(),
  getChapterPlan: vi.fn(),
  getJob: vi.fn(),
  getOutline: vi.fn(),
  getProjectJobs: vi.fn(),
  reviewChapter: vi.fn(),
  saveChapter: vi.fn(),
  startDocumentGeneration: vi.fn(),
  startImageBatch: vi.fn(),
}))
vi.mock('../../api/images', () => ({
  applyImagePlan: vi.fn(),
  createImage: vi.fn(),
  deleteImage: vi.fn(),
  getImagePlan: vi.fn(),
  getImagePolicy: vi.fn(),
  listImages: vi.fn(),
  regenerateImage: vi.fn(),
  updateImagePolicy: vi.fn(),
  uploadImage: vi.fn(),
}))

describe('DraftTab auto 模式失效规划重建', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    workspace.canWrite = true
    planHook.entries = [{ node_id: 'n1', plan: null, mode: 'auto' }]
    vi.mocked(getOutline).mockResolvedValue([
      {
        id: 'n1',
        heading: '第一章 技术方案',
        intent: '',
        target_words: 1000,
        words: 0,
        has_body: false,
        status: 'draft',
        review_status: 'draft',
      },
    ])
    vi.mocked(getChapter).mockResolvedValue({ id: 'n1', body: '', revision: 1 } as never)
    vi.mocked(getChapterCitations).mockResolvedValue([] as never)
    vi.mocked(getProjectJobs).mockResolvedValue([])
    vi.mocked(startDocumentGeneration).mockResolvedValue({ job_id: 'job-1' } as never)
  })

  it('全书生成前自动重新生成失效规划，再提交生成任务', async () => {
    render(
      <MemoryRouter>
        <DraftTab />
      </MemoryRouter>,
    )
    const button = await screen.findByRole('button', { name: /生成全书正文/ })
    fireEvent.click(button)
    await waitFor(() => expect(batchRegeneratePlans).toHaveBeenCalled())
    expect(vi.mocked(batchRegeneratePlans).mock.calls[0]?.[1]).toEqual(['n1'])
    expect(missingPlanNodeIds).toHaveBeenCalled()
    await waitFor(() => expect(startDocumentGeneration).toHaveBeenCalledWith(PROJECT_REF, false))
  })
})

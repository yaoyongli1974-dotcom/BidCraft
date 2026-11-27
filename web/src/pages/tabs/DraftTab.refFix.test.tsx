import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DraftTab } from './DraftTab'
import { getChapterLock, listChapterRevisions } from '../../api/collaboration'
import {
  getChapter,
  getChapterCitations,
  getChapterPlan,
  getOutline,
  getProjectJobs,
} from '../../api/generation'
import { clearQueryCache } from '../../lib/queryCache'

// 项目 ref 是 UUID 字符串：作为 prop 传下去时必须叫 projectRef，不能叫 ref。
const PROJECT_REF = '22222222-2222-2222-2222-222222222222'

const workspace = vi.hoisted(() => ({
  ref: '22222222-2222-2222-2222-222222222222',
  basePath: '/app/projects/22222222-2222-2222-2222-222222222222',
  project: {
    id: '22222222-2222-2222-2222-222222222222',
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
vi.mock('../../api/collaboration', () => ({
  acquireChapterLock: vi.fn(),
  getChapterDiff: vi.fn(),
  getChapterLock: vi.fn(),
  listChapterRevisions: vi.fn(),
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

describe('DraftTab 章节协作的项目 ref', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    workspace.canWrite = true
    workspace.project.my_role = 'owner'
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
    vi.mocked(getChapter).mockResolvedValue({
      id: 'n1',
      body: '',
      revision: 1,
    } as never)
    vi.mocked(getChapterCitations).mockResolvedValue([] as never)
    vi.mocked(getChapterPlan).mockResolvedValue({ plan: null, mode: 'auto' })
    vi.mocked(getProjectJobs).mockResolvedValue([])
    vi.mocked(getChapterLock).mockResolvedValue(null)
    vi.mocked(listChapterRevisions).mockResolvedValue([])
  })

  it('单个大纲章节打开编辑器，并用项目 UUID 查询章节编辑锁', async () => {
    render(
      <MemoryRouter>
        <DraftTab />
      </MemoryRouter>,
    )

    expect(
      await screen.findByRole('textbox', { name: '第一章 技术方案 正文' }),
    ).toBeTruthy()
    await waitFor(() => expect(getChapterLock).toHaveBeenCalledWith(PROJECT_REF, 'n1'))
    expect(vi.mocked(getChapterLock).mock.calls[0]?.[0]).toBe(PROJECT_REF)
  })
})

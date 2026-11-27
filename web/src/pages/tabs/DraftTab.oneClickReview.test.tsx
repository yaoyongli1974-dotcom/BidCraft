import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DraftTab } from './DraftTab'
import {
  getChapter,
  getChapterCitations,
  getChapterPlan,
  getOutline,
  getProjectJobs,
  reviewChapter,
  saveChapter,
} from '../../api/generation'
import { clearQueryCache } from '../../lib/queryCache'

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

describe('DraftTab 一键提交复核并通过', () => {
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
        words: 20,
        has_body: true,
        status: 'draft',
        review_status: 'draft',
      },
    ])
    vi.mocked(getChapter).mockResolvedValue({
      id: 'n1',
      body: '已有正文内容',
      revision: 1,
      review_status: 'draft',
    } as never)
    vi.mocked(getChapterCitations).mockResolvedValue([] as never)
    vi.mocked(getChapterPlan).mockResolvedValue({ plan: null, mode: 'auto' })
    vi.mocked(getProjectJobs).mockResolvedValue([])
    vi.mocked(reviewChapter).mockImplementation(async (_ref, _nodeId, action) => ({
      id: 'n1',
      body: '已有正文内容',
      revision: 2,
      review_status: action === 'submit' ? 'review' : 'confirmed',
    } as never))
    vi.mocked(saveChapter).mockResolvedValue({
      id: 'n1',
      body: '修改后的正文',
      revision: 2,
      review_status: 'draft',
    } as never)
  })

  it('已保存正文直接提交并确认，不跳过任何状态', async () => {
    render(
      <MemoryRouter>
        <DraftTab />
      </MemoryRouter>,
    )
    const button = await screen.findByRole('button', { name: /提交复核并通过/ })
    await waitFor(() => expect(getChapter).toHaveBeenCalledWith(PROJECT_REF, 'n1'))
    await screen.findByText('已有正文内容')
    fireEvent.click(button)
    await waitFor(() =>
      expect(reviewChapter).toHaveBeenNthCalledWith(1, PROJECT_REF, 'n1', 'submit', ''),
    )
    expect(reviewChapter).toHaveBeenNthCalledWith(2, PROJECT_REF, 'n1', 'confirm', '')
    expect(saveChapter).not.toHaveBeenCalled()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('已提交复核并通过', '第一章 技术方案'))
  })

  it('有未保存修改时先保存，再提交并确认', async () => {
    render(
      <MemoryRouter>
        <DraftTab />
      </MemoryRouter>,
    )
    fireEvent.click(await screen.findByRole('button', { name: '编辑' }))
    const editor = await screen.findByRole('textbox', { name: '第一章 技术方案 正文' })
    fireEvent.change(editor, { target: { value: '修改后的正文' } })
    const button = await screen.findByRole('button', { name: /提交复核并通过/ })
    fireEvent.click(button)
    await waitFor(() =>
      expect(saveChapter).toHaveBeenCalledWith(PROJECT_REF, 'n1', '修改后的正文', 1),
    )
    await waitFor(() =>
      expect(reviewChapter).toHaveBeenNthCalledWith(1, PROJECT_REF, 'n1', 'submit', ''),
    )
    expect(reviewChapter).toHaveBeenNthCalledWith(2, PROJECT_REF, 'n1', 'confirm', '')
  })

  it('普通编辑一键保存并提交复核，不执行负责人确认', async () => {
    workspace.project.my_role = 'editor'
    render(
      <MemoryRouter>
        <DraftTab />
      </MemoryRouter>,
    )
    fireEvent.click(await screen.findByRole('button', { name: '编辑' }))
    const editor = await screen.findByRole('textbox', { name: '第一章 技术方案 正文' })
    fireEvent.change(editor, { target: { value: '修改后的正文' } })
    const button = await screen.findByRole('button', { name: /保存并提交复核/ })
    await waitFor(() => expect(getChapter).toHaveBeenCalledWith(PROJECT_REF, 'n1'))
    fireEvent.click(button)
    await waitFor(() =>
      expect(saveChapter).toHaveBeenCalledWith(PROJECT_REF, 'n1', '修改后的正文', 1),
    )
    await waitFor(() =>
      expect(reviewChapter).toHaveBeenCalledWith(PROJECT_REF, 'n1', 'submit', ''),
    )
    expect(reviewChapter).not.toHaveBeenCalledWith(PROJECT_REF, 'n1', 'confirm', '')
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('已保存并提交复核', '第一章 技术方案'),
    )
  })
})

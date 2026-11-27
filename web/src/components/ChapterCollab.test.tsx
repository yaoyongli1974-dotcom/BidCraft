import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { acquireChapterLock, getChapterLock, listChapterRevisions } from '../api/collaboration'
import { clearQueryCache } from '../lib/queryCache'
import { ChapterCollab } from './ChapterCollab'

// 项目 ref 是 UUID 字符串，不能命名为 `ref`，否则 React 会把它当作组件 ref 拦截。
const PROJECT_REF = '11111111-1111-1111-1111-111111111111'

const toast = vi.hoisted(() => ({
  notify: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
  dismiss: vi.fn(),
}))

const auth = vi.hoisted(() => ({ user: { id: 'u1', username: 'editor1' } }))

vi.mock('../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../context/useAuth', () => ({ useAuth: () => auth }))
vi.mock('../api/collaboration', () => ({
  acquireChapterLock: vi.fn(),
  getChapterDiff: vi.fn(),
  getChapterLock: vi.fn(),
  listChapterRevisions: vi.fn(),
  releaseChapterLock: vi.fn(),
  renewChapterLock: vi.fn(),
  restoreChapterRevision: vi.fn(),
}))

function renderCollab() {
  return render(
    <ChapterCollab
      projectRef={PROJECT_REF}
      nodeId="n1"
      heading="第一章 技术方案"
      canWrite
    />,
  )
}

describe('ChapterCollab 项目 ref 传参', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    vi.mocked(getChapterLock).mockResolvedValue(null)
    vi.mocked(listChapterRevisions).mockResolvedValue([])
  })

  it('用 projectRef 查询章节编辑锁', async () => {
    renderCollab()
    expect(await screen.findByText(/未锁定/)).toBeTruthy()
    await waitFor(() => expect(getChapterLock).toHaveBeenCalledWith(PROJECT_REF, 'n1'))
    expect(vi.mocked(getChapterLock).mock.calls[0]?.[0]).toBe(PROJECT_REF)
  })

  it('用 projectRef 获取章节编辑锁', async () => {
    vi.mocked(acquireChapterLock).mockResolvedValue({
      node_id: 'n1',
      user_id: 'u1',
      display_name: '编辑甲',
      expires_at: '2030-01-01T00:00:00.000Z',
      lock_token: 'token-1',
    })
    renderCollab()
    fireEvent.click(await screen.findByRole('button', { name: '获取编辑锁' }))
    await waitFor(() =>
      expect(acquireChapterLock).toHaveBeenCalledWith(PROJECT_REF, 'n1'),
    )
    expect(await screen.findByText(/已获取编辑锁|编辑锁/)).toBeTruthy()
  })
})

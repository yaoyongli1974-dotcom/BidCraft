import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AssignmentPanel } from './AssignmentPanel'
import { listAssignments } from '../api/collaboration'
import { getOutline } from '../api/generation'
import { clearQueryCache } from '../lib/queryCache'
import type { Member } from '../lib/types'

const toast = vi.hoisted(() => ({
  notify: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), dismiss: vi.fn(),
}))

vi.mock('../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../api/collaboration', () => ({
  listAssignments: vi.fn(),
  createAssignment: vi.fn(),
  updateAssignment: vi.fn(),
}))
vi.mock('../api/generation', () => ({
  getOutline: vi.fn(),
}))

const MEMBERS: Member[] = [
  { user_id: 'u1', username: 'editor1', display_name: '编辑甲', role: 'editor' },
  { user_id: 'u2', username: 'viewer1', display_name: '浏览乙', role: 'viewer' },
]
const CHAPTERS = [
  { id: 'n1', heading: '第一章 项目理解', target_words: 800, status: 'draft', review_status: 'draft' },
]

describe('AssignmentPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    vi.mocked(listAssignments).mockResolvedValue([
      { id: 'a1', node_id: 'n1', assignee_id: 'u1', status: 'in_progress', note: '抓紧写' },
    ] as never)
    vi.mocked(getOutline).mockResolvedValue(CHAPTERS as never)
  })

  it('renders the assignment panel and loads data', async () => {
    render(<AssignmentPanel projectRef="p1" members={MEMBERS} canWrite />)
    expect(await screen.findByText('章节分工')).toBeTruthy()
    await waitFor(() => expect(listAssignments).toHaveBeenCalledWith('p1'))
    await waitFor(() => expect(getOutline).toHaveBeenCalledWith('p1'))
    expect(screen.getAllByText(/第一章/).length).toBeGreaterThan(0)
  })

  it('renders read-only without crashing', async () => {
    render(<AssignmentPanel projectRef="p1" members={MEMBERS} canWrite={false} />)
    expect(await screen.findByText('章节分工')).toBeTruthy()
  })
})

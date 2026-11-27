import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  createBoqVersion: vi.fn(),
  listBoqVersions: vi.fn(async () => []),
  getMembers: vi.fn(async () => []),
  setData: vi.fn(),
}))
const { createBoqVersion } = mocks

vi.mock('../../api/boq', () => ({
  createBoqVersion: mocks.createBoqVersion,
  listBoqVersions: mocks.listBoqVersions,
  deleteBoqVersion: vi.fn(),
  getBoqVersionDiff: vi.fn(),
  restoreBoqVersion: vi.fn(),
  reviewBoqVersion: vi.fn(),
  submitBoqVersion: vi.fn(),
  unapproveBoqVersion: vi.fn(),
}))

vi.mock('../../api/project', () => ({
  getMembers: mocks.getMembers,
  applicableStages: vi.fn(() => []),
  explainApprovalStages: vi.fn(() => []),
  normalizeApprovalExplain: vi.fn(() => []),
  updateProject: vi.fn(),
}))

vi.mock('../../lib/hooks', () => ({
  useQuery: vi.fn(() => ({
    data: [],
    loading: false,
    error: null,
    refresh: vi.fn(),
    setData: mocks.setData,
  })),
}))

vi.mock('../../context/useToast', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}))

vi.mock('../../context/useConfirm', () => ({
  useConfirm: () => vi.fn(async () => true),
}))

import { QuoteVersionsPanel } from './BoqVersions'

describe('QuoteVersionsPanel save-and-submit', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    createBoqVersion.mockResolvedValue({
      id: 'version-1',
      name: '版本 1',
      status: 'draft',
      note: '',
      package_id: null,
      summary: { total_amount: 0, priced: 0, unpriced: 0, total_diffs: 0, match_rate: 0 },
      approvals: [],
      price_count: 0,
      created_at: '',
      updated_at: '',
    })
  })

  it('offers a one-click save-and-submit action for the current quote', async () => {
    render(
      <QuoteVersionsPanel
        projectRef="p1"
        packageId={null}
        canWrite
        canReview
        approvalChain={[]}
        isOwner
        currentUserId="u1"
        isSuperAdmin={false}
        onChainSaved={() => undefined}
        onRestored={() => undefined}
      />,
    )

    const button = screen.getByRole('button', { name: '保存并提交审批' })
    fireEvent.click(button)

    await waitFor(() => {
      expect(createBoqVersion).toHaveBeenCalledWith('p1', {
        name: '版本 1',
        note: '',
        package_id: null,
      })
    })
    expect(await screen.findByText('提交「版本 1」审批？')).toBeTruthy()
  })
})

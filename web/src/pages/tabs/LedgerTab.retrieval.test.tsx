import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getLedger } from '../../api/ledger'
import { clearQueryCache } from '../../lib/queryCache'
import { LedgerTab } from './LedgerTab'

const workspace = vi.hoisted(() => ({
  ref: 'p1',
  project: { id: 'p1', name: '测试项目', my_role: 'viewer' as string | undefined },
  reloadProject: vi.fn(async () => ({})),
  setUnsaved: vi.fn(),
  canWrite: false,
  readOnly: true,
}))

vi.mock('../useWorkspace', () => ({ useWorkspace: () => workspace }))
vi.mock('../../context/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))
vi.mock('../../context/useConfirm', () => ({ useConfirm: () => vi.fn(async () => true) }))
vi.mock('../../lib/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/hooks')>()
  return { ...actual, useUnsavedGuard: vi.fn() }
})
vi.mock('../../api/ledger', () => ({
  getLedger: vi.fn(),
  updateFact: vi.fn(),
}))

function renderTab() {
  return render(
    <MemoryRouter>
      <LedgerTab />
    </MemoryRouter>,
  )
}

describe('LedgerTab 检索预览集成与权限', () => {
  beforeEach(() => {
    clearQueryCache()
    vi.clearAllMocks()
    vi.mocked(getLedger).mockResolvedValue({
      facts: [],
      materials: [],
      parameters: [],
      scoring: [],
      requirements: [],
      risks: [],
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('viewer / editor 看到的是只读检索预览，查询仍可用', async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [] }),
      text: async () => '{"items":[]}',
    })
    vi.stubGlobal('fetch', fetch)

    renderTab()
    const preview = await screen.findByRole('region', { name: '检索预览' })
    expect(preview.textContent).toContain('只读')
    fireEvent.change(screen.getByLabelText('检索预览查询'), { target: { value: '证书' } })
    fireEvent.click(screen.getByRole('button', { name: '检索' }))

    await waitFor(() => expect(fetch.mock.calls[0][0]).toContain('/projects/p1/retrieval/preview?q='))
    expect(await screen.findByText('没有命中片段')).toBeInTheDocument()
    expect((screen.getByRole('button', { name: '刷新台账' }) as HTMLButtonElement).disabled).toBe(
      false,
    )
  })

  it('editor（可写）也不显示任何知识资产写操作', async () => {
    workspace.canWrite = true
    workspace.readOnly = false
    renderTab()
    await screen.findByRole('region', { name: '检索预览' })
    const preview = screen.getByRole('region', { name: '检索预览' })
    expect(preview.textContent).toContain('只读')
    expect(within(preview).getAllByRole('button')).toHaveLength(1)
    expect(within(preview).getByRole('button', { name: '检索' })).toBeInTheDocument()
  })
})

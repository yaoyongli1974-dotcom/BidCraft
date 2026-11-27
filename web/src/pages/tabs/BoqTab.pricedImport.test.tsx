import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BoqTab } from './BoqTab'
import { uploadBoq } from '../../api/boq'
import type { BoqPayload } from '../../lib/types'

const state = vi.hoisted(() => ({
  boq: null as BoqPayload | null,
  packages: { supported: false, packages: [] } as unknown,
}))

const workspace = vi.hoisted(() => ({
  ref: 'p1',
  basePath: '/app/projects/p1',
  project: { id: 'p1', name: '清单项目', status: 'draft', my_role: 'owner' },
  canWrite: true,
  readOnly: false,
  reloadProject: vi.fn(async () => undefined),
}))

const toast = vi.hoisted(() => ({
  notify: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
  dismiss: vi.fn(),
}))

function queryState(data: unknown) {
  return {
    data,
    error: null,
    loading: false,
    refreshing: false,
    refresh: vi.fn(),
    setData: vi.fn(),
  }
}

vi.mock('../useWorkspace', () => ({ useWorkspace: () => workspace }))
vi.mock('../../context/useAuth', () => ({
  useAuth: () => ({ user: { id: 'u1', role: 'admin' }, isSuperAdmin: false }),
}))
vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../context/useConfirm', () => ({ useConfirm: () => vi.fn(async () => true) }))
vi.mock('../../lib/hooks', () => ({
  useQuery: (key: string) => {
    if (key.startsWith('boq-packages:')) return queryState(state.packages)
    if (key.startsWith('boq:')) return queryState(state.boq)
    if (key.startsWith('price-policy:')) return queryState(null)
    return queryState(undefined)
  },
}))
vi.mock('../../api/boq', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/boq')>()
  return { ...actual, uploadBoq: vi.fn() }
})

function pricedPayload(): BoqPayload {
  return {
    snapshot: {
      id: 's1',
      filename: 'priced.xlsx',
      sheet: '工程量清单',
      header_row: 1,
      item_count: 2,
      warnings: [],
      created_at: '2026-11-26T08:00:00.000Z',
    },
    rows: [],
    summary: {
      boq_rows: 2,
      matched: 0,
      qty_diff: 0,
      unit_diff: 0,
      spec_diff: 0,
      missing_in_ledger: 2,
      missing_in_boq: 0,
      ignored: 0,
      total_diffs: 0,
      match_rate: 0.0,
      priced: 2,
      unpriced: 0,
      quantity_missing: 0,
      total_amount: 325.0,
      tax_amount: null,
      total_with_tax: null,
      cny_total_with_tax: null,
      adjusted_total: null,
    },
    ledger_summary: { materials: 0, matched: 0, missing_in_boq: 0 },
  }
}

function renderTab() {
  return render(
    <MemoryRouter initialEntries={['/app/projects/p1/boq']}>
      <BoqTab />
    </MemoryRouter>,
  )
}

describe('BoqTab imported prices', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.boq = pricedPayload()
  })

  it('describes prices as imported and overridable, not manual-only', () => {
    renderTab()

    expect(screen.getByText(/清单中的单价\/合价会自动识别/)).toBeTruthy()
    expect(screen.getByText(/单价优先取清单中识别到的「单价\/合价」列/)).toBeTruthy()
    expect(screen.queryByText(/程序不会自动生成价格/)).toBeNull()
  })

  it('reports the number of extracted prices after upload', async () => {
    state.boq = null
    vi.mocked(uploadBoq).mockResolvedValue(pricedPayload())
    renderTab()

    const input = screen.getByLabelText('选择工程量清单文件')
    fireEvent.change(input, {
      target: { files: [new File(['xlsx'], 'priced.xlsx')] },
    })

    await waitFor(() => {
      expect(uploadBoq).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith(
        '清单已导入',
        expect.stringContaining('识别到 2 条单价'),
      )
    })
  })
})

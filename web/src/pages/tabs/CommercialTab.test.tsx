import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CommercialTab } from './CommercialTab'
import {
  createCommercialDoc,
  exportCommercialDoc,
  listCommercialDocs,
  submitCommercialDoc,
} from '../../api/commercial'
import { listBoqVersions } from '../../api/boq'
import { downloadBlob } from '../../api/generation'
import type { BoqQuoteVersion, CommercialDoc } from '../../lib/types'
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

const confirmStub = vi.hoisted(() => vi.fn(async () => true))

vi.mock('../useWorkspace', () => ({ useWorkspace: () => workspace }))
vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../context/useConfirm', () => ({ useConfirm: () => confirmStub }))
vi.mock('../../api/boq', () => ({ listBoqVersions: vi.fn() }))
vi.mock('../../api/generation', () => ({ downloadBlob: vi.fn() }))
vi.mock('../../api/commercial', () => ({
  listCommercialDocs: vi.fn(),
  createCommercialDoc: vi.fn(),
  deleteCommercialDoc: vi.fn(),
  exportCommercialDoc: vi.fn(),
  submitCommercialDoc: vi.fn(),
  reviewCommercialDoc: vi.fn(),
  unapproveCommercialDoc: vi.fn(),
}))

const VERSION = {
  id: 'v1',
  name: '终版报价',
  note: '',
  package_id: null,
  summary: {},
  created_at: '2026-11-18T08:00:00.000Z',
  price_count: 10,
  status: 'approved',
  submit_note: '',
  submitted_by: '张三',
  submitted_at: null,
  review_note: '',
  reviewed_by: '李四',
  reviewed_at: null,
  approvals: [],
  approval_stage: 0,
  approval_stage_count: 1,
} as unknown as BoqQuoteVersion

function commercialDoc(overrides: Partial<CommercialDoc> = {}): CommercialDoc {
  return {
    id: 'c1',
    name: 'XX 项目商务标',
    quote_version_id: 'v1',
    package_id: null,
    status: 'draft',
    sections: [
      { key: 'price', title: '投标报价', content: '报价合计 1,200,000 元', status: 'ok' },
      { key: 'terms', title: '商务条款', content: '响应全部付款条件', status: 'ok' },
    ],
    validation: { ok: false, issues: [{ code: 'V-01', message: '缺少履约保证金说明' }] },
    submit_note: '',
    submitted_by: '',
    submitted_at: null,
    review_note: '',
    reviewed_by: '',
    reviewed_at: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}

function renderTab() {
  return render(
    <MemoryRouter initialEntries={['/app/projects/p1/commercial']}>
      <CommercialTab />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  clearQueryCache()
  vi.clearAllMocks()
  workspace.canWrite = true
  workspace.readOnly = false
  confirmStub.mockResolvedValue(true)
  vi.mocked(listBoqVersions).mockResolvedValue([VERSION])
  vi.mocked(listCommercialDocs).mockResolvedValue([commercialDoc()])
  vi.mocked(createCommercialDoc).mockResolvedValue(commercialDoc({ id: 'c2', name: '新商务标' }))
  vi.mocked(submitCommercialDoc).mockResolvedValue(commercialDoc({ status: 'submitted' }))
  vi.mocked(exportCommercialDoc).mockResolvedValue({
    blob: new Blob(['x']),
    filename: '商务标.xlsx',
  })
})

describe('CommercialTab', () => {
  it('warns and disables creation when there is no approved quote version', async () => {
    vi.mocked(listBoqVersions).mockResolvedValue([])
    vi.mocked(listCommercialDocs).mockResolvedValue([])
    renderTab()

    expect(
      await screen.findByText(/尚无已定稿的报价版本，不能新建或定稿商务标/),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新建商务标' })).toBeDisabled()
  })

  it('renders document status, validation issues and section preview', async () => {
    renderTab()

    expect(await screen.findByText('XX 项目商务标')).toBeInTheDocument()
    expect(screen.getByText('草稿')).toBeInTheDocument()
    expect(screen.getByText('缺少履约保证金说明')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /章节预览/ }))
    expect(screen.getByText('投标报价')).toBeInTheDocument()
    expect(screen.getByText('报价合计 1,200,000 元')).toBeInTheDocument()
  })

  it('creates a commercial document bound to the approved version', async () => {
    vi.mocked(listCommercialDocs).mockResolvedValue([])
    renderTab()

    const createButtons = await screen.findAllByRole('button', { name: '新建商务标' })
    fireEvent.click(createButtons[0])

    fireEvent.change(await screen.findByLabelText('商务标名称'), {
      target: { value: '新商务标' },
    })
    fireEvent.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() =>
      expect(createCommercialDoc).toHaveBeenCalledWith('p1', {
        name: '新商务标',
        quote_version_id: 'v1',
      }),
    )
    expect(toast.success).toHaveBeenCalled()
  })

  it('submits a draft with an optional note', async () => {
    renderTab()

    const card = (await screen.findByText('XX 项目商务标')).closest('article') as HTMLElement
    fireEvent.click(within(card).getByRole('button', { name: '提交审批' }))
    fireEvent.change(await screen.findByLabelText(/提交说明/), {
      target: { value: '请审批终版报价' },
    })
    fireEvent.click(screen.getByRole('button', { name: '确认提交审批' }))

    await waitFor(() =>
      expect(submitCommercialDoc).toHaveBeenCalledWith('p1', 'c1', '请审批终版报价'),
    )
  })

  it('disables write actions for read-only members but keeps export available', async () => {
    workspace.canWrite = false
    workspace.readOnly = true
    workspace.project.my_role = 'viewer'
    renderTab()

    const card = (await screen.findByText('XX 项目商务标')).closest('article') as HTMLElement
    expect(within(card).getByRole('button', { name: '提交审批' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '新建商务标' })).toBeDisabled()

    fireEvent.click(within(card).getByRole('button', { name: '导出 xlsx' }))
    await waitFor(() =>
      expect(exportCommercialDoc).toHaveBeenCalledWith('p1', 'c1', 'xlsx'),
    )
    expect(downloadBlob).toHaveBeenCalled()
  })
})

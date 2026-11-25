import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { EnterpriseTab } from './EnterpriseTab'
import {
  addEnterpriseDocument,
  addEnterpriseMember,
  createEnterprise,
  getEnterpriseDocuments,
  getEnterpriseMembers,
  getEnterprisePackage,
  getEnterprises,
  listEnterpriseKnowledgeAssetDuplicates,
  listEnterpriseKnowledgeAssets,
  matchEnterpriseAssets,
} from '../../api/enterprise'
import type {
  Enterprise,
  EnterpriseDocument,
  EnterpriseMatch,
  KnowledgeAsset,
  KnowledgeAssetDuplicateGroup,
} from '../../lib/types'
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
vi.mock('../../api/enterprise', () => ({
  getEnterprises: vi.fn(),
  createEnterprise: vi.fn(),
  getEnterpriseMembers: vi.fn(),
  addEnterpriseMember: vi.fn(),
  removeEnterpriseMember: vi.fn(),
  getEnterpriseDocuments: vi.fn(),
  addEnterpriseDocument: vi.fn(),
  matchEnterpriseAssets: vi.fn(),
  getEnterprisePackage: vi.fn(),
  listEnterpriseKnowledgeAssets: vi.fn(),
  listEnterpriseKnowledgeAssetDuplicates: vi.fn(),
  extractEnterpriseKnowledgeAsset: vi.fn(),
  reviewEnterpriseKnowledgeAsset: vi.fn(),
  resolveEnterpriseKnowledgeAssetDuplicate: vi.fn(),
}))

const ENTERPRISE: Enterprise = {
  id: 'e1',
  name: '示例建设集团',
  role: 'owner',
  is_owner: true,
  member_count: 2,
  document_count: 3,
}

const ASSET: KnowledgeAsset = {
  id: 'a1',
  document_id: 'd1',
  kb_id: 'e1',
  source: '资质证书.pdf',
  review_status: 'draft',
  fields: { cert_no: 'C-1' },
  chunks: [{ id: 'c1', page: 1, heading: '资质证书' }],
}

const DUPLICATE_GROUP: KnowledgeAssetDuplicateGroup = {
  id: 'g1',
  fingerprint: 'fp-1',
  similarity: 0.91,
  status: 'pending',
  members: [{ id: 'm1', document_id: 'd1', score: 0.91, source: '资质证书.pdf' }],
}

const DOCUMENT: EnterpriseDocument = {
  id: 'd1',
  source: '安全生产许可证',
  content: '证书编号 AQ-2026，有效期三年。',
  kind: 'certificate',
  tags: ['安全', '证书'],
  valid_to: '2027-12-31',
}

const MATCH: EnterpriseMatch = {
  id: 'm1',
  document_id: 'd1',
  source: '安全生产许可证',
  kind: 'certificate',
  excerpt: '证书编号 AQ-2026',
  score: 0.93,
  tags: ['证书'],
}

function renderTab() {
  return render(
    <MemoryRouter initialEntries={['/app/projects/p1/enterprise']}>
      <EnterpriseTab />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  clearQueryCache()
  vi.clearAllMocks()
  workspace.canWrite = true
  workspace.readOnly = false
  confirmStub.mockResolvedValue(true)
  vi.mocked(getEnterprises).mockResolvedValue([ENTERPRISE])
  vi.mocked(getEnterpriseMembers).mockResolvedValue([
    {
      user_id: 'u1',
      username: 'zhangsan',
      display_name: '张三',
      role: 'owner',
    },
  ])
  vi.mocked(getEnterpriseDocuments).mockResolvedValue([DOCUMENT])
  vi.mocked(createEnterprise).mockResolvedValue({ id: 'e2', name: '新企业' })
  vi.mocked(addEnterpriseMember).mockResolvedValue({
    user_id: 'u2',
    username: 'lisi',
    display_name: '李四',
  })
  vi.mocked(addEnterpriseDocument).mockResolvedValue({
    ...DOCUMENT,
    id: 'd2',
    source: '类似业绩',
  })
  vi.mocked(matchEnterpriseAssets).mockResolvedValue([MATCH])
  vi.mocked(listEnterpriseKnowledgeAssets).mockResolvedValue([])
  vi.mocked(listEnterpriseKnowledgeAssetDuplicates).mockResolvedValue([])
  vi.mocked(getEnterprisePackage).mockResolvedValue({
    name: '项目材料包',
    items: [{ label: '安全生产许可证', count: 1 }],
    documents: [DOCUMENT],
  })
})

describe('EnterpriseTab', () => {
  it('renders the selected enterprise with members and shared documents', async () => {
    renderTab()

    expect(await screen.findByText('示例建设集团')).toBeInTheDocument()
    expect(await screen.findByText('张三')).toBeInTheDocument()
    expect(await screen.findByText('安全生产许可证')).toBeInTheDocument()
    expect(screen.getByText('安全')).toBeInTheDocument()
    expect(getEnterpriseMembers).toHaveBeenCalledWith('e1')
    expect(getEnterpriseDocuments).toHaveBeenCalledWith('e1')
  })

  it('creates an enterprise from the empty state', async () => {
    vi.mocked(getEnterprises).mockResolvedValue([])
    renderTab()

    expect(await screen.findByText('还没有企业')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('企业名称'), {
      target: { value: '新企业' },
    })
    fireEvent.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => expect(createEnterprise).toHaveBeenCalledWith('新企业'))
    expect(toast.success).toHaveBeenCalled()
  })

  it('adds a member and a document to the selected enterprise', async () => {
    renderTab()

    await screen.findByText('张三')
    fireEvent.change(screen.getByLabelText('成员用户名'), {
      target: { value: 'lisi' },
    })
    fireEvent.click(screen.getByRole('button', { name: '添加成员' }))
    await waitFor(() => expect(addEnterpriseMember).toHaveBeenCalledWith('e1', { username: 'lisi' }))

    fireEvent.change(screen.getByLabelText('资料名称'), {
      target: { value: '类似业绩' },
    })
    fireEvent.change(screen.getByLabelText('资料内容摘要'), {
      target: { value: '近三年同类项目业绩三项。' },
    })
    fireEvent.click(screen.getByRole('button', { name: '资料入库' }))

    await waitFor(() =>
      expect(addEnterpriseDocument).toHaveBeenCalledWith(
        'e1',
        expect.objectContaining({ source: '类似业绩', content: '近三年同类项目业绩三项。' }),
      ),
    )
  })

  it('matches project requirements against enterprise assets', async () => {
    renderTab()

    await screen.findByText('示例建设集团')
    fireEvent.change(screen.getByLabelText('需求描述'), {
      target: { value: '投标人须具备安全生产许可证' },
    })
    fireEvent.change(screen.getByLabelText('资料类型（可选）'), {
      target: { value: 'certificate' },
    })
    fireEvent.click(screen.getByRole('button', { name: '匹配资产' }))

    await waitFor(() =>
      expect(matchEnterpriseAssets).toHaveBeenCalledWith('p1', {
        requirement: '投标人须具备安全生产许可证',
        kind: 'certificate',
      }),
    )
    expect(await screen.findByText('匹配度 0.93')).toBeInTheDocument()
    expect(screen.getByText('证书编号 AQ-2026')).toBeInTheDocument()
  })

  it('disables management actions for read-only members', async () => {
    workspace.canWrite = false
    workspace.readOnly = true
    renderTab()

    await screen.findByText('示例建设集团')
    expect(screen.getByRole('button', { name: '创建' })).toBeDisabled()
    expect(screen.getByLabelText('成员用户名')).toBeDisabled()
    expect(screen.getByLabelText('资料名称')).toBeDisabled()
    expect(screen.getByRole('button', { name: '资料入库' })).toBeDisabled()
  })

  it('disables knowledge asset mutations when the enterprise is not owned', async () => {
    vi.mocked(getEnterprises).mockResolvedValue([{ ...ENTERPRISE, is_owner: false }])
    vi.mocked(listEnterpriseKnowledgeAssets).mockResolvedValue([ASSET])
    vi.mocked(listEnterpriseKnowledgeAssetDuplicates).mockResolvedValue([DUPLICATE_GROUP])
    renderTab()

    expect(await screen.findByText('示例建设集团')).toBeInTheDocument()
    expect(await screen.findAllByText('资质证书.pdf')).not.toHaveLength(0)
    expect(listEnterpriseKnowledgeAssets).toHaveBeenCalledWith('e1')
    expect(screen.getByRole('button', { name: '提炼' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '提交复核' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '解决' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '忽略' })).toBeDisabled()
    expect(screen.getByLabelText('审核意见')).toBeDisabled()
  })

  it('enables knowledge asset mutations for an owning editor', async () => {
    vi.mocked(listEnterpriseKnowledgeAssets).mockResolvedValue([ASSET])
    vi.mocked(listEnterpriseKnowledgeAssetDuplicates).mockResolvedValue([DUPLICATE_GROUP])
    renderTab()

    expect(await screen.findAllByText('资质证书.pdf')).not.toHaveLength(0)
    expect(screen.getByRole('button', { name: '提炼' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '提交复核' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '解决' })).toBeEnabled()
  })
})

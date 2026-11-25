import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { KnowledgeAssetPanel } from './KnowledgeAssetPanel'
import { ApiError } from '../lib/http'
import { clearQueryCache } from '../lib/queryCache'
import type { KnowledgeAsset, KnowledgeAssetDuplicateGroup } from '../lib/types'

const mocks = vi.hoisted(() => ({
  admin: {
    listKnowledgeAssets: vi.fn(),
    listKnowledgeAssetDuplicates: vi.fn(),
    extractKnowledgeAsset: vi.fn(),
    reviewKnowledgeAsset: vi.fn(),
    resolveKnowledgeAssetDuplicate: vi.fn(),
  },
  enterprise: {
    listEnterpriseKnowledgeAssets: vi.fn(),
    listEnterpriseKnowledgeAssetDuplicates: vi.fn(),
    extractEnterpriseKnowledgeAsset: vi.fn(),
    reviewEnterpriseKnowledgeAsset: vi.fn(),
    resolveEnterpriseKnowledgeAssetDuplicate: vi.fn(),
  },
}))

vi.mock('../api/knowledge', () => ({ ...mocks.admin }))
vi.mock('../api/enterprise', () => ({ ...mocks.enterprise }))

const DRAFT: KnowledgeAsset = {
  id: 'a1',
  document_id: 'd1',
  kb_id: 'kb1',
  source: '资质证书.pdf',
  sha256: 'sha-1',
  review_status: 'draft',
  fields: { cert_no: 'C-1', owner: '示例公司' },
  chunks: [
    { id: 'c1', page: 3, heading: '资质证书', source_file_id: 'sf-1' },
  ],
  source_file_id: 'sf-1',
  ok: true,
  reason: '',
}

const GROUP: KnowledgeAssetDuplicateGroup = {
  id: 'g1',
  kb_id: 'kb1',
  fingerprint: 'fp-1',
  similarity: 0.92,
  status: 'pending',
  reason: '字段高度相似',
  members: [
    { id: 'm1', document_id: 'd1', score: 0.92, role: 'primary', source: '资质证书.pdf' },
  ],
}

beforeEach(() => {
  clearQueryCache()
  vi.clearAllMocks()
  mocks.admin.listKnowledgeAssets.mockResolvedValue([DRAFT])
  mocks.admin.listKnowledgeAssetDuplicates.mockResolvedValue([GROUP])
  mocks.enterprise.listEnterpriseKnowledgeAssets.mockResolvedValue([DRAFT])
  mocks.enterprise.listEnterpriseKnowledgeAssetDuplicates.mockResolvedValue([GROUP])
})

describe('KnowledgeAssetPanel', () => {
  it('渲染来源文档、提取字段、生命周期、溯源与重复组', async () => {
    render(<KnowledgeAssetPanel kbId="kb1" canManage canReview />)

    expect(await screen.findAllByText('资质证书.pdf')).not.toHaveLength(0)
    expect(screen.getByText('C-1')).toBeInTheDocument()
    expect(screen.getByText('示例公司')).toBeInTheDocument()
    expect(screen.getByText('资质证书')).toBeInTheDocument()
    expect(screen.getByText('sf-1')).toBeInTheDocument()
    expect(screen.getByText('sha-1')).toBeInTheDocument()
    expect(screen.getAllByText('草稿').length).toBeGreaterThan(0)
    expect(mocks.admin.listKnowledgeAssets).toHaveBeenCalledWith('kb1')
    expect(mocks.admin.listKnowledgeAssetDuplicates).toHaveBeenCalledWith('kb1')
  })

  it('空列表使用真实空状态，不伪造资产或重复组', async () => {
    mocks.admin.listKnowledgeAssets.mockResolvedValue([])
    mocks.admin.listKnowledgeAssetDuplicates.mockResolvedValue([])
    render(<KnowledgeAssetPanel kbId="kb1" canManage canReview />)

    expect(await screen.findByText('还没有可提炼的资产')).toBeInTheDocument()
    expect(screen.getByText('没有重复组')).toBeInTheDocument()
  })

  it('加载失败展示服务端消息', async () => {
    mocks.admin.listKnowledgeAssets.mockRejectedValue(
      new ApiError(500, null, '服务端暂时不可用'),
    )
    render(<KnowledgeAssetPanel kbId="kb1" canManage canReview />)

    expect(await screen.findByRole('alert')).toHaveTextContent('服务端暂时不可用')
  })

  it('无能力时所有写操作禁用', async () => {
    render(<KnowledgeAssetPanel kbId="kb1" />)
    await screen.findAllByText('资质证书.pdf')

    expect(screen.getByRole('button', { name: '提炼' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '提交复核' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '解决' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '忽略' })).toBeDisabled()
    expect(screen.getByLabelText('审核意见')).toBeDisabled()
    expect(screen.getByLabelText('重复组 g1 处理意见')).toBeDisabled()
  })

  it('非法状态流转禁用，合法流转开放', async () => {
    render(<KnowledgeAssetPanel kbId="kb1" canManage canReview />)
    await screen.findAllByText('资质证书.pdf')

    expect(screen.getByRole('button', { name: '提交复核' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '驳回' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '审核通过' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '发布' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '归档' })).toBeDisabled()
  })

  it('审核成功只展示服务端返回的新状态', async () => {
    mocks.admin.reviewKnowledgeAsset.mockResolvedValue({
      ...DRAFT,
      review_status: 'review',
      reviewed_by: 'u-admin',
    })
    render(<KnowledgeAssetPanel kbId="kb1" canManage canReview />)
    await screen.findAllByText('资质证书.pdf')

    fireEvent.change(screen.getByLabelText('审核意见'), {
      target: { value: '请复核' },
    })
    fireEvent.click(screen.getByRole('button', { name: '提交复核' }))

    await waitFor(() =>
      expect(mocks.admin.reviewKnowledgeAsset).toHaveBeenCalledWith('kb1', 'd1', {
        decision: 'review',
        note: '请复核',
      }),
    )
    expect((await screen.findAllByText('待审核')).length).toBeGreaterThan(0)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('审核失败只显示后端错误，状态保持不变', async () => {
    mocks.admin.reviewKnowledgeAsset.mockRejectedValueOnce(
      new ApiError(403, '没有审核权限'),
    )
    render(<KnowledgeAssetPanel kbId="kb1" canManage canReview />)
    await screen.findAllByText('资质证书.pdf')

    fireEvent.click(screen.getByRole('button', { name: '提交复核' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('没有审核权限')
    expect(mocks.admin.reviewKnowledgeAsset).toHaveBeenCalledTimes(1)
    expect(screen.getAllByText('草稿').length).toBeGreaterThan(0)
    expect(screen.queryByText('待审核')).toBeNull()
  })

  it('重复组裁决提交 decide/note 并展示服务端返回的组状态', async () => {
    mocks.admin.resolveKnowledgeAssetDuplicate.mockResolvedValue({
      ...GROUP,
      status: 'resolved',
    })
    render(<KnowledgeAssetPanel kbId="kb1" canManage canReview />)
    await screen.findAllByText('资质证书.pdf')

    fireEvent.change(screen.getByLabelText('重复组 g1 处理意见'), {
      target: { value: '已确认' },
    })
    fireEvent.click(screen.getByRole('button', { name: '解决' }))

    await waitFor(() =>
      expect(mocks.admin.resolveKnowledgeAssetDuplicate).toHaveBeenCalledWith('kb1', 'g1', {
        decision: 'resolve',
        note: '已确认',
      }),
    )
    expect(await screen.findByText('resolved')).toBeInTheDocument()
  })

  it('传 enterpriseId 时全部请求走企业接口', async () => {
    render(<KnowledgeAssetPanel enterpriseId="e1" canManage canReview />)
    await screen.findAllByText('资质证书.pdf')

    expect(screen.getByRole('heading', { name: '企业知识资产' })).toBeInTheDocument()
    expect(mocks.enterprise.listEnterpriseKnowledgeAssets).toHaveBeenCalledWith('e1')
    expect(mocks.enterprise.listEnterpriseKnowledgeAssetDuplicates).toHaveBeenCalledWith('e1')
    expect(mocks.admin.listKnowledgeAssets).not.toHaveBeenCalled()
  })

  it('缺少范围时不发请求并提示配置错误', async () => {
    render(<KnowledgeAssetPanel canManage canReview />)
    expect(await screen.findByText('未指定资产范围')).toBeInTheDocument()
    expect(mocks.admin.listKnowledgeAssets).not.toHaveBeenCalled()
    expect(mocks.enterprise.listEnterpriseKnowledgeAssets).not.toHaveBeenCalled()
  })
})

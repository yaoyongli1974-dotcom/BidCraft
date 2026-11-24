import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AdminKbDocsModal } from './AdminKbDocsModal'
import {
  deleteKbDoc,
  getKbDocs,
  listKbDocuments,
  previewKbRetrieval,
  updateKbDoc,
  type KbDocumentRow,
} from '../../api/knowledge'
import type { KbDocMetaResponse, KbSearchPassage, KnowledgeBase } from '../../lib/types'

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}))
const confirm = vi.hoisted(() => vi.fn())

vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../context/useConfirm', () => ({ useConfirm: () => confirm }))
vi.mock('../../api/knowledge', () => ({
  getKbDocs: vi.fn(),
  listKbDocuments: vi.fn(),
  previewKbRetrieval: vi.fn(),
  updateKbDoc: vi.fn(),
  deleteKbDoc: vi.fn(),
}))

const kb: KnowledgeBase = {
  id: 'kb-1',
  name: '投标知识库',
  kind: 'local',
  provider: 'local',
  location: '/data/kb',
  enabled: true,
  is_default: true,
  has_api_key: false,
  chunk_count: 12,
  indexed_at: '2026-11-20T00:00:00Z',
  updated_at: '2026-11-20T00:00:00Z',
}

const kbDocs: KbDocMetaResponse = {
  policy: {
    qualification: { label: '资质证书', required: ['cert_no'], hint: '证书编号' },
    other: { label: '其他素材', required: [], hint: '' },
  },
  items: [],
  sources: [{ source: '资质.pdf', chunks: 3 }],
}

const docRows: KbDocumentRow[] = [
  {
    id: 'doc-1',
    kb_id: 'kb-1',
    source: '/data/资质.pdf',
    sha256: 'abc',
    size: 2048,
    mime: 'application/pdf',
    pages: 6,
    status: 'ready',
    version: 2,
    kind: 'qualification',
    tags: [],
    valid_to: '2027-01-31',
    error: '',
    created_at: null,
    updated_at: null,
    filename: '/data/资质.pdf',
    chunk_count: 12,
  },
  {
    id: 'doc-2',
    kb_id: 'kb-1',
    source: '/data/旧合同.pdf',
    sha256: '',
    size: 1024,
    mime: 'application/pdf',
    pages: 2,
    status: 'expired',
    version: 1,
    kind: 'performance',
    tags: [],
    valid_to: '2025-01-01',
    error: '证书已过期',
    created_at: null,
    updated_at: null,
    filename: '旧合同.pdf',
    chunk_count: 0,
  },
]

const passages: KbSearchPassage[] = [
  {
    kb_id: 'kb-1',
    source: '资质.pdf',
    text: '证书编号 XYZ-001',
    score: 0.9,
    document_id: 'doc-1',
    page: 3,
    heading: '资质证书',
  },
  {
    kb_id: 'kb-1',
    source: '合同.pdf',
    text: '合同金额 100 万',
    score: 0.7,
    document_id: null,
    page: null,
    heading: '',
  },
]

describe('AdminKbDocsModal 文档清单与检索预览', () => {
  const onClose = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getKbDocs).mockResolvedValue(kbDocs)
    vi.mocked(listKbDocuments).mockResolvedValue(docRows)
    vi.mocked(previewKbRetrieval).mockResolvedValue(passages)
    vi.mocked(updateKbDoc).mockResolvedValue({
      source: '资质.pdf',
      kind: 'qualification',
      fields: {},
      ok: true,
      reason: '',
    })
    vi.mocked(deleteKbDoc).mockResolvedValue({ deleted: true, source: '资质.pdf' })
    confirm.mockResolvedValue(true)
  })

  function renderDocsView() {
    return render(<AdminKbDocsModal kb={kb} onClose={onClose} initialView="docs" />)
  }

  async function submitQuery(value: string) {
    fireEvent.change(screen.getByLabelText('检索预览'), { target: { value } })
    fireEvent.click(screen.getByRole('button', { name: '检索' }))
  }

  it('保留原有素材登记视图，切换后可渲染文档的状态/有效期/片段数', async () => {
    render(<AdminKbDocsModal kb={kb} onClose={onClose} />)
    expect(await screen.findByText('素材来源')).toBeTruthy()
    expect(getKbDocs).toHaveBeenCalledWith('kb-1')

    fireEvent.click(screen.getByRole('button', { name: '文档与预览' }))
    expect(await screen.findByText('资质.pdf')).toBeTruthy()
    expect(listKbDocuments).toHaveBeenCalledWith('kb-1')
    expect(screen.getByText('就绪')).toBeTruthy()
    expect(screen.getByText('有效 2027-01-31')).toBeTruthy()
    expect(screen.getByText('12 片段')).toBeTruthy()
    expect(screen.getByText('已过期')).toBeTruthy()
    expect(screen.getByText('有效 2025-01-01')).toBeTruthy()
  })

  it('提交检索词时带 kb_id 调用预览接口，并展示片段与溯源信息', async () => {
    renderDocsView()
    await screen.findByText('12 片段')

    await submitQuery('证书编号')

    await waitFor(() =>
      expect(previewKbRetrieval).toHaveBeenCalledWith('kb-1', '证书编号'),
    )
    expect(await screen.findByText('证书编号 XYZ-001')).toBeTruthy()
    expect(screen.getByText('资质证书')).toBeTruthy()
    expect(screen.getByText('文档 doc-1')).toBeTruthy()
    expect(screen.getByText('第 3 页')).toBeTruthy()
  })

  it('没有片段时展示空状态', async () => {
    vi.mocked(previewKbRetrieval).mockResolvedValue([])
    renderDocsView()
    await submitQuery('无结果')
    expect(await screen.findByText('没有匹配的片段')).toBeTruthy()
  })

  it('预览失败时展示错误，重试可重新查询', async () => {
    vi.mocked(previewKbRetrieval)
      .mockRejectedValueOnce(new Error('检索服务暂不可用'))
      .mockResolvedValue(passages)
    renderDocsView()

    await submitQuery('证书编号')
    expect(await screen.findByText('检索服务暂不可用')).toBeTruthy()
    const region = screen.getByRole('region', { name: '检索预览结果' })
    fireEvent.click(within(region).getByRole('button', { name: '重试' }))

    await waitFor(() => expect(previewKbRetrieval).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('证书编号 XYZ-001')).toBeTruthy()
  })

  it('文档清单为空或加载失败时分别展示空状态与可重试错误', async () => {
    vi.mocked(listKbDocuments).mockResolvedValue([])
    renderDocsView()
    expect(await screen.findByText('还没有文档实体')).toBeTruthy()
  })

  it('文档清单加载失败后重试成功', async () => {
    vi.mocked(listKbDocuments)
      .mockRejectedValueOnce(new Error('无法读取文档清单'))
      .mockResolvedValue(docRows)
    renderDocsView()

    expect(await screen.findByText('无法读取文档清单')).toBeTruthy()
    const region = screen.getByRole('region', { name: '文档清单' })
    fireEvent.click(within(region).getByRole('button', { name: '重试' }))

    expect(await screen.findByText('12 片段')).toBeTruthy()
    expect(listKbDocuments).toHaveBeenCalledTimes(2)
  })

  it('点击关闭调用 onClose', async () => {
    renderDocsView()
    await screen.findByText('12 片段')
    fireEvent.click(screen.getAllByRole('button', { name: '关闭' })[1])
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

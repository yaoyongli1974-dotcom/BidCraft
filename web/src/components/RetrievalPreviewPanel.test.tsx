import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { previewProjectRetrieval } from '../api/retrieval'
import { RetrievalPreviewPanel } from './RetrievalPreviewPanel'

vi.mock('../api/retrieval', () => ({ previewProjectRetrieval: vi.fn() }))

const RESULT = {
  query: '证书',
  items: [
    {
      kb_id: 'kb-1',
      source: 'cert.pdf',
      text: '证书编号 XYZ-001',
      score: 0.9,
      rerank_score: 0.86,
      trust_tier: 'excerpt',
      degraded: true,
      chunk_id: 'chunk-7',
      chunk_index: 2,
      document_id: 'doc-1',
      page: 3,
      heading: '资质证书',
      diagnostics: null,
    },
  ],
  diagnostics: { searched: ['kb-1'] },
  degraded: true,
}

describe('RetrievalPreviewPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(previewProjectRetrieval).mockResolvedValue(RESULT)
  })

  it('只读面板默认不发起请求，查询需显式输入', () => {
    render(<RetrievalPreviewPanel projectRef="p1" readOnly />)
    const view = screen.getByRole('region', { name: '检索预览' })
    expect(screen.getByText('只读')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '检索' })).toBeDisabled()
    expect(previewProjectRetrieval).not.toHaveBeenCalled()
    expect(view.textContent).toContain('输入检索词后可查看片段')
  })

  it('直接请求同步检索端点并展示分数 / 档位 / 块溯源', async () => {
    render(<RetrievalPreviewPanel projectRef="p1" />)
    fireEvent.change(screen.getByLabelText('检索预览查询'), { target: { value: '  证书  ' } })
    fireEvent.click(screen.getByRole('button', { name: '检索' }))

    await waitFor(() =>
      expect(previewProjectRetrieval).toHaveBeenCalledWith('p1', '证书'),
    )
    expect(await screen.findByText('证书编号 XYZ-001')).toBeInTheDocument()
    expect(screen.getByText('分 0.9')).toBeInTheDocument()
    expect(screen.getByText('重排 0.86')).toBeInTheDocument()
    expect(screen.getByText('有限摘录')).toBeInTheDocument()
    expect(screen.getByText('chunk-7')).toBeInTheDocument()
    expect(screen.getByText('第 3 页')).toBeInTheDocument()
    expect(screen.getByText('cert.pdf')).toBeInTheDocument()
    expect(screen.getByText('命中 1 条')).toBeInTheDocument()
  })

  it('失败只展示服务端消息，不伪造命中结果', async () => {
    vi.mocked(previewProjectRetrieval).mockRejectedValue(new Error('服务端暂时不可用'))
    render(<RetrievalPreviewPanel projectRef="p1" />)
    fireEvent.change(screen.getByLabelText('检索预览查询'), { target: { value: '证书' } })
    fireEvent.click(screen.getByRole('button', { name: '检索' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('服务端暂时不可用')
    expect(screen.queryByText(/命中 \d+ 条/)).toBeNull()
  })

  it('空结果明确说明没有命中', async () => {
    vi.mocked(previewProjectRetrieval).mockResolvedValue({
      query: '无',
      items: [],
      diagnostics: null,
      degraded: null,
    })
    render(<RetrievalPreviewPanel projectRef="p1" />)
    fireEvent.change(screen.getByLabelText('检索预览查询'), { target: { value: '无' } })
    fireEvent.click(screen.getByRole('button', { name: '检索' }))
    expect(await screen.findByText('没有命中片段')).toBeInTheDocument()
  })
})

import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
  KnowledgeRefList,
  RetrievalTraceBadges,
  RetrievalTraceMeta,
} from './RetrievalTraceBadges'

describe('检索溯源标签与溯源明细', () => {
  it('真实信号渲染分数 / 重排 / 信任档位 / 降级标签', () => {
    render(
      <RetrievalTraceBadges
        score={0.91}
        rerank_score={0}
        trust_tier="excerpt"
        degraded={false}
      />,
    )
    const group = screen.getByLabelText('检索溯源')
    expect(within(group).getByText('分 0.91')).toBeInTheDocument()
    expect(within(group).getByText('重排 0')).toBeInTheDocument()
    expect(within(group).getByText('有限摘录')).toBeInTheDocument()
    expect(within(group).getByText('未降级')).toBeInTheDocument()
  })

  it('缺失信号显示未知，不把 null 伪装成 0 分或 trusted', () => {
    render(<RetrievalTraceBadges />)
    const group = screen.getByLabelText('检索溯源')
    expect(within(group).getByText('分 分值未知')).toBeInTheDocument()
    expect(within(group).getByText('重排 分值未知')).toBeInTheDocument()
    expect(within(group).getByText('信任档位未知')).toBeInTheDocument()
    expect(within(group).getByText('降级未知')).toBeInTheDocument()
    expect(within(group).queryByText('trusted')).toBeNull()

    render(<RetrievalTraceBadges degraded trust_tier="suppressed" />)
    expect(screen.getByText('降级运行')).toBeInTheDocument()
    expect(screen.getByText('已抑制')).toBeInTheDocument()
  })

  it('溯源明细展示块 / 文档 / 页码 / 标题 / 来源，缺失时如实说明', () => {
    const { rerender } = render(
      <RetrievalTraceMeta
        chunk_id="chunk-7"
        chunk_index={2}
        document_id="doc-1"
        page={3}
        heading="资质证书"
        source="cert.pdf"
        kb_id="kb-1"
      />,
    )
    for (const text of ['chunk-7', '2', 'doc-1', '第 3 页', '资质证书', 'cert.pdf', 'kb-1']) {
      expect(screen.getByText(text)).toBeInTheDocument()
    }
    rerender(<RetrievalTraceMeta />)
    expect(screen.getByText('溯源信息未知')).toBeInTheDocument()
  })

  it('知识引用列表兼容旧字符串并展示新引用的真实溯源', () => {
    render(
      <KnowledgeRefList
        refs={[
          '知识库A',
          {
            id: 'chunk-9',
            heading: '资质证书',
            score: 0.88,
            rerank_score: 0.83,
            trust_tier: 'title_only',
            degraded: true,
            chunk_id: 'chunk-9',
            chunk_index: 1,
            page: 4,
            source: 'cert.pdf',
          },
        ]}
      />,
    )
    expect(screen.getByText('知识库A')).toBeInTheDocument()
    expect(screen.getAllByText('chunk-9').length).toBeGreaterThan(0)
    expect(screen.getByText('分 0.88')).toBeInTheDocument()
    expect(screen.getByText('重排 0.83')).toBeInTheDocument()
    expect(screen.getByText('仅题录')).toBeInTheDocument()
    expect(screen.getByText('降级运行')).toBeInTheDocument()
    expect(screen.getByText('第 4 页')).toBeInTheDocument()
  })
})

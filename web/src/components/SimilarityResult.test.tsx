import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SimilarityResult } from './SimilarityResult'
import type { SimilarityHit } from '../lib/types'

const HIT: SimilarityHit = {
  id: 'h1',
  kind: 'text',
  source_a: '本项目正文',
  source_b: '历史标书',
  loc_a: '3.1 节',
  loc_b: '第 8 页',
  excerpt_a: '第一段原文内容。',
  excerpt_b: '第二段原文内容。',
  similarity: 0.876,
  review_status: 'pending',
  review_note: '',
}

describe('SimilarityResult', () => {
  it('renders both excerpts, similarity score and status', () => {
    render(<SimilarityResult hit={HIT} canReview onReview={vi.fn()} />)

    expect(screen.getByText('第一段原文内容。')).toBeInTheDocument()
    expect(screen.getByText('第二段原文内容。')).toBeInTheDocument()
    expect(screen.getByText('88%')).toBeInTheDocument()
    expect(screen.getByText('文本重合')).toBeInTheDocument()
    expect(screen.getByText('待复核')).toBeInTheDocument()
    expect(screen.getByText('3.1 节')).toBeInTheDocument()
  })

  it('passes the review note with the chosen status', () => {
    const onReview = vi.fn()
    render(<SimilarityResult hit={HIT} canReview onReview={onReview} />)

    fireEvent.change(screen.getByLabelText('复核备注'), {
      target: { value: '已改写' },
    })
    fireEvent.click(screen.getByRole('button', { name: '确认重复' }))

    expect(onReview).toHaveBeenCalledWith('confirmed', '已改写')
  })

  it('disables review controls for read-only members', () => {
    render(<SimilarityResult hit={HIT} canReview={false} onReview={vi.fn()} />)

    expect(screen.getByRole('button', { name: '确认重复' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '标记原创' })).toBeDisabled()
    expect(screen.getByLabelText('复核备注')).toBeDisabled()
  })
})

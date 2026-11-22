import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CommercialDocCard } from './CommercialDocCard'
import type { CommercialDoc } from '../lib/types'

function doc(overrides: Partial<CommercialDoc> = {}): CommercialDoc {
  return {
    id: 'c1',
    name: '测试商务标',
    quote_version_id: 'v1',
    package_id: null,
    status: 'draft',
    sections: [{ key: 'price', title: '投标报价', content: '100 万元' }],
    validation: { ok: true, issues: [] },
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

const baseProps = {
  canWrite: true,
  canReview: true,
  onAction: vi.fn(),
  onExport: vi.fn(),
  onDelete: vi.fn(),
}

describe('CommercialDocCard', () => {
  it('shows status, validation issues and expands the section table', () => {
    render(
      <CommercialDocCard
        {...baseProps}
        doc={doc({
          status: 'submitted',
          validation: {
            ok: false,
            issues: [{ code: 'V-02', message: '报价与报价版本不一致' }],
          },
        })}
      />,
    )

    expect(screen.getByText('待审批')).toBeInTheDocument()
    expect(screen.getByText('报价与报价版本不一致')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /章节预览/ }))
    expect(screen.getByText('投标报价')).toBeInTheDocument()
    expect(screen.getByText('100 万元')).toBeInTheDocument()
  })

  it('routes submit, approve and export actions', () => {
    const onAction = vi.fn()
    const onExport = vi.fn()
    const { rerender } = render(
      <CommercialDocCard
        {...baseProps}
        onAction={onAction}
        onExport={onExport}
        doc={doc()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '提交审批' }))
    expect(onAction).toHaveBeenCalledWith('submit')
    fireEvent.click(screen.getByRole('button', { name: '导出 xlsx' }))
    expect(onExport).toHaveBeenCalledWith('xlsx')

    rerender(
      <CommercialDocCard
        {...baseProps}
        onAction={onAction}
        onExport={onExport}
        doc={doc({ status: 'submitted' })}
      />,
    )
    const card = screen.getByRole('button', { name: '审批通过' }).closest('article') as HTMLElement
    fireEvent.click(within(card).getByRole('button', { name: '审批通过' }))
    expect(onAction).toHaveBeenCalledWith('approve')
  })

  it('hides review buttons for members without review rights', () => {
    render(<CommercialDocCard {...baseProps} canReview={false} doc={doc({ status: 'submitted' })} />)

    expect(screen.queryByRole('button', { name: '审批通过' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '驳回' })).not.toBeInTheDocument()
  })
})

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EnterpriseAssetList } from './EnterpriseAssetList'
import type { EnterpriseDocument } from '../lib/types'

const DOC: EnterpriseDocument = {
  id: 'd1',
  source: '营业执照',
  content: '统一社会信用代码 91310000XXXX',
  kind: 'license',
  tags: ['证照'],
  valid_to: '2030-01-01',
}

describe('EnterpriseAssetList', () => {
  it('renders source, kind, tags and validity', () => {
    render(<EnterpriseAssetList documents={[DOC]} />)

    expect(screen.getByText('营业执照')).toBeInTheDocument()
    expect(screen.getByText('license')).toBeInTheDocument()
    expect(screen.getByText('证照')).toBeInTheDocument()
    expect(screen.getByText(/有效至/)).toBeInTheDocument()
    expect(screen.getByText(/91310000XXXX/)).toBeInTheDocument()
  })

  it('shows a placeholder instead of fabricating assets', () => {
    render(<EnterpriseAssetList documents={[]} emptyText="暂无企业资料" />)

    expect(screen.getByText('暂无企业资料')).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })
})

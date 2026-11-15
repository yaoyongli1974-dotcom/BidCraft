import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Badge, BidSectionBadge } from './Badge'

describe('Badge', () => {
  it('renders the tone modifier plus optional dot and title', () => {
    const { container } = render(
      <Badge tone="danger" dot title="存在阻断项">
        阻断
      </Badge>,
    )
    const badge = container.querySelector('.badge')
    expect(badge).not.toBeNull()
    expect(badge).toHaveClass('badge--danger')
    expect(badge).toHaveAttribute('title', '存在阻断项')
    expect(badge?.querySelector('.badge-dot')).toBeInTheDocument()
    expect(screen.getByText('阻断')).toBeInTheDocument()
  })

  it('defaults to the neutral tone without a dot', () => {
    const { container } = render(<Badge>只读</Badge>)
    const badge = container.querySelector('.badge')
    expect(badge).toHaveClass('badge--neutral')
    expect(badge?.querySelector('.badge-dot')).toBeNull()
  })

  it('supports the subtle variant', () => {
    const { container } = render(<Badge subtle>已确认</Badge>)
    expect(container.querySelector('.badge')).toHaveClass('badge--subtle')
  })
})

describe('BidSectionBadge', () => {
  it('renders nothing for blank section names', () => {
    const { container } = render(<BidSectionBadge value="   " />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the section marker and a labelled title', () => {
    const { container } = render(<BidSectionBadge value="二标段" />)
    const badge = container.querySelector('.bid-section-badge')
    expect(badge).toHaveAttribute('title', '标段/包件：二标段')
    expect(container.textContent).toContain('二标段')
  })
})

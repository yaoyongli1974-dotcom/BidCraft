import type { ReactNode } from 'react'
import { className, type Tone } from '../lib/format'

interface BadgeProps {
  tone?: Tone
  children: ReactNode
  dot?: boolean
  subtle?: boolean
  title?: string
}

export function Badge({ tone = 'neutral', children, dot, subtle, title }: BadgeProps) {
  return (
    <span
      className={className('badge', `badge--${tone}`, subtle && 'badge--subtle')}
      title={title}
    >
      {dot && <span className="badge-dot" aria-hidden />}
      {children}
    </span>
  )
}

/**
 * 标段 / 包件标识：项目可能属于某个标段，用铜色小签与状态徽标区分开。
 * 空值不渲染任何内容。
 */
export function BidSectionBadge({ value }: { value?: string }) {
  const text = (value || '').trim()
  if (!text) return null
  return (
    <span className="bid-section-badge" title={`标段/包件：${text}`}>
      <span className="bid-section-mark" aria-hidden>
        §
      </span>
      <span className="bid-section-text">{text}</span>
    </span>
  )
}

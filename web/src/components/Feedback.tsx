import type { CSSProperties, ReactNode } from 'react'
import { AlertCircle, Loader2, type LucideIcon } from 'lucide-react'
import { className, type Tone } from '../lib/format'
import { Button } from './Button'

export function Skeleton({
  width = '100%',
  height = 16,
  radius = 4,
  className: extra,
}: {
  width?: number | string
  height?: number | string
  radius?: number
  className?: string
}) {
  const style: CSSProperties = { width, height, borderRadius: radius }
  return <span className={className('skeleton', extra)} style={style} aria-hidden />
}

export function Spinner({ size = 16 }: { size?: number }) {
  return <Loader2 className="spin" size={size} aria-hidden />
}

export function ProgressBar({
  value,
  tone = 'accent',
  thin,
  label,
}: {
  value: number
  tone?: Tone
  thin?: boolean
  label?: string
}) {
  const clamped = Math.max(0, Math.min(100, Math.round(value)))
  return (
    <div
      className={className('progress', `progress--${tone}`, thin && 'progress--thin')}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
      aria-label={label || '进度'}
    >
      <span className="progress-fill" style={{ width: `${clamped}%` }} />
    </div>
  )
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  actions,
  compact,
}: {
  icon: LucideIcon
  title: string
  description?: string
  actions?: ReactNode
  compact?: boolean
}) {
  return (
    <div className={className('empty', compact && 'empty--compact')}>
      <div className="empty-icon" aria-hidden>
        <Icon size={compact ? 20 : 26} strokeWidth={1.6} />
      </div>
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {actions && <div className="empty-actions">{actions}</div>}
    </div>
  )
}

export function ErrorState({
  message,
  onRetry,
  compact,
}: {
  message: string
  onRetry?: () => void
  compact?: boolean
}) {
  return (
    <div className={className('error-state', compact && 'error-state--compact')} role="alert">
      <AlertCircle size={18} aria-hidden />
      <div>
        <p>{message}</p>
        {onRetry && (
          <Button size="sm" variant="secondary" onClick={onRetry}>
            重试
          </Button>
        )}
      </div>
    </div>
  )
}

export function LoadingBlock({ lines = 3 }: { lines?: number }) {
  return (
    <div className="loading-block" aria-busy="true" aria-label="加载中">
      <Skeleton width="32%" height={12} />
      {Array.from({ length: lines }).map((_, index) => (
        <Skeleton key={index} width={index === lines - 1 ? '68%' : '100%'} height={16} />
      ))}
    </div>
  )
}

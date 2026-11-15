import {
  useLayoutEffect,
  useRef,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react'
import { Quote, ShieldAlert, type LucideIcon } from 'lucide-react'
import { className, sourceLabel, type Tone } from '../lib/format'
import { Button } from './Button'
import { ProgressBar } from './Feedback'

export function TabHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="tab-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h2>{title}</h2>
        {description && <p className="tab-lede">{description}</p>}
      </div>
      {actions && <div className="tab-actions">{actions}</div>}
    </div>
  )
}

export function StatTile({
  label,
  value,
  detail,
  tone = 'neutral',
  icon: Icon,
  progress,
}: {
  label: string
  value: ReactNode
  detail?: ReactNode
  tone?: Tone
  icon?: LucideIcon
  progress?: number
}) {
  return (
    <div className={className('stat-tile', `stat-tile--${tone}`)}>
      <div className="stat-head">
        <span className="stat-label">{label}</span>
        {Icon && (
          <span className="stat-icon" aria-hidden>
            <Icon size={15} />
          </span>
        )}
      </div>
      <div className="stat-value">{value}</div>
      {progress !== undefined && (
        <ProgressBar value={progress} thin tone={tone === 'neutral' ? 'accent' : tone} />
      )}
      {detail && <div className="stat-detail">{detail}</div>}
    </div>
  )
}

export function GateBanner({
  count,
  keys,
  onLocate,
}: {
  count: number
  keys?: string[]
  onLocate?: () => void
}) {
  const shown = (keys || []).slice(0, 5)
  return (
    <div className="gate-banner" role="alert">
      <div className="gate-icon" aria-hidden>
        <ShieldAlert size={21} />
      </div>
      <div className="gate-copy">
        <strong>生成门禁未通过 · 还缺 {count} 项必填事实</strong>
        <p>补齐必填项并确认后才能生成正文。缺失字段已在下方标出。</p>
        {shown.length > 0 && (
          <div className="gate-keys">
            {shown.map((key) => (
              <code key={key}>{key}</code>
            ))}
            {keys && keys.length > shown.length && <span>+{keys.length - shown.length}</span>}
          </div>
        )}
      </div>
      {onLocate && (
        <Button size="sm" variant="secondary" onClick={onLocate}>
          定位缺失项
        </Button>
      )}
    </div>
  )
}

export function SourceQuote({
  quote,
  file,
  page,
  summaryLabel,
  compact,
}: {
  quote?: string
  file?: string
  page?: number | string | null
  summaryLabel?: string
  compact?: boolean
}) {
  if (!quote) {
    return <span className="source-missing">无自动出处</span>
  }
  return (
    <details className={className('quote-disclosure', compact && 'quote-disclosure--compact')}>
      <summary title={summaryLabel || sourceLabel({ file, page, quote })}>
        <Quote size={13} aria-hidden />
        <span>{summaryLabel || '查看原文'}</span>
      </summary>
      <div className="quote-body">
        <div className="quote-meta mono">
          {file || '招标文件'}
          {page ? ` · 第 ${page} 页` : ''}
        </div>
        <blockquote>{quote}</blockquote>
      </div>
    </details>
  )
}

interface AutoTextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  value: string
  onValueChange: (value: string) => void
  minRows?: number
}

export function AutoTextarea({
  value,
  onValueChange,
  minRows = 2,
  className: extra,
  ...rest
}: AutoTextareaProps) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight}px`
  }, [value])
  return (
    <textarea
      ref={ref}
      rows={minRows}
      className={className('textarea auto-textarea', extra)}
      value={value}
      onChange={(event) => onValueChange(event.target.value)}
      {...rest}
    />
  )
}

export function KeyChip({ children }: { children: ReactNode }) {
  return <code className="key-chip">{children}</code>
}

export function SectionCard({
  title,
  eyebrow,
  aside,
  children,
  className: extra,
}: {
  title: ReactNode
  eyebrow?: string
  aside?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={className('section-card', extra)}>
      <header className="section-card-head">
        <div>
          {eyebrow && <div className="eyebrow">{eyebrow}</div>}
          <h3>{title}</h3>
        </div>
        {aside}
      </header>
      {children}
    </section>
  )
}

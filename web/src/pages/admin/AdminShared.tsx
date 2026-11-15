import { AlertCircle, CheckCircle2, X, XCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import type { TestResult } from '../../lib/types'
import { className, formatLatency, formatRelative, testResultMeta } from '../../lib/format'
import { Badge } from '../../components/Badge'
import { Skeleton } from '../../components/Feedback'

export function AdminActionError({
  message,
  onClose,
}: {
  message: string
  onClose: () => void
}) {
  return (
    <div className="admin-action-error" role="alert">
      <AlertCircle size={16} />
      <span>{message}</span>
      <button type="button" onClick={onClose} aria-label="关闭错误提示">
        <X size={14} />
      </button>
    </div>
  )
}

export function TestFeedback({ result }: { result: TestResult }) {
  return (
    <div
      className={className('admin-test-feedback', result.ok ? 'is-ok' : 'is-fail')}
      role="status"
    >
      {result.ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
      <div>
        <strong>{result.ok ? '连接测试通过' : '连接测试失败'}</strong>
        <span>{result.message || '未返回说明'}</span>
      </div>
      {result.latency_ms !== undefined && (
        <span className="admin-test-latency mono">{formatLatency(result.latency_ms)}</span>
      )}
    </div>
  )
}

export function LastTestStatus({
  ok,
  at,
  message,
}: {
  ok?: boolean | null
  at?: string | null
  message?: string
}) {
  const meta = testResultMeta(ok)
  return (
    <div className="admin-last-test" title={message || undefined}>
      <Badge tone={meta.tone} subtle>
        {meta.label}
      </Badge>
      {at && <span>{formatRelative(at)}</span>}
    </div>
  )
}

export function AdminSegment({
  items,
  value,
  onChange,
  label,
}: {
  items: Array<{ id: string; label: string; count?: number }>
  value: string
  onChange: (value: string) => void
  label: string
}) {
  return (
    <div className="admin-segment" role="group" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className={className('admin-segment-item', value === item.id && 'is-active')}
          aria-pressed={value === item.id}
          onClick={() => onChange(item.id)}
        >
          {item.label}
          {item.count !== undefined && <span>{item.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function AdminSkeletonRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="admin-skeleton" aria-busy="true" aria-label="加载中">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="admin-skeleton-row">
          <Skeleton width={38} height={38} radius={8} />
          <div>
            <Skeleton width="34%" height={15} />
            <Skeleton width="62%" height={11} />
          </div>
          <Skeleton width={88} height={26} radius={5} />
        </div>
      ))}
    </div>
  )
}

export function AdminSectionLabel({
  eyebrow,
  title,
  description,
  aside,
}: {
  eyebrow?: string
  title: string
  description?: ReactNode
  aside?: ReactNode
}) {
  return (
    <div className="admin-section-label">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {aside}
    </div>
  )
}

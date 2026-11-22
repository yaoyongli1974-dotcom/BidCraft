import { FileDiff } from 'lucide-react'
import type { SectionDiff } from '../lib/types'
import { className } from '../lib/format'
import { EmptyState, ErrorState, Skeleton } from './Feedback'

/**
 * 双栏修订差异：左栏是旧版本（删除行），右栏是新版本（新增行）。
 * 后端只返回结构化 rows 或可解析的 diff 文本时都可用；空数据不伪造内容。
 */
export function RevisionDiff({
  diff,
  loading,
  error,
  onRetry,
}: {
  diff: SectionDiff | null
  loading?: boolean
  error?: string | null
  onRetry?: () => void
}) {
  if (loading && !diff) {
    return (
      <div className="revision-diff revision-diff--loading" aria-busy="true">
        <Skeleton width="100%" height={26} />
        <Skeleton width="100%" height={140} radius={8} />
      </div>
    )
  }

  if (error && !diff) {
    return <ErrorState message={error} onRetry={onRetry} compact />
  }

  if (!diff || (!diff.rows.length && !diff.text)) {
    return (
      <EmptyState
        icon={FileDiff}
        compact
        title="没有可显示的差异"
        description="选择一个修订版本后，这里会对比它与上一版本的变化。"
      />
    )
  }

  return (
    <div className="revision-diff">
      <div className="revision-diff-head">
        <span>
          版本 <strong className="mono">r{diff.from ?? '?'}</strong>
          <span aria-hidden> → </span>
          <strong className="mono">r{diff.to ?? '?'}</strong>
        </span>
        <span className="revision-diff-counts">
          <span className="diff-count diff-count--add">+{diff.added}</span>
          <span className="diff-count diff-count--remove">-{diff.removed}</span>
        </span>
      </div>
      {diff.rows.length ? (
        <div className="revision-diff-grid" role="table" aria-label="修订差异双栏对比">
          <div className="revision-diff-col" role="columnheader">
            旧版本
          </div>
          <div className="revision-diff-col" role="columnheader">
            新版本
          </div>
          {diff.rows.map((row, index) => (
            <div className="revision-diff-row" role="row" key={index}>
              <div
                className={className(
                  'revision-diff-cell',
                  row.kind === 'remove' && 'is-remove',
                  row.kind === 'change' && 'is-change',
                  row.kind === 'add' && 'is-blank',
                )}
                role="cell"
              >
                {row.left !== undefined && row.left !== '' ? (
                  <code>{row.left}</code>
                ) : row.kind === 'add' ? (
                  <span className="diff-placeholder" aria-hidden>
                    ·
                  </span>
                ) : null}
              </div>
              <div
                className={className(
                  'revision-diff-cell',
                  row.kind === 'add' && 'is-add',
                  row.kind === 'change' && 'is-change',
                  row.kind === 'remove' && 'is-blank',
                )}
                role="cell"
              >
                {row.right !== undefined && row.right !== '' ? (
                  <code>{row.right}</code>
                ) : row.kind === 'remove' ? (
                  <span className="diff-placeholder" aria-hidden>
                    ·
                  </span>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <pre className="revision-diff-raw">{diff.text}</pre>
      )}
    </div>
  )
}

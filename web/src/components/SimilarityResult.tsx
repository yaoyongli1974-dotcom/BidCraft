import { useState } from 'react'
import { Check, RotateCcw, ShieldOff } from 'lucide-react'
import type { SimilarityHit, SimilarityHitStatus } from '../lib/types'
import { className } from '../lib/format'
import { similarityKindMeta, similarityStatusMeta } from '../lib/similarityMeta'
import { Badge } from './Badge'
import { Button } from './Button'

/** 双栏摘录 + 人工复核的单条查重命中。 */
export function SimilarityResult({
  hit,
  canReview,
  busy,
  onReview,
}: {
  hit: SimilarityHit
  canReview: boolean
  busy?: boolean
  onReview: (status: SimilarityHitStatus, note?: string) => void
}) {
  const [note, setNote] = useState('')
  const kind = similarityKindMeta(hit.kind)
  const status = similarityStatusMeta(hit.review_status)
  const score = Math.max(0, Math.min(100, Math.round((hit.similarity || 0) * 100)))

  return (
    <article
      className={className('sim-hit', `sim-hit--${hit.review_status || 'pending'}`)}
      aria-label={`查重命中 · ${kind.label}`}
    >
      <header className="sim-hit-head">
        <Badge tone={kind.tone} subtle>
          {kind.label}
        </Badge>
        <span className="sim-hit-score mono" title="文本相似度">
          {score}%
        </span>
        <Badge tone={status.tone}>{status.label}</Badge>
      </header>

      <div className="sim-hit-grid">
        <div className="sim-excerpt">
          <div className="sim-excerpt-source">
            <strong>{hit.source_a || '来源 A'}</strong>
            {hit.loc_a && <span className="mono">{hit.loc_a}</span>}
          </div>
          <blockquote>{hit.excerpt_a || '（无摘录）'}</blockquote>
        </div>
        <div className="sim-excerpt">
          <div className="sim-excerpt-source">
            <strong>{hit.source_b || '来源 B'}</strong>
            {hit.loc_b && <span className="mono">{hit.loc_b}</span>}
          </div>
          <blockquote>{hit.excerpt_b || '（无摘录）'}</blockquote>
        </div>
      </div>

      {hit.review_note && (
        <p className="sim-hit-note">复核备注：{hit.review_note}</p>
      )}

      <div className="sim-hit-foot">
        <input
          className="input input--sm sim-hit-note-input"
          aria-label="复核备注"
          placeholder="复核备注（可选）"
          value={note}
          maxLength={200}
          disabled={!canReview || busy}
          onChange={(event) => setNote(event.target.value)}
        />
        <div className="sim-hit-actions">
          <Button
            size="sm"
            variant="secondary"
            icon={<Check size={13} />}
            disabled={!canReview || busy || hit.review_status === 'confirmed'}
            title={!canReview ? '只读成员不能复核查重结果' : undefined}
            onClick={() => onReview('confirmed', note)}
          >
            确认重复
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon={<ShieldOff size={13} />}
            disabled={!canReview || busy || hit.review_status === 'ignored'}
            title={!canReview ? '只读成员不能复核查重结果' : undefined}
            onClick={() => onReview('ignored', note)}
          >
            标记原创
          </Button>
          {hit.review_status !== 'pending' && (
            <Button
              size="sm"
              variant="ghost"
              icon={<RotateCcw size={13} />}
              disabled={!canReview || busy}
              onClick={() => onReview('pending', note)}
            >
              重置待复核
            </Button>
          )}
        </div>
      </div>
    </article>
  )
}

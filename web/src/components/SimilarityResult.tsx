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
  const rerankScore = hit.rerank_score
  const hasRerank = typeof rerankScore === 'number' && Number.isFinite(rerankScore)
  const displayScore = hasRerank ? (rerankScore as number) : hit.similarity || 0
  const score = Math.max(0, Math.min(100, Math.round(displayScore * 100)))
  const evidence = hit.evidence
  const vectorScore =
    typeof evidence?.vector_similarity === 'number' && Number.isFinite(evidence.vector_similarity)
      ? Math.round(evidence.vector_similarity * 100)
      : null

  return (
    <article
      className={className('sim-hit', `sim-hit--${hit.review_status || 'pending'}`)}
      aria-label={`查重命中 · ${kind.label}`}
    >
      <header className="sim-hit-head">
        <Badge tone={kind.tone} subtle>
          {kind.label}
        </Badge>
        <span
          className="sim-hit-score mono"
          title={hasRerank ? '重排相似度（语义与文本加权）' : '原始相似度'}
        >
          {score}%
        </span>
        {hasRerank && (
          <Badge tone="info" subtle title="语义重排后的最终排序分">
            重排
          </Badge>
        )}
        <Badge tone={status.tone}>{status.label}</Badge>
      </header>

      {(evidence?.reasonable_duplicate || evidence?.common || evidence?.degraded || vectorScore !== null) && (
        <div className="sim-hit-evidence">
          {(evidence?.reasonable_duplicate || evidence?.common) && (
            <Badge tone="warn" subtle title={evidence?.common_reason || '法律法规、标准或固定条款等常见表述'}>
              合理重复候选
            </Badge>
          )}
          {evidence?.degraded && (
            <Badge tone="warn" subtle title="语义模型不可用，已回退文本查重">
              降级运行
            </Badge>
          )}
          {vectorScore !== null && (
            <Badge tone="neutral" subtle title="语义向量余弦相似度">
              向量 {vectorScore}%
            </Badge>
          )}
        </div>
      )}

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

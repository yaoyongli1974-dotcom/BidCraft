import type { ReactNode } from 'react'
import { className } from '../lib/format'
import { trustTierLabel, trustTierTone } from '../lib/retrievalTrace'
import type { KnowledgeRef, RetrievalTrace } from '../lib/types'
import { Badge } from './Badge'

/**
 * 检索溯源标签组：分数 / 重排分 / 信任档位 / 降级状态。
 * 缺失信号显示「未知」，不把 null 伪装成 0 分、trusted 或未降级。
 */
export interface RetrievalTraceBadgesProps extends RetrievalTrace {
  score?: number | null
  compact?: boolean
}

function scoreText(value: number | null | undefined): string {
  return value === null || value === undefined ? '分值未知' : String(Math.round(value * 1000) / 1000)
}

export function RetrievalTraceBadges({
  score,
  rerank_score,
  trust_tier,
  degraded,
  compact,
}: RetrievalTraceBadgesProps) {
  const shownScore = score === null || score === undefined ? null : score
  return (
    <span className={className('trace-badges', compact && 'is-compact')} aria-label="检索溯源">
      <Badge tone={shownScore === null ? 'neutral' : 'info'} subtle title="检索原始分">
        分 {scoreText(shownScore)}
      </Badge>
      <Badge
        tone={rerank_score === null || rerank_score === undefined ? 'neutral' : 'accent'}
        subtle
        title="混合重排融合分；旧后端没有该字段"
      >
        重排 {scoreText(rerank_score)}
      </Badge>
      <Badge
        tone={trustTierTone(trust_tier)}
        subtle
        title="素材注入档位：全文 / 有限摘录 / 仅题录 / 已抑制"
      >
        {trustTierLabel(trust_tier)}
      </Badge>
      {degraded === true ? (
        <Badge tone="warn" subtle title="检索降级运行，结果未经过完整排序">
          降级运行
        </Badge>
      ) : degraded === false ? (
        <Badge tone="success" subtle title="服务端明确报告未降级">
          未降级
        </Badge>
      ) : (
        <Badge tone="neutral" subtle title="服务端未返回降级信号">
          降级未知
        </Badge>
      )}
    </span>
  )
}

export interface RetrievalTraceMetaProps {
  chunk_id?: string | null
  chunk_index?: number | null
  document_id?: string | null
  page?: number | null
  heading?: string | null
  source?: string | null
  kb_id?: string | null
  compact?: boolean
}

function field(label: string, value: ReactNode, mono = false): ReactNode {
  if (value === null || value === undefined || value === '') return null
  return (
    <div>
      <dt>{label}</dt>
      <dd className={mono ? 'mono' : undefined}>{value}</dd>
    </div>
  )
}

/** 块 / 文档 / 页码 / 标题 / 来源的溯源明细；没有证据的行不渲染。 */
export function RetrievalTraceMeta({
  chunk_id,
  chunk_index,
  document_id,
  page,
  heading,
  source,
  kb_id,
  compact,
}: RetrievalTraceMetaProps) {
  const rows = [
    field('块 ID', chunk_id, true),
    field('块序号', chunk_index, true),
    field('文档 ID', document_id, true),
    field('页码', typeof page === 'number' ? `第 ${page} 页` : null),
    field('标题', heading),
    field('来源', source),
    field('知识库', kb_id, true),
  ].filter((row): row is ReactNode => row !== null)
  if (rows.length === 0) return <span className="subtle">溯源信息未知</span>
  return <dl className={className('trace-meta', compact && 'is-compact')}>{rows}</dl>
}

/** 章节规划的知识引用：既兼容旧后端的纯字符串，也展示新引用的真实溯源。 */
export function KnowledgeRefList({ refs }: { refs?: Array<KnowledgeRef | string> }) {
  if (!refs || refs.length === 0) return <span className="subtle">未填写</span>
  return (
    <ul className="trace-ref-list">
      {refs.map((ref, index) => {
        if (typeof ref === 'string') {
          return <li key={`ref-text-${index}`}>{ref}</li>
        }
        return (
          <li key={ref.id || `ref-${index}`}>
            <span className="trace-ref-title">
              {ref.heading || ref.source || ref.text || ref.id || '知识引用'}
            </span>
            {ref.id && <code className="mono">{ref.id}</code>}
            <RetrievalTraceBadges {...ref} score={ref.score} compact />
            <RetrievalTraceMeta {...ref} compact />
          </li>
        )
      })}
    </ul>
  )
}

import { AlertTriangle, Check, Target } from 'lucide-react'
import { Badge } from './Badge'
import type { ScoringCoverageReport, ScoringCoverageRow } from '../lib/types'
import { className, formatNumber, percent, type Tone } from '../lib/format'

const STATUS_TONE: Record<ScoringCoverageRow['status'], Tone> = {
  covered: 'success',
  weak: 'warn',
  uncovered: 'danger',
}

function statusText(status: ScoringCoverageRow['status']): string {
  if (status === 'covered') return '已覆盖'
  if (status === 'weak') return '覆盖薄弱'
  return '未覆盖'
}

interface ScoreCoverageProps {
  report: ScoringCoverageReport | null | undefined
  loading?: boolean
}

/**
 * 评分点覆盖矩阵（只读）：逐条列出评分点与章节的对应关系与覆盖状态。
 *
 * 这是纯展示组件，不触发生成或改动大纲——匹配口径由后端保守判定。
 */
export function ScoreCoverage({ report, loading }: ScoreCoverageProps) {
  if (loading) {
    return (
      <section className="panel score-coverage" aria-busy="true">
        <div className="eyebrow">评分覆盖</div>
        <p className="subtle">正在计算评分点与章节的对应关系…</p>
      </section>
    )
  }

  if (!report || report.total_score <= 0) {
    return (
      <section className="panel score-coverage">
        <div className="config-head">
          <div>
            <div className="eyebrow">评分覆盖</div>
            <h3>评分点覆盖矩阵</h3>
          </div>
        </div>
        <p className="subtle">当前项目没有可计算的有效评分项；请检查评分办法是否包含明确分值，或重新提取招标文件评分表。</p>
      </section>
    )
  }

  const ratioPct = Math.round(report.covered_ratio * 100)

  return (
    <section className="panel score-coverage">
      <div className="config-head">
        <div>
          <div className="eyebrow">评分覆盖</div>
          <h3>评分点覆盖矩阵</h3>
        </div>
        <Badge tone={ratioPct >= 80 ? 'success' : ratioPct >= 50 ? 'warn' : 'danger'}>
          {ratioPct}% 分值已覆盖
        </Badge>
      </div>

      <div className="score-coverage-summary">
        <span>
          评分项 <strong>{formatNumber(report.total_items)}</strong>
        </span>
        <span>
          总分值 <strong>{formatNumber(report.total_score)}</strong>
        </span>
        <span className="is-ok">
          已覆盖 <strong>{formatNumber(report.covered_score)}</strong>
        </span>
        {report.weak_score > 0 && (
          <span className="is-warn">
            薄弱 <strong>{formatNumber(report.weak_score)}</strong>
          </span>
        )}
        {report.uncovered_count > 0 && (
          <span className="is-danger">
            未覆盖 <strong>{formatNumber(report.uncovered_count)}</strong> 项
          </span>
        )}
      </div>

      <div className="score-coverage-progress" aria-hidden>
        <span style={{ width: `${percent(report.covered_score, report.total_score)}%` }} />
      </div>

      {(report.issues?.length ?? 0) > 0 && (
        <div className="score-coverage-issues" role="status">
          {(report.issues ?? []).map((issue, index) => (
            <div key={`${issue}-${index}`} className="score-coverage-issue">
              <AlertTriangle size={13} />
              <span>{issue}</span>
            </div>
          ))}
        </div>
      )}

      <div className="score-coverage-rows">
        {report.rows.map((row, index) => {
          const tone = STATUS_TONE[row.status]
          return (
            <article
              key={row.id || `${row.name}-${index}`}
              className={className('score-coverage-row', `is-${row.status}`)}
            >
              <div className="score-coverage-row-head">
                <span className="score-coverage-icon" aria-hidden>
                  {row.status === 'covered' ? <Check size={14} /> : <Target size={14} />}
                </span>
                <strong>{row.name}</strong>
                {row.score > 0 && <span className="score-coverage-score">{formatNumber(row.score)} 分</span>}
                <Badge tone={tone} subtle>
                  {statusText(row.status)}
                </Badge>
              </div>
              <div className="score-coverage-nodes">
                {row.nodes.length === 0 ? (
                  <span className="subtle">未找到对应章节</span>
                ) : (
                  row.nodes.map((node) => (
                    <span key={node.id} className="score-coverage-node" title={node.reasons.join('、')}>
                      {node.heading}
                      <em>{formatNumber(node.words)} 字</em>
                    </span>
                  ))
                )}
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}

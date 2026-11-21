import { useEffect, useMemo, useState } from 'react'
import { Save, TrendingDown } from 'lucide-react'
import type { ScorePatch } from '../api/account'
import { getProjectScores, saveProjectScores } from '../api/account'
import { messageOf } from '../lib/http'
import type { ScoreAnalysis } from '../lib/types'
import { formatNumber } from '../lib/format'
import { useQuery } from '../lib/hooks'
import { useToast } from '../context/useToast'
import { Badge } from './Badge'
import { Button } from './Button'
import { ErrorState, Skeleton } from './Feedback'

interface Draft {
  actual: string
  note: string
}

function toDrafts(rows: ScoreAnalysis['items']): Record<string, Draft> {
  const next: Record<string, Draft> = {}
  for (const row of rows) {
    next[row.id] = {
      actual: row.actual_score === null ? '' : String(row.actual_score),
      note: row.score_note || '',
    }
  }
  return next
}

function parseScore(value: string): number | null | 'bad' {
  const text = value.trim()
  if (!text) return null
  const num = Number(text)
  return Number.isFinite(num) ? num : 'bad'
}

export function ScoreBackfill({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const toast = useToast()
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState('')
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [dirty, setDirty] = useState(false)
  const query = useQuery(`scores:${projectId}`, () => getProjectScores(projectId))
  const data = query.data

  useEffect(() => {
    if (data && !dirty) setDrafts(toDrafts(data.items))
  }, [data, dirty])

  const rateText = useMemo(() => {
    if (!data || data.rate === null) return '未回填'
    return `${(data.rate * 100).toFixed(1)}%`
  }, [data])

  function update(id: string, patch: Partial<Draft>) {
    setDrafts((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }))
    setDirty(true)
    setFormError('')
  }

  async function save() {
    if (!data) return
    const rows: ScorePatch[] = []
    for (const item of data.items) {
      const draft = drafts[item.id]
      if (!draft) continue
      const parsed = parseScore(draft.actual)
      if (parsed === 'bad') {
        setFormError(`「${item.name}」的得分不是数字`)
        return
      }
      if (parsed !== null && item.score !== null && parsed > item.score + 1e-6) {
        setFormError(`「${item.name}」的得分不能超过 ${formatNumber(item.score)}`)
        return
      }
      rows.push({ id: item.id, actual_score: parsed, score_note: draft.note.trim() })
    }
    setSaving(true)
    setFormError('')
    try {
      const next = await saveProjectScores(projectId, rows)
      query.setData(next)
      setDrafts(toDrafts(next.items))
      setDirty(false)
      toast.success('评分已保存')
    } catch (error) {
      setFormError(messageOf(error))
    } finally {
      setSaving(false)
    }
  }

  if (query.loading && !data) return <Skeleton height={140} />
  if (query.error && !data) {
    return <ErrorState message={messageOf(query.error)} onRetry={() => query.refresh()} />
  }
  if (!data) return null

  return (
    <section className="score-backfill">
      <header className="score-backfill-head">
        <div>
          <h4>评分回填</h4>
          <p className="cell-sub">
            录入实际得分后，失分项会作为生成提示注入；未回填不参与计算。
          </p>
        </div>
        <div className="score-backfill-stats">
          <span>
            总得分率 <strong>{rateText}</strong>
          </span>
          <span>
            已回填 <strong>{data.filled}</strong>/{data.total_items}
          </span>
          {data.losses[0] && (
            <Badge tone="warn" subtle>
              <TrendingDown size={11} /> 最大失分 {data.losses[0].name}{' '}
              {formatNumber(data.losses[0].loss)}
            </Badge>
          )}
        </div>
      </header>

      <div className="score-backfill-table" role="table">
        <div className="score-backfill-row score-backfill-header" role="row">
          <span role="columnheader">评分项</span>
          <span role="columnheader">分值</span>
          <span role="columnheader">实际得分</span>
          <span role="columnheader">得分率</span>
          <span role="columnheader">备注</span>
        </div>
        {data.items.map((item) => {
          const draft = drafts[item.id] || { actual: '', note: '' }
          const parsed = parseScore(draft.actual)
          const rate =
            typeof parsed === 'number' && item.score
              ? `${((parsed / item.score) * 100).toFixed(1)}%`
              : '—'
          return (
            <div className="score-backfill-row" role="row" key={item.id}>
              <span role="cell">
                <strong>{item.name}</strong>
                {item.category && <small className="cell-sub">{item.category}</small>}
              </span>
              <span role="cell" className="mono">
                {formatNumber(item.score)}
              </span>
              <span role="cell">
                <input
                  className="input input--sm score-input"
                  inputMode="decimal"
                  value={draft.actual}
                  placeholder="—"
                  disabled={!canWrite}
                  aria-label={`${item.name} 实际得分`}
                  onChange={(event) => update(item.id, { actual: event.target.value })}
                />
              </span>
              <span role="cell" className="mono">
                {rate}
              </span>
              <span role="cell">
                <input
                  className="input input--sm"
                  value={draft.note}
                  placeholder="扣分原因"
                  disabled={!canWrite}
                  aria-label={`${item.name} 备注`}
                  onChange={(event) => update(item.id, { note: event.target.value })}
                />
              </span>
            </div>
          )
        })}
      </div>

      {formError && <p className="form-alert" role="alert">{formError}</p>}
      {canWrite && (
        <footer className="score-backfill-actions">
          {dirty && <span className="unsaved-chip">未保存</span>}
          <Button
            variant="primary"
            size="sm"
            icon={<Save size={14} />}
            loading={saving}
            disabled={!dirty}
            onClick={save}
          >
            保存评分
          </Button>
        </footer>
      )}
    </section>
  )
}

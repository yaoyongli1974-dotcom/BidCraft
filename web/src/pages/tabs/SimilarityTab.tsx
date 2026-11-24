import { useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  AlertTriangle,
  Download,
  FileSearch,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import {
  checkSimilarity,
  exportSimilarityReport,
  getSimilarityHits,
  getSimilarityReport,
  listSimilarityReports,
  similarityTileValues,
  updateSimilarityHit,
} from '../../api/similarity'
import { downloadBlob } from '../../api/generation'
import { messageOf } from '../../lib/http'
import type { SimilarityHit, SimilarityHitStatus, SimilarityReport } from '../../lib/types'
import { className, formatRelative, type Tone } from '../../lib/format'
import { useQuery } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, Skeleton, Spinner } from '../../components/Feedback'
import { StatTile, TabHeading } from '../../components/Workbench'
import { SimilarityResult } from '../../components/SimilarityResult'
import { similarityKindMeta } from '../../lib/similarityMeta'
import { useToast } from '../../context/useToast'

const TERMINAL_STATUS = new Set([
  'done',
  'completed',
  'complete',
  'failed',
  'error',
  'cancelled',
  'canceled',
])

const REPORT_STATUS_META: Record<string, { label: string; tone: Tone }> = {
  queued: { label: '排队中', tone: 'info' },
  pending: { label: '排队中', tone: 'info' },
  running: { label: '查重中', tone: 'warn' },
  done: { label: '已完成', tone: 'success' },
  completed: { label: '已完成', tone: 'success' },
  complete: { label: '已完成', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
  error: { label: '失败', tone: 'danger' },
  cancelled: { label: '已取消', tone: 'neutral' },
  canceled: { label: '已取消', tone: 'neutral' },
}

function reportStatusMeta(status: string): { label: string; tone: Tone } {
  return REPORT_STATUS_META[(status || '').toLowerCase()] || {
    label: status || '未知状态',
    tone: 'neutral',
  }
}

function isTerminal(status: string | undefined): boolean {
  return Boolean(status && TERMINAL_STATUS.has(status.toLowerCase()))
}

// 与后端枚举保持一致（bidcraft/similarity/service.py:23-24）。
const SCOPE_LABELS: Record<string, string> = {
  project: '项目内部',
  history: '历史项目',
}
const MODE_LABELS: Record<string, string> = {
  text: '文本',
  structure: '结构',
  semantic: '语义',
  image: '图片',
  all: '全部维度',
}

function scopeLabel(value: string | undefined): string {
  return (value && SCOPE_LABELS[value]) || value || '项目内部'
}

function modeLabel(value: string | undefined): string {
  return (value && MODE_LABELS[value]) || value || '文本'
}

/** 轮询单份查重报告，直到服务端返回终态。 */
function useSimilarityReportPolling(ref: string, reportId: string | null) {
  const [report, setReport] = useState<SimilarityReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [polling, setPolling] = useState(false)

  useEffect(() => {
    if (!reportId) {
      setReport(null)
      setError(null)
      setPolling(false)
      return
    }
    let stopped = false
    let timer: number | undefined
    setReport(null)
    setError(null)
    setPolling(true)

    const stopTimer = () => {
      if (timer !== undefined) {
        window.clearInterval(timer)
        timer = undefined
      }
    }
    const tick = async () => {
      try {
        const next = await getSimilarityReport(ref, reportId)
        if (stopped) return
        setReport(next)
        if (isTerminal(next.status)) {
          stopTimer()
          setPolling(false)
        }
      } catch (err) {
        if (stopped) return
        setError(messageOf(err))
        stopTimer()
        setPolling(false)
      }
    }

    void tick()
    timer = window.setInterval(() => void tick(), 1800)
    return () => {
      stopped = true
      stopTimer()
    }
  }, [ref, reportId])

  return { report, error, polling }
}

export function SimilarityTab() {
  const { ref, canWrite } = useWorkspace()
  const toast = useToast()
  const reportsQuery = useQuery(`similarity-reports:${ref}`, () => listSimilarityReports(ref))
  const reports = useMemo(() => reportsQuery.data || [], [reportsQuery.data])

  const [scope, setScope] = useState('project')
  const [mode, setMode] = useState('text')
  const [threshold, setThreshold] = useState('0.85')
  const [sourceProjectId, setSourceProjectId] = useState('')
  const [starting, setStarting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [kindFilter, setKindFilter] = useState('all')
  const [busyHit, setBusyHit] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)

  const { report, error: reportError, polling } = useSimilarityReportPolling(ref, activeId)
  const finished = isTerminal(report?.status)
  const hitsQuery = useQuery(
    `similarity-hits:${ref}:${activeId || 'none'}`,
    () => getSimilarityHits(ref, activeId || ''),
    { enabled: Boolean(activeId) && finished },
  )
  const hits = useMemo(() => hitsQuery.data || [], [hitsQuery.data])

  useEffect(() => {
    if (!activeId && reports.length > 0) setActiveId(reports[0].id)
  }, [activeId, reports])

  const kindCounts = useMemo(() => {
    const counts: Record<string, number> = { all: hits.length }
    hits.forEach((hit) => {
      const key = (hit.kind || 'other').toLowerCase()
      counts[key] = (counts[key] || 0) + 1
    })
    return counts
  }, [hits])

  useEffect(() => {
    if (kindFilter !== 'all' && (kindCounts[kindFilter] || 0) === 0) {
      setKindFilter('all')
    }
  }, [kindFilter, kindCounts])

  const visibleHits = useMemo(
    () =>
      kindFilter === 'all'
        ? hits
        : hits.filter((hit) => (hit.kind || 'other').toLowerCase() === kindFilter),
    [hits, kindFilter],
  )

  const start = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!canWrite || starting) return
    const value = Number(threshold)
    if (!Number.isFinite(value) || value <= 0 || value > 1) {
      setFormError('相似度阈值需在 0 到 1 之间。')
      return
    }
    // 历史项目比对必须指定来源项目，否则后端无法定位比对对象。
    const source = sourceProjectId.trim()
    if (scope === 'history' && !source) {
      setFormError('历史项目比对需填写来源项目编号。')
      return
    }
    setStarting(true)
    setFormError(null)
    try {
      const response = await checkSimilarity(ref, {
        scope,
        mode,
        threshold: value,
        ...(scope === 'history' && source ? { source_project_id: source } : {}),
      })
      if (!response.report_id) {
        throw new Error('服务端未返回查重报告编号。')
      }
      setActiveId(response.report_id)
      setKindFilter('all')
      reportsQuery.refresh()
      toast.success('查重已启动', '报告完成后会自动刷新结果。')
    } catch (error) {
      const message = messageOf(error)
      setFormError(message)
      toast.error('启动查重失败', message)
    } finally {
      setStarting(false)
    }
  }

  const review = async (hit: SimilarityHit, status: SimilarityHitStatus, note?: string) => {
    if (!canWrite || busyHit) return
    setBusyHit(hit.id)
    try {
      const updated = await updateSimilarityHit(ref, hit.id, status, note)
      hitsQuery.setData((current) =>
        (current || []).map((item) =>
          item.id === hit.id
            ? { ...item, ...updated, id: updated.id || item.id }
            : item,
        ),
      )
      toast.success('复核状态已更新', '结果已保存到查重报告。')
    } catch (error) {
      toast.error('更新复核状态失败', messageOf(error))
    } finally {
      setBusyHit(null)
    }
  }

  const exportReport = async () => {
    if (!activeId || exporting) return
    setExporting(true)
    try {
      const { blob, filename } = await exportSimilarityReport(ref, activeId)
      downloadBlob(blob, filename)
      toast.success('查重报告已导出', filename)
    } catch (error) {
      toast.error('导出查重报告失败', messageOf(error))
    } finally {
      setExporting(false)
    }
  }

  const status = report ? reportStatusMeta(report.status) : null
  const stats = report?.stats || {}
  const skippedText = Array.isArray(report?.skipped)
    ? report?.skipped.join('、')
    : typeof report?.skipped === 'number'
      ? `${report.skipped} 项`
      : ''
  // 后端 stats 不含 checked/reviewed；从已规范化的命中明细派生，避免磁贴显示「—」。
  const tiles = similarityTileValues(hits, stats, skippedText)

  return (
    <div className="tab-content sim-tab">
      <TabHeading
        eyebrow="多维查重"
        title="多维查重与人工复核"
        description="按范围与粒度比对项目正文、历史标书与企业资料，命中结果需人工复核后才计入结论。"
        actions={
          <Button
            size="sm"
            variant="secondary"
            icon={<RefreshCw size={13} className={reportsQuery.refreshing ? 'spin' : undefined} />}
            disabled={reportsQuery.refreshing}
            onClick={reportsQuery.refresh}
          >
            刷新报告
          </Button>
        }
      />

      <div className="sim-layout">
        <section className="panel sim-form-panel" aria-labelledby="sim-form-heading">
          <div className="panel-head">
            <div>
              <div className="eyebrow">新建查重</div>
              <h3 id="sim-form-heading">选择范围与阈值</h3>
            </div>
            <Badge tone="info" subtle>
              后台执行
            </Badge>
          </div>
          <form className="sim-form" onSubmit={(event) => void start(event)}>
            <div className="sim-form-grid">
              <label className="form-field">
                <span>查重范围</span>
                <select
                  className="select"
                  value={scope}
                  onChange={(event) => setScope(event.target.value)}
                >
                  <option value="project">项目内部</option>
                  <option value="history">历史项目</option>
                </select>
              </label>
              <label className="form-field">
                <span>比对粒度</span>
                <select
                  className="select"
                  value={mode}
                  onChange={(event) => setMode(event.target.value)}
                >
                  <option value="text">文本</option>
                  <option value="structure">结构</option>
                  <option value="semantic">语义</option>
                  <option value="image">图片</option>
                  <option value="all">全部维度</option>
                </select>
              </label>
              <label className="form-field">
                <span>相似度阈值</span>
                <input
                  className="input"
                  type="number"
                  min={0.01}
                  max={1}
                  step={0.01}
                  value={threshold}
                  onChange={(event) => setThreshold(event.target.value)}
                />
                <span className="field-hint">0–1，越高越严格。</span>
              </label>
              <label className="form-field">
                <span>{scope === 'history' ? '来源项目（必填）' : '来源项目（历史比对可选）'}</span>
                <input
                  className="input"
                  value={sourceProjectId}
                  placeholder={scope === 'history' ? '历史项目编号' : '历史比对时填写项目编号'}
                  onChange={(event) => setSourceProjectId(event.target.value)}
                />
              </label>
            </div>
            {formError && (
              <p className="field-error" role="alert">
                {formError}
              </p>
            )}
            <div className="sim-form-foot">
              <span className="subtle">
                <ShieldCheck size={13} /> 仅比对本机数据，不上传外部服务。
              </span>
              <Button
                type="submit"
                variant="primary"
                icon={<Sparkles size={14} />}
                loading={starting}
                disabled={!canWrite || (scope === 'history' && !sourceProjectId.trim())}
                title={
                  !canWrite
                    ? '只读成员不能启动查重'
                    : scope === 'history' && !sourceProjectId.trim()
                      ? '历史项目比对需填写来源项目编号'
                      : undefined
                }
              >
                启动查重
              </Button>
            </div>
          </form>
        </section>

        <aside className="panel sim-reports" aria-labelledby="sim-reports-heading">
          <div className="panel-head">
            <div>
              <div className="eyebrow">历史报告</div>
              <h3 id="sim-reports-heading">查重报告</h3>
            </div>
            <span className="subtle">{reports.length} 份</span>
          </div>
          {reportsQuery.loading && !reportsQuery.data ? (
            <div className="sim-report-list" aria-busy="true">
              <Skeleton height={46} />
              <Skeleton height={46} />
            </div>
          ) : reportsQuery.error ? (
            <div className="sim-report-list">
              <ErrorState
                compact
                message={reportsQuery.error}
                onRetry={reportsQuery.refresh}
              />
            </div>
          ) : reports.length === 0 ? (
            <div className="sim-report-list">
              <EmptyState
                compact
                icon={FileSearch}
                title="还没有查重报告"
                description="填写范围与阈值后启动一次查重。"
              />
            </div>
          ) : (
            <ul className="sim-report-list">
              {reports.map((item) => {
                const meta = reportStatusMeta(item.status)
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={className(
                        'sim-report-item',
                        item.id === activeId && 'is-active',
                      )}
                      aria-pressed={item.id === activeId}
                      onClick={() => setActiveId(item.id)}
                    >
                      <span className="sim-report-item-top">
                        <strong>{scopeLabel(item.scope)}</strong>
                        <Badge tone={meta.tone} subtle>
                          {meta.label}
                        </Badge>
                      </span>
                      <span className="sim-report-item-meta mono">
                        {item.mode ? modeLabel(item.mode) : '—'} · 阈值 {item.threshold || '—'}
                        {item.created_at ? ` · ${formatRelative(item.created_at)}` : ''}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </aside>
      </div>

      {activeId && (
        <section className="panel sim-report" aria-live="polite">
          <div className="panel-head">
            <div>
              <div className="eyebrow">查重结果</div>
              <h3>
                {status ? status.label : '正在读取报告'}
                {polling && <Spinner size={14} />}
              </h3>
              {report?.created_at && (
                <p className="panel-sub">创建于 {formatRelative(report.created_at)}</p>
              )}
            </div>
            <Button
              size="sm"
              variant="secondary"
              icon={<Download size={13} />}
              loading={exporting}
              disabled={!finished}
              title={!finished ? '报告完成后才能导出' : undefined}
              onClick={() => void exportReport()}
            >
              导出报告
            </Button>
          </div>

          {reportError ? (
            <div className="sim-report-body">
              <ErrorState message={reportError} />
            </div>
          ) : !report ? (
            <div className="sim-report-body">
              <div className="loading-block" aria-busy="true">
                <Skeleton width={160} height={12} />
                <Skeleton width="100%" height={80} radius={8} />
              </div>
            </div>
          ) : (
            <>
              <div className="sim-stats">
                <StatTile
                  label="比对条目"
                  value={tiles.checked}
                  detail="已检查内容块"
                />
                <StatTile
                  label="命中"
                  value={tiles.hits}
                  tone={tiles.hits > 0 ? 'warn' : 'success'}
                  detail="需人工复核"
                />
                <StatTile
                  label="已复核"
                  value={tiles.reviewed}
                  tone="info"
                  detail="人工确认或忽略"
                />
                <StatTile
                  label="跳过"
                  value={tiles.skipped}
                  detail="无法比对的内容"
                />
              </div>

              {!finished ? (
                <div className="sim-report-body sim-report-waiting" role="status">
                  <Spinner size={18} />
                  <p>查重进行中，报告完成后会自动加载命中结果。</p>
                </div>
              ) : hitsQuery.error ? (
                <div className="sim-report-body">
                  <ErrorState message={hitsQuery.error} onRetry={hitsQuery.refresh} />
                </div>
              ) : hitsQuery.loading && !hitsQuery.data ? (
                <div className="sim-report-body">
                  <div className="loading-block" aria-busy="true">
                    <Skeleton width="100%" height={110} radius={8} />
                    <Skeleton width="88%" height={110} radius={8} />
                  </div>
                </div>
              ) : hits.length === 0 ? (
                <div className="sim-report-body">
                  <EmptyState
                    icon={FileSearch}
                    title="没有命中"
                    description="本次查重没有发现超过阈值的内容，或报告未返回命中明细。"
                  />
                </div>
              ) : (
                <div className="sim-report-body">
                  <div className="sim-kind-filter" role="group" aria-label="按重复类型筛选">
                    <button
                      type="button"
                      className={className('sim-kind-chip', kindFilter === 'all' && 'is-active')}
                      aria-pressed={kindFilter === 'all'}
                      onClick={() => setKindFilter('all')}
                    >
                      全部 <span>{hits.length}</span>
                    </button>
                    {Object.keys(kindCounts)
                      .filter((key) => key !== 'all')
                      .map((key) => {
                        const meta = similarityKindMeta(key)
                        return (
                          <button
                            key={key}
                            type="button"
                            className={className(
                              'sim-kind-chip',
                              kindFilter === key && 'is-active',
                            )}
                            aria-pressed={kindFilter === key}
                            onClick={() => setKindFilter(key)}
                          >
                            {meta.label} <span>{kindCounts[key]}</span>
                          </button>
                        )
                      })}
                  </div>
                  <div className="sim-hit-list">
                    {visibleHits.map((hit) => (
                      <SimilarityResult
                        key={hit.id}
                        hit={hit}
                        canReview={canWrite}
                        busy={busyHit === hit.id}
                        onReview={(next, note) => void review(hit, next, note)}
                      />
                    ))}
                  </div>
                </div>
              )}

              {skippedText && (
                <p className="sim-skipped">
                  <AlertTriangle size={13} /> 跳过：{skippedText}
                </p>
              )}
            </>
          )}
        </section>
      )}
    </div>
  )
}

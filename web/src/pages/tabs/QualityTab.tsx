import { useEffect, useRef, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Download,
  FileBarChart,
  RefreshCw,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import {
  exportEvaluationReport,
  getEvaluationFindings,
  getEvaluationReportDetail,
  getEvaluationSummary,
  getLatestEvaluation,
  listEvaluationReports,
  runEvaluation,
  runEvaluationAsync,
  updateQualityFinding,
} from '../../api/evaluation'
import { downloadBlob } from '../../api/generation'
import { getProjectLessons } from '../../api/learning'
import { watchJob } from '../../hooks/watchJob'
import { messageOf } from '../../lib/http'
import type { Job, QualityDimension, QualityFinding, QualityReport } from '../../lib/types'
import { className, formatDate, formatRelative } from '../../lib/format'
import { useQuery } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, Skeleton } from '../../components/Feedback'
import { StatTile, TabHeading } from '../../components/Workbench'
import { useToast } from '../../context/useToast'

const VERDICT_META: Record<string, { label: string; tone: 'success' | 'warn' | 'danger' }> = {
  pass: { label: '通过', tone: 'success' },
  warn: { label: '需关注', tone: 'warn' },
  blocked: { label: '已阻断', tone: 'danger' },
}

const LEVEL_TONE: Record<string, 'danger' | 'warn' | 'info' | 'neutral'> = {
  blocking: 'danger',
  critical: 'danger',
  major: 'warn',
  minor: 'info',
  info: 'neutral',
}

type QualityMetrics = NonNullable<QualityReport['summary']['metrics']>
type TenderQuality = NonNullable<QualityReport['summary']['tender_quality']>

function metricText(
  value: number | string | null | undefined,
  kind: 'rate' | 'density',
): string {
  if (value === null || value === undefined || value === '') return '未测量'
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numeric)) return String(value)
  if (kind === 'rate') return `${(numeric * 100).toFixed(1)}%`
  return `${numeric.toFixed(1)} /千词`
}

function detailText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function dimensionClass(score: number): string {
  if (score >= 90) return 'is-ok'
  if (score >= 60) return 'is-warn'
  return 'is-danger'
}

function dimensionTone(score: number): 'success' | 'warn' | 'danger' {
  if (score >= 90) return 'success'
  if (score >= 60) return 'warn'
  return 'danger'
}

/** 问题来源行：后端有值就展示真值，缺失一律「—」，不拿旧报告或推测值填充。 */
function findingSourceParts(finding: QualityFinding): Array<{ label: string; value: string }> {
  const evidence = finding.evidence || {}
  return [
    { label: '核对码', value: detailText(evidence.code) },
    { label: '页码', value: detailText(evidence.page) },
    { label: '位置', value: detailText(evidence.location) },
    { label: '条款', value: detailText(evidence.clause_no) },
    { label: '章节', value: detailText(finding.node_id) },
    { label: '要求', value: detailText(finding.requirement_id) },
    { label: '评分点', value: detailText(finding.scoring_item_id) },
    {
      label: '稳定键',
      value: detailText(finding.stable_key ?? evidence.stable_key ?? evidence.fingerprint),
    },
    {
      label: '源行',
      value: detailText(
        finding.source_row_id ??
          evidence.source_row_id ??
          evidence.source_row ??
          evidence.row_id ??
          evidence.issue_id ??
          evidence.audit_id ??
          evidence.run_id ??
          evidence.task_id,
      ),
    },
  ]
}

/** 后台任务的 result 只作为报告索引；真正的报告内容一律走详情接口。 */
function reportIdFromJob(job: Job): string {
  const result: unknown = job.result
  if (!result || typeof result !== 'object') return ''
  const id = (result as Record<string, unknown>).report_id
  return typeof id === 'string' && id.trim() ? id : ''
}

function MetricDetails({ metrics, tender }: { metrics?: QualityMetrics; tender?: TenderQuality }) {
  const rows = [
    {
      label: '人工修改率',
      value: metrics?.manual_edit_rate,
      coverage: metrics?.coverage?.manual_edit_rate,
      samples: metrics?.samples?.manual_edit_rate,
    },
    {
      label: '问题密度',
      value: metrics?.issue_density,
      coverage: metrics?.coverage?.issue_density,
      samples: metrics?.samples?.issue_density,
    },
    {
      label: '清单匹配率',
      value: metrics?.boq_match_rate,
      coverage: metrics?.coverage?.boq_match_rate,
      samples: metrics?.samples?.boq_match_rate,
    },
    {
      label: '条款召回率',
      value: tender?.clause_recall,
      coverage: tender?.coverage?.clauses,
      samples: tender?.samples?.matched_clauses,
    },
    {
      label: '关键值解析率',
      value: tender?.value_parse_rate,
      coverage: tender?.coverage?.values,
      samples: tender?.samples?.matched_values,
    },
    {
      label: '判定一致率',
      value: tender?.verdict_agreement,
      coverage: tender?.coverage?.verdict,
      samples: tender?.samples?.verdict_checks,
    },
  ]
  const mismatches = [
    ...(metrics?.mismatches || []),
    ...(tender?.mismatches || []),
  ]
  return (
    <details className="quality-metric-details">
      <summary>查看指标计算明细（分子 / 分母 / 样本）</summary>
      <ul>
        {rows.map((row) => {
          const measured = row.value !== null && row.value !== undefined && row.value !== ''
          const sampleRows = row.samples || []
          return (
            <li key={row.label}>
              <strong>{row.label}</strong>：
              {measured
                ? `分子 ${detailText(row.coverage?.numerator)} / 分母 ${detailText(
                    row.coverage?.denominator,
                  )} / 样本 ${detailText(row.coverage?.sample_count)}`
                : '未测量'}
              {sampleRows.length > 0 && (
                <div className="quality-metric-samples">
                  <span>可抽查样本 {sampleRows.length} 条</span>
                  {sampleRows.slice(0, 3).map((sample, index) => (
                    <code key={index}>{JSON.stringify(sample)}</code>
                  ))}
                </div>
              )}
            </li>
          )
        })}
      </ul>
      {mismatches.length > 0 && (
        <div className="quality-metric-samples">
          <span>不匹配明细 {mismatches.length} 条</span>
          {mismatches.slice(0, 3).map((item, index) => (
            <code key={index}>{JSON.stringify(item)}</code>
          ))}
        </div>
      )}
    </details>
  )
}

function QualityDimensions({ dimensions }: { dimensions: QualityDimension[] }) {
  if (dimensions.length === 0) return <p className="subtle">暂无维度数据</p>
  return (
    <div className="quality-dimensions">
      {dimensions.map((dim) => (
        <div className="quality-dim" key={dim.key}>
          <div className="quality-dim-head">
            <span>{dim.label}</span>
            <strong className={dimensionClass(dim.score)}>{dim.score.toFixed(1)}</strong>
            <span className="quality-dim-weight">权重 {dim.weight}%</span>
          </div>
          <div className="quality-dim-track" aria-hidden>
            <span className={dimensionClass(dim.score)} style={{ width: `${Math.min(100, Math.max(0, dim.score))}%` }} />
          </div>
        </div>
      ))}
    </div>
  )
}

function FindingCard({
  finding,
  canWrite,
  onUpdate,
}: {
  finding: QualityFinding
  canWrite: boolean
  onUpdate: (finding: QualityFinding, status: string, note?: string) => void
}) {
  const [note, setNote] = useState('')
  const [noteError, setNoteError] = useState('')
  const isOpen = finding.status === 'open'
  const commit = (status: string) => {
    const trimmed = note.trim()
    // 后端对 status='ignored' 要求非空 note；提交前在本地拦下并明确提示。
    if (status === 'ignored' && !trimmed) {
      setNoteError('忽略时必须填写处置意见')
      return
    }
    setNoteError('')
    onUpdate(finding, status, trimmed || undefined)
    setNote('')
  }
  return (
    <article className={className('quality-finding', `is-${finding.level}`)}>
      <div className="quality-finding-head">
        <Badge tone={LEVEL_TONE[finding.level] || 'neutral'} subtle>{finding.level}</Badge>
        <strong>{finding.title}</strong>
        <span className="quality-finding-cat">{finding.category}</span>
        <Badge tone={isOpen ? 'warn' : 'success'} subtle>
          {isOpen ? '待处置' : finding.status}
        </Badge>
      </div>
      {finding.description && <p className="quality-finding-desc">{finding.description}</p>}
      <p className="quality-finding-source">
        {findingSourceParts(finding).map((part) => (
          <span key={part.label}>
            {part.label}：{part.value}
          </span>
        ))}
      </p>
      {finding.recommendation && (
        <p className="quality-finding-rec"><CheckCircle2 size={13} /> {finding.recommendation}</p>
      )}
      {(finding.resolution_note || finding.resolved_by || finding.resolved_at) && (
        <p className="quality-finding-resolution">
          处置人：{finding.resolved_by || '—'}
          {finding.resolved_at && ` · 处置时间：${formatDate(finding.resolved_at)}`}
          {finding.resolution_note ? ` · 意见：${finding.resolution_note}` : ''}
        </p>
      )}
      {canWrite && (
        <div className="quality-finding-actions">
          <input
            className="input input--sm"
            aria-label={`处置意见：${finding.title}`}
            placeholder="填写处置意见（忽略时必填）"
            value={note}
            aria-invalid={Boolean(noteError)}
            onChange={(event) => {
              setNote(event.target.value)
              if (noteError) setNoteError('')
            }}
          />
          {isOpen ? (
            <>
              <Button variant="secondary" size="sm" onClick={() => commit('handled')}>
                标记已处置
              </Button>
              <Button variant="ghost" size="sm" onClick={() => commit('ignored')}>
                忽略
              </Button>
            </>
          ) : (
            <Button variant="secondary" size="sm" onClick={() => commit('open')}>
              重新打开
            </Button>
          )}
        </div>
      )}
      {noteError && (
        <p className="inline-error" role="alert">{noteError}</p>
      )}
    </article>
  )
}

/**
 * 建议经验（学习闭环）只读面板。内容来自人工修改与质量 finding 的回流，
 * 未经审核不自动生效，也不是已验证事实或法规；此处不提供写/发布操作。
 */
export function LessonsPanel({ projectRef }: { projectRef: string }) {
  const [nodeFilter, setNodeFilter] = useState('')
  const [appliedNode, setAppliedNode] = useState('')
  const lessonsQuery = useQuery(`lessons:${projectRef}:${appliedNode}`, () =>
    getProjectLessons(projectRef, {
      ...(appliedNode ? { node_id: appliedNode } : {}),
      limit: 50,
    }),
  )
  const lessons = lessonsQuery.data?.items || []
  const status = lessonsQuery.data?.status || '未审核建议经验'

  return (
    <section className="panel quality-panel" aria-label="建议经验">
      <div className="config-head">
        <div>
          <div className="eyebrow">学习闭环 · 只读</div>
          <h3>建议经验</h3>
        </div>
        <Badge tone="warn" subtle>
          {status}
        </Badge>
        <Button
          variant="ghost"
          size="sm"
          icon={<RefreshCw size={14} className={lessonsQuery.refreshing ? 'spin' : undefined} />}
          onClick={lessonsQuery.refresh}
        >
          刷新
        </Button>
      </div>
      <p className="subtle quality-lessons-note">
        由人工修改与质量 finding 回流生成；仅作建议，未审核不会写入企业规范，也不代表已验证事实或法规。
      </p>
      <form
        className="quality-lessons-filter"
        onSubmit={(event) => {
          event.preventDefault()
          setAppliedNode(nodeFilter.trim())
        }}
      >
        <label>
          <span>章节节点筛选</span>
          <input
            className="input input--sm"
            aria-label="经验节点筛选"
            placeholder="输入 node_id（留空为全部）"
            value={nodeFilter}
            onChange={(event) => setNodeFilter(event.target.value)}
          />
        </label>
        <Button type="submit" variant="secondary" size="sm">
          筛选
        </Button>
        {appliedNode && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setAppliedNode('')
              setNodeFilter('')
            }}
          >
            清除筛选
          </Button>
        )}
      </form>
      {lessonsQuery.error && (
        <p className="inline-error">
          建议经验加载失败：{lessonsQuery.error}
          <button type="button" className="text-btn" onClick={lessonsQuery.refresh}>
            重试
          </button>
        </p>
      )}
      {lessonsQuery.loading && <Skeleton height={90} />}
      {!lessonsQuery.loading && !lessonsQuery.error && lessons.length === 0 && (
        <EmptyState
          icon={ClipboardCheck}
          title="暂无建议经验"
          description="当前项目（或所选章节）还没有从人工修改与质量 finding 回流的建议。"
        />
      )}
      {lessons.length > 0 && (
        <div className="quality-lessons">
          {lessons.map((lesson) => (
            <article className="quality-lesson" key={lesson.id}>
              <div className="quality-lesson-meta">
                <Badge tone="neutral" subtle>
                  ID {lesson.id}
                </Badge>
                <Badge tone="info" subtle>
                  来源 {lesson.source || '—'}
                </Badge>
                <Badge tone="accent" subtle>
                  类型 {lesson.kind || '—'}
                </Badge>
                {lesson.node_id && <span className="mono">{lesson.node_id}</span>}
                <span>作者 {lesson.author || '—'}</span>
                <span>{formatRelative(lesson.created_at)}</span>
              </div>
              <p className="quality-lesson-text">{lesson.text}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

export function QualityTab() {
  const { ref, canWrite } = useWorkspace()
  const toast = useToast()
  const [running, setRunning] = useState(false)
  const [jobProgress, setJobProgress] = useState<number | null>(null)
  const watchRef = useRef<(() => void) | null>(null)
  const [report, setReport] = useState<QualityReport | null>(null)
  const [note, setNote] = useState('')
  const [historyLoadingId, setHistoryLoadingId] = useState<string | null>(null)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [downloadingId, setDownloadingId] = useState<string | null>(null)

  useEffect(() => () => watchRef.current?.(), [])

  const latestQuery = useQuery(`quality-latest:${ref}`, () => getLatestEvaluation(ref))
  const reportsQuery = useQuery(`quality-reports:${ref}`, () => listEvaluationReports(ref))
  const summaryQuery = useQuery(`quality-summary:${ref}`, () => getEvaluationSummary(ref))

  // 最新报告作为初始真相源；运行/历史切换由下方交互覆盖。
  useEffect(() => {
    if (report === null && latestQuery.data) setReport(latestQuery.data)
  }, [latestQuery.data, report])

  const findings = report?.findings || []

  const handleRun = async () => {
    setRunning(true)
    try {
      const created = await runEvaluation(ref, { note })
      // 后端可能只在响应里回报告头、findings 由列表接口补齐；重新拉一次最新报告，
      // 避免刚跑完评估却因 created.findings 为空而展示空白问题列表。
      const refreshed = await getLatestEvaluation(ref).catch(() => null)
      const adopted =
        [created, refreshed, latestQuery.data].find((item) => item && item.findings.length > 0) ??
        created
      setReport(adopted)
      toast.success(`已生成质量报告 v${adopted.report_version}`)
      await Promise.all([latestQuery.refresh(), reportsQuery.refresh(), summaryQuery.refresh()])
    } catch (error) {
      toast.error(messageOf(error))
    } finally {
      setRunning(false)
    }
  }

  const handleRunAsync = async () => {
    setRunning(true)
    setJobProgress(0)
    try {
      const { job_id: jobId } = await runEvaluationAsync(ref, { note })
      toast.info('质量评估已提交后台任务')
      watchRef.current?.()
      watchRef.current = watchJob(jobId, {
        onUpdate: (data) => setJobProgress(data.progress ?? 0),
        onDone: async (job) => {
          watchRef.current = null
          setRunning(false)
          setJobProgress(null)
          // result 里的 report_id 只是索引：必须按它取详情，不能拿异步窗口的最新报告冒充本次结果。
          const reportId = reportIdFromJob(job)
          try {
            if (reportId) {
              const exact = await getEvaluationReportDetail(ref, reportId)
              setReport(exact)
              toast.success(`已生成质量报告 v${exact.report_version}`)
            } else {
              toast.success('后台质量评估已完成')
            }
            await Promise.all([latestQuery.refresh(), reportsQuery.refresh(), summaryQuery.refresh()])
          } catch (error) {
            toast.error(messageOf(error))
          }
        },
        onFailed: (job) => {
          watchRef.current = null
          setRunning(false)
          setJobProgress(null)
          toast.error(job.error || job.message || '质量评估任务失败')
        },
        onPollError: (message) => {
          watchRef.current = null
          setRunning(false)
          setJobProgress(null)
          toast.error(message)
        },
      })
    } catch (error) {
      setRunning(false)
      setJobProgress(null)
      toast.error(messageOf(error))
    }
  }

  const handleUpdate = async (finding: QualityFinding, status: string, note?: string) => {
    try {
      const updated = await updateQualityFinding(
        ref,
        finding.id,
        { status, ...(note ? { note } : {}) },
      )
      setReport((prev) =>
        prev
          ? { ...prev, findings: prev.findings.map((f) => (f.id === updated.id ? updated : f)) }
          : prev,
      )
      toast.success('质量问题已更新')
    } catch (error) {
      toast.error(messageOf(error))
    }
  }

  /** 列表接口不返回 findings（快照列表很大），选中历史报告时按需取详情。 */
  const selectReport = async (item: QualityReport) => {
    if (item.id === report?.id) return
    setHistoryError(null)
    setHistoryLoadingId(item.id)
    try {
      const findings = await getEvaluationFindings(ref, item.id)
      setReport({ ...item, findings })
    } catch (error) {
      setHistoryError(messageOf(error))
    } finally {
      setHistoryLoadingId(null)
    }
  }

  const handleDownload = async (item: QualityReport) => {
    setDownloadingId(item.id)
    try {
      const { blob, filename } = await exportEvaluationReport(ref, item.id)
      downloadBlob(blob, filename)
      toast.success(`已下载 ${filename}`)
    } catch (error) {
      toast.error(messageOf(error))
    } finally {
      setDownloadingId(null)
    }
  }

  const hardRisk = Boolean(report?.risk_summary?.has_hard_risk)
  const verdictMeta = report?.verdict
    ? VERDICT_META[report.verdict] || { label: report.verdict, tone: 'neutral' as const }
    : { label: '—', tone: 'neutral' as const }

  return (
    <div className="quality-tab">
      <TabHeading
        eyebrow="投标质量评估中心"
        title="质量评估"
        description="整合规则核对、实质性响应、评分覆盖、证据追溯与章节确认，生成可审计的质量报告。硬性风险一票否决，质量分不替代评标得分。"
        actions={
          <>
            <Button
              variant="primary"
              size="sm"
              icon={<RefreshCw size={14} />}
              loading={running && jobProgress === null}
              disabled={!canWrite || running}
              title={!canWrite ? '只读成员不能运行评估' : undefined}
              onClick={() => void handleRun()}
            >
              运行评估
            </Button>
            <Button
              variant="secondary"
              size="sm"
              icon={<Activity size={14} />}
              disabled={!canWrite || running}
              title="后台生成报告，适合数据量大时使用"
              onClick={() => void handleRunAsync()}
            >
              后台评估
            </Button>
          </>
        }
      />

      <label className="quality-note">
        <span>评估说明（可选）</span>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="例如：交付前例行评估"
          disabled={!canWrite}
        />
      </label>

      {jobProgress !== null && (
        <p className="subtle" role="status">后台评估进行中：{jobProgress}%</p>
      )}

      {summaryQuery.error && <ErrorState message={messageOf(summaryQuery.error)} />}
      {summaryQuery.data && (
        <section className="panel quality-panel" aria-label="质量评估概览">
          <div className="config-head">
            <div>
              <div className="eyebrow">跨版本评估概览</div>
              <h3>质量趋势</h3>
            </div>
            <Button
              variant="ghost"
              size="sm"
              icon={<RefreshCw size={14} />}
              loading={summaryQuery.refreshing}
              onClick={() => summaryQuery.refresh()}
            >
              刷新
            </Button>
          </div>
          {summaryQuery.data.items.length === 0 ? (
            <EmptyState
              icon={FileBarChart}
              title="暂无可访问的评估概览"
              description="当前账号没有可读取的质量报告。"
            />
          ) : (
            <div className="quality-overview">
              {summaryQuery.data.items.slice(0, 6).map((item) => {
                const verdict = item.measured && item.verdict
                  ? VERDICT_META[item.verdict] || { label: item.verdict, tone: 'neutral' as const }
                  : { label: '未测量', tone: 'neutral' as const }
                return (
                  <StatTile
                    key={item.project_id}
                    label={item.name || item.ref}
                    value={
                      <span className="quality-summary-value">
                        {item.measured ? item.overall_score?.toFixed(1) ?? '—' : '未测量'}
                        <Badge tone={verdict.tone} subtle>{verdict.label}</Badge>
                      </span>
                    }
                  />
                )
              })}
            </div>
          )}
        </section>
      )}

      {hardRisk && (
        <div className="quality-hard-risk" role="alert">
          <AlertTriangle size={16} />
          <span>存在硬性风险（★ 条款未确认或未处置阻断项），综合分不影响「已阻断」结论。</span>
        </div>
      )}

      {latestQuery.loading && <Skeleton height={180} />}
      {latestQuery.error && <ErrorState message={messageOf(latestQuery.error)} />}

      {!latestQuery.loading && !latestQuery.error && !report && (
        <EmptyState
          icon={FileBarChart}
          title="还没有质量报告"
          description="点击「运行评估」生成第一份质量报告快照。"
        />
      )}

      {report && (
        <>
          <div className="quality-overview">
            <StatTile label="综合分" value={report.overall_score.toFixed(1)} tone={dimensionTone(report.overall_score)} />
            <StatTile label="等级" value={report.grade || '—'} />
            <StatTile label="结论" value={<Badge tone={verdictMeta.tone}>{verdictMeta.label}</Badge>} />
            <StatTile label="版本" value={`v${report.report_version}`} />
            <StatTile label="生成时间" value={report.created_at ? formatRelative(report.created_at) : '—'} />
          </div>

          <section className="panel quality-panel">
            <div className="config-head">
              <div>
                <div className="eyebrow">六维评分</div>
                <h3>质量维度</h3>
              </div>
            </div>
            <QualityDimensions dimensions={report.dimensions || []} />
          </section>

          <section className="panel quality-panel">
            <div className="config-head">
              <div>
                <div className="eyebrow">可追踪质量口径</div>
                <h3>过程与真实件指标</h3>
              </div>
              <Badge tone={report.summary?.tender_quality?.status === '已就绪' ? 'success' : 'neutral'} subtle>
                {report.summary?.tender_quality?.status || '真实件未测量'}
              </Badge>
            </div>
            <div className="quality-overview">
              <StatTile label="人工修改率" value={metricText(report.summary?.metrics?.manual_edit_rate, 'rate')} />
              <StatTile label="问题密度" value={metricText(report.summary?.metrics?.issue_density, 'density')} />
              <StatTile label="清单匹配率" value={metricText(report.summary?.metrics?.boq_match_rate, 'rate')} />
              <StatTile label="条款召回率" value={metricText(report.summary?.tender_quality?.clause_recall, 'rate')} />
              <StatTile label="关键值解析率" value={metricText(report.summary?.tender_quality?.value_parse_rate, 'rate')} />
              <StatTile label="判定一致率" value={metricText(report.summary?.tender_quality?.verdict_agreement, 'rate')} />
            </div>
            {(report.summary?.tender_quality?.reason ||
              (report.summary?.tender_quality?.notes || []).length > 0) && (
              <p className="subtle">
                {report.summary?.tender_quality?.reason ||
                  (report.summary?.tender_quality?.notes || []).join('；')}
              </p>
            )}
            <MetricDetails
              metrics={report.summary?.metrics}
              tender={report.summary?.tender_quality}
            />
          </section>

          {(report.summary?.recommendations || []).length > 0 && (
            <section className="panel quality-panel">
              <div className="eyebrow">处置建议</div>
              <ul className="quality-recs">
                {(report.summary.recommendations || []).map((rec, i) => (
                  <li key={`${rec}-${i}`}><Activity size={13} /> {rec}</li>
                ))}
              </ul>
            </section>
          )}

          <section className="panel quality-panel">
            <div className="config-head">
              <div>
                <div className="eyebrow">质量问题</div>
                <h3>问题清单</h3>
              </div>
              <Badge tone="neutral" subtle>{findings.length} 项</Badge>
            </div>
            {findings.length === 0 ? (
              <EmptyState icon={ClipboardCheck} title="未发现质量问题" description="报告未聚合到待处置问题。" />
            ) : (
              <div className="quality-findings">
                {findings.map((f) => (
                  <FindingCard
                    key={f.id}
                    finding={f}
                    canWrite={canWrite}
                    onUpdate={(item, status, note) => void handleUpdate(item, status, note)}
                  />
                ))}
              </div>
            )}
          </section>
        </>
      )}

      <LessonsPanel projectRef={ref} />

      {(reportsQuery.data || []).length > 1 && (
        <section className="panel quality-panel">
          <div className="eyebrow">历史报告</div>
          <div className="quality-history">
            {(reportsQuery.data || []).map((r) => (
              <div
                key={r.id}
                className={className('quality-history-item', r.id === report?.id && 'is-active')}
              >
                <button
                  type="button"
                  className="quality-history-select"
                  disabled={historyLoadingId !== null}
                  onClick={() => void selectReport(r)}
                >
                  <strong>v{r.report_version}</strong>
                  <span>{r.overall_score.toFixed(1)} 分</span>
                  <Badge
                    tone={r.verdict ? (VERDICT_META[r.verdict]?.tone || 'neutral') : 'neutral'}
                    subtle
                  >
                    {r.grade || '—'}
                  </Badge>
                  {historyLoadingId === r.id && <span className="subtle">加载中…</span>}
                </button>
                <Button
                  variant="ghost"
                  size="sm"
                  icon={<Download size={14} />}
                  aria-label={`下载报告 ${r.id}`}
                  disabled={downloadingId !== null}
                  onClick={() => void handleDownload(r)}
                >
                  下载
                </Button>
              </div>
            ))}
          </div>
          {historyError && (
            <div className="inline-error" role="alert">
              历史报告详情加载失败：{historyError}
            </div>
          )}
        </section>
      )}
    </div>
  )
}

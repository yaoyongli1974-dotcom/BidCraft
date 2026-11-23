import { useEffect, useRef, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  FileBarChart,
  RefreshCw,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import {
  getLatestEvaluation,
  listEvaluationReports,
  runEvaluation,
  runEvaluationAsync,
  updateQualityFinding,
} from '../../api/evaluation'
import { watchJob } from '../../hooks/watchJob'
import { messageOf } from '../../lib/http'
import type { QualityDimension, QualityFinding, QualityReport } from '../../lib/types'
import { className, formatRelative } from '../../lib/format'
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
  onUpdate: (finding: QualityFinding, status: string) => void
}) {
  return (
    <article className={className('quality-finding', `is-${finding.level}`)}>
      <div className="quality-finding-head">
        <Badge tone={LEVEL_TONE[finding.level] || 'neutral'} subtle>{finding.level}</Badge>
        <strong>{finding.title}</strong>
        <span className="quality-finding-cat">{finding.category}</span>
        <Badge tone={finding.status === 'open' ? 'warn' : 'success'} subtle>
          {finding.status === 'open' ? '待处置' : finding.status}
        </Badge>
      </div>
      {finding.description && <p className="quality-finding-desc">{finding.description}</p>}
      {finding.recommendation && (
        <p className="quality-finding-rec"><CheckCircle2 size={13} /> {finding.recommendation}</p>
      )}
      {canWrite && finding.status === 'open' && (
        <div className="quality-finding-actions">
          <Button variant="secondary" size="sm" onClick={() => onUpdate(finding, 'handled')}>
            标记已处置
          </Button>
          <Button variant="ghost" size="sm" onClick={() => onUpdate(finding, 'ignored')}>
            忽略
          </Button>
        </div>
      )}
    </article>
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

  useEffect(() => () => watchRef.current?.(), [])

  const latestQuery = useQuery(`quality-latest:${ref}`, () => getLatestEvaluation(ref))
  const reportsQuery = useQuery(`quality-reports:${ref}`, () => listEvaluationReports(ref))

  // 最新报告作为初始真相源；运行/历史切换由下方交互覆盖。
  useEffect(() => {
    if (report === null && latestQuery.data) setReport(latestQuery.data)
  }, [latestQuery.data, report])

  const findings = report?.findings || []

  const handleRun = async () => {
    setRunning(true)
    try {
      const created = await runEvaluation(ref, { note })
      setReport(created)
      toast.success(`已生成质量报告 v${created.report_version}`)
      await Promise.all([latestQuery.refresh(), reportsQuery.refresh()])
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
        onDone: async () => {
          watchRef.current = null
          setRunning(false)
          setJobProgress(null)
          try {
            const latest = await getLatestEvaluation(ref)
            if (latest) setReport(latest)
            toast.success(
              latest ? `已生成质量报告 v${latest.report_version}` : '后台质量评估已完成',
            )
            await Promise.all([latestQuery.refresh(), reportsQuery.refresh()])
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

  const handleUpdate = async (finding: QualityFinding, status: string) => {
    try {
      const updated = await updateQualityFinding(ref, finding.id, { status })
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

  const hardRisk = Boolean(report?.risk_summary?.has_hard_risk)
  const verdictMeta = VERDICT_META[report?.verdict || 'warn'] || VERDICT_META.warn

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
            <StatTile label="等级" value={report.grade} />
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
                  <FindingCard key={f.id} finding={f} canWrite={canWrite} onUpdate={(item, status) => void handleUpdate(item, status)} />
                ))}
              </div>
            )}
          </section>
        </>
      )}

      {(reportsQuery.data || []).length > 1 && (
        <section className="panel quality-panel">
          <div className="eyebrow">历史报告</div>
          <div className="quality-history">
            {(reportsQuery.data || []).map((r) => (
              <button
                key={r.id}
                type="button"
                className={className('quality-history-item', r.report_version === report?.report_version && 'is-active')}
                onClick={() => setReport(r)}
              >
                <strong>v{r.report_version}</strong>
                <span>{r.overall_score.toFixed(1)} 分</span>
                <Badge tone={VERDICT_META[r.verdict]?.tone || 'warn'} subtle>{r.grade}</Badge>
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

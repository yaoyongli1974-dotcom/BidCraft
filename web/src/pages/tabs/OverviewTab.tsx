import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  Check,
  CircleAlert,
  ClipboardCheck,
  FileStack,
  FileText,
  ListTree,
  PenLine,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  type LucideIcon,
} from 'lucide-react'
import { useWorkspace } from '../WorkspacePage'
import { getIssues } from '../../lib/api'
import {
  className,
  formatRelative,
  issueLevel,
  issueTitle,
  outlineCount,
  parseState,
  percent,
  projectStats,
  truncate,
} from '../../lib/format'
import { useLocalStorageState, useQuery } from '../../lib/hooks'
import type { ExportRecord } from '../../lib/types'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, Skeleton } from '../../components/Feedback'
import { StatTile, TabHeading } from '../../components/Workbench'

type StepState = 'done' | 'current' | 'warn' | 'pending'

interface Step {
  index: number
  key: string
  title: string
  description: string
  action: string
  to: string
  icon: LucideIcon
  done: boolean
  warn?: boolean
}

export function OverviewTab() {
  const { ref, basePath, project, sources, reloadProject } = useWorkspace()
  const stats = projectStats(project)
  const [checkTimes] = useLocalStorageState<Record<string, string>>('bidcraft:check-times', {})
  const [exportRecords] = useLocalStorageState<Record<string, ExportRecord[]>>(
    'bidcraft:exports',
    {},
  )
  const issuesQuery = useQuery(`issues:${ref}`, () => getIssues(ref))

  const checkedAt = checkTimes[ref]
  const exports = exportRecords[ref] || []
  const outline = outlineCount(project)
  const parsedFiles = sources.filter((source) => source.parse_state === 'parsed').length
  const parsed =
    parsedFiles > 0 || stats.facts_total > 0 || (sources.length > 0 && stats.sources > 0)
  const status = String(project.status || '').toLowerCase()
  const checked =
    Boolean(checkedAt) ||
    stats.issues_open > 0 ||
    ['review', 'checking', 'ready', 'exported', 'delivered'].includes(status)
  const exported =
    exports.length > 0 || ['exported', 'delivered'].includes(status)

  const steps: Step[] = [
    {
      index: 1,
      key: 'import',
      title: '导入招标文件',
      description:
        sources.length > 0 ? `已导入 ${sources.length} 份文件` : '尚未导入 PDF、DOCX、TXT 或 XLSX',
      action: sources.length > 0 ? '管理文件' : '导入文件',
      to: `${basePath}/sources`,
      icon: UploadCloud,
      done: sources.length > 0,
    },
    {
      index: 2,
      key: 'parse',
      title: '解析与抽取',
      description: parsed
        ? parsedFiles > 0
          ? `${parsedFiles} 份已解析 · ${stats.facts_total} 项事实`
          : `已建立台账 · ${stats.facts_total} 项事实`
        : '解析招标文件，建立事实台账',
      action: parsed ? '查看解析' : '去解析',
      to: `${basePath}/sources`,
      icon: FileStack,
      done: parsed,
    },
    {
      index: 3,
      key: 'facts',
      title: '校订事实台账',
      description:
        stats.missing_required > 0
          ? `还缺 ${stats.missing_required} 项必填事实`
          : stats.facts_total > 0
            ? `已填 ${stats.facts_filled} / ${stats.facts_total} 项`
            : '等待解析结果',
      action: stats.missing_required > 0 ? '补齐缺失' : '校订事实',
      to: `${basePath}/ledger`,
      icon: ClipboardCheck,
      done: parsed && stats.missing_required === 0 && stats.facts_filled > 0,
      warn: stats.missing_required > 0,
    },
    {
      index: 4,
      key: 'outline',
      title: '编排目录大纲',
      description: outline > 0 ? `${outline} 个章节已编排` : '选择编排方式并分配目标字数',
      action: outline > 0 ? '编辑大纲' : '生成大纲',
      to: `${basePath}/outline`,
      icon: ListTree,
      done: outline > 0,
    },
    {
      index: 5,
      key: 'draft',
      title: '撰写正文',
      description:
        stats.written > 0
          ? `${stats.written} / ${outline} 章已写`
          : outline > 0
            ? '按章节生成或手工撰写'
            : '先有大纲才能撰写正文',
      action: stats.written > 0 ? '继续写作' : '开始写作',
      to: `${basePath}/draft`,
      icon: PenLine,
      done: outline > 0 && stats.written >= outline && outline > 0,
    },
    {
      index: 6,
      key: 'check',
      title: '合规核对',
      description:
        stats.issues_open > 0
          ? `${stats.issues_open} 项问题未关闭`
          : checked
            ? checkedAt
              ? `本机记录于 ${formatRelative(checkedAt)}`
              : '已运行核对'
            : '运行废标风险与实质性响应核对',
      action: checked ? '查看结果' : '运行核对',
      to: `${basePath}/check`,
      icon: ShieldCheck,
      done: checked && stats.issues_open === 0,
      warn: checked && stats.issues_open > 0,
    },
    {
      index: 7,
      key: 'export',
      title: '导出交付',
      description:
        exported && exports.length > 0
          ? `本机记录 ${exports.length} 次导出`
          : exported
            ? '已导出'
            : '生成 DOCX、PDF 或 XLSX',
      action: exported ? '再次导出' : '去导出',
      to: `${basePath}/export`,
      icon: FileText,
      done: exported,
    },
  ]

  const currentIndex = steps.findIndex((step) => !step.done)
  const nextStep = currentIndex >= 0 ? steps[currentIndex] : null
  const completed = steps.filter((step) => step.done).length
  const issues = issuesQuery.data || []
  const recentSources = useMemo(
    () =>
      [...sources]
        .sort(
          (left, right) =>
            new Date(right.created_at || 0).getTime() - new Date(left.created_at || 0).getTime(),
        )
        .slice(0, 3),
    [sources],
  )
  const recentIssues = issues.slice(0, 3)

  return (
    <div className="tab-content overview-tab">
      <TabHeading
        eyebrow="项目概览"
        title="从事实到交付"
        description="按顺序推进七个步骤。事实缺失会阻断正文生成，核对问题不会自动改写正文。"
        actions={
          <Button
            variant="ghost"
            size="sm"
            icon={<RefreshCw size={14} />}
            onClick={() => {
              void reloadProject()
              issuesQuery.refresh()
            }}
          >
            刷新状态
          </Button>
        }
      />

      <div className="overview-grid">
        <div className="overview-main">
          <section className={className('next-action', !nextStep && 'next-action--done')}>
            <div className="next-action-deco" aria-hidden>
              {nextStep ? String(nextStep.index).padStart(2, '0') : '✓'}
            </div>
            <div className="next-action-copy">
              <div className="next-action-eyebrow">
                {nextStep ? `下一步 · 第 ${nextStep.index} 步` : '流程已完成'}
              </div>
              <h3>
                {nextStep ? nextStep.title : '七个步骤已全部完成'}
              </h3>
              <p>
                {nextStep
                  ? nextStep.key === 'facts' && stats.missing_required > 0
                    ? `还有 ${stats.missing_required} 项必填事实没有补齐。系统不会在事实不足时编造证书、业绩或参数。`
                    : nextStep.description
                  : '项目已具备交付条件。导出前建议再运行一次合规核对，确认最新正文。'}
              </p>
              <div className="next-action-actions">
                <Link
                  to={nextStep ? nextStep.to : `${basePath}/export`}
                  className="btn btn--primary btn--md"
                >
                  <span className="btn-label">
                    {nextStep
                      ? nextStep.key === 'facts' && stats.missing_required > 0
                        ? '补齐缺失事实'
                        : `继续：${nextStep.action}`
                      : '前往导出'}
                  </span>
                  <ArrowRight size={15} />
                </Link>
                {stats.missing_required > 0 && nextStep?.key !== 'facts' && (
                  <Link to={`${basePath}/ledger`} className="next-action-link">
                    先处理 {stats.missing_required} 项缺失
                  </Link>
                )}
              </div>
            </div>
            <div className="next-action-progress">
              <div className="next-progress-label">
                <span>整体进度</span>
                <span className="mono">
                  {completed} / {steps.length}
                </span>
              </div>
              <div className="step-pips" aria-hidden>
                {steps.map((step) => (
                  <span
                    key={step.key}
                    className={className(
                      'step-pip',
                      step.done && 'is-done',
                      step.warn && 'is-warn',
                      nextStep?.key === step.key && 'is-current',
                    )}
                  />
                ))}
              </div>
              <span className="next-progress-note">
                {stats.missing_required > 0
                  ? '生成门禁未通过'
                  : stats.issues_open > 0
                    ? '有核对问题待确认'
                    : '事实与正文按本机数据实时统计'}
              </span>
            </div>
          </section>

          <section className="panel steps-panel">
            <div className="panel-head">
              <div>
                <div className="eyebrow">推进清单</div>
                <h3>推进步骤</h3>
              </div>
              <span className="subtle">{completed} / {steps.length} 已完成</span>
            </div>
            <ol className="step-list">
              {steps.map((step, index) => {
                const state: StepState = step.done
                  ? 'done'
                  : step.warn
                    ? 'warn'
                    : nextStep?.key === step.key
                      ? 'current'
                      : 'pending'
                return (
                  <li key={step.key} className={className('step-row', `step-row--${state}`)}>
                    <div className="step-marker" aria-hidden>
                      {step.done ? <Check size={14} strokeWidth={2.4} /> : step.index}
                    </div>
                    <div className="step-body">
                      <div className="step-title">
                        {step.title}
                        {state === 'warn' && (
                          <Badge tone="danger" subtle>
                            {step.key === 'facts' ? '待补齐' : '待处理'}
                          </Badge>
                        )}
                        {state === 'current' && (
                          <Badge tone="accent" subtle>
                            进行中
                          </Badge>
                        )}
                      </div>
                      <p>{step.description}</p>
                    </div>
                    <Link to={step.to} className="step-action">
                      {step.done ? '查看' : step.action}
                      <ArrowRight size={13} />
                    </Link>
                    {index < steps.length - 1 && <span className="step-line" aria-hidden />}
                  </li>
                )
              })}
            </ol>
          </section>
        </div>

        <aside className="overview-side">
          <div className="stat-grid">
            <StatTile
              label="事实完成率"
              value={`${percent(stats.facts_filled, stats.facts_total)}%`}
              detail={`${stats.facts_filled} / ${stats.facts_total} 项已填`}
              tone="accent"
              progress={percent(stats.facts_filled, stats.facts_total)}
            />
            <StatTile
              label="章节进度"
              value={`${stats.written}/${stats.outline}`}
              detail={stats.outline > 0 ? '已写章节 / 大纲章节' : '尚未生成大纲'}
              tone="accent"
              progress={percent(stats.written, stats.outline)}
            />
            <StatTile
              label="缺失必填"
              value={stats.missing_required}
              detail={stats.missing_required > 0 ? '会阻断正文生成' : '生成门禁已通过'}
              tone={stats.missing_required > 0 ? 'danger' : 'success'}
            />
            <StatTile
              label="未关闭问题"
              value={stats.issues_open}
              detail={
                stats.issues_open > 0
                  ? '以已保存的核对问题为准'
                  : checkedAt
                    ? `上次核对 ${formatRelative(checkedAt)}`
                    : '尚无本机核对记录'
              }
              tone={stats.issues_open > 0 ? 'warn' : 'neutral'}
            />
          </div>

          <section className="panel side-panel">
            <div className="panel-head">
              <div>
                <div className="eyebrow">文件</div>
                <h3>最近文件</h3>
              </div>
              <Link to={`${basePath}/sources`} className="text-link">
                全部
              </Link>
            </div>
            {recentSources.length === 0 ? (
              <EmptyState
                icon={FileStack}
                compact
                title="还没有文件"
                description="导入招标文件后，这里会显示解析状态。"
                actions={
                  <Link className="btn btn--secondary btn--sm" to={`${basePath}/sources`}>
                    <span className="btn-label">导入文件</span>
                  </Link>
                }
              />
            ) : (
              <ul className="mini-file-list">
                {recentSources.map((source) => {
                  const meta = parseState(source.parse_state)
                  return (
                    <li key={source.id}>
                      <span className="mini-file-icon" aria-hidden>
                        <FileText size={16} />
                      </span>
                      <div className="mini-file-copy">
                        <span className="mini-file-name" title={source.filename}>
                          {source.filename}
                        </span>
                        <span className="mini-file-meta">
                          {source.page_count ? `${source.page_count} 页 · ` : ''}
                          {formatRelative(source.created_at)}
                        </span>
                      </div>
                      <Badge tone={meta.tone} subtle>
                        {meta.label}
                      </Badge>
                    </li>
                  )
                })}
              </ul>
            )}
          </section>

          <section className="panel side-panel">
            <div className="panel-head">
              <div>
                <div className="eyebrow">问题</div>
                <h3>核对问题</h3>
              </div>
              <Link to={`${basePath}/check`} className="text-link">
                核对
              </Link>
            </div>
            {issuesQuery.loading ? (
              <div className="mini-list-loading">
                <Skeleton height={34} />
                <Skeleton height={34} />
              </div>
            ) : recentIssues.length > 0 ? (
              <ul className="mini-issue-list">
                {recentIssues.map((issue, index) => {
                  const level = issueLevel(issue.level)
                  return (
                    <li key={issue.id || `${issue.code}-${index}`}>
                      <Badge tone={level.tone} subtle>
                        {level.label}
                      </Badge>
                      <div>
                        <span>{truncate(issueTitle(issue), 46)}</span>
                        <small className="mono">
                          {issue.code}
                          {issue.location ? ` · ${issue.location}` : ''}
                        </small>
                      </div>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <div className="mini-empty">
                <ShieldCheck size={20} />
                <p>
                  {stats.issues_open > 0
                    ? `记录显示还有 ${stats.issues_open} 项未关闭问题，打开核对页查看明细。`
                    : '暂无未关闭问题。运行核对后，风险会显示在这里。'}
                </p>
                <Link to={`${basePath}/check`} className="text-link">
                  去核对 <ArrowRight size={12} />
                </Link>
              </div>
            )}
          </section>

          <section className="panel side-panel">
            <div className="panel-head">
              <div>
                <div className="eyebrow">项目</div>
                <h3>项目资料</h3>
              </div>
            </div>
            <dl className="mini-kv">
              <div>
                <dt>规则包</dt>
                <dd className="mono">{project.package}</dd>
              </div>
              <div>
                <dt>招标人</dt>
                <dd>{project.buyer || '未提取'}</dd>
              </div>
              <div>
                <dt>项目编号</dt>
                <dd className={project.number ? 'mono' : 'subtle'}>
                  {project.number || '未提取'}
                </dd>
              </div>
              <div>
                <dt>源文件</dt>
                <dd>{sources.length} 份</dd>
              </div>
            </dl>
          </section>
        </aside>
      </div>

      {steps.some((step) => !step.done) && (
        <div className="overview-footnote">
          <Sparkles size={15} aria-hidden />
          <span>
            {stats.missing_required > 0 ? (
              <>
                <CircleAlert size={13} /> 必填事实缺失时，生成接口会返回 409 并阻止生成。
              </>
            ) : (
              '所有内容都基于本机事实台账生成；没有依据的信息会保留 {{待填:...}} 占位。'
            )}
          </span>
        </div>
      )}
    </div>
  )
}

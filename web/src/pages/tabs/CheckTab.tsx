import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Filter,
  Info,
  MapPin,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  X,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import { getIssues, runCheck } from '../../api/compliance'
import { ApiError, messageOf } from '../../lib/http'
import type { CheckResult, Issue } from '../../lib/types'
import {
  className,
  evidenceText,
  formatRelative,
  issueLevel,
  issueStatus,
  issueTitle,
  verdictMeta,
} from '../../lib/format'
import { useLocalStorageState, useQuery } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, Skeleton } from '../../components/Feedback'
import { StatTile, TabHeading } from '../../components/Workbench'
import { useToast } from '../../context/useToast'

const LEVEL_ORDER = ['blocking', 'critical', 'major', 'minor', 'info'] as const

interface CheckRecord {
  verdict: string
  issues: number
  at: string
}

export function CheckTab() {
  const { ref, basePath, project, reloadProject, canWrite } = useWorkspace()
  const toast = useToast()
  const navigate = useNavigate()
  const issuesQuery = useQuery(`issues:${ref}`, () => getIssues(ref))
  const [result, setResult] = useState<CheckResult | null>(null)
  const [checking, setChecking] = useState(false)
  const [levelFilter, setLevelFilter] = useState<string>('all')
  const [categoryFilter, setCategoryFilter] = useState<string>('all')
  const [search, setSearch] = useState('')
  const [checkRecords, setCheckRecords] = useLocalStorageState<Record<string, CheckRecord>>(
    'bidcraft:check-results',
    {},
  )
  const [, setCheckTimes] = useLocalStorageState<Record<string, string>>(
    'bidcraft:check-times',
    {},
  )
  const localRecord = checkRecords[ref]

  const persistedIssues = useMemo(() => issuesQuery.data || [], [issuesQuery.data])
  const failedIssues = useMemo(
    () => (result?.issues ?? persistedIssues).filter((issue) => !issue.passed),
    [result?.issues, persistedIssues],
  )
  const issues = failedIssues
  const ruleCount = result?.summary?.total ?? project.checks_total ?? failedIssues.length
  const derivedVerdict = useMemo<string | null>(() => {
    if (result?.verdict) return result.verdict
    const unresolved = persistedIssues.filter(
      (issue) =>
        !issue.passed &&
        !['resolved', 'closed', 'ignored'].includes(String(issue.status || '').toLowerCase()),
    )
    if (unresolved.length > 0) {
      if (unresolved.some((issue) => issue.level === 'blocking')) return 'blocked'
      // Any other failed finding (critical / major / minor / info) must not
      // produce a false "pass" banner. This mirrors the backend verdict.
      return 'warn'
    }
    if (persistedIssues.length > 0) return 'pass'
    return localRecord?.verdict || null
  }, [result?.verdict, persistedIssues, localRecord?.verdict])
  const verdict = derivedVerdict ? verdictMeta(derivedVerdict) : null
  const checkedAt = result ? new Date().toISOString() : localRecord?.at
  const verdictSource = result
    ? '本次核对'
    : persistedIssues.length > 0
      ? '已保存的核对问题'
      : localRecord
        ? '本机核对记录'
        : null

  const levelCounts = useMemo(() => {
    const counts: Record<string, number> = { all: failedIssues.length }
    LEVEL_ORDER.forEach((level) => {
      counts[level] = failedIssues.filter((issue) => issue.level === level).length
    })
    return counts
  }, [failedIssues])

  const categories = useMemo(() => {
    return Array.from(
      new Set(failedIssues.map((issue) => issue.category).filter(Boolean) as string[]),
    ).sort((left, right) => left.localeCompare(right, 'zh-CN'))
  }, [failedIssues])

  useEffect(() => {
    if (levelFilter !== 'all' && (levelCounts[levelFilter] || 0) === 0) {
      setLevelFilter('all')
    }
    if (categoryFilter !== 'all' && !categories.includes(categoryFilter)) {
      setCategoryFilter('all')
    }
  }, [levelFilter, levelCounts, categoryFilter, categories])

  const visibleIssues = useMemo(() => {
    const query = search.trim().toLowerCase()
    return issues.filter((issue) => {
      if (levelFilter !== 'all' && issue.level !== levelFilter) return false
      if (categoryFilter !== 'all' && issue.category !== categoryFilter) return false
      if (!query) return true
      return [
        issue.code,
        issue.finding,
        issue.message,
        issue.location,
        issue.recommendation,
        issue.category,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query))
    })
  }, [issues, levelFilter, categoryFilter, search])

  const filtersActive =
    levelFilter !== 'all' || categoryFilter !== 'all' || Boolean(search)

  const recheck = async () => {
    if (!canWrite || checking) return
    setChecking(true)
    try {
      const response = await runCheck(ref)
      const at = new Date().toISOString()
      setResult(response)
      issuesQuery.setData(response.issues)
      setCheckRecords((current) => ({
        ...current,
        [ref]: {
          verdict: response.verdict,
          issues: response.issues.filter((issue) => !issue.passed).length,
          at,
        },
      }))
      setCheckTimes((current) => ({ ...current, [ref]: at }))
      setLevelFilter('all')
      setCategoryFilter('all')
      setSearch('')
      await reloadProject()
      const meta = verdictMeta(response.verdict)
      const count = response.issues.filter((issue) => !issue.passed).length
      if (response.verdict === 'pass') {
        toast.success('核对通过', count ? `有 ${count} 条提示项，见下方明细。` : '未发现问题。')
      } else if (response.verdict === 'blocked') {
        toast.error('存在阻断项', `共 ${count} 条问题，请先处理阻断项再导出。`)
      } else {
        toast.warning('核对完成，需要关注', `共 ${count} 条问题。${meta.description}`)
      }
      issuesQuery.refresh()
    } catch (error) {
      if (error instanceof ApiError && error.missingRequired.length > 0) {
        toast.error('核对前请先补齐事实', error.message)
        navigate(`${basePath}/ledger`, {
          state: { highlight: error.missingRequired },
        })
      } else {
        toast.error('核对失败', messageOf(error))
      }
    } finally {
      setChecking(false)
    }
  }

  const clearFilters = () => {
    setLevelFilter('all')
    setCategoryFilter('all')
    setSearch('')
  }

  const checkDescription = result
    ? verdict?.description
    : persistedIssues.length > 0
      ? '以下为已保存的核对问题。重新核对可基于最新正文与台账更新结论。'
      : localRecord
        ? '这是本机记录的最近一次核对结论；问题明细可能已被后端更新或清理。'
        : '运行核对会检查实质性响应、数量一致、参数覆盖与废标风险。核对只提示，不会改写正文。'

  return (
    <div className="tab-content check-tab">
      <TabHeading
        eyebrow="合规核对"
        title="交付前核对"
        description="按阻断、严重、主要、提示分级检查。问题需要人工确认与修改，系统不会自动改写。"
        actions={
          <Button
            variant="primary"
            size="sm"
            icon={<RefreshCw size={14} className={checking ? 'spin' : undefined} />}
            loading={checking}
            onClick={() => void recheck()}
            disabled={!canWrite}
            title={!canWrite ? '只读成员不能运行核对' : undefined}
          >
            {verdict ? '重新核对' : '运行核对'}
          </Button>
        }
      />

      <section
        className={className(
          'verdict-banner',
          verdict ? `verdict-banner--${verdict.tone}` : 'verdict-banner--unknown',
        )}
      >
        <div className="verdict-seal" aria-hidden>
          {verdict ? (
            verdict.tone === 'success' ? (
              <ShieldCheck size={30} strokeWidth={1.6} />
            ) : verdict.tone === 'danger' ? (
              <ShieldAlert size={30} strokeWidth={1.6} />
            ) : (
              <AlertTriangle size={30} strokeWidth={1.6} />
            )
          ) : (
            <CircleAlert size={30} strokeWidth={1.6} />
          )}
        </div>
        <div className="verdict-copy">
          <div className="eyebrow">
            {verdictSource ? `核对结论 · ${verdictSource}` : '核对结论 · 尚未核对'}
          </div>
          <h3>{verdict ? verdict.label : '还没有核对结论'}</h3>
          <p>{checkDescription}</p>
          <div className="verdict-meta">
            {checkedAt && (
              <span title={checkedAt}>
                <RefreshCw size={12} /> {formatRelative(checkedAt)}
              </span>
            )}
            <span>
              <strong>{failedIssues.length}</strong> 项待确认
            </span>
            <span>
              阻断 {levelCounts.blocking || 0} · 严重 {levelCounts.critical || 0} · 主要{' '}
              {levelCounts.major || 0}
            </span>
          </div>
        </div>
      </section>

      {result?.verdict === 'pass' && failedIssues.length === 0 && (
        <div className="check-pass-note">
          <CheckCircle2 size={17} />
          <div>
            <strong>没有需要处理的问题</strong>
            <p>导出前建议再核对一次正文是否有最新修改。</p>
          </div>
        </div>
      )}

      {issuesQuery.error && !issuesQuery.data && !result ? (
        <div className="panel">
          <ErrorState message={issuesQuery.error} onRetry={issuesQuery.refresh} />
        </div>
      ) : (
        <>
          <div className="check-stats">
            <StatTile
              label="阻断"
              value={levelCounts.blocking || 0}
              detail="必须处理"
              tone={(levelCounts.blocking || 0) > 0 ? 'danger' : 'neutral'}
            />
            <StatTile
              label="严重 / 主要"
              value={`${levelCounts.critical || 0} / ${levelCounts.major || 0}`}
              detail="建议逐条确认"
              tone={
                (levelCounts.critical || 0) + (levelCounts.major || 0) > 0 ? 'warn' : 'neutral'
              }
            />
            <StatTile
              label="提示"
              value={levelCounts.minor || 0}
              detail="不影响交付"
              tone="info"
            />
            <StatTile
              label="检查规则"
              value={ruleCount}
              detail={
                result
                  ? '本次运行'
                  : project.checks_total !== undefined
                    ? '当前规则包'
                    : '已保存问题'
              }
              tone="neutral"
            />
          </div>

          <section className="panel check-panel">
            <div className="panel-head check-panel-head">
              <div>
                <div className="eyebrow">问题明细</div>
                <h3>问题明细</h3>
                <p className="panel-sub">
                  显示 {visibleIssues.length} 条 · 共 {failedIssues.length} 项待确认
                </p>
              </div>
            </div>

            <div className="check-toolbar">
              <div className="level-filters" role="group" aria-label="问题级别筛选">
                <button
                  type="button"
                  className={className('filter-chip', levelFilter === 'all' && 'is-active')}
                  onClick={() => setLevelFilter('all')}
                >
                  全部 <span className="chip-count">{levelCounts.all || 0}</span>
                </button>
                {LEVEL_ORDER.filter((level) => levelCounts[level] > 0).map((level) => {
                  const meta = issueLevel(level)
                  return (
                    <button
                      key={level}
                      type="button"
                      className={className(
                        'filter-chip',
                        `filter-chip--${meta.tone}`,
                        levelFilter === level && 'is-active',
                      )}
                      onClick={() => setLevelFilter(level)}
                    >
                      {meta.label} <span className="chip-count">{levelCounts[level]}</span>
                    </button>
                  )
                })}
              </div>
              <div className="check-toolbar-right">
                <label className="select-field select-field--compact">
                  <Filter size={14} aria-hidden />
                  <span className="sr-only">按类别筛选</span>
                  <select
                    value={categoryFilter}
                    onChange={(event) => setCategoryFilter(event.target.value)}
                  >
                    <option value="all">全部类别</option>
                    {categories.map((category) => (
                      <option key={category} value={category}>
                        {category}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="search-field">
                  <Search size={14} aria-hidden />
                  <span className="sr-only">搜索问题</span>
                  <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="搜索编号、位置或建议"
                  />
                  {search && (
                    <button
                      type="button"
                      className="search-clear"
                      onClick={() => setSearch('')}
                      aria-label="清除搜索"
                    >
                      <X size={12} />
                    </button>
                  )}
                </label>
                {filtersActive && (
                  <button type="button" className="text-btn" onClick={clearFilters}>
                    清除筛选
                  </button>
                )}
              </div>
            </div>

            {issuesQuery.loading && !issues.length && !result ? (
              <div className="issue-list-loading" aria-busy="true">
                {Array.from({ length: 3 }).map((_, index) => (
                  <div key={index} className="issue-skeleton">
                    <Skeleton width={54} height={18} radius={999} />
                    <Skeleton width="56%" height={17} />
                    <Skeleton width="88%" height={12} />
                  </div>
                ))}
              </div>
            ) : visibleIssues.length === 0 ? (
              <EmptyState
                icon={verdict?.tone === 'success' ? CheckCircle2 : Filter}
                compact
                title={
                  issues.length === 0
                    ? verdict
                      ? '没有需要处理的问题'
                      : '还没有核对结果'
                    : '没有匹配的问题'
                }
                description={
                  issues.length === 0
                    ? verdict
                      ? '当前结论来自最近一次核对。正文修改后请重新核对。'
                      : '运行核对后，阻断、严重、主要与提示项会显示在这里。'
                    : '调整级别、类别或搜索条件后再试。'
                }
                actions={
                  issues.length === 0 && !verdict ? (
                    <Button
                      variant="primary"
                      size="sm"
                      loading={checking}
                      onClick={() => void recheck()}
                      disabled={!canWrite}
                      title={!canWrite ? '只读成员不能运行核对' : undefined}
                    >
                      运行核对
                    </Button>
                  ) : issues.length > 0 ? (
                    <Button variant="secondary" size="sm" onClick={clearFilters}>
                      清除筛选
                    </Button>
                  ) : undefined
                }
              />
            ) : (
              <div className="issue-list">
                {visibleIssues.map((issue, index) => (
                  <IssueCard key={issue.id || `${issue.code}-${index}`} issue={issue} />
                ))}
              </div>
            )}

            <div className="check-footnote">
              <Info size={14} />
              <span>
                核对规则来自当前规则包。问题位置以章节名或正文片段表示；证据保留原始计算与取值，
                便于人工复核。
              </span>
            </div>
          </section>
        </>
      )}
    </div>
  )
}

function IssueCard({ issue }: { issue: Issue }) {
  const level = issueLevel(issue.level)
  const title = issueTitle(issue)
  const showMessage = Boolean(issue.finding && issue.message && issue.finding !== issue.message)
  const status = issueStatus(issue.status)
  const evidence = evidenceText(issue.evidence)
  return (
    <article className={className('issue-card', `issue-card--${level.tone}`)}>
      <span className="issue-rail" aria-hidden />
      <header className="issue-head">
        <div className="issue-tags">
          <span className="issue-code mono">{issue.code}</span>
          <Badge tone={level.tone} subtle>
            {level.label}
          </Badge>
          {issue.category && <Badge tone="neutral" subtle>{issue.category}</Badge>}
          {status && (
            <Badge tone={issue.status === 'open' ? 'warn' : 'success'} subtle>
              {status}
            </Badge>
          )}
        </div>
        {issue.location && (
          <span className="issue-location" title={issue.location}>
            <MapPin size={12} />
            {issue.location}
          </span>
        )}
      </header>
      <h4 className="issue-title">{title}</h4>
      {showMessage && <p className="issue-message">{issue.message}</p>}
      {issue.recommendation && (
        <div className="issue-recommendation">
          <span>修订建议</span>
          <p>{issue.recommendation}</p>
        </div>
      )}
      {evidence && (
        <details className="issue-evidence">
          <summary>
            <ChevronDown size={13} /> 查看证据
          </summary>
          <pre>{truncateEvidence(evidence)}</pre>
        </details>
      )}
    </article>
  )
}

function truncateEvidence(evidence: string): string {
  const max = 2400
  return evidence.length > max ? `${evidence.slice(0, max)}\n…` : evidence
}

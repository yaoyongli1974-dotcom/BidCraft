import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  CircleAlert,
  EyeOff,
  Filter,
  LocateFixed,
  MapPin,
  MapPinOff,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Star,
  X,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import {
  ApiError,
  bulkIssueStatus,
  getRequirements,
  getRiskOverview,
  messageOf,
  runCheck,
  updateIssueStatus,
  updateRequirementResponse,
} from '../../lib/api'
import type {
  Issue,
  RequirementResponse,
  RequirementResponseStatus,
  RiskOverview,
  RiskTierLevel,
} from '../../lib/types'
import {
  className,
  evidenceText,
  formatDate,
  formatRelative,
  issueLevel,
  issueResolved,
  issueStatusMeta,
  issueTitle,
  riskTier,
  verdictMeta,
} from '../../lib/format'
import { peekQueryEntry, storeQueryData } from '../../lib/queryCache'
import { useQuery } from '../../lib/hooks'
import {
  groupRiskItems,
  jumpQuote,
  filterRiskItems,
  TIER_NOTES,
  TIER_ORDER,
} from './riskModel'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, Skeleton } from '../../components/Feedback'
import { Modal } from '../../components/Modal'
import { TabHeading } from '../../components/Workbench'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'

/** 单条更新后同步 CheckTab 使用的 `issues` 缓存，避免两页状态分叉。 */
function syncIssuesCache(ref: string, updated: Issue[]) {
  if (!updated.length) return
  const key = `issues:${ref}`
  const entry = peekQueryEntry<Issue[]>(key)
  if (!entry?.data) return
  const byId = new Map(updated.filter((issue) => issue.id).map((issue) => [issue.id, issue]))
  storeQueryData(
    key,
    entry.data.map((issue) => (issue.id ? byId.get(issue.id) ?? issue : issue)),
  )
}

function truncateEvidence(evidence: string): string {
  const max = 2400
  return evidence.length > max ? `${evidence.slice(0, max)}\n…` : evidence
}

type PendingAction = 'handled' | 'ignored' | 'open'

export function RiskTab() {
  const { ref, basePath, reloadProject, canWrite } = useWorkspace()
  const toast = useToast()
  const confirm = useConfirm()
  const navigate = useNavigate()
  const overviewQuery = useQuery(`risk-overview:${ref}`, () => getRiskOverview(ref))
  const overview: RiskOverview | undefined = overviewQuery.data

  const [tierFilter, setTierFilter] = useState<RiskTierLevel | 'all'>('all')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [onlyOpen, setOnlyOpen] = useState(false)
  const [collapsed, setCollapsed] = useState<Partial<Record<RiskTierLevel, boolean>>>({})
  const [pending, setPending] = useState<{ id: string; action: PendingAction } | null>(null)
  const [checking, setChecking] = useState(false)
  const [bulkRunning, setBulkRunning] = useState(false)
  const [ignoreTarget, setIgnoreTarget] = useState<Issue | null>(null)
  const [ignoreNote, setIgnoreNote] = useState('')
  const [ignoreError, setIgnoreError] = useState('')
  const [ignoring, setIgnoring] = useState(false)
  const initializedRef = useRef<string | null>(null)

  const items = useMemo(() => overview?.items || [], [overview?.items])

  const tierCards = useMemo(() => {
    const server = overview?.tiers || []
    const computed = groupRiskItems(items)
    return TIER_ORDER.map((tier) => {
      const fromServer = server.find((entry) => entry.level === tier)
      const group = computed.find((entry) => entry.tier === tier)
      const open = fromServer ? fromServer.open : group?.open ?? 0
      const total = fromServer ? fromServer.total : group?.items.length ?? 0
      const handled = fromServer
        ? fromServer.handled + fromServer.ignored
        : group?.handled ?? 0
      return { tier, open, total, handled, ...riskTier(tier) }
    })
  }, [overview?.tiers, items])

  const openCount = useMemo(
    () => items.filter((issue) => !issueResolved(issue.status)).length,
    [items],
  )

  const categories = useMemo(() => {
    const fromServer = (overview?.by_category || []).filter((entry) => entry.category)
    if (fromServer.length) {
      return fromServer.map((entry) => ({
        value: entry.category,
        label: entry.open > 0 ? `${entry.category} · ${entry.open} 未处置` : entry.category,
      }))
    }
    return Array.from(
      new Set(items.map((issue) => issue.category).filter(Boolean) as string[]),
    )
      .sort((left, right) => left.localeCompare(right, 'zh-CN'))
      .map((category) => ({ value: category, label: category }))
  }, [overview?.by_category, items])

  const visibleItems = useMemo(
    () =>
      filterRiskItems(items, {
        tier: tierFilter,
        category: categoryFilter,
        query: search,
        onlyOpen,
      }),
    [items, tierFilter, categoryFilter, search, onlyOpen],
  )

  const groups = useMemo(
    () => groupRiskItems(visibleItems).filter((group) => group.items.length > 0),
    [visibleItems],
  )

  const filtersActive =
    tierFilter !== 'all' || categoryFilter !== 'all' || Boolean(search) || onlyOpen

  const bulkIds = useMemo(
    () =>
      visibleItems
        .filter((issue) => !issueResolved(issue.status) && issue.id)
        .map((issue) => issue.id as string),
    [visibleItems],
  )
  const canBulk = canWrite && bulkIds.length > 1 && !bulkRunning

  const checked = Boolean(overview?.checked_at)
  const verdict = overview?.verdict ? verdictMeta(overview.verdict) : null
  const verdictKnown = Boolean(
    overview && ['blocked', 'warn', 'pass'].includes(String(overview.verdict).toLowerCase()),
  )
  const gateBlocked = Boolean(overview?.gate?.blocked)

  // 数据首次到达时给阻断/严重档默认展开，其余折叠；筛选切到某一档时展开该档。
  useEffect(() => {
    if (!overview || initializedRef.current === `${ref}:${overview.checked_at || 'none'}`) return
    initializedRef.current = `${ref}:${overview.checked_at || 'none'}`
    setCollapsed({ major: true, minor: true })
  }, [overview, ref])

  useEffect(() => {
    if (tierFilter === 'all') return
    setCollapsed((current) => ({ ...current, [tierFilter]: false }))
  }, [tierFilter])

  useEffect(() => {
    if (categoryFilter === 'all') return
    if (!categories.some((entry) => entry.value === categoryFilter)) setCategoryFilter('all')
  }, [categories, categoryFilter])

  const applyIssueUpdates = (updated: Issue[]) => {
    if (!updated.length) return
    const byId = new Map(updated.filter((issue) => issue.id).map((issue) => [issue.id, issue]))
    overviewQuery.setData((current) => {
      if (!current) return current
      return {
        ...current,
        items: current.items.map((issue) => (issue.id ? byId.get(issue.id) ?? issue : issue)),
      }
    })
    syncIssuesCache(ref, updated)
  }

  const recheck = async () => {
    if (!canWrite || checking) return
    setChecking(true)
    try {
      const response = await runCheck(ref)
      await reloadProject()
      // 核对会产生一整套新问题，直接覆盖明细页缓存，避免两页数据分叉。
      storeQueryData(`issues:${ref}`, response.issues)
      overviewQuery.refresh()
      const failed = response.issues.filter((issue) => !issue.passed).length
      if (response.verdict === 'pass') {
        toast.success('核对通过', failed ? `有 ${failed} 条提示项，见下方明细。` : '未发现问题。')
      } else if (response.verdict === 'blocked') {
        toast.error('存在废标/阻断风险', `共 ${failed} 条问题，请先处置阻断项再导出。`)
      } else {
        toast.warning('核对完成，需要关注', `共 ${failed} 条问题，建议逐条确认。`)
      }
    } catch (error) {
      if (error instanceof ApiError && error.missingRequired.length > 0) {
        toast.error('核对前请先补齐事实', error.message)
        navigate(`${basePath}/ledger`, { state: { highlight: error.missingRequired } })
      } else {
        toast.error('核对失败', messageOf(error))
      }
    } finally {
      setChecking(false)
    }
  }

  const jumpToDraft = (issue: Issue) => {
    if (!issue.node_id) return
    navigate(`${basePath}/draft`, {
      state: { focusNode: issue.node_id, quote: jumpQuote(issue) },
    })
  }

  const markHandled = async (issue: Issue) => {
    if (!canWrite || !issue.id || pending) return
    setPending({ id: issue.id, action: 'handled' })
    try {
      const updated = await updateIssueStatus(ref, issue.id, 'handled')
      applyIssueUpdates([updated])
      overviewQuery.refresh()
      toast.success('已标记为已处理', issueTitle(issue))
    } catch (error) {
      toast.error('标记失败', messageOf(error))
    } finally {
      setPending(null)
    }
  }

  const restoreOpen = async (issue: Issue) => {
    if (!canWrite || !issue.id || pending) return
    setPending({ id: issue.id, action: 'open' })
    try {
      const updated = await updateIssueStatus(ref, issue.id, 'open')
      applyIssueUpdates([updated])
      overviewQuery.refresh()
      toast.info('已恢复为待处理', issueTitle(issue))
    } catch (error) {
      toast.error('恢复失败', messageOf(error))
    } finally {
      setPending(null)
    }
  }

  const openIgnore = (issue: Issue) => {
    if (!canWrite || !issue.id) return
    setIgnoreTarget(issue)
    setIgnoreNote('')
    setIgnoreError('')
  }

  const submitIgnore = async () => {
    if (!ignoreTarget?.id || ignoring) return
    const note = ignoreNote.trim()
    if (!note) {
      setIgnoreError('忽略必须填写原因，便于后续复核为何跳过这条风险。')
      return
    }
    setIgnoring(true)
    try {
      const updated = await updateIssueStatus(ref, ignoreTarget.id, 'ignored', note)
      applyIssueUpdates([updated])
      overviewQuery.refresh()
      toast.success('已忽略该风险', note)
      setIgnoreTarget(null)
    } catch (error) {
      setIgnoreError(messageOf(error))
      toast.error('忽略失败', messageOf(error))
    } finally {
      setIgnoring(false)
    }
  }

  const runBulkHandled = async () => {
    if (!canBulk) return
    const accepted = await confirm({
      title: `批量标记 ${bulkIds.length} 项为已处理？`,
      description:
        '将把当前筛选出的全部未处置风险一次性标记为已处理。此操作可逐条恢复，但请确认相关修改确已完成。',
      confirmLabel: `标记 ${bulkIds.length} 项`,
      tone: 'primary',
    })
    if (!accepted) return
    setBulkRunning(true)
    try {
      const result = await bulkIssueStatus(ref, bulkIds, 'handled')
      applyIssueUpdates(result.items)
      overviewQuery.refresh()
      toast.success('批量处置完成', `已标记 ${result.updated} 项风险为已处理。`)
    } catch (error) {
      toast.error('批量处置失败', messageOf(error))
    } finally {
      setBulkRunning(false)
    }
  }

  const toggleTier = (tier: RiskTierLevel) => {
    setTierFilter((current) => (current === tier ? 'all' : tier))
  }

  const toggleGroup = (tier: RiskTierLevel) => {
    setCollapsed((current) => ({
      ...current,
      [tier]: !(current[tier] ?? (tier === 'major' || tier === 'minor')),
    }))
  }

  const clearFilters = () => {
    setTierFilter('all')
    setCategoryFilter('all')
    setSearch('')
    setOnlyOpen(false)
  }

  const checkpoint = (value?: string | null) =>
    value ? `上次核对 ${formatRelative(value)}` : '尚未核对'

  const bannerDescription = gateBlocked
    ? overview?.gate?.reason ||
      '存在未处置的废标/阻断风险，导出已被门禁拦截。请先逐条处置或跳转正文修改。'
    : verdict?.description || '运行核对后，风险会按四档聚合到这里。'

  if (overviewQuery.error && !overview) {
    return (
      <div className="tab-content risk-tab">
        <TabHeading
          eyebrow="交付门禁"
          title="交付前风险总览"
          description="按废标/阻断、严重、主要、提示四档聚合核对风险，处置完成后才能导出。"
        />
        <div className="panel">
          <ErrorState message={overviewQuery.error} onRetry={overviewQuery.refresh} />
        </div>
      </div>
    )
  }

  return (
    <div className="tab-content risk-tab">
      <TabHeading
        eyebrow="交付门禁"
        title="交付前风险总览"
        description="按废标/阻断、严重、主要、提示四档聚合全部核对风险，逐条处置或跳回正文修改；全部阻断项处置完成后才能导出。"
        actions={
          <Button
            variant="primary"
            size="sm"
            icon={<RefreshCw size={14} className={checking ? 'spin' : undefined} />}
            loading={checking}
            disabled={!canWrite}
            title={!canWrite ? '只读成员不能运行核对' : '基于最新正文与台账重新核对'}
            onClick={() => void recheck()}
          >
            {checked ? '重新核对' : '运行核对'}
          </Button>
        }
      />

      {overviewQuery.loading && !overview ? (
        <div className="risk-loading" aria-busy="true">
          <Skeleton width="100%" height={108} radius={10} />
          <div className="risk-loading-tiers">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} width="100%" height={92} radius={9} />
            ))}
          </div>
          <Skeleton width="100%" height={220} radius={10} />
        </div>
      ) : (
        <>
          <section
            className={className(
              'verdict-banner risk-verdict',
              verdictKnown
                ? `verdict-banner--${verdict?.tone}`
                : 'verdict-banner--unknown',
            )}
          >
            <div className="verdict-seal" aria-hidden>
              {!verdictKnown ? (
                <CircleAlert size={30} strokeWidth={1.6} />
              ) : verdict?.tone === 'success' ? (
                <ShieldCheck size={30} strokeWidth={1.6} />
              ) : verdict?.tone === 'danger' ? (
                <ShieldAlert size={30} strokeWidth={1.6} />
              ) : (
                <AlertTriangle size={30} strokeWidth={1.6} />
              )}
            </div>
            <div className="verdict-copy">
              <div className="eyebrow">
                {verdictKnown ? `交付结论 · ${checkpoint(overview?.checked_at)}` : '交付结论 · 尚未核对'}
              </div>
              <h3>{verdictKnown ? verdict?.label : '还没有核对结论'}</h3>
              <p>{bannerDescription}</p>
              <div className="verdict-meta">
                {overview?.checked_at && (
                  <span title={formatDate(overview.checked_at)}>
                    <RefreshCw size={12} /> {formatRelative(overview.checked_at)}
                  </span>
                )}
                <span>
                  <strong>{openCount}</strong> 项未处置
                </span>
                <span>
                  阻断 {tierCards[0].open} · 严重 {tierCards[1].open} · 主要{' '}
                  {tierCards[2].open} · 提示 {tierCards[3].open}
                </span>
                {gateBlocked && (
                  <span className="risk-verdict-gate">
                    <ShieldAlert size={12} /> 导出门禁已拦截
                  </span>
                )}
              </div>
              {!canWrite && (
                <p className="risk-viewonly-hint">
                  你是只读成员：可以查看风险与跳转正文，但不能标记处置、忽略或批量操作。
                </p>
              )}
            </div>
          </section>

          <div className="risk-tiers" role="group" aria-label="按风险档筛选">
            {tierCards.map((card) => {
              const alert = card.tier === 'blocking' && card.open > 0
              return (
                <button
                  key={card.tier}
                  type="button"
                  className={className(
                    'risk-tier',
                    `risk-tier--${card.tone}`,
                    alert && 'risk-tier--alert',
                    tierFilter === card.tier && 'is-active',
                  )}
                  aria-pressed={tierFilter === card.tier}
                  onClick={() => toggleTier(card.tier)}
                >
                  <span className="risk-tier-head">
                    <span className="risk-tier-label">{card.label}</span>
                    {alert && <span className="risk-tier-pulse" aria-hidden />}
                  </span>
                  <span className="risk-tier-figure">
                    <strong className="mono">{card.open}</strong>
                    <span>未处置</span>
                  </span>
                  <span className="risk-tier-detail">
                    共 {card.total} 项 · 已处置 {card.handled}
                  </span>
                  <span className="risk-tier-note">{TIER_NOTES[card.tier]}</span>
                </button>
              )
            })}
          </div>

          <section className="panel risk-panel">
            <div className="panel-head check-panel-head">
              <div>
                <div className="eyebrow">风险清单</div>
                <h3>按档分组处置</h3>
                <p className="panel-sub">
                  显示 {visibleItems.length} 条 · 共 {openCount} 项未处置
                  {gateBlocked && ` · ${overview?.gate.blocking_open ?? 0} 项阻断未处置`}
                </p>
              </div>
              {canBulk && (
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<Check size={14} />}
                  loading={bulkRunning}
                  onClick={() => void runBulkHandled()}
                >
                  批量标记已处理（{bulkIds.length}）
                </Button>
              )}
            </div>

            <div className="check-toolbar risk-toolbar">
              <div className="level-filters" role="group" aria-label="风险档筛选">
                <button
                  type="button"
                  className={className('filter-chip', tierFilter === 'all' && 'is-active')}
                  onClick={() => setTierFilter('all')}
                >
                  全部 <span className="chip-count">{items.length}</span>
                </button>
                {tierCards
                  .filter((card) => card.total > 0)
                  .map((card) => (
                    <button
                      key={card.tier}
                      type="button"
                      className={className(
                        'filter-chip',
                        `filter-chip--${card.tone}`,
                        tierFilter === card.tier && 'is-active',
                      )}
                      onClick={() => setTierFilter(card.tier)}
                    >
                      {card.label} <span className="chip-count">{card.open}</span>
                    </button>
                  ))}
              </div>
              <div className="check-toolbar-right">
                {categories.length > 0 && (
                  <label className="select-field select-field--compact">
                    <Filter size={14} aria-hidden />
                    <span className="sr-only">按类别筛选</span>
                    <select
                      value={categoryFilter}
                      onChange={(event) => setCategoryFilter(event.target.value)}
                    >
                      <option value="all">全部类别</option>
                      {categories.map((category) => (
                        <option key={category.value} value={category.value}>
                          {category.label}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <button
                  type="button"
                  className={className('filter-chip risk-open-toggle', onlyOpen && 'is-active')}
                  aria-pressed={onlyOpen}
                  onClick={() => setOnlyOpen((value) => !value)}
                >
                  只看未处置
                </button>
                <label className="search-field">
                  <Search size={14} aria-hidden />
                  <span className="sr-only">搜索风险</span>
                  <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="搜索编号、章节或建议"
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

            {items.length === 0 ? (
              checked ? (
                <EmptyState
                  icon={ShieldCheck}
                  title="当前没有需要处理的风险，可以交付"
                  description="最近一次核对未发现废标、阻断、严重或主要问题。导出前如正文又有修改，建议再核对一次。"
                  actions={
                    <Button
                      variant="secondary"
                      size="sm"
                      icon={<RefreshCw size={13} />}
                      loading={checking}
                      disabled={!canWrite}
                      onClick={() => void recheck()}
                    >
                      再核对一次
                    </Button>
                  }
                />
              ) : (
                <EmptyState
                  icon={ShieldAlert}
                  title="还没有风险数据"
                  description="先运行一次核对。废标、阻断、严重、主要与提示风险会按四档聚合到这里，并可逐条跳转正文处置。"
                  actions={
                    <Button
                      variant="primary"
                      size="sm"
                      icon={<RefreshCw size={13} />}
                      loading={checking}
                      disabled={!canWrite}
                      title={!canWrite ? '只读成员不能运行核对' : undefined}
                      onClick={() => void recheck()}
                    >
                      运行核对
                    </Button>
                  }
                />
              )
            ) : groups.length === 0 ? (
              <EmptyState
                icon={Filter}
                compact
                title="没有匹配的风险"
                description="调整档位、类别或搜索条件，或关闭「只看未处置」后再试。"
                actions={
                  <Button variant="secondary" size="sm" onClick={clearFilters}>
                    清除筛选
                  </Button>
                }
              />
            ) : (
              <div className="risk-groups">
                {groups.map((group) => {
                  const tierMeta = riskTier(group.tier)
                  const isCollapsed = collapsed[group.tier] ?? (group.tier === 'major' || group.tier === 'minor')
                  return (
                    <section
                      key={group.tier}
                      className={className('risk-group', `risk-group--${tierMeta.tone}`)}
                    >
                      <button
                        type="button"
                        className="risk-group-head"
                        aria-expanded={!isCollapsed}
                        onClick={() => toggleGroup(group.tier)}
                      >
                        <ChevronDown
                          size={14}
                          className={className('risk-group-chevron', isCollapsed && 'is-collapsed')}
                        />
                        <span className="risk-group-label">{tierMeta.label}</span>
                        <Badge tone={group.open > 0 ? tierMeta.tone : 'neutral'} subtle>
                          {group.open} 待处置
                        </Badge>
                        <span className="risk-group-count">{group.items.length} 项</span>
                      </button>
                      {!isCollapsed && (
                        <div className="risk-group-body">
                          {group.items.map((issue, index) => (
                            <RiskItemCard
                              key={issue.id || `${issue.code}-${index}`}
                              issue={issue}
                              canWrite={canWrite}
                              pending={
                                pending && pending.id === issue.id ? pending.action : null
                              }
                              onJump={() => jumpToDraft(issue)}
                              onHandled={() => void markHandled(issue)}
                              onIgnore={() => openIgnore(issue)}
                              onRestore={() => void restoreOpen(issue)}
                            />
                          ))}
                        </div>
                      )}
                    </section>
                  )
                })}
              </div>
            )}

            <div className="check-footnote">
              <AlertTriangle size={14} />
              <span>
                处置只记录状态，不会改写正文。跳转修改后回到这里重新核对，结论会依据最新正文更新。
                导出门禁只统计未处置的废标/阻断风险；xlsx 台账导出不受门禁影响。
              </span>
            </div>
          </section>

          <SubstantiveSection />
        </>
      )}

      <Modal
        open={Boolean(ignoreTarget)}
        onClose={() => {
          if (!ignoring) setIgnoreTarget(null)
        }}
        size="sm"
        eyebrow="忽略风险"
        title="忽略这条风险"
        footer={
          <>
            <Button variant="ghost" onClick={() => setIgnoreTarget(null)} disabled={ignoring}>
              取消
            </Button>
            <Button
              variant="danger"
              loading={ignoring}
              disabled={!ignoreNote.trim()}
              onClick={() => void submitIgnore()}
            >
              确认忽略
            </Button>
          </>
        }
      >
        <div className="risk-ignore-body">
          <p>
            忽略后该项不再计入未处置数量，也不会拦截导出，但仍保留在清单中，可随时恢复为待处理。
          </p>
          {ignoreTarget && (
            <div className="risk-ignore-target">
              <span className="issue-code mono">{ignoreTarget.code}</span>
              <span>{issueTitle(ignoreTarget)}</span>
            </div>
          )}
          <label className="risk-ignore-field">
            <span>
              忽略原因 <em>必填</em>
            </span>
            <textarea
              className="textarea"
              rows={3}
              value={ignoreNote}
              placeholder="例如：该风险为模板固定表述，已在投标函中另行承诺。"
              onChange={(event) => {
                setIgnoreNote(event.target.value)
                if (ignoreError) setIgnoreError('')
              }}
              aria-label="忽略原因"
            />
          </label>
          {ignoreError && (
            <p className="field-error" role="alert">
              {ignoreError}
            </p>
          )}
        </div>
      </Modal>
    </div>
  )
}

type SubstantiveFilter = 'unanswered' | 'covered' | 'responded' | 'na' | 'all'

const SUBSTANTIVE_FILTERS: { id: SubstantiveFilter; label: string }[] = [
  { id: 'unanswered', label: '未响应' },
  { id: 'covered', label: '正文已覆盖' },
  { id: 'responded', label: '已响应' },
  { id: 'na', label: '不适用' },
  { id: 'all', label: '全部' },
]

const SUBSTANTIVE_STATUS: Record<
  string,
  { label: string; tone: 'danger' | 'warn' | 'success' | 'neutral' }
> = {
  pending: { label: '待确认', tone: 'warn' },
  responded: { label: '已响应', tone: 'success' },
  na: { label: '不适用', tone: 'neutral' },
}

function requirementMatches(item: RequirementResponse, filter: SubstantiveFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'unanswered') return item.response_status === 'pending' && !item.auto_covered
  if (filter === 'covered') return item.response_status === 'pending' && item.auto_covered
  return item.response_status === filter
}

/**
 * 实质性条款响应：把「评分办法里标了 ★/▲、正文却没写」的条款摆到最前面。
 * 自动判定只做字符覆盖，人工可以标「已响应」或「不适用」并留痕。
 */
function SubstantiveSection() {
  const { ref, basePath, canWrite } = useWorkspace()
  const navigate = useNavigate()
  const toast = useToast()
  const query = useQuery(`requirements:${ref}`, () => getRequirements(ref))
  const items: RequirementResponse[] = useMemo(() => query.data || [], [query.data])

  const [filter, setFilter] = useState<SubstantiveFilter>('unanswered')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [busyId, setBusyId] = useState<string | null>(null)
  const [naTarget, setNaTarget] = useState<RequirementResponse | null>(null)
  const [naNote, setNaNote] = useState('')
  const [naError, setNaError] = useState('')
  const [naBusy, setNaBusy] = useState(false)

  const counts = useMemo(() => {
    let unanswered = 0
    let covered = 0
    let responded = 0
    let na = 0
    for (const item of items) {
      if (item.response_status === 'responded') responded += 1
      else if (item.response_status === 'na') na += 1
      else if (item.auto_covered) covered += 1
      else unanswered += 1
    }
    return { unanswered, covered, responded, na, total: items.length }
  }, [items])

  const starOpen = items.filter(
    (item) => item.marker === '★' && item.response_status === 'pending' && !item.auto_covered,
  ).length
  const visible = items.filter((item) => requirementMatches(item, filter))

  const applyUpdate = (updated: RequirementResponse) => {
    query.setData((current) => (current || []).map((row) => (row.id === updated.id ? updated : row)))
  }

  const confirmResponse = async (item: RequirementResponse, status: RequirementResponseStatus) => {
    if (!canWrite || busyId) return
    setBusyId(item.id)
    try {
      const updated = await updateRequirementResponse(ref, item.id, status)
      applyUpdate(updated)
      query.refresh()
      toast.success(
        status === 'responded' ? '已标记为已响应' : '已恢复为待确认',
        item.text.slice(0, 30),
      )
    } catch (error) {
      toast.error('更新失败', messageOf(error))
    } finally {
      setBusyId(null)
    }
  }

  const openNa = (item: RequirementResponse) => {
    if (!canWrite) return
    setNaTarget(item)
    setNaNote('')
    setNaError('')
  }

  const submitNa = async () => {
    if (!naTarget || naBusy) return
    const note = naNote.trim()
    if (note.length < 4) {
      setNaError('请说明为什么这一条不适用（至少 4 个字），便于复核与留痕。')
      return
    }
    setNaBusy(true)
    try {
      const updated = await updateRequirementResponse(ref, naTarget.id, 'na', note)
      applyUpdate(updated)
      query.refresh()
      toast.success('已标记为不适用', note)
      setNaTarget(null)
    } catch (error) {
      setNaError(messageOf(error))
      toast.error('标记失败', messageOf(error))
    } finally {
      setNaBusy(false)
    }
  }

  const jumpToSection = (item: RequirementResponse) => {
    if (!item.node_id) return
    navigate(`${basePath}/draft`, {
      state: { focusNode: item.node_id, quote: item.source_quote || item.text.slice(0, 40) },
    })
  }

  return (
    <section className="panel substantive-panel" aria-label="实质性条款响应">
      <div className="panel-head">
        <div>
          <div className="eyebrow">评分办法绑定</div>
          <h3>实质性条款响应</h3>
          <p className="panel-sub">
            {counts.total > 0
              ? `共 ${counts.total} 条实质性条款 · 未响应 ${counts.unanswered} · 正文已覆盖 ${counts.covered} · 已响应 ${counts.responded} · 不适用 ${counts.na}`
              : '招标文件里标了 ★/▲ 或写明「必须 / 不得」的条款，会在这里逐条对照正文。'}
          </p>
        </div>
        {starOpen > 0 && (
          <span className="substantive-alert" role="status">
            <ShieldAlert size={14} /> {starOpen} 条 ★ 条款未响应，导出会被拦截
          </span>
        )}
      </div>

      {query.loading && !query.data ? (
        <div className="substantive-skeleton" aria-busy="true">
          <Skeleton width="100%" height={64} radius={8} />
          <Skeleton width="100%" height={64} radius={8} />
        </div>
      ) : query.error && !query.data ? (
        <ErrorState message={query.error} onRetry={query.refresh} compact />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Star}
          compact
          title="没有解析出实质性条款"
          description="解析招标文件后，标了 ★/▲ 或写明「必须 / 不得」的条款会出现在这里。若确有此类条款却为空，请确认招标文件是否解析完整。"
        />
      ) : (
        <>
          <div className="level-filters substantive-filters" role="group" aria-label="按响应状态筛选">
            {SUBSTANTIVE_FILTERS.map((entry) => {
              const count =
                entry.id === 'all'
                  ? counts.total
                  : entry.id === 'unanswered'
                    ? counts.unanswered
                    : entry.id === 'covered'
                      ? counts.covered
                      : entry.id === 'responded'
                        ? counts.responded
                        : counts.na
              return (
                <button
                  key={entry.id}
                  type="button"
                  className={className('filter-chip', filter === entry.id && 'is-active')}
                  aria-pressed={filter === entry.id}
                  onClick={() => setFilter(entry.id)}
                >
                  {entry.label} <span className="chip-count">{count}</span>
                </button>
              )
            })}
          </div>

          {visible.length === 0 ? (
            <EmptyState
              icon={Check}
              compact
              title={filter === 'unanswered' ? '所有实质性条款都有下落' : '这一档暂时是空的'}
              description={
                filter === 'unanswered'
                  ? '未响应的条款已全部人工确认或已在正文覆盖。导出前如正文又有修改，建议重新核对一次。'
                  : '切换上方状态筛选查看其它条款。'
              }
            />
          ) : (
            <div className="substantive-list">
              {visible.map((item) => {
                const statusMeta =
                  SUBSTANTIVE_STATUS[item.response_status] || SUBSTANTIVE_STATUS.pending
                const unresponded = item.response_status === 'pending' && !item.auto_covered
                const isExpanded = Boolean(expanded[item.id])
                return (
                  <article
                    key={item.id}
                    className={className(
                      'substantive-item',
                      item.marker === '★' && 'substantive-item--star',
                      item.marker === '▲' && 'substantive-item--triangle',
                      unresponded && 'is-unanswered',
                    )}
                  >
                    <header className="substantive-head">
                      <span className="substantive-tags">
                        {item.marker ? (
                          <span
                            className={className(
                              'substantive-marker',
                              `substantive-marker--${item.marker === '★' ? 'star' : 'triangle'}`,
                            )}
                          >
                            {item.marker}
                          </span>
                        ) : (
                          <span className="substantive-marker substantive-marker--plain">条款</span>
                        )}
                        <Badge tone={statusMeta.tone} subtle>
                          {statusMeta.label}
                        </Badge>
                        {item.auto_covered && (
                          <Badge tone="success" subtle>
                            正文已覆盖
                          </Badge>
                        )}
                        {unresponded && (
                          <Badge tone={item.marker === '★' ? 'danger' : 'warn'} subtle>
                            {item.marker === '★' ? '最高优先级' : '未找到响应'}
                          </Badge>
                        )}
                      </span>
                    </header>

                    <p className={className('substantive-text', isExpanded && 'is-expanded')}>
                      {item.text}
                    </p>
                    {item.text.length > 64 && (
                      <button
                        type="button"
                        className="text-btn substantive-toggle"
                        onClick={() =>
                          setExpanded((current) => ({ ...current, [item.id]: !current[item.id] }))
                        }
                      >
                        {isExpanded ? '收起原文' : '展开原文'}
                      </button>
                    )}

                    {item.source_quote && item.source_quote !== item.text && (
                      <p className="substantive-quote">招标原文：{item.source_quote}</p>
                    )}

                    {item.auto_covered && item.section_heading && (
                      <p className="substantive-cover">
                        <MapPin size={12} /> 正文已覆盖于「{item.section_heading}」
                      </p>
                    )}

                    {item.response_status !== 'pending' && (
                      <p className="substantive-resolution">
                        <Check size={12} />
                        {statusMeta.label}
                        {item.responded_by ? ` · ${item.responded_by}` : ''}
                        {item.responded_at ? ` · ${formatRelative(item.responded_at)}` : ''}
                        {item.response_note ? ` · ${item.response_note}` : ''}
                      </p>
                    )}

                    <footer className="substantive-actions">
                      {item.node_id && (
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<LocateFixed size={13} />}
                          onClick={() => jumpToSection(item)}
                        >
                          查看章节
                        </Button>
                      )}
                      {item.response_status === 'pending' ? (
                        <>
                          <Button
                            size="sm"
                            variant="primary"
                            icon={<Check size={13} />}
                            loading={busyId === item.id}
                            disabled={!canWrite}
                            title={!canWrite ? '只读成员不能确认条款响应' : '确认正文已逐条响应'}
                            onClick={() => void confirmResponse(item, 'responded')}
                          >
                            标记已响应
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={!canWrite}
                            title={!canWrite ? '只读成员不能标记不适用' : '本项目不涉及该条款'}
                            onClick={() => openNa(item)}
                          >
                            标记不适用
                          </Button>
                        </>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<RotateCcw size={13} />}
                          loading={busyId === item.id}
                          disabled={!canWrite}
                          title={!canWrite ? '只读成员不能撤销确认' : '恢复为待确认'}
                          onClick={() => void confirmResponse(item, 'pending')}
                        >
                          撤销确认
                        </Button>
                      )}
                    </footer>
                  </article>
                )
              })}
            </div>
          )}
        </>
      )}

      <div className="check-footnote">
        <AlertTriangle size={14} />
        <span>
          标记为「已响应」只表示你确认正文已经写了，系统不会替你核对内容；★ 条款默认按未响应计入阻断并拦截导出，
          ▲ 与「必须 / 不得」条款按严重提示。撤销确认后会重新参与核对。
        </span>
      </div>

      <Modal
        open={Boolean(naTarget)}
        onClose={() => {
          if (!naBusy) setNaTarget(null)
        }}
        size="sm"
        eyebrow="标记不适用"
        title="这一条为什么用不上"
        footer={
          <>
            <Button variant="ghost" onClick={() => setNaTarget(null)} disabled={naBusy}>
              取消
            </Button>
            <Button
              variant="primary"
              loading={naBusy}
              disabled={!naNote.trim()}
              onClick={() => void submitNa()}
            >
              确认不适用
            </Button>
          </>
        }
      >
        <div className="risk-ignore-body">
          <p>
            标记为「不适用」后，该条款不再计入未响应数量，也不会拦截导出；原因会随确认记录保存，供后续复核。
          </p>
          {naTarget && (
            <div className="risk-ignore-target">
              <span className="issue-code mono">{naTarget.marker || '条款'}</span>
              <span>{naTarget.text.slice(0, 80)}</span>
            </div>
          )}
          <label className="risk-ignore-field">
            <span>
              不适用原因 <em>必填</em>
            </span>
            <textarea
              className="textarea"
              rows={3}
              value={naNote}
              placeholder="例如：本次为设备采购，不含施工内容，无需提供施工总承包资质。"
              onChange={(event) => {
                setNaNote(event.target.value)
                if (naError) setNaError('')
              }}
              aria-label="不适用原因"
            />
          </label>
          {naError && (
            <p className="field-error" role="alert">
              {naError}
            </p>
          )}
        </div>
      </Modal>
    </section>
  )
}

function RiskItemCard({
  issue,
  canWrite,
  pending,
  onJump,
  onHandled,
  onIgnore,
  onRestore,
}: {
  issue: Issue
  canWrite: boolean
  pending: PendingAction | null
  onJump: () => void
  onHandled: () => void
  onIgnore: () => void
  onRestore: () => void
}) {
  const tierMeta = riskTier(issue.level)
  const levelMeta = issueLevel(issue.level)
  const statusMeta = issueStatusMeta(issue.status)
  const resolved = issueResolved(issue.status)
  const title = issueTitle(issue)
  const showMessage = Boolean(issue.finding && issue.message && issue.finding !== issue.message)
  const evidence = evidenceText(issue.evidence)
  const anchor = issue.heading || issue.location
  const anchorable = Boolean(issue.node_id)
  const actionHint = !issue.id
    ? '该问题缺少编号，无法更新状态'
    : !canWrite
      ? '只读成员不能处置风险'
      : undefined

  return (
    <article
      className={className(
        'risk-item',
        `risk-item--${tierMeta.tone}`,
        resolved && 'is-handled',
      )}
    >
      <span className="issue-rail" aria-hidden />
      <header className="issue-head">
        <div className="issue-tags">
          <span className="issue-code mono">{issue.code}</span>
          <Badge tone={levelMeta.tone} subtle>
            {levelMeta.label}
          </Badge>
          {issue.category && (
            <Badge tone="neutral" subtle>
              {issue.category}
            </Badge>
          )}
          <Badge tone={statusMeta.tone} subtle>
            {statusMeta.label}
          </Badge>
        </div>
        {anchorable ? (
          <button
            type="button"
            className="risk-anchor"
            onClick={onJump}
            title={`跳转到正文「${anchor || issue.node_id}」`}
          >
            <MapPin size={12} />
            <span className="risk-anchor-text">{anchor || '跳转到所在章节'}</span>
            <ArrowRight size={11} />
          </button>
        ) : (
          <span
            className="risk-anchor risk-anchor--missing"
            title="该问题没有章节锚点，无法自动跳转，请根据位置描述手动查找"
          >
            <MapPinOff size={12} />
            <span className="risk-anchor-text">
              未定位到章节{anchor ? ` · ${anchor}` : ''}
            </span>
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
      {resolved && (
        <details className="risk-resolution">
          <summary>
            <Check size={12} /> 处置记录 · {statusMeta.label}
            {issue.resolved_by && ` · ${issue.resolved_by}`}
            {issue.resolved_at && ` · ${formatRelative(issue.resolved_at)}`}
          </summary>
          <p>
            {issue.resolution_note || '未填写处置原因。'}
            {issue.resolved_at && (
              <span className="risk-resolution-time">{formatDate(issue.resolved_at)}</span>
            )}
          </p>
        </details>
      )}
      <footer className="risk-actions">
        <Button
          size="sm"
          variant="ghost"
          icon={<LocateFixed size={13} />}
          disabled={!anchorable}
          title={!anchorable ? '该问题没有章节锚点，无法跳转' : '在正文中定位并高亮相关段落'}
          onClick={onJump}
        >
          跳转修改
        </Button>
        {resolved ? (
          <Button
            size="sm"
            variant="ghost"
            icon={<RotateCcw size={13} />}
            loading={pending === 'open'}
            disabled={!canWrite || !issue.id}
            title={actionHint}
            onClick={onRestore}
          >
            恢复待处理
          </Button>
        ) : (
          <>
            <Button
              size="sm"
              variant="primary"
              icon={<Check size={13} />}
              loading={pending === 'handled'}
              disabled={!canWrite || !issue.id}
              title={actionHint}
              onClick={onHandled}
            >
              标记已处理
            </Button>
            <Button
              size="sm"
              variant="ghost"
              icon={<EyeOff size={13} />}
              disabled={!canWrite || !issue.id}
              title={actionHint}
              onClick={onIgnore}
            >
              忽略
            </Button>
          </>
        )}
      </footer>
    </article>
  )
}

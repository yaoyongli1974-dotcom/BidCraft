import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  FileCheck2,
  Filter,
  PackageOpen,
  Pencil,
  RefreshCw,
  Ruler,
  Search,
  ShieldAlert,
  SlidersHorizontal,
  Sparkles,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import { getLedger, messageOf, updateFact } from '../../lib/api'
import type { Fact, Ledger } from '../../lib/types'
import {
  className,
  confidencePercent,
  confidenceText,
  factGroups,
  factStatus,
  formatNumber,
  requirementKind,
  riskLevel,
} from '../../lib/format'
import { useQuery, useUnsavedGuard } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { ScoreBackfill } from '../../components/ScoreBackfill'
import { Button } from '../../components/Button'
import { ErrorState, Skeleton } from '../../components/Feedback'
import {
  AutoTextarea,
  GateBanner,
  SourceQuote,
  TabHeading,
} from '../../components/Workbench'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'

type LedgerTab = 'facts' | 'materials' | 'parameters' | 'scoring' | 'requirements' | 'risks'

const FACT_STATUS_OPTIONS = [
  { value: 'confirmed', label: '已确认' },
  { value: 'manual', label: '手工录入' },
  { value: 'conflict', label: '取值冲突' },
  { value: 'missing', label: '缺失' },
]

interface EditState {
  key: string
  value: string
  status: string
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable
}

export function LedgerTab() {
  const { ref, project, reloadProject, setUnsaved, canWrite } = useWorkspace()
  const confirm = useConfirm()
  const toast = useToast()
  const navigate = useNavigate()
  const location = useLocation()
  const ledgerQuery = useQuery(`ledger:${ref}`, () => getLedger(ref))
  const [activeTab, setActiveTab] = useState<LedgerTab>('facts')
  const [search, setSearch] = useState('')
  const [onlyMissing, setOnlyMissing] = useState(false)
  const [onlyRequired, setOnlyRequired] = useState(false)
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({})
  const [highlight, setHighlight] = useState<Set<string>>(new Set())
  const [edit, setEdit] = useState<EditState | null>(null)
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [savedKey, setSavedKey] = useState<string | null>(null)
  const [rowError, setRowError] = useState<{ key: string; message: string } | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const rowRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const lastScrolledRef = useRef<string | null>(null)
  const ledger = ledgerQuery.data

  const hasOpenEdit = Boolean(edit)

  useEffect(() => {
    setUnsaved(hasOpenEdit)
    return () => setUnsaved(false)
  }, [hasOpenEdit, setUnsaved])

  useUnsavedGuard(hasOpenEdit, {
    title: '事实修改尚未保存',
    description: '当前字段已经进入编辑状态，尚未写回事实台账。离开会丢失这次输入。',
    confirmLabel: '放弃修改并离开',
  })

  const facts = useMemo(() => ledger?.facts || [], [ledger?.facts])
  const missingKeys = useMemo(
    () => facts.filter((fact) => fact.required && !fact.value).map((fact) => fact.key),
    [facts],
  )
  const missingCount = facts.length
    ? missingKeys.length
    : (project.missing_required?.length || 0)
  const missingList = useMemo(
    () => (missingKeys.length ? missingKeys : project.missing_required || []),
    [missingKeys, project.missing_required],
  )

  const stats = useMemo(() => {
    const filled = facts.filter((fact) => Boolean(fact.value)).length
    const required = facts.filter((fact) => fact.required).length
    const missing = facts.filter((fact) => fact.required && !fact.value).length
    return { filled, required, missing, total: facts.length }
  }, [facts])

  useEffect(() => {
    const state = location.state as { highlight?: string[] } | null
    if (!state?.highlight?.length) return
    const keys = state.highlight
    setActiveTab('facts')
    setSearch('')
    setOnlyRequired(true)
    setOnlyMissing(true)
    setHighlight(new Set(keys))
    setCollapsedGroups((current) => {
      const next = { ...current }
      keys.forEach((key) => {
        next[factGroups(key).id] = false
      })
      return next
    })
    navigate(location.pathname, { replace: true, state: null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key])

  useEffect(() => {
    if (activeTab !== 'facts' || !ledger) return
    const target = missingList.find((key) => highlight.has(key))
    if (!target || lastScrolledRef.current === target) return
    const element = rowRefs.current[target]
    if (element) {
      lastScrolledRef.current = target
      window.setTimeout(() => {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' })
        element.focus({ preventScroll: true })
      }, 120)
    }
  }, [activeTab, ledger, highlight, missingList])

  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (
        event.key === '/' &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.altKey &&
        !isEditableTarget(event.target)
      ) {
        event.preventDefault()
        searchRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  const filteredFacts = useMemo(() => {
    const query = search.trim().toLowerCase()
    return facts.filter((fact) => {
      if (onlyMissing && fact.value) return false
      if (onlyRequired && !fact.required) return false
      if (!query) return true
      return [fact.label, fact.key, fact.value, fact.unit, fact.source?.quote]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query))
    })
  }, [facts, search, onlyMissing, onlyRequired])

  const groupedFacts = useMemo(() => {
    const map = new Map<
      string,
      { id: string; label: string; order: number; facts: Fact[] }
    >()
    filteredFacts.forEach((fact) => {
      const group = factGroups(fact.key)
      const existing = map.get(group.id)
      if (existing) existing.facts.push(fact)
      else map.set(group.id, { ...group, facts: [fact] })
    })
    return Array.from(map.values()).sort((left, right) => left.order - right.order)
  }, [filteredFacts])

  const allGroupMissing = useMemo(() => {
    const map: Record<string, number> = {}
    facts.forEach((fact) => {
      if (fact.required && !fact.value) {
        const group = factGroups(fact.key)
        map[group.id] = (map[group.id] || 0) + 1
      }
    })
    return map
  }, [facts])

  const locateMissing = () => {
    const keys = missingList
    setActiveTab('facts')
    setSearch('')
    setOnlyRequired(true)
    setOnlyMissing(true)
    setHighlight(new Set(keys))
    setCollapsedGroups((current) => {
      const next = { ...current }
      keys.forEach((key) => {
        next[factGroups(key).id] = false
      })
      return next
    })
    lastScrolledRef.current = null
  }

  const changeTab = async (tab: LedgerTab): Promise<boolean> => {
    if (tab === activeTab) return true
    if (edit) {
      const accepted = await confirm({
        title: '放弃未保存的修改？',
        description: '切换分类会关闭当前编辑行，尚未保存的取值会丢失。',
        confirmLabel: '放弃并切换',
        tone: 'warning',
      })
      if (!accepted) return false
      setEdit(null)
      setRowError(null)
    }
    setActiveTab(tab)
    setSearch('')
    setOnlyMissing(false)
    setOnlyRequired(false)
    setSavedKey(null)
    setCollapsedGroups({})
    return true
  }

  const startEdit = async (fact: Fact) => {
    if (!canWrite) return
    if (edit && edit.key !== fact.key) {
      const accepted = await confirm({
        title: '切换编辑字段？',
        description: '当前字段的修改还没有保存，切换后会丢失这次输入。',
        confirmLabel: '放弃并切换',
        tone: 'warning',
      })
      if (!accepted) return
    }
    setEdit({
      key: fact.key,
      value: fact.value || '',
      status: fact.value
        ? fact.status === 'manual'
          ? 'manual'
          : fact.status === 'conflict'
            ? 'confirmed'
            : 'confirmed'
        : 'manual',
    })
    setRowError(null)
  }

  const saveFact = async (fact: Fact) => {
    if (!canWrite || !edit || savingKey) return
    const value = edit.value.trim()
    const status = value ? (edit.status === 'missing' ? 'confirmed' : edit.status) : 'missing'
    setSavingKey(fact.key)
    setRowError(null)
    try {
      const updated = await updateFact(ref, fact.key, value, status)
      ledgerQuery.setData((current) =>
        current
          ? {
              ...current,
              facts: current.facts.map((item) =>
                item.key === fact.key
                  ? { ...item, value: updated.value, status: updated.status }
                  : item,
              ),
            }
          : current,
      )
      setEdit(null)
      setSavedKey(fact.key)
      window.setTimeout(() => setSavedKey(null), 2400)
      const stale = updated.stale_sections || []
      if (stale.length > 0) {
        const headings = stale
          .map((item) => item.heading)
          .filter(Boolean)
          .slice(0, 3)
          .join('、')
        toast.warning(
          '已标记待复核的章节',
          `${stale.length} 个章节引用过旧值${
            headings ? `（${headings}${stale.length > 3 ? ' 等' : ''}）` : ''
          }，请按新值核对正文。`,
        )
      }
      setHighlight((current) => {
        const next = new Set(current)
        next.delete(fact.key)
        return next
      })
      try {
        await reloadProject()
      } catch {
        // The fact itself is saved; a failed stats refresh should not hide that.
      }
    } catch (error) {
      setRowError({ key: fact.key, message: messageOf(error) })
    } finally {
      setSavingKey(null)
    }
  }

  const retry = () => ledgerQuery.refresh()

  const tabItems: Array<{ id: LedgerTab; label: string; count: number; icon: LucideIcon }> = [
    { id: 'facts', label: '事实字段', count: facts.length, icon: ClipboardList },
    { id: 'materials', label: '材料清单', count: ledger?.materials.length || 0, icon: PackageOpen },
    { id: 'parameters', label: '技术参数', count: ledger?.parameters.length || 0, icon: Ruler },
    { id: 'scoring', label: '评分办法', count: ledger?.scoring.length || 0, icon: Sparkles },
    {
      id: 'requirements',
      label: '实质性要求',
      count: ledger?.requirements.length || 0,
      icon: FileCheck2,
    },
    { id: 'risks', label: '废标风险', count: ledger?.risks.length || 0, icon: ShieldAlert },
  ]

  // WAI-ARIA horizontal tabs: arrows wrap, Home/End jump to the edges.
  const onTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    const total = tabItems.length
    let nextIndex: number
    switch (event.key) {
      case 'ArrowRight':
        nextIndex = (index + 1) % total
        break
      case 'ArrowLeft':
        nextIndex = (index - 1 + total) % total
        break
      case 'Home':
        nextIndex = 0
        break
      case 'End':
        nextIndex = total - 1
        break
      default:
        return
    }
    event.preventDefault()
    const next = tabItems[nextIndex]
    if (!next) return
    void changeTab(next.id).then((changed) => {
      if (changed) tabRefs.current[next.id]?.focus()
    })
  }

  return (
    <div className="tab-content ledger-page">
      <TabHeading
        eyebrow="事实台账"
        title="先对齐事实，再写正文"
        description="所有生成内容只允许使用这里的事实。缺失必填项会阻断生成；没有来源的信息不会自动编造。"
        actions={
          <Button
            variant="ghost"
            size="sm"
            icon={<RefreshCw size={14} className={ledgerQuery.refreshing ? 'spin' : undefined} />}
            onClick={retry}
          >
            刷新台账
          </Button>
        }
      />

      {missingCount > 0 && (
        <GateBanner count={missingCount} keys={missingList} onLocate={locateMissing} />
      )}

      <section className="ledger-summary">
        <div className="ledger-summary-main">
          <div className="ledger-summary-title">
            <span className="eyebrow">字段覆盖</span>
            <strong>
              {stats.filled}
              <span className="muted"> / {stats.total} 项已填</span>
            </strong>
          </div>
          <div className="ledger-progress">
            <div className="ledger-progress-meta">
              <span>字段完成度</span>
              <span className="mono">
                {stats.total ? Math.round((stats.filled / stats.total) * 100) : 0}%
              </span>
            </div>
            <div className="ledger-progress-track" aria-hidden>
              <span style={{ width: `${stats.total ? (stats.filled / stats.total) * 100 : 0}%` }} />
            </div>
          </div>
        </div>
        <dl className="ledger-figures">
          <div>
            <dt>必填字段</dt>
            <dd>{stats.required}</dd>
          </div>
          <div className={stats.missing > 0 ? 'is-danger' : undefined}>
            <dt>缺失必填</dt>
            <dd>{stats.missing}</dd>
          </div>
          <div>
            <dt>材料 / 参数</dt>
            <dd>
              {ledger?.materials.length || 0}
              <span className="muted"> / {ledger?.parameters.length || 0}</span>
            </dd>
          </div>
          <div>
            <dt>评分 / 风险</dt>
            <dd>
              {ledger?.scoring.length || 0}
              <span className="muted"> / {ledger?.risks.length || 0}</span>
            </dd>
          </div>
        </dl>
      </section>

      <section className="ledger-card">
        <div className="ledger-tabs" role="tablist" aria-label="台账分类">
          {tabItems.map((tab, index) => (
            <button
              key={tab.id}
              id={`ledger-tab-${tab.id}`}
              ref={(element) => {
                tabRefs.current[tab.id] = element
              }}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              aria-controls="ledger-tabpanel"
              tabIndex={activeTab === tab.id ? 0 : -1}
              className={className('ledger-tab', activeTab === tab.id && 'is-active')}
              onClick={() => void changeTab(tab.id)}
              onKeyDown={(event) => onTabKeyDown(event, index)}
            >
              <tab.icon size={14} aria-hidden />
              <span>{tab.label}</span>
              <span className="ledger-tab-count">{tab.count}</span>
            </button>
          ))}
        </div>

        <div
          id="ledger-tabpanel"
          role="tabpanel"
          aria-labelledby={`ledger-tab-${activeTab}`}
        >
        <div className="ledger-toolbar">
          <label className="search-field search-field--grow">
            <Search size={15} aria-hidden />
            <span className="sr-only">搜索台账</span>
            <input
              ref={searchRef}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={
                activeTab === 'facts'
                  ? '搜索字段、取值、键名或原文（按 / 聚焦）'
                  : '搜索名称、规格、条款或原文'
              }
            />
            {search && (
              <button
                type="button"
                className="search-clear"
                onClick={() => setSearch('')}
                aria-label="清除搜索"
              >
                <X size={13} />
              </button>
            )}
          </label>
          {activeTab === 'facts' && (
            <div className="filter-toggles">
              <button
                type="button"
                className={className('filter-chip', onlyMissing && 'is-active')}
                aria-pressed={onlyMissing}
                onClick={() => setOnlyMissing((value) => !value)}
              >
                <Filter size={13} /> 仅缺失
              </button>
              <button
                type="button"
                className={className('filter-chip', onlyRequired && 'is-active')}
                aria-pressed={onlyRequired}
                onClick={() => setOnlyRequired((value) => !value)}
              >
                <SlidersHorizontal size={13} /> 仅必填
              </button>
            </div>
          )}
          <span className="toolbar-count">
            {activeTab === 'facts'
              ? `${filteredFacts.length} / ${facts.length} 项`
              : `共 ${tabItems.find((tab) => tab.id === activeTab)?.count || 0} 条`}
          </span>
          {highlight.size > 0 && activeTab === 'facts' && (
            <button
              type="button"
              className="text-btn"
              onClick={() => setHighlight(new Set())}
            >
              清除高亮
            </button>
          )}
        </div>

        {ledgerQuery.error && !ledger ? (
          <ErrorState message={ledgerQuery.error} onRetry={retry} />
        ) : ledgerQuery.loading ? (
          <div className="ledger-loading" aria-busy="true">
            {Array.from({ length: 6 }).map((_, index) => (
              <div key={index} className="fact-skeleton">
                <Skeleton width="36%" height={14} />
                <Skeleton width="24%" height={14} />
                <Skeleton width={64} height={20} radius={999} />
                <Skeleton width={80} height={8} />
              </div>
            ))}
          </div>
        ) : activeTab === 'facts' ? (
          groupedFacts.length === 0 ? (
            <div className="ledger-empty">
              <Search size={22} />
              <strong>没有匹配的事实</strong>
              <p>清除搜索或筛选条件后再试。</p>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setSearch('')
                  setOnlyMissing(false)
                  setOnlyRequired(false)
                }}
              >
                清除筛选
              </Button>
            </div>
          ) : (
            <div className="fact-table" role="table" aria-label="事实台账">
              <div className="fact-table-head" role="row">
                <span role="columnheader">字段</span>
                <span role="columnheader">取值</span>
                <span role="columnheader">状态</span>
                <span role="columnheader">置信度</span>
                <span role="columnheader">原文出处</span>
              </div>
              {groupedFacts.map((group) => {
                const isCollapsed = Boolean(collapsedGroups[group.id])
                return (
                  <div className="fact-group" key={group.id}>
                    <button
                      type="button"
                      className="fact-group-head"
                      onClick={() =>
                        setCollapsedGroups((current) => ({
                          ...current,
                          [group.id]: !current[group.id],
                        }))
                      }
                      aria-expanded={!isCollapsed}
                    >
                      {isCollapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
                      <span className="fact-group-title">{group.label}</span>
                      <span className="fact-group-count">{group.facts.length} 项</span>
                      {(allGroupMissing[group.id] || 0) > 0 && (
                        <Badge tone="danger" subtle>
                          缺 {allGroupMissing[group.id]} 必填
                        </Badge>
                      )}
                    </button>
                    {!isCollapsed &&
                      group.facts.map((fact) => (
                        <FactRow
                          key={fact.key}
                          fact={fact}
                          readOnly={!canWrite}
                          editing={edit?.key === fact.key}
                          edit={edit}
                          saving={savingKey === fact.key}
                          saved={savedKey === fact.key}
                          highlighted={highlight.has(fact.key)}
                          error={rowError?.key === fact.key ? rowError.message : null}
                          rowRef={(element) => {
                            rowRefs.current[fact.key] = element
                          }}
                          onStartEdit={() => void startEdit(fact)}
                          onCancelEdit={() => {
                            setEdit(null)
                            setRowError(null)
                          }}
                          onChange={(value) =>
                            setEdit((current) =>
                              current ? { ...current, value } : current,
                            )
                          }
                          onChangeStatus={(status) =>
                            setEdit((current) =>
                              current ? { ...current, status } : current,
                            )
                          }
                          onSave={() => void saveFact(fact)}
                        />
                      ))}
                  </div>
                )
              })}
            </div>
          )
        ) : activeTab === 'scoring' ? (
          <div className="tab-stack">
            <ScoreBackfill projectId={project?.id || ref || ''} canWrite={canWrite} />
            <ReadonlyLedger
              tab={activeTab}
              ledger={ledger}
              search={search}
              projectFormatSections={ledger?.template_sections || []}
            />
          </div>
        ) : (
          <ReadonlyLedger
            tab={activeTab}
            ledger={ledger}
            search={search}
            projectFormatSections={ledger?.template_sections || []}
          />
        )}
        </div>
      </section>

      <p className="ledger-footnote">
        字段状态：已提取（规则命中）、已确认（人工核对）、手工录入（人工补充）、缺失、取值冲突。
        来源引用保留原文件与页码，可直接回查。
      </p>
    </div>
  )
}

function FactRow({
  fact,
  readOnly,
  editing,
  edit,
  saving,
  saved,
  highlighted,
  error,
  rowRef,
  onStartEdit,
  onCancelEdit,
  onChange,
  onChangeStatus,
  onSave,
}: {
  fact: Fact
  readOnly: boolean
  editing: boolean
  edit: EditState | null
  saving: boolean
  saved: boolean
  highlighted: boolean
  error: string | null
  rowRef: (element: HTMLDivElement | null) => void
  onStartEdit: () => void
  onCancelEdit: () => void
  onChange: (value: string) => void
  onChangeStatus: (status: string) => void
  onSave: () => void
}) {
  const status = factStatus(fact.status)
  const missingRequired = fact.required && !fact.value
  const confidence = confidencePercent(fact.confidence)
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onCancelEdit()
    }
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault()
      onSave()
    }
  }

  return (
    <div
      ref={rowRef}
      tabIndex={highlighted ? -1 : undefined}
      className={className(
        'fact-row',
        missingRequired && 'fact-row--missing',
        highlighted && 'is-highlighted',
        saved && 'is-saved',
      )}
      role="row"
    >
      <div className="fact-ident" role="cell">
        <div className="fact-label-line">
          <span className="fact-label">{fact.label}</span>
          {fact.required && <span className="required-flag">必填</span>}
        </div>
        <code className="fact-key">{fact.key}</code>
        {fact.unit && <span className="fact-unit">单位：{fact.unit}</span>}
      </div>

      <div className="fact-value-cell" role="cell">
        {editing && edit ? (
          <div className="fact-editor">
            <AutoTextarea
              value={edit.value}
              onValueChange={onChange}
              minRows={2}
              onKeyDown={onKeyDown}
              placeholder={`填写${fact.label}${fact.unit ? `（${fact.unit}）` : ''}`}
              aria-label={`编辑${fact.label}取值`}
              data-autofocus
            />
            <div className="fact-editor-actions">
              <select
                className="select select--sm"
                value={edit.status}
                onChange={(event) => onChangeStatus(event.target.value)}
                aria-label="事实状态"
              >
                {FACT_STATUS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <span className="editor-hint">Ctrl + Enter 保存</span>
              <Button size="sm" variant="ghost" onClick={onCancelEdit} disabled={saving}>
                取消
              </Button>
              <Button
                size="sm"
                variant="primary"
                onClick={onSave}
                loading={saving}
                icon={<Check size={13} />}
              >
                保存
              </Button>
            </div>
            {error && (
              <div className="row-error" role="alert">
                {error}
              </div>
            )}
          </div>
        ) : (
          <button
            type="button"
            className="fact-value"
            onClick={onStartEdit}
            disabled={readOnly}
            title={readOnly ? '只读成员不能修改事实' : undefined}
          >
            {fact.value ? (
              <span>{fact.value}</span>
            ) : (
              <span className="fact-value-empty">
                {readOnly ? '未填写' : '未填写 · 点击补录'}
              </span>
            )}
            {!readOnly && <Pencil size={13} className="fact-edit-icon" aria-hidden />}
          </button>
        )}
        {saved && (
          <span className="save-flash">
            <Check size={12} /> 已保存
          </span>
        )}
        {fact.required && missingRequired && (
          <span className="missing-hint">
            <AlertTriangle size={12} /> 必填缺失，会阻止生成
          </span>
        )}
      </div>

      <div className="fact-status-cell" role="cell">
        <Badge tone={status.tone} dot>
          {status.label}
        </Badge>
        {fact.status === 'conflict' && (
          <span className="fact-conflict">多处取值冲突，请人工裁决</span>
        )}
      </div>

      <div className="fact-confidence-cell" role="cell">
        {fact.confidence !== undefined && fact.confidence > 0 ? (
          <div className="confidence">
            <div className="confidence-track" aria-hidden>
              <span
                className={className(
                  'confidence-fill',
                  confidence < 60 && 'is-low',
                  confidence < 85 && confidence >= 60 && 'is-mid',
                )}
                style={{ width: `${confidence}%` }}
              />
            </div>
            <span className="mono">{confidenceText(fact.confidence)}</span>
          </div>
        ) : (
          <span className="subtle">{fact.status === 'manual' ? '人工' : '—'}</span>
        )}
      </div>

      <div className="fact-source-cell" role="cell">
        <SourceQuote
          compact
          quote={fact.source?.quote}
          file={fact.source?.file}
          page={fact.source?.page}
          summaryLabel={
            fact.source?.page ? `原文 · p.${fact.source.page}` : undefined
          }
        />
      </div>
    </div>
  )
}

function ReadonlyLedger({
  tab,
  ledger,
  search,
  projectFormatSections,
}: {
  tab: Exclude<LedgerTab, 'facts'>
  ledger?: Ledger
  search: string
  projectFormatSections: string[]
}) {
  const query = search.trim().toLowerCase()
  const matches = (...values: Array<string | number | boolean | undefined | null>) =>
    !query ||
    values.some(
      (value) =>
        value !== undefined &&
        value !== null &&
        String(value).toLowerCase().includes(query),
    )

  if (!ledger) return null

  if (tab === 'materials') {
    const rows = ledger.materials.filter((item) =>
      matches(item.name, item.spec, item.unit, item.qty, item.note, item.source_quote),
    )
    return (
      <ReadonlyTable
        columns={['名称', '规格', '单位', '数量', '备注', '原文']}
        count={rows.length}
        empty={<RegisterEmpty icon={<PackageOpen size={22} />} label="材料清单" searching={Boolean(query)} />}
        rows={rows.map((item) => [
          <strong>{item.name}</strong>,
          item.spec || '—',
          item.unit || '—',
          <span className="mono">{formatNumber(item.qty)}</span>,
          item.note || '—',
          <SourceQuote quote={item.source_quote} summaryLabel="原文" compact />,
        ])}
      />
    )
  }

  if (tab === 'parameters') {
    const rows = ledger.parameters.filter((item) =>
      matches(
        item.name,
        item.value,
        item.unit,
        item.requirement,
        item.category,
        item.source_quote,
      ),
    )
    return (
      <ReadonlyTable
        columns={['参数', '招标要求', '单位', '要求值', '类别', '原文']}
        count={rows.length}
        empty={<RegisterEmpty icon={<Ruler size={22} />} label="技术参数" searching={Boolean(query)} />}
        rows={rows.map((item) => [
          <span>
            <strong>{item.name}</strong>
            {item.mandatory && (
              <Badge tone="danger" subtle>
                必须响应
              </Badge>
            )}
          </span>,
          item.requirement || '—',
          item.unit || '—',
          item.value || '—',
          item.category || '—',
          <SourceQuote quote={item.source_quote} summaryLabel="原文" compact />,
        ])}
      />
    )
  }

  if (tab === 'scoring') {
    const rows = ledger.scoring.filter((item) =>
      matches(
        item.name,
        item.score,
        item.criteria,
        item.category,
        item.section_hint,
        item.source_quote,
      ),
    )
    return (
      <ReadonlyTable
        columns={['评分项', '分值', '评分标准', '类别 / 章节提示', '原文']}
        count={rows.length}
        empty={<RegisterEmpty icon={<Sparkles size={22} />} label="评分办法" searching={Boolean(query)} />}
        rows={rows.map((item) => [
          <strong>{item.name}</strong>,
          <span className="score-value mono">{formatNumber(item.score)}</span>,
          item.criteria || '—',
          <span>
            {item.category || '—'}
            {item.section_hint && <small className="cell-sub">{item.section_hint}</small>}
          </span>,
          <SourceQuote quote={item.source_quote} summaryLabel="原文" compact />,
        ])}
      />
    )
  }

  if (tab === 'requirements') {
    const rows = ledger.requirements.filter((item) =>
      matches(item.kind, item.text, item.mandatory, item.source_quote),
    )
    return (
      <>
        <ReadonlyTable
          columns={['类型', '要求', '响应级别', '原文']}
          count={rows.length}
          empty={<RegisterEmpty icon={<FileCheck2 size={22} />} label="实质性要求" searching={Boolean(query)} />}
          rows={rows.map((item) => [
            <Badge tone={item.mandatory ? 'danger' : 'neutral'} subtle>
              {requirementKind(item.kind)}
            </Badge>,
            item.text,
            item.mandatory ? (
              <Badge tone="danger" subtle>
                实质性 / 必须响应
              </Badge>
            ) : (
              '一般要求'
            ),
            <SourceQuote quote={item.source_quote} summaryLabel="原文" compact />,
          ])}
        />
        {projectFormatSections.length > 0 && (
          <div className="format-sections">
            <div className="format-sections-head">
              <strong>招标文件格式骨架</strong>
              <span className="subtle">{projectFormatSections.length} 项</span>
            </div>
            <div className="format-chips">
              {projectFormatSections.map((section, index) => (
                <span key={`${section}-${index}`}>
                  <span className="format-no mono">{String(index + 1).padStart(2, '0')}</span>
                  {section}
                </span>
              ))}
            </div>
          </div>
        )}
      </>
    )
  }

  const rows = ledger.risks.filter((item) =>
    matches(item.level, item.text, item.source_quote),
  )
  return (
    <ReadonlyTable
      columns={['风险等级', '风险条款 / 废标情形', '原文']}
      count={rows.length}
      empty={<RegisterEmpty icon={<ShieldAlert size={22} />} label="废标风险" searching={Boolean(query)} />}
      rows={rows.map((item) => {
        const level = riskLevel(item.level)
        return [
          <Badge tone={level.tone} subtle>
            {level.label}
          </Badge>,
          item.text,
          <SourceQuote quote={item.source_quote} summaryLabel="原文" compact />,
        ]
      })}
    />
  )
}

function ReadonlyTable({
  columns,
  rows,
  empty,
  count,
}: {
  columns: string[]
  rows: ReactNode[][]
  empty: ReactNode
  count: number
}) {
  if (rows.length === 0) return <>{empty}</>
  return (
    <div className="readonly-wrap">
      <div className="readonly-meta">
        <span>查得 {count} 条</span>
        <span className="subtle">只读 · 来源于招标文件解析</span>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function RegisterEmpty({
  icon,
  label,
  searching,
}: {
  icon: ReactNode
  label: string
  searching?: boolean
}) {
  if (searching) {
    return (
      <div className="ledger-empty">
        {icon}
        <strong>没有匹配的{label}</strong>
        <p>当前搜索没有命中。清除搜索后可以查看全部解析结果。</p>
      </div>
    )
  }
  return (
    <div className="ledger-empty">
      {icon}
      <strong>未提取到{label}</strong>
      <p>可能招标文件未包含该部分，或文件为扫描件。请回查原文后在事实字段中补录。</p>
      <span className="subtle">只展示真实解析结果，不生成示例数据。</span>
    </div>
  )
}

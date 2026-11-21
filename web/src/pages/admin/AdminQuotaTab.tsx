import { useMemo, useState } from 'react'
import {
  LibraryBig,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  ScanSearch,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import type { QuotaItem, QuotaMatch } from '../../lib/types'
import {
  formatMoney,
  formatRelative,
} from '../../lib/format'
import { deleteQuota, getQuotas, matchQuotas } from '../../api/boq'
import { messageOf } from '../../lib/http'
import { useDebouncedValue, useQuery } from '../../lib/hooks'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'
import { Badge } from '../../components/Badge'
import { Button, IconButton } from '../../components/Button'
import { EmptyState, ErrorState } from '../../components/Feedback'
import {
  AdminActionError,
  AdminSectionLabel,
  AdminSkeletonRows,
} from './AdminShared'
import { AdminQuotaModal, type QuotaModalState } from './AdminQuotaModal'

interface QuotaTabProps {
  quotas: QuotaItem[]
  loading: boolean
  refreshing: boolean
  error: string | null
  refresh: () => void
}

const QUOTA_LIMIT = 100

function scoreTone(score: number): 'success' | 'warn' | 'neutral' {
  if (score >= 0.9) return 'success'
  if (score >= 0.8) return 'warn'
  return 'neutral'
}

export function AdminQuotaTab({
  quotas,
  loading,
  refreshing,
  error,
  refresh,
}: QuotaTabProps) {
  const toast = useToast()
  const confirm = useConfirm()
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('all')
  const [modal, setModal] = useState<QuotaModalState | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const [matchName, setMatchName] = useState('')
  const [matchSpec, setMatchSpec] = useState('')
  const [matching, setMatching] = useState(false)
  const [matchResults, setMatchResults] = useState<QuotaMatch[] | null>(null)
  const [matchError, setMatchError] = useState<string | null>(null)

  // 服务端搜索只在有关键字时启用：平时的浏览直接用控制台已经加载的列表。
  const debounced = useDebouncedValue(search, 240)
  const query = debounced.trim()
  const searchQuery = useQuery(
    `admin:quotas:search:${query}`,
    () => getQuotas(query, QUOTA_LIMIT),
    { enabled: query.length > 0 },
  )

  const source = useMemo(
    () => (query ? searchQuery.data ?? [] : quotas),
    [query, searchQuery.data, quotas],
  )
  const busy = query ? searchQuery.loading : loading

  const categories = useMemo(() => {
    const found = new Set<string>()
    for (const item of [...quotas, ...source]) {
      const name = item.category.trim()
      if (name) found.add(name)
    }
    return Array.from(found).sort((left, right) => left.localeCompare(right, 'zh'))
  }, [quotas, source])

  const rows = useMemo(
    () => (category === 'all' ? source : source.filter((item) => item.category === category)),
    [source, category],
  )

  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = { all: source.length }
    for (const item of source) {
      if (!item.category) continue
      counts[item.category] = (counts[item.category] || 0) + 1
    }
    return counts
  }, [source])

  const refreshActive = () => {
    if (query) searchQuery.refresh()
    else refresh()
  }

  const remove = async (quota: QuotaItem) => {
    if (deletingId) return
    const accepted = await confirm({
      title: `删除定额「${quota.name}」？`,
      description: `该定额单价（¥${formatMoney(quota.unit_price)}）将不再参与清单套用。已经写入清单的单价不受影响。`,
      note: '删除后不可撤销；如需保留历史价格，可以先编辑名称标注版本。',
      confirmLabel: '删除定额',
      tone: 'danger',
    })
    if (!accepted) return
    setDeletingId(quota.id)
    setActionError(null)
    try {
      await deleteQuota(quota.id)
      refreshActive()
      toast.success('定额已删除', quota.name)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('删除定额失败', message)
    } finally {
      setDeletingId(null)
    }
  }

  const onSaved = (quota: QuotaItem, created: boolean) => {
    setModal(null)
    refreshActive()
    toast.success(
      created ? '定额已新增' : '定额已保存',
      `${quota.name} · ¥${formatMoney(quota.unit_price)}`,
    )
  }

  const runMatch = async () => {
    const name = matchName.trim()
    if (!name || matching) {
      if (!name) setMatchError('请先填写清单名称。')
      return
    }
    setMatching(true)
    setMatchError(null)
    try {
      const results = await matchQuotas(name, matchSpec)
      setMatchResults(results)
    } catch (err) {
      setMatchResults(null)
      setMatchError(messageOf(err))
    } finally {
      setMatching(false)
    }
  }

  const resetFilters = () => {
    setSearch('')
    setCategory('all')
  }

  const filtered = Boolean(query || category !== 'all')
  const showEmptyLibrary = !loading && quotas.length === 0 && !query

  return (
    <div className="admin-quotas">
      <div className="admin-tab-head">
        <AdminSectionLabel
          eyebrow="价格基准"
          title="企业定额库"
          description="维护企业积累的综合单价。上传清单后可在清单页用「定额套用」按名称与规格匹配这里的单价，程序不会凭空生成价格。"
          aside={
            <Button
              variant="primary"
              icon={<Plus size={15} />}
              onClick={() => setModal({ mode: 'create' })}
            >
              新增定额
            </Button>
          }
        />
      </div>

      {actionError && (
        <AdminActionError message={actionError} onClose={() => setActionError(null)} />
      )}

      <div className="admin-callout admin-callout--ok">
        <LibraryBig size={18} />
        <div>
          <strong>
            {loading
              ? '正在读取定额库…'
              : `${quotas.length}${quotas.length >= QUOTA_LIMIT ? '+' : ''} 条定额 · ${
                  categories.length
                } 个分类`}
          </strong>
          <p>
            套用时按名称为主、规格加分计算相似度，低于 0.75 的行不会被套用；
            填错价格的定额会直接影响报价，修改前建议先在「匹配测试」里确认命中的是哪一条。
          </p>
        </div>
      </div>

      <div className="admin-list-toolbar quota-toolbar">
        <label className="search-field quota-search">
          <Search size={15} aria-hidden />
          <span className="sr-only">搜索定额</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索名称、规格或分类"
            aria-label="按名称、规格或分类搜索定额"
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

        <div className="quota-toolbar-right">
          <label className="select-field select-field--compact">
            <span className="sr-only">按分类筛选定额</span>
            <select
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              <option value="all">全部分类（{categoryCounts.all}）</option>
              {categories.map((item) => (
                <option key={item} value={item}>
                  {item}（{categoryCounts[item] || 0}）
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="text-btn"
            onClick={refreshActive}
            disabled={refreshing || searchQuery.refreshing}
          >
            <RefreshCw
              size={13}
              className={refreshing || searchQuery.refreshing ? 'spin' : undefined}
            />
            刷新
          </button>
        </div>
      </div>

      <details className="quota-match">
        <summary>
          <ScanSearch size={14} aria-hidden />
          <strong>匹配测试</strong>
          <span>用一条清单名称试着匹配，确认会命中哪条定额</span>
        </summary>
        <div className="quota-match-body">
          <div className="quota-match-form">
            <label className="quota-match-field">
              <span>清单名称</span>
              <input
                className="input input--sm"
                value={matchName}
                onChange={(event) => {
                  setMatchName(event.target.value)
                  if (matchError) setMatchError(null)
                }}
                placeholder="例如：六类非屏蔽双绞线 敷设"
                maxLength={120}
                autoComplete="off"
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    void runMatch()
                  }
                }}
              />
            </label>
            <label className="quota-match-field quota-match-field--spec">
              <span>规格（可选）</span>
              <input
                className="input input--sm"
                value={matchSpec}
                onChange={(event) => setMatchSpec(event.target.value)}
                placeholder="UTP CAT6 4P"
                maxLength={200}
                autoComplete="off"
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    void runMatch()
                  }
                }}
              />
            </label>
            <Button
              size="sm"
              variant="secondary"
              icon={<ScanSearch size={13} />}
              loading={matching}
              disabled={!matchName.trim()}
              onClick={() => void runMatch()}
            >
              试匹配
            </Button>
          </div>

          {matchError && (
            <div className="form-alert" role="alert">
              {matchError}
            </div>
          )}

          {matchResults && matchResults.length === 0 && (
            <p className="quota-match-empty">
              没有相似度达到 0.75 的定额。可以调整名称写法，或在上面新增一条更贴近的定额。
            </p>
          )}

          {matchResults && matchResults.length > 0 && (
            <ul className="quota-match-list">
              {matchResults.map((item) => {
                const pct = Math.round(Math.max(0, Math.min(1, item.score)) * 100)
                return (
                  <li key={item.id}>
                    <div className="quota-match-main">
                      <strong>{item.name}</strong>
                      <span className="quota-match-meta">
                        {item.spec || '未填规格'}
                        {item.unit ? ` · ${item.unit}` : ''}
                      </span>
                    </div>
                    <span className="quota-match-price mono">
                      ¥{formatMoney(item.unit_price)}
                    </span>
                    <span className="quota-score">
                      <span className="quota-score-track" aria-hidden>
                        <span style={{ width: `${pct}%` }} />
                      </span>
                      <span className="mono">{pct}%</span>
                    </span>
                    <Badge tone={scoreTone(item.score)} subtle>
                      {item.score >= 0.9
                        ? '高度相似'
                        : item.score >= 0.8
                          ? '较为相似'
                          : '勉强命中'}
                    </Badge>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </details>

      {error ? (
        <div className="admin-panel-inner">
          <ErrorState message={error} onRetry={refresh} />
        </div>
      ) : showEmptyLibrary ? (
        <div className="admin-panel-inner">
          <EmptyState
            icon={LibraryBig}
            title="还没有企业定额"
            description="把常用的材料与做法连同综合单价录进来，上传清单后就能一键套价。程序只使用这里维护的价格。"
            actions={
              <Button
                variant="primary"
                icon={<Plus size={15} />}
                onClick={() => setModal({ mode: 'create' })}
              >
                新增第一条定额
              </Button>
            }
          />
          <div className="admin-empty-note">
            可以先从最近中标的清单里整理一份常用单价；同一条定额按「名称 + 规格 + 单位」描述清楚即可。
          </div>
        </div>
      ) : busy && rows.length === 0 ? (
        <AdminSkeletonRows rows={4} />
      ) : searchQuery.error ? (
        <div className="admin-panel-inner">
          <ErrorState message={searchQuery.error} onRetry={() => searchQuery.refresh()} />
        </div>
      ) : rows.length === 0 ? (
        <div className="admin-panel-inner">
          <EmptyState
            icon={Search}
            compact
            title="没有匹配的定额"
            description="试试名称或规格里的关键字；也可以切换到「全部分类」查看全部定额。"
            actions={
              <Button variant="secondary" onClick={resetFilters} disabled={!filtered}>
                清除筛选
              </Button>
            }
          />
        </div>
      ) : (
        <div className="admin-panel-inner">
          <div className="table-scroll">
            <table className="data-table quota-table">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>规格</th>
                  <th>单位</th>
                  <th className="num">定额单价（元）</th>
                  <th>分类</th>
                  <th className="quota-col-actions">操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <strong>{item.name}</strong>
                      {item.note && <span className="cell-sub">{item.note}</span>}
                    </td>
                    <td>{item.spec || <span className="subtle">—</span>}</td>
                    <td>{item.unit || <span className="subtle">—</span>}</td>
                    <td className="num">
                      <span className="quota-price mono">¥{formatMoney(item.unit_price)}</span>
                    </td>
                    <td>
                      {item.category ? (
                        <span className="quota-category">{item.category}</span>
                      ) : (
                        <span className="subtle">未分类</span>
                      )}
                      {item.updated_at && (
                        <span className="cell-sub">更新于 {formatRelative(item.updated_at)}</span>
                      )}
                    </td>
                    <td className="quota-col-actions">
                      <div className="admin-row-actions">
                        <Button
                          variant="ghost"
                          size="sm"
                          icon={<Pencil size={12} />}
                          disabled={deletingId !== null}
                          onClick={() => setModal({ mode: 'edit', quota: item })}
                        >
                          编辑
                        </Button>
                        <IconButton
                          label={`删除定额 ${item.name}`}
                          tone="danger"
                          disabled={deletingId !== null}
                          onClick={() => void remove(item)}
                        >
                          {deletingId === item.id ? (
                            <Loader2 size={14} className="spin" />
                          ) : (
                            <Trash2 size={14} />
                          )}
                        </IconButton>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="admin-footnote">
        定额单价是清单套价的唯一来源；套用时相似度低于 0.75 的清单行会被跳过，已录入的单价不会被自动覆盖（除非关闭「仅填补未报价行」）。
        {quotas.length >= QUOTA_LIMIT && ' 列表最多显示前 100 条，输入关键字可搜索更多。'}
      </div>

      {modal && (
        <AdminQuotaModal
          open
          state={modal}
          onClose={() => setModal(null)}
          onSaved={onSaved}
        />
      )}
    </div>
  )
}

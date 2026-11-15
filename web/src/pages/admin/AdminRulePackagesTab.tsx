import { useMemo, useState } from 'react'
import {
  AlertTriangle,
  Boxes,
  Building2,
  Eye,
  FileCode2,
  Flame,
  History,
  Layers,
  Paintbrush,
  Pencil,
  Plus,
  RefreshCw,
  RotateCw,
  Search,
  Trash2,
  Wifi,
  Wind,
  Wrench,
  X,
  type LucideIcon,
} from 'lucide-react'
import type { AdminRulePackage } from '../../lib/types'
import {
  ALL_PROJECT_TYPES,
  className,
  formatNumber,
  normalizeProjectType,
  projectTypeMeta,
} from '../../lib/format'
import {
  deleteRulePackage,
  messageOf,
  restoreRulePackage,
} from '../../lib/api'
import { invalidatePackageCache } from '../../lib/hooks'
import { useConfirm } from '../../context/ConfirmContext'
import { useToast } from '../../context/ToastContext'
import { Badge } from '../../components/Badge'
import { Button, IconButton } from '../../components/Button'
import { EmptyState, ErrorState } from '../../components/Feedback'
import {
  AdminActionError,
  AdminSectionLabel,
  AdminSkeletonRows,
} from './AdminShared'
import {
  AdminRulePackageModal,
  type RulePackageModalState,
} from './AdminRulePackageModal'

interface RulePackagesTabProps {
  packages: AdminRulePackage[]
  loading: boolean
  refreshing: boolean
  error: string | null
  refresh: () => void
}

const TYPE_ICONS: Record<string, LucideIcon> = {
  common: Layers,
  weakcurrent: Wifi,
  fire: Flame,
  installation: Wrench,
  civil: Building2,
  hvac: Wind,
  decoration: Paintbrush,
  other: Boxes,
}

export function AdminRulePackagesTab({
  packages,
  loading,
  refreshing,
  error,
  refresh,
}: RulePackagesTabProps) {
  const toast = useToast()
  const confirm = useConfirm()
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [showHidden, setShowHidden] = useState(false)
  const [modal, setModal] = useState<RulePackageModalState | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const hiddenCount = packages.filter((pkg) => pkg.hidden).length
  const referencedProjects = packages.reduce((total, pkg) => total + pkg.usage, 0)
  const builtinCount = packages.filter((pkg) => pkg.builtin).length
  const customCount = packages.length - builtinCount

  const baseList = useMemo(
    () => (showHidden ? packages : packages.filter((pkg) => !pkg.hidden)),
    [packages, showHidden],
  )

  const typeCounts = useMemo(() => {
    const result: Record<string, number> = { all: baseList.length }
    ALL_PROJECT_TYPES.forEach((type) => {
      result[type.id] = baseList.filter(
        (pkg) => normalizeProjectType(pkg.project_type) === type.id,
      ).length
    })
    return result
  }, [baseList])

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase()
    return baseList.filter((pkg) => {
      if (typeFilter !== 'all' && normalizeProjectType(pkg.project_type) !== typeFilter) {
        return false
      }
      if (!query) return true
      const extendsText = Array.isArray(pkg.extends) ? pkg.extends.join(' ') : ''
      return [
        pkg.title,
        pkg.name,
        pkg.description,
        pkg.project_type,
        pkg.project_type_label,
        extendsText,
        pkg.usage,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query))
    })
  }, [baseList, search, typeFilter])

  const groups = useMemo(
    () =>
      ALL_PROJECT_TYPES.map((type) => ({
        ...type,
        items: filtered.filter(
          (pkg) => normalizeProjectType(pkg.project_type) === type.id,
        ),
      })).filter((group) => group.items.length > 0),
    [filtered],
  )

  const afterChange = () => {
    invalidatePackageCache()
    refresh()
  }

  const restore = async (pkg: AdminRulePackage) => {
    if (pending) return
    setPending(`restore:${pkg.name}`)
    setActionError(null)
    try {
      await restoreRulePackage(pkg.name)
      afterChange()
      toast.success('规则包已恢复', `「${pkg.title || pkg.name}」重新出现在新建项目列表。`)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('恢复规则包失败', message)
    } finally {
      setPending(null)
    }
  }

  const resetOverride = async (pkg: AdminRulePackage) => {
    if (pending) return
    const accepted = await confirm({
      title: '恢复内置原文？',
      description: `「${pkg.title || pkg.name}」当前使用用户目录中的覆盖版本。`,
      note: '恢复后会删除覆盖文件，改用安装目录里的内置 YAML。管理员对该包的所有修改都会消失。',
      confirmLabel: '继续',
      tone: 'danger',
    })
    if (!accepted) return
    const confirmedAgain = await confirm({
      title: '再次确认：恢复内置原文',
      description: '这一步不可撤销。恢复后，覆盖版本中的字段、章节与核对规则将全部丢失。',
      note: pkg.hidden ? '该包同时会取消隐藏。' : '恢复后仍可再次编辑，但需要重新编写 YAML。',
      confirmLabel: '确认恢复原文',
      tone: 'danger',
    })
    if (!confirmedAgain) return
    setPending(`reset:${pkg.name}`)
    setActionError(null)
    try {
      await restoreRulePackage(pkg.name, true)
      afterChange()
      toast.success('已恢复内置原文', `「${pkg.title || pkg.name}」回到安装目录版本。`)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('恢复内置原文失败', message)
    } finally {
      setPending(null)
    }
  }

  const remove = async (pkg: AdminRulePackage) => {
    if (pending) return
    const accepted = await confirm({
      title: '删除规则包？',
      description: `「${pkg.title || pkg.name}」将从新建项目的规则包列表中隐藏，已有项目继续可用。`,
      note:
        pkg.usage > 0
          ? `当前有 ${pkg.usage} 个项目使用该规则包，后端会拒绝删除。可以先让这些项目改用其他包，或暂时保留它。`
          : '规则包文件不会被删除，之后可以在「显示已隐藏」里恢复。',
      confirmLabel: '隐藏规则包',
      tone: 'danger',
    })
    if (!accepted) return
    setPending(`delete:${pkg.name}`)
    setActionError(null)
    try {
      await deleteRulePackage(pkg.name)
      afterChange()
      toast.success('规则包已隐藏', `「${pkg.title || pkg.name}」不再出现在新建项目列表。`)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('删除规则包失败', message)
    } finally {
      setPending(null)
    }
  }

  const onSaved = (pkg: AdminRulePackage, created: boolean) => {
    setModal(null)
    afterChange()
    toast.success(
      created ? '规则包已创建' : '规则包已保存',
      `${pkg.title || pkg.name} · v${String(pkg.version)}`,
    )
  }

  return (
    <div className="admin-packages">
      <div className="admin-tab-head">
        <AdminSectionLabel
          eyebrow="规则与骨架"
          title="规则包"
          description="行业规则包决定事实字段、章节骨架与核对规则。内置包可以覆盖为本地版本；删除只会从新建项目隐藏，已有项目继续可用。"
          aside={
            <Button
              variant="primary"
              icon={<Plus size={15} />}
              onClick={() => setModal({ mode: 'create' })}
            >
              新建规则包
            </Button>
          }
        />
      </div>

      {actionError && (
        <AdminActionError message={actionError} onClose={() => setActionError(null)} />
      )}

      <div className="admin-callout admin-callout--ok">
        <Layers size={18} />
        <div>
          <strong>
            {formatNumber(packages.length)} 个规则包 · {builtinCount} 个内置 ·{' '}
            {customCount} 个自定义
            {hiddenCount > 0 ? ` · ${hiddenCount} 个已隐藏` : ''}
          </strong>
          <p>
            共被 {formatNumber(referencedProjects)} 个项目引用。管理员修改写入本机用户目录，
            不会改动安装目录里的内置 YAML；YAML 损坏的包会保留在列表中并标明原因。
          </p>
        </div>
      </div>

      <div className="admin-list-toolbar admin-package-toolbar">
        <label className="search-field admin-package-search">
          <Search size={15} aria-hidden />
          <span className="sr-only">搜索规则包</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索标题、包名、类型或说明"
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

        <div className="admin-package-toolbar-right">
          <label className="select-field select-field--compact">
            <span className="sr-only">按项目类型筛选规则包</span>
            <select
              value={typeFilter}
              onChange={(event) => setTypeFilter(event.target.value)}
            >
              <option value="all">全部类型（{typeCounts.all}）</option>
              {ALL_PROJECT_TYPES.filter(
                (type) => typeCounts[type.id] > 0 || typeFilter === type.id,
              ).map((type) => (
                <option key={type.id} value={type.id}>
                  {type.label}（{typeCounts[type.id]}）
                </option>
              ))}
            </select>
          </label>

          <button
            type="button"
            className={className('filter-chip', showHidden && 'is-active')}
            aria-pressed={showHidden}
            onClick={() => setShowHidden((value) => !value)}
          >
            <Eye size={13} />
            显示已隐藏
            <span className="admin-package-hidden-count mono">{hiddenCount}</span>
          </button>

          <button type="button" className="text-btn" onClick={refresh} disabled={refreshing}>
            <RefreshCw size={13} className={refreshing ? 'spin' : undefined} />
            刷新
          </button>
        </div>
      </div>

      {error ? (
        <div className="admin-panel-inner">
          <ErrorState message={error} onRetry={refresh} />
        </div>
      ) : loading ? (
        <AdminSkeletonRows rows={3} />
      ) : packages.length === 0 ? (
        <div className="admin-panel-inner">
          <EmptyState
            icon={FileCode2}
            title="还没有规则包"
            description="规则包用 YAML 定义事实字段、章节骨架与核对表达式。内置包随安装目录提供；也可以在这里新建行业规则包。"
            actions={
              <Button
                variant="primary"
                icon={<Plus size={15} />}
                onClick={() => setModal({ mode: 'create' })}
              >
                新建第一个规则包
              </Button>
            }
          />
          <div className="admin-empty-note">
            保存前会完整校验继承链、字段、章节与核对表达式；建议先使用编辑器的模板。
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="admin-panel-inner">
          <EmptyState
            icon={hiddenCount > 0 && !showHidden ? Eye : Search}
            compact
            title={
              hiddenCount > 0 && !showHidden && !search.trim() && typeFilter === 'all'
                ? '所有规则包都已隐藏'
                : '没有匹配的规则包'
            }
            description={
              hiddenCount > 0 && !showHidden && !search.trim() && typeFilter === 'all'
                ? '隐藏只影响新建项目列表，已有项目仍可继续使用。打开「显示已隐藏」即可恢复。'
                : '试试标题、包名、说明或项目类型；也可以切换「显示已隐藏」查看隐藏的包。'
            }
            actions={
              hiddenCount > 0 && !showHidden && !search.trim() && typeFilter === 'all' ? (
                <Button
                  variant="secondary"
                  icon={<Eye size={14} />}
                  onClick={() => setShowHidden(true)}
                >
                  显示已隐藏
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setSearch('')
                    setTypeFilter('all')
                  }}
                >
                  清除筛选
                </Button>
              )
            }
          />
        </div>
      ) : (
        <div className="admin-groups">
          {groups.map((group) => {
            const Icon = TYPE_ICONS[group.id] || FileCode2
            return (
              <section className="admin-group" key={group.id} aria-label={group.label}>
                <header className="admin-group-head">
                  <div className="admin-group-title">
                    <span className="admin-group-icon" aria-hidden>
                      <Icon size={17} />
                    </span>
                    <div>
                      <h3>
                        {group.label}
                        <span className="admin-group-count">{group.items.length}</span>
                      </h3>
                      <p>{group.description}</p>
                    </div>
                  </div>
                </header>

                <div className="admin-package-list">
                  {group.items.map((pkg) => {
                    const type = projectTypeMeta(pkg.project_type)
                    const extendsText =
                      Array.isArray(pkg.extends) && pkg.extends.length > 0
                        ? pkg.extends.join('、')
                        : '无'
                    const pendingAction =
                      pending && pending.endsWith(`:${pkg.name}`)
                        ? pending.split(':')[0]
                        : ''
                    const busy = pending !== null
                    return (
                      <article
                        key={pkg.name}
                        className={className(
                          'admin-rule-package-row',
                          pkg.hidden && 'is-hidden',
                          pkg.error && 'has-error',
                        )}
                      >
                        <div className="admin-package-main">
                          <div className="admin-model-title-line">
                            <h4>{pkg.title || pkg.name}</h4>
                            <Badge tone={pkg.builtin ? 'accent' : 'info'} subtle>
                              {pkg.builtin ? '内置' : '自定义'}
                            </Badge>
                            {pkg.overridden && (
                              <Badge tone="warn" subtle>
                                已覆盖
                              </Badge>
                            )}
                            {pkg.abstract && (
                              <Badge tone="neutral" subtle>
                                抽象基包
                              </Badge>
                            )}
                            {pkg.hidden && (
                              <Badge tone="neutral" subtle>
                                已隐藏
                              </Badge>
                            )}
                          </div>
                          <p className="admin-package-desc">
                            {pkg.description || '未填写说明。'}
                          </p>
                          <div className="admin-model-meta">
                            <span>
                              <span className="admin-meta-label">包名</span>
                              <span className="mono">{pkg.name}</span>
                            </span>
                            <span>
                              <span className="admin-meta-label">版本</span>v
                              {String(pkg.version)}
                            </span>
                            <span>
                              <span className="admin-meta-label">继承</span>
                              {extendsText}
                            </span>
                          </div>
                        </div>

                        <div className="admin-package-side">
                          <Badge tone={type.tone} subtle>
                            {type.label}
                          </Badge>
                          <span className="admin-package-usage">
                            {pkg.abstract
                              ? '抽象基包 · 不单独创建项目'
                              : pkg.usage > 0
                                ? `引用 ${formatNumber(pkg.usage)} 个项目`
                                : '暂无项目引用'}
                          </span>
                          {pkg.error && (
                            <p className="admin-package-error" title={pkg.error}>
                              <AlertTriangle size={11} />
                              <span>{pkg.error}</span>
                            </p>
                          )}
                        </div>

                        <div className="admin-row-actions">
                          <Button
                            variant="ghost"
                            size="sm"
                            icon={<Pencil size={12} />}
                            disabled={busy}
                            onClick={() => setModal({ mode: 'edit', pkg })}
                          >
                            编辑
                          </Button>
                          {pkg.hidden && (
                            <Button
                              variant="secondary"
                              size="sm"
                              icon={<RotateCw size={12} />}
                              loading={pendingAction === 'restore'}
                              disabled={busy}
                              onClick={() => void restore(pkg)}
                            >
                              恢复显示
                            </Button>
                          )}
                          {pkg.builtin && pkg.overridden && (
                            <Button
                              variant="ghost"
                              size="sm"
                              icon={<History size={12} />}
                              loading={pendingAction === 'reset'}
                              disabled={busy}
                              onClick={() => void resetOverride(pkg)}
                            >
                              恢复内置原文
                            </Button>
                          )}
                          {!pkg.hidden && !pkg.abstract && (
                            <IconButton
                              label={`删除规则包 ${pkg.name}`}
                              tone="danger"
                              disabled={busy}
                              onClick={() => void remove(pkg)}
                            >
                              <Trash2 size={14} />
                            </IconButton>
                          )}
                        </div>
                      </article>
                    )
                  })}
                </div>
              </section>
            )
          })}
        </div>
      )}

      <div className="admin-footnote">
        规则包以 YAML 为唯一格式；继承链里的 abstract 包不会出现在新建项目列表。
        删除仅加入隐藏清单，文件保留；恢复内置原文会删除用户目录中的覆盖文件。
      </div>

      {modal && (
        <AdminRulePackageModal
          open
          state={modal}
          onClose={() => setModal(null)}
          onSaved={onSaved}
        />
      )}
    </div>
  )
}

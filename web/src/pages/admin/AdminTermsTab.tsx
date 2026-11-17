import { useEffect, useMemo, useState } from 'react'
import {
  ArrowRight,
  BookMarked,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import type { TermInput, TermItem } from '../../lib/types'
import { className, formatRelative } from '../../lib/format'
import {
  createTerm,
  deleteTerm,
  getTerms,
  messageOf,
  updateTerm,
} from '../../lib/api'
import { useDebouncedValue, useQuery } from '../../lib/hooks'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'
import { Badge } from '../../components/Badge'
import { Button, IconButton } from '../../components/Button'
import { EmptyState, ErrorState } from '../../components/Feedback'
import { Modal } from '../../components/Modal'
import {
  AdminActionError,
  AdminSectionLabel,
  AdminSkeletonRows,
} from './AdminShared'

interface TermsTabProps {
  terms: TermItem[]
  loading: boolean
  refreshing: boolean
  error: string | null
  refresh: () => void
}

const TERM_LIMIT = 200

type TermModalState = { mode: 'create' } | { mode: 'edit'; term: TermItem }

function kindMeta(kind: string): { label: string; tone: 'danger' | 'info' } {
  return kind === 'forbidden'
    ? { label: '禁用表述', tone: 'danger' }
    : { label: '建议替换', tone: 'info' }
}

export function AdminTermsTab({
  terms,
  loading,
  refreshing,
  error,
  refresh,
}: TermsTabProps) {
  const toast = useToast()
  const confirm = useConfirm()
  const [search, setSearch] = useState('')
  const [kindFilter, setKindFilter] = useState('all')
  const [modal, setModal] = useState<TermModalState | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [togglingId, setTogglingId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  // 有关键字时走服务端搜索（术语总量可能超过一次加载的上限）；
  // 仅切换类型时直接在已加载列表里筛选，避免无谓请求。
  const debounced = useDebouncedValue(search, 240)
  const query = debounced.trim()
  const searchKind = kindFilter === 'all' ? undefined : kindFilter
  const searchQuery = useQuery(
    `admin:terms:search:${kindFilter}:${query}`,
    () => getTerms({ search: query, kind: searchKind, limit: TERM_LIMIT }),
    { enabled: query.length > 0 },
  )

  const source = useMemo(
    () => (query ? searchQuery.data ?? [] : terms),
    [query, searchQuery.data, terms],
  )
  const rows = useMemo(
    () =>
      kindFilter === 'all'
        ? source
        : source.filter((item) => item.kind === kindFilter),
    [source, kindFilter],
  )
  const busy = query ? searchQuery.loading : loading

  const counts = useMemo(() => {
    const result = { all: terms.length, replace: 0, forbidden: 0 }
    for (const item of terms) {
      if (item.kind === 'forbidden') result.forbidden += 1
      else result.replace += 1
    }
    return result
  }, [terms])

  const refreshActive = () => {
    if (query) searchQuery.refresh()
    else refresh()
  }

  const toggleEnabled = async (term: TermItem, enabled: boolean) => {
    if (togglingId || deletingId) return
    setTogglingId(term.id)
    setActionError(null)
    try {
      await updateTerm(term.id, { enabled })
      refreshActive()
      toast.success(enabled ? '术语已启用' : '术语已停用', term.source)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('更新术语状态失败', message)
    } finally {
      setTogglingId(null)
    }
  }

  const remove = async (term: TermItem) => {
    if (deletingId) return
    const accepted = await confirm({
      title: `删除术语「${term.source}」？`,
      description:
        term.kind === 'forbidden'
          ? '该表述将不再被正文核对标记为禁用词。'
          : `正文核对将不再提示把「${term.source}」改为「${term.target || '建议写法'}」。`,
      note: '删除后不可撤销；如果只是暂时不用，可以改为停用。',
      confirmLabel: '删除术语',
      tone: 'danger',
    })
    if (!accepted) return
    setDeletingId(term.id)
    setActionError(null)
    try {
      await deleteTerm(term.id)
      refreshActive()
      toast.success('术语已删除', term.source)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('删除术语失败', message)
    } finally {
      setDeletingId(null)
    }
  }

  const onSaved = (term: TermItem, created: boolean) => {
    setModal(null)
    refreshActive()
    toast.success(
      created ? '术语已新增' : '术语已保存',
      term.kind === 'forbidden'
        ? `${term.source} · 禁用表述`
        : `${term.source} → ${term.target || '未填目标词'}`,
    )
  }

  const resetFilters = () => {
    setSearch('')
    setKindFilter('all')
  }

  const filtered = Boolean(query || kindFilter !== 'all')
  const showEmptyLibrary = !loading && terms.length === 0 && !query

  return (
    <div className="admin-terms">
      <div className="admin-tab-head">
        <AdminSectionLabel
          eyebrow="写作规范"
          title="企业术语库"
          description="统一投标文件的用词。禁用表述会在正文核对时被标记；建议替换会提示把旧写法改成企业认可的说法。只有启用的条目参与检查。"
          aside={
            <Button
              variant="primary"
              icon={<Plus size={15} />}
              onClick={() => setModal({ mode: 'create' })}
            >
              新增术语
            </Button>
          }
        />
      </div>

      {actionError && (
        <AdminActionError message={actionError} onClose={() => setActionError(null)} />
      )}

      <div className="admin-callout admin-callout--ok">
        <BookMarked size={18} />
        <div>
          <strong>
            {loading
              ? '正在读取术语库…'
              : `${terms.length}${terms.length >= TERM_LIMIT ? '+' : ''} 条术语 · ${
                  counts.replace
                } 条建议替换 · ${counts.forbidden} 条禁用表述`}
          </strong>
          <p>
            「禁用表述」只要出现在正文里就会被提示；「建议替换」会同时检查旧写法是否仍在使用。
            术语按「类型 + 原词」唯一，重复添加会被后端拒绝。
          </p>
        </div>
      </div>

      <div className="admin-list-toolbar quota-toolbar">
        <label className="search-field quota-search">
          <Search size={15} aria-hidden />
          <span className="sr-only">搜索术语</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索原词或建议写法"
            aria-label="按原词或建议写法搜索术语"
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
            <span className="sr-only">按类型筛选术语</span>
            <select
              value={kindFilter}
              onChange={(event) => setKindFilter(event.target.value)}
            >
              <option value="all">全部类型（{counts.all}）</option>
              <option value="forbidden">禁用表述（{counts.forbidden}）</option>
              <option value="replace">建议替换（{counts.replace}）</option>
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

      {error ? (
        <div className="admin-panel-inner">
          <ErrorState message={error} onRetry={refresh} />
        </div>
      ) : showEmptyLibrary ? (
        <div className="admin-panel-inner">
          <EmptyState
            icon={BookMarked}
            title="还没有企业术语"
            description="把公司惯用的写法与必须避免的说法录进来，正文核对时会按这里生效。"
            actions={
              <Button
                variant="primary"
                icon={<Plus size={15} />}
                onClick={() => setModal({ mode: 'create' })}
              >
                新增第一条术语
              </Button>
            }
          />
          <div className="admin-empty-note">
            可先整理禁用词（如绝对化用语），再补充「旧写法 → 建议写法」的替换对。
          </div>
        </div>
      ) : busy && rows.length === 0 ? (
        <AdminSkeletonRows rows={4} />
      ) : searchQuery.error ? (
        <div className="admin-panel-inner">
          <ErrorState
            message={searchQuery.error}
            onRetry={() => searchQuery.refresh()}
          />
        </div>
      ) : rows.length === 0 ? (
        <div className="admin-panel-inner">
          <EmptyState
            icon={Search}
            compact
            title="没有匹配的术语"
            description="试试原词里的关键字；也可以切换到「全部类型」查看全部术语。"
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
            <table className="data-table quota-table terms-table">
              <thead>
                <tr>
                  <th className="term-kind-col">类型</th>
                  <th>术语</th>
                  <th>备注</th>
                  <th className="term-enabled-col">启用</th>
                  <th className="quota-col-actions">操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => {
                  const meta = kindMeta(item.kind)
                  return (
                    <tr key={item.id}>
                      <td>
                        <Badge tone={meta.tone} subtle>
                          {meta.label}
                        </Badge>
                        {!item.enabled && <span className="cell-sub">已停用</span>}
                      </td>
                      <td>
                        <div className="term-flow">
                          <span className="term-source">{item.source}</span>
                          {item.kind === 'replace' && (
                            <>
                              <ArrowRight size={13} aria-hidden />
                              <span
                                className={className(
                                  'term-target',
                                  !item.target && 'is-empty',
                                )}
                              >
                                {item.target || '待补充建议写法'}
                              </span>
                            </>
                          )}
                        </div>
                        {item.updated_at && (
                          <span className="cell-sub">
                            更新于 {formatRelative(item.updated_at)}
                          </span>
                        )}
                      </td>
                      <td>
                        {item.note || <span className="subtle">—</span>}
                      </td>
                      <td className="term-enabled-col">
                        <label
                          className={className(
                            'quota-switch',
                            (togglingId === item.id || deletingId !== null) &&
                              'is-disabled',
                          )}
                        >
                          <input
                            type="checkbox"
                            checked={item.enabled}
                            disabled={togglingId === item.id || deletingId !== null}
                            onChange={(event) =>
                              void toggleEnabled(item, event.target.checked)
                            }
                            aria-label={`${item.enabled ? '停用' : '启用'}术语 ${item.source}`}
                          />
                          <span className="quota-switch-track" aria-hidden>
                            <span />
                          </span>
                        </label>
                      </td>
                      <td className="quota-col-actions">
                        <div className="admin-row-actions">
                          <Button
                            variant="ghost"
                            size="sm"
                            icon={<Pencil size={12} />}
                            disabled={deletingId !== null}
                            onClick={() => setModal({ mode: 'edit', term: item })}
                          >
                            编辑
                          </Button>
                          <IconButton
                            label={`删除术语 ${item.source}`}
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
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="admin-footnote">
        术语只影响正文核对提示，不会自动改写章节内容；停用的条目保留在库里但不参与检查。
        {terms.length >= TERM_LIMIT && ' 列表最多显示前 200 条，输入关键字可搜索更多。'}
      </div>

      {modal && (
        <AdminTermModal
          open
          state={modal}
          onClose={() => setModal(null)}
          onSaved={onSaved}
        />
      )}
    </div>
  )
}

const TERM_MAX = {
  source: 120,
  target: 120,
  note: 200,
} as const

interface AdminTermModalProps {
  open: boolean
  state: TermModalState
  onClose: () => void
  onSaved: (term: TermItem, created: boolean) => void
}

/** 新建 / 编辑一条企业术语；replace 必须给出建议写法，否则核对时没有可执行的目标。 */
function AdminTermModal({ open, state, onClose, onSaved }: AdminTermModalProps) {
  const editing = state.mode === 'edit' ? state.term : undefined

  const [kind, setKind] = useState<'replace' | 'forbidden'>('replace')
  const [source, setSource] = useState('')
  const [target, setTarget] = useState('')
  const [note, setNote] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) return
    setSaving(false)
    setError(null)
    setTouched(false)
    setKind(editing?.kind === 'forbidden' ? 'forbidden' : 'replace')
    setSource(editing?.source ?? '')
    setTarget(editing?.target ?? '')
    setNote(editing?.note ?? '')
    setEnabled(editing?.enabled ?? true)
  }, [open, editing])

  const sourceMissing = !source.trim()
  const targetMissing = kind === 'replace' && !target.trim()

  const submit = async () => {
    setTouched(true)
    if (sourceMissing) return
    if (targetMissing) {
      setError('「建议替换」需要填写建议写法。')
      return
    }
    const payload: TermInput = {
      kind,
      source: source.trim(),
      target: kind === 'replace' ? target.trim() : '',
      note: note.trim(),
      enabled,
    }
    setSaving(true)
    setError(null)
    try {
      const saved = editing
        ? await updateTerm(editing.id, payload)
        : await createTerm(payload)
      onSaved(saved, !editing)
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={saving ? () => undefined : onClose}
      title={editing ? '编辑术语' : '新增术语'}
      eyebrow="企业术语库"
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="term-form"
            loading={saving}
            disabled={!source.trim() && touched}
          >
            {editing ? '保存修改' : '新增术语'}
          </Button>
        </>
      }
    >
      <form
        id="term-form"
        className="admin-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="admin-form-note">
          <BookMarked size={15} aria-hidden />
          <span>
            术语按「类型 + 原词」唯一。禁用表述只填原词；建议替换需要同时填写企业认可的建议写法。
          </span>
        </div>

        <div className="admin-form-grid">
          <div className="form-field">
            <label htmlFor="term-kind">
              类型 <span className="required-mark">必填</span>
            </label>
            <select
              id="term-kind"
              className="input"
              value={kind}
              onChange={(event) => {
                setKind(event.target.value === 'forbidden' ? 'forbidden' : 'replace')
                if (error) setError(null)
              }}
              disabled={saving}
            >
              <option value="replace">建议替换</option>
              <option value="forbidden">禁用表述</option>
            </select>
            <p className="field-hint">
              {kind === 'replace'
                ? '提示把旧写法改成更规范的说法。'
                : '正文里出现该表述即被标记。'}
            </p>
          </div>

          <div className="form-field">
            <label htmlFor="term-source">
              原词 <span className="required-mark">必填</span>
            </label>
            <input
              id="term-source"
              className={className('input', touched && sourceMissing && 'input--error')}
              value={source}
              onChange={(event) => {
                setSource(event.target.value)
                if (error) setError(null)
              }}
              placeholder={kind === 'replace' ? '例如：甲方' : '例如：绝对领先'}
              maxLength={TERM_MAX.source}
              autoComplete="off"
              data-autofocus
              disabled={saving}
            />
            <p className="field-hint">不超过 {TERM_MAX.source} 字。</p>
            {touched && sourceMissing && <span className="field-error">请填写原词。</span>}
          </div>

          {kind === 'replace' && (
            <div className="form-field admin-form-span">
              <label htmlFor="term-target">
                建议写法 <span className="required-mark">必填</span>
              </label>
              <input
                id="term-target"
                className={className(
                  'input',
                  touched && targetMissing && 'input--error',
                )}
                value={target}
                onChange={(event) => {
                  setTarget(event.target.value)
                  if (error) setError(null)
                }}
                placeholder="例如：招标人"
                maxLength={TERM_MAX.target}
                autoComplete="off"
                disabled={saving}
              />
              <p className="field-hint">正文核对会把原词指向这条建议写法。</p>
              {touched && targetMissing && (
                <span className="field-error">请填写建议写法。</span>
              )}
            </div>
          )}

          <div className="form-field admin-form-span">
            <label htmlFor="term-note">
              备注 <span className="optional-mark">可选</span>
            </label>
            <input
              id="term-note"
              className="input"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="适用范围、依据等"
              maxLength={TERM_MAX.note}
              autoComplete="off"
              disabled={saving}
            />
          </div>

          <div className="form-field admin-form-span">
            <label className="quota-switch">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(event) => setEnabled(event.target.checked)}
                disabled={saving}
              />
              <span className="quota-switch-track" aria-hidden>
                <span />
              </span>
              <span className="plugin-switch-text">
                {enabled ? '启用（参与正文核对）' : '停用（仅保留在库里）'}
              </span>
            </label>
          </div>
        </div>

        {error && (
          <div className="form-alert" role="alert">
            {error}
          </div>
        )}
      </form>
    </Modal>
  )
}

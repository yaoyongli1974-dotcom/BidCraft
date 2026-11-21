import { useEffect, useMemo, useState } from 'react'
import { Plus, RefreshCw, Save, SpellCheck, Trash2, Undo2 } from 'lucide-react'
import type { TextQaSettings, TypoRule } from '../../lib/types'
import { TYPO_RULE_LIMIT, TYPO_RULE_MAX, saveTextQa } from '../../api/compliance'
import { messageOf } from '../../lib/http'
import { className } from '../../lib/format'
import { useSaveShortcut, useUnsavedGuard } from '../../lib/hooks'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'
import { Button, IconButton } from '../../components/Button'
import { EmptyState, ErrorState } from '../../components/Feedback'
import {
  AdminActionError,
  AdminSectionLabel,
  AdminSkeletonRows,
} from './AdminShared'

interface TextQaTabProps {
  settings: TextQaSettings | undefined
  loading: boolean
  refreshing: boolean
  error: string | null
  refresh: () => void
  /** 保存成功后写回控制台缓存，页签计数与表格同步刷新。 */
  onSaved: (next: TextQaSettings) => void
}

/** 行内编辑需要稳定 key：按索引复用输入框会在增删行时把焦点串到别的行。 */
interface DraftRow extends TypoRule {
  key: string
}

type RowIssue = { kind: 'empty' | 'long' | 'duplicate'; message: string }

let rowSeq = 0
const nextRowKey = () => `typo-row-${(rowSeq += 1)}`

const toDraft = (rules: TypoRule[]): DraftRow[] =>
  rules.map((rule) => ({ ...rule, key: nextRowKey() }))

const toRules = (rows: DraftRow[]): TypoRule[] =>
  rows.map((row) => ({ wrong: row.wrong, right: row.right, note: row.note }))

/**
 * 逐行前端校验：错词必填、不超过 20 字、按错词去重。重复的两行互相标注，
 * 保存时整体拦截（后端同样按 wrong 去重，这里提前给出可读的行号提示）。
 */
function validateRows(rows: DraftRow[]): Array<RowIssue | null> {
  const issues: Array<RowIssue | null> = rows.map(() => null)
  const firstIndex = new Map<string, number>()
  rows.forEach((row, index) => {
    const wrong = row.wrong.trim()
    if (!wrong) {
      issues[index] = { kind: 'empty', message: '错词必填' }
      return
    }
    if (wrong.length > TYPO_RULE_MAX.wrong) {
      issues[index] = {
        kind: 'long',
        message: `错词不超过 ${TYPO_RULE_MAX.wrong} 字`,
      }
      return
    }
    const first = firstIndex.get(wrong)
    if (first !== undefined) {
      issues[index] = { kind: 'duplicate', message: `与第 ${first + 1} 行重复` }
      if (!issues[first]) {
        issues[first] = { kind: 'duplicate', message: `与第 ${index + 1} 行重复` }
      }
      return
    }
    firstIndex.set(wrong, index)
  })
  return issues
}

/**
 * 文本规则（错别字词库）页签：整表覆盖式编辑 + 一次保存。
 * 内置词库只读（只展示条数），自定义规则优先级更高，命中计入 TXT-### 问题。
 */
export function AdminTextQaTab({
  settings,
  loading,
  refreshing,
  error,
  refresh,
  onSaved,
}: TextQaTabProps) {
  const toast = useToast()
  const confirm = useConfirm()
  const saved = settings?.typos
  const savedCount = saved?.length ?? 0
  const builtinCount = settings?.builtin_count ?? 0

  const [rows, setRows] = useState<DraftRow[]>([])
  const [saving, setSaving] = useState(false)
  const [touched, setTouched] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  // 服务端数据到达 / 保存成功 / 手动刷新后，用已保存的规则重置草稿。
  useEffect(() => {
    setRows(toDraft(saved ?? []))
    setTouched(false)
  }, [saved])

  const issues = useMemo(() => validateRows(rows), [rows])
  const dirty = useMemo(() => {
    const baseline = saved ?? []
    if (baseline.length !== rows.length) return true
    return rows.some((row, index) => {
      const base = baseline[index]
      return (
        row.wrong !== base.wrong || row.right !== base.right || row.note !== base.note
      )
    })
  }, [rows, saved])

  const overLimit = rows.length > TYPO_RULE_LIMIT
  const full = rows.length >= TYPO_RULE_LIMIT

  const patchRow = (key: string, patch: Partial<TypoRule>) => {
    setRows((current) =>
      current.map((row) => (row.key === key ? { ...row, ...patch } : row)),
    )
  }

  const addRow = () => {
    if (saving || full) return
    setActionError(null)
    setRows((current) => [
      ...current,
      { key: nextRowKey(), wrong: '', right: '', note: '' },
    ])
  }

  const removeRow = (key: string) => {
    if (saving) return
    setRows((current) => current.filter((row) => row.key !== key))
  }

  const save = async () => {
    if (saving || !dirty) return
    setTouched(true)
    if (overLimit) {
      setActionError(
        `自定义规则最多 ${TYPO_RULE_LIMIT} 条，当前 ${rows.length} 条，请删减后再保存。`,
      )
      return
    }
    const bad = issues.findIndex((issue) => issue !== null)
    if (bad >= 0) {
      setActionError(`第 ${bad + 1} 行：${issues[bad]?.message}。请修正后再保存。`)
      return
    }
    setSaving(true)
    setActionError(null)
    try {
      const next = await saveTextQa(toRules(rows))
      onSaved(next)
      toast.success(
        '文本规则已保存',
        `${next.count} 条自定义规则 · ${next.builtin_count} 条内置规则仍在生效`,
      )
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('保存文本规则失败', message)
    } finally {
      setSaving(false)
    }
  }

  const discard = async () => {
    if (saving || !dirty) return
    const accepted = await confirm({
      title: '放弃未保存的修改？',
      description: '表格会恢复为最近一次保存的自定义规则，本次改动不会写入后端。',
      confirmLabel: '放弃修改',
      tone: 'danger',
    })
    if (!accepted) return
    setRows(toDraft(saved ?? []))
    setTouched(false)
    setActionError(null)
  }

  const reload = async () => {
    if (saving || refreshing) return
    if (dirty) {
      const accepted = await confirm({
        title: '刷新会丢弃未保存的修改？',
        description: '重新读取后，服务端已保存的规则会覆盖当前表格里的改动。',
        confirmLabel: '丢弃并刷新',
        tone: 'danger',
      })
      if (!accepted) return
    }
    refresh()
  }

  useUnsavedGuard(dirty && !saving, {
    title: '文本规则尚未保存',
    description:
      '离开管理控制台会丢失尚未保存的错别字规则。可以先留在本页保存，再继续离开。',
    confirmLabel: '放弃修改并离开',
  })
  useSaveShortcut(() => void save(), dirty && !saving)

  // 删空所有行也算一种待保存的改动：此时仍要给出保存入口，不能落进空态。
  const view: 'error' | 'loading' | 'empty' | 'table' =
    error && rows.length === 0 && !dirty
      ? 'error'
      : loading && rows.length === 0
        ? 'loading'
        : rows.length === 0 && !dirty
          ? 'empty'
          : 'table'

  return (
    <div className="admin-textqa">
      <div className="admin-tab-head">
        <AdminSectionLabel
          eyebrow="写作规范"
          title="文本规则 · 错别字词库"
          description="维护企业自定义的错别字对照。内置词库始终生效且不可删除；同一个错词以自定义规则为准。命中的错别字会计入正文核对的 TXT-### 问题。"
          aside={
            <Button
              variant="secondary"
              icon={<RefreshCw size={15} className={refreshing ? 'spin' : undefined} />}
              disabled={refreshing || saving}
              onClick={() => void reload()}
            >
              刷新
            </Button>
          }
        />
      </div>

      {actionError && (
        <AdminActionError message={actionError} onClose={() => setActionError(null)} />
      )}

      <div className="admin-callout admin-callout--ok">
        <SpellCheck size={18} />
        <div>
          <strong>
            {loading
              ? '正在读取文本规则…'
              : `${savedCount} 条自定义规则 · ${builtinCount} 条内置规则`}
          </strong>
          <p>
            内置错别字词库不可删除；自定义规则优先级更高——同一个错词以这里填写的「改为」与备注为准。
            「改为」留空时只提示错词、不给替换建议。
          </p>
        </div>
      </div>

      {error && rows.length > 0 && <ErrorState message={error} onRetry={refresh} compact />}

      {view === 'error' ? (
        <div className="admin-panel-inner">
          <ErrorState message={error || '读取文本规则失败'} onRetry={refresh} />
        </div>
      ) : view === 'loading' ? (
        <AdminSkeletonRows rows={4} />
      ) : view === 'empty' ? (
        <div className="admin-panel-inner">
          <EmptyState
            icon={SpellCheck}
            title="还没有自定义错别字规则"
            description="内置词库仍在生效。把企业文稿里常见的错别字录进来，正文核对时会一并提示。"
            actions={
              <Button
                variant="primary"
                icon={<Plus size={15} />}
                onClick={addRow}
                disabled={saving}
              >
                添加第一行
              </Button>
            }
          />
          <div className="admin-empty-note">
            内置词库已有 {builtinCount} 条规则；这里只维护企业自己的对照，保存后整体覆盖旧列表。
          </div>
        </div>
      ) : (
        <div className="admin-panel-inner">
          <div className="table-scroll">
            <table className="data-table quota-table textqa-table">
              <thead>
                <tr>
                  <th className="textqa-col-wrong">
                    错词 <span className="required-mark">必填</span>
                  </th>
                  <th className="textqa-col-right">改为</th>
                  <th>备注</th>
                  <th className="textqa-col-action">操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={4} className="textqa-cleared">
                      已清空全部自定义规则，点击「保存规则」后生效；内置词库不受影响。
                    </td>
                  </tr>
                )}
                {rows.map((row, index) => {
                  const issue = issues[index]
                  // 重复即时提示（可立刻改），空错词只在尝试保存后才标红，避免新行一进来就报错。
                  const showIssue =
                    issue !== null && (touched || issue.kind === 'duplicate')
                  return (
                    <tr key={row.key}>
                      <td>
                        <input
                          className={className(
                            'input',
                            'input--sm',
                            showIssue && 'input--error',
                          )}
                          value={row.wrong}
                          maxLength={TYPO_RULE_MAX.wrong}
                          placeholder="例如：帐号"
                          autoComplete="off"
                          disabled={saving}
                          aria-label={`第 ${index + 1} 行错词`}
                          aria-invalid={showIssue || undefined}
                          onChange={(event) =>
                            patchRow(row.key, { wrong: event.target.value })
                          }
                        />
                        {issue && showIssue && (
                          <span className="field-error textqa-row-error">
                            {issue.message}
                          </span>
                        )}
                      </td>
                      <td>
                        <input
                          className="input input--sm"
                          value={row.right}
                          maxLength={TYPO_RULE_MAX.right}
                          placeholder="例如：账号（留空只提示）"
                          autoComplete="off"
                          disabled={saving}
                          aria-label={`第 ${index + 1} 行改为`}
                          onChange={(event) =>
                            patchRow(row.key, { right: event.target.value })
                          }
                        />
                      </td>
                      <td>
                        <input
                          className="input input--sm"
                          value={row.note}
                          maxLength={TYPO_RULE_MAX.note}
                          placeholder="适用范围、依据等"
                          autoComplete="off"
                          disabled={saving}
                          aria-label={`第 ${index + 1} 行备注`}
                          onChange={(event) =>
                            patchRow(row.key, { note: event.target.value })
                          }
                        />
                      </td>
                      <td className="textqa-col-action">
                        <IconButton
                          label={`删除第 ${index + 1} 行规则`}
                          tone="danger"
                          disabled={saving}
                          onClick={() => removeRow(row.key)}
                        >
                          <Trash2 size={14} />
                        </IconButton>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {view === 'table' && (
        <div className="textqa-foot">
          <div className="textqa-foot-left">
            <Button
              variant="secondary"
              icon={<Plus size={15} />}
              onClick={addRow}
              disabled={saving || full}
              title={full ? `最多 ${TYPO_RULE_LIMIT} 条` : undefined}
            >
              添加一行
            </Button>
            <span className="textqa-count">
              {rows.length} / {TYPO_RULE_LIMIT} 条
            </span>
          </div>
          <div className="textqa-foot-actions">
            {dirty && <span className="unsaved-chip">未保存</span>}
            {dirty && (
              <Button
                variant="ghost"
                icon={<Undo2 size={14} />}
                disabled={saving}
                onClick={() => void discard()}
              >
                放弃修改
              </Button>
            )}
            <Button
              variant="primary"
              icon={<Save size={15} />}
              loading={saving}
              disabled={!dirty || saving}
              title={!dirty ? '改动后可保存' : 'Ctrl/⌘ + S 也可保存'}
              onClick={() => void save()}
            >
              保存规则
            </Button>
          </div>
        </div>
      )}

      <div className="admin-footnote">
        错词必填且不超过 {TYPO_RULE_MAX.wrong} 字，「改为」与备注分别不超过{' '}
        {TYPO_RULE_MAX.right} / {TYPO_RULE_MAX.note} 字；同一个错词只保留第一条。
        自定义规则最多 {TYPO_RULE_LIMIT} 条，保存会整体覆盖旧列表，内置词库不受影响。
        {overLimit && ` 当前已超出 ${rows.length - TYPO_RULE_LIMIT} 条，请删减后再保存。`}
      </div>
    </div>
  )
}

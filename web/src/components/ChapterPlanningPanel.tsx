import { useEffect, useState } from 'react'
import {
  AlertTriangle,
  History,
  RefreshCw,
  Save,
  Sparkles,
  Wand2,
} from 'lucide-react'
import {
  confirmChapterPlan,
  generateChapterPlan,
  listChapterPlanHistory,
  regenerateChapterPlan,
  updateChapterPlan,
} from '../api/generation'
import { Badge } from './Badge'
import { Button } from './Button'
import { KnowledgeRefList } from './RetrievalTraceBadges'
import { useToast } from '../context/useToast'
import {
  chapterPlanGate,
  isConfirmedPlan,
  planItems,
  planStatusLabel,
  type ChapterPlanMode,
} from '../lib/chapterPlan'
import { formatDate } from '../lib/format'
import { messageOf } from '../lib/http'
import { canReviewChapter } from '../lib/permissions'
import type { ChapterPlan, Project } from '../lib/types'
import {
  batchConfirmPlans,
  batchGeneratePlans,
  missingPlanNodeIds,
  pendingConfirmNodeIds,
  type ChapterPlanEntry,
  type PlanBatchOutcome,
  type PlanBatchProgress,
} from '../hooks/useChapterPlans'

interface ChapterOption {
  id?: string
  heading: string
}

interface ChapterPlanningPanelProps {
  /** 项目 ref；不能命名为 `ref`，React 会把字符串 ref 拦截。 */
  projectRef: string
  chapters: ChapterOption[]
  project: Project | null | undefined
  canWrite: boolean
  readOnly: boolean
  entries: ChapterPlanEntry[]
  mode: ChapterPlanMode
  loading: boolean
  error: string | null
  onRefresh: () => void
  onPlanChange: (nodeId: string, plan: ChapterPlan) => void
  /** 大纲存在未保存修改时禁止批量操作，避免按旧的章节 ID 生成规划。 */
  outlineDirty?: boolean
  /** 批量结束后由页面刷新大纲与项目状态（章节规划本身由 onRefresh 刷新）。 */
  onBatchComplete?: () => void | Promise<void>
}

type BusyAction =
  | 'generate'
  | 'regenerate'
  | 'save'
  | 'confirm'
  | 'history'
  | 'batch-generate'
  | 'batch-confirm'
  | null

interface BatchCopy {
  start: string
  done: string
  partial: string
  empty: string
  emptyDetail: string
}

type EditableField = 'objective' | 'strategy' | 'measures' | 'quality_criteria'

const EDITABLE_FIELDS: ReadonlyArray<{ key: EditableField; label: string; placeholder: string }> = [
  { key: 'objective', label: '章节规划目标', placeholder: '这一章要达成的目标' },
  { key: 'strategy', label: '章节规划策略', placeholder: '每行一条写作策略' },
  { key: 'measures', label: '章节规划措施', placeholder: '每行一条落实措施' },
  { key: 'quality_criteria', label: '章节规划质量标准', placeholder: '每行一条质量标准' },
]

const DISPLAY_FIELDS: ReadonlyArray<{ key: keyof ChapterPlan; label: string }> = [
  { key: 'score_points', label: '评分点' },
  { key: 'requirements', label: '要求' },
  { key: 'conditions', label: '条件' },
  { key: 'challenges', label: '挑战' },
  { key: 'strategy', label: '策略' },
  { key: 'measures', label: '措施' },
  { key: 'knowledge_refs', label: '知识引用' },
  { key: 'fact_refs', label: '事实引用' },
  { key: 'planned_tables', label: '计划表格' },
  { key: 'planned_diagrams', label: '计划图表' },
  { key: 'forbidden_assumptions', label: '禁止假设' },
  { key: 'quality_criteria', label: '质量标准' },
]

function splitLines(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

function draftFromPlan(plan: ChapterPlan | null): Record<EditableField, string> {
  const text = (list: unknown[] | undefined) => planItems(list).join('\n')
  return {
    objective: plan?.objective || '',
    strategy: text(plan?.strategy),
    measures: text(plan?.measures),
    quality_criteria: text(plan?.quality_criteria),
  }
}

/** 目录大纲页内的章节规划面板；不新增路由，也不接管工作区状态。 */
export function ChapterPlanningPanel({
  projectRef,
  chapters,
  project,
  canWrite,
  readOnly,
  entries,
  mode,
  loading,
  error,
  onRefresh,
  onPlanChange,
  outlineDirty = false,
  onBatchComplete,
}: ChapterPlanningPanelProps) {
  const toast = useToast()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [instruction, setInstruction] = useState('')
  const [confirmNote, setConfirmNote] = useState('')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Record<EditableField, string>>(() => draftFromPlan(null))
  const [history, setHistory] = useState<ChapterPlan[] | null>(null)
  const [busy, setBusy] = useState<BusyAction>(null)
  const [batchProgress, setBatchProgress] = useState<{
    done: number
    total: number
    label: string
  } | null>(null)

  const chapterOptions = chapters.filter((chapter) => Boolean(chapter.id))
  const selectedChapter = chapterOptions.find((chapter) => chapter.id === selectedId)
  const plan = entries.find((entry) => entry.node_id === selectedId)?.plan ?? null
  const mutable = canWrite && !readOnly
  const reviewAllowed = mutable && canReviewChapter(project)
  const confirmed = isConfirmedPlan(plan)
  const gate = chapterPlanGate(mode, plan, selectedChapter?.heading)
  const missingIds = new Set(missingPlanNodeIds(chapters, entries))
  const pendingIds = new Set(pendingConfirmNodeIds(chapters, entries))
  const batchGenerateTargets = chapterOptions.filter((chapter) => chapter.id && missingIds.has(chapter.id))
  const batchConfirmTargets = chapterOptions.filter((chapter) => chapter.id && pendingIds.has(chapter.id))

  useEffect(() => {
    if (!selectedId || !chapterOptions.some((chapter) => chapter.id === selectedId)) {
      setSelectedId(chapterOptions[0]?.id ?? null)
    }
    // 章节 ID 变化时切换选中项；标题编辑不应打断正在填写的规划。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chapters, selectedId])

  useEffect(() => {
    setEditing(false)
    setHistory(null)
    setDraft(draftFromPlan(null))
    setInstruction('')
    setConfirmNote('')
  }, [selectedId])

  const run = async (
    action: Exclude<BusyAction, null>,
    request: () => Promise<ChapterPlan>,
    successTitle: string,
  ) => {
    if (!selectedId || !mutable || busy) return
    setBusy(action)
    try {
      const next = await request()
      onPlanChange(selectedId, next)
      setEditing(false)
      toast.success(successTitle, `版本 ${next.version ?? '未记录'} · ${planStatusLabel(next.status)}`)
    } catch (failure) {
      toast.error(`${successTitle}失败`, messageOf(failure))
    } finally {
      setBusy(null)
    }
  }

  const generate = () =>
    void run(
      'generate',
      () =>
        instruction.trim()
          ? generateChapterPlan(projectRef, selectedId as string, {
              user_instruction: instruction.trim(),
            })
          : generateChapterPlan(projectRef, selectedId as string),
      '生成章节规划',
    )

  const regenerate = () =>
    void run(
      'regenerate',
      () =>
        instruction.trim()
          ? regenerateChapterPlan(projectRef, selectedId as string, {
              user_instruction: instruction.trim(),
            })
          : regenerateChapterPlan(projectRef, selectedId as string),
      '重新生成章节规划',
    )

  const save = () => {
    if (!selectedId || !mutable || busy) return
    const updates: Record<string, unknown> = {}
    if (draft.objective !== (plan?.objective ?? '')) updates.objective = draft.objective
    for (const field of ['strategy', 'measures', 'quality_criteria'] as const) {
      const next = splitLines(draft[field])
      if (JSON.stringify(next) !== JSON.stringify(plan?.[field] ?? [])) updates[field] = next
    }
    if (Object.keys(updates).length === 0) {
      setEditing(false)
      toast.info('没有需要保存的修改', '规划内容没有变化。')
      return
    }
    void run(
      'save',
      () => updateChapterPlan(projectRef, selectedId as string, { updates }),
      '保存章节规划',
    )
  }

  const confirm = () => {
    if (!selectedId || !reviewAllowed || busy) return
    const note = confirmNote.trim()
    void run(
      'confirm',
      () =>
        note
          ? confirmChapterPlan(projectRef, selectedId as string, { note })
          : confirmChapterPlan(projectRef, selectedId as string),
      '确认章节规划',
    )
  }

  const showHistory = () => {
    if (!selectedId || !mutable || busy) return
    if (history !== null) {
      setHistory(null)
      return
    }
    setBusy('history')
    void listChapterPlanHistory(projectRef, selectedId)
      .then((plans) => setHistory(plans))
      .catch((failure) => toast.error('读取规划历史失败', messageOf(failure)))
      .finally(() => setBusy(null))
  }

  /**
   * 批量操作复用单章 API：逐章顺序执行、逐章回写、单章失败不中断；
   * 生成与确认严格分开，生成后绝不自动确认。
   */
  const runBatch = async (
    action: 'batch-generate' | 'batch-confirm',
    targets: ChapterOption[],
    copy: BatchCopy,
    execute: (onProgress: PlanBatchProgress) => Promise<PlanBatchOutcome>,
  ) => {
    if (!mutable || outlineDirty || busy) return
    if (action === 'batch-confirm' && !reviewAllowed) return
    if (targets.length === 0) {
      toast.info(copy.empty, copy.emptyDetail)
      return
    }
    setBusy(action)
    setBatchProgress({ done: 0, total: targets.length, label: '' })
    try {
      toast.info(copy.start, `共 ${targets.length} 章，正在依次处理，不会自动确认。`)
      const outcome = await execute((done, total, nodeId) => {
        const heading = targets.find((chapter) => chapter.id === nodeId)?.heading || nodeId
        setBatchProgress({ done, total, label: heading })
      })
      const failedLabels = outcome.failed
        .map(
          (failure) =>
            targets.find((chapter) => chapter.id === failure.nodeId)?.heading || failure.nodeId,
        )
        .join('、')
      if (outcome.failed.length === 0) {
        toast.success(
          copy.done,
          `成功 ${outcome.succeeded.length} 章；规划未自动确认，请复核后确认。`,
        )
      } else {
        toast.warning(
          copy.partial,
          `成功 ${outcome.succeeded.length} 章，失败 ${outcome.failed.length} 章：${failedLabels}`,
        )
      }
    } finally {
      setBusy(null)
      setBatchProgress(null)
      onRefresh()
      if (onBatchComplete) {
        void Promise.resolve(onBatchComplete()).catch((failure) =>
          toast.error('刷新项目状态失败', messageOf(failure)),
        )
      }
    }
  }

  const batchGenerate = () =>
    void runBatch(
      'batch-generate',
      batchGenerateTargets,
      {
        start: '批量生成缺失规划',
        done: '批量生成完成',
        partial: '批量生成部分完成',
        empty: '没有需要生成的章节规划',
        emptyDetail: '所有章节都已有规划，或还没有真实章节 ID。',
      },
      (onProgress) =>
        batchGeneratePlans(
          projectRef,
          batchGenerateTargets.map((chapter) => chapter.id as string),
          onPlanChange,
          onProgress,
        ),
    )

  const batchConfirm = () =>
    void runBatch(
      'batch-confirm',
      batchConfirmTargets,
      {
        start: '批量确认待确认规划',
        done: '批量确认完成',
        partial: '批量确认部分完成',
        empty: '没有待确认的章节规划',
        emptyDetail: '只有草稿、已更新或待复核的规划可以批量确认。',
      },
      (onProgress) =>
        batchConfirmPlans(
          projectRef,
          batchConfirmTargets.map((chapter) => chapter.id as string),
          confirmNote,
          onPlanChange,
          onProgress,
        ),
    )

  const validationErrors = planItems(plan?.validation_errors)

  return (
    <section className="panel chapter-planning" aria-label="章节规划">
      <div className="config-head">
        <div>
          <div className="eyebrow">章节规划</div>
          <h3>逐章规划与确认</h3>
        </div>
        <span className="subtle">
          {mode === 'auto' ? '自动模式：规划不要求人工确认' : '复核模式：正文生成前需确认本章规划'}
        </span>
      </div>

      <div className="plan-toolbar">
        <label htmlFor="plan-chapter-select">规划章节</label>
        <select
          id="plan-chapter-select"
          className="input"
          value={selectedId ?? ''}
          onChange={(event) => setSelectedId(event.target.value || null)}
        >
          {chapterOptions.length === 0 && <option value="">（还没有可规划的章节）</option>}
          {chapterOptions.map((chapter) => (
            <option key={chapter.id} value={chapter.id}>
              {chapter.heading || '未命名章节'}
            </option>
          ))}
        </select>
        <input
          className="input"
          aria-label="规划补充指令"
          placeholder="补充要求（可选）"
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          readOnly={!mutable}
        />
        <input
          className="input"
          aria-label="批量确认备注"
          placeholder="批量确认备注（可选）"
          value={confirmNote}
          onChange={(event) => setConfirmNote(event.target.value)}
          readOnly={!reviewAllowed || outlineDirty}
        />
        <div className="plan-toolbar-actions">
          <Button
            size="sm"
            variant="secondary"
            icon={<Wand2 size={14} />}
            loading={busy === 'generate'}
            disabled={!mutable || !selectedId || Boolean(busy)}
            title={!mutable ? '只读成员不能生成规划' : !selectedId ? '请先选择章节' : undefined}
            onClick={generate}
          >
            生成规划
          </Button>
          <Button
            size="sm"
            variant="secondary"
            icon={<Wand2 size={14} />}
            loading={busy === 'batch-generate'}
            disabled={!mutable || outlineDirty || Boolean(busy)}
            title={
              !mutable
                ? '只读成员不能生成规划'
                : outlineDirty
                  ? '请先保存大纲再批量生成'
                  : undefined
            }
            onClick={batchGenerate}
          >
            批量生成缺失规划
          </Button>
          <Button
            size="sm"
            variant={reviewAllowed ? 'primary' : 'secondary'}
            icon={<Sparkles size={14} />}
            loading={busy === 'batch-confirm'}
            disabled={!reviewAllowed || outlineDirty || Boolean(busy)}
            title={
              !mutable
                ? '只读成员不能确认规划'
                : !reviewAllowed
                  ? '仅项目负责人可以批量确认规划'
                  : outlineDirty
                    ? '请先保存大纲再批量确认'
                    : undefined
            }
            onClick={batchConfirm}
          >
            确认全部待确认
          </Button>
          <Button
            size="sm"
            variant="secondary"
            icon={<RefreshCw size={14} />}
            loading={busy === 'regenerate'}
            disabled={!mutable || !selectedId || !plan || Boolean(busy)}
            title={!plan ? '本章还没有规划，请先生成' : undefined}
            onClick={regenerate}
          >
            重新生成
          </Button>
          <Button
            size="sm"
            variant="secondary"
            icon={<Save size={14} />}
            disabled={!mutable || !selectedId || !plan || Boolean(busy)}
            title={!mutable ? '只读成员不能修改规划' : !plan ? '请先生成规划' : undefined}
            onClick={() => {
              if (!plan) return
              setDraft(draftFromPlan(plan))
              setEditing(true)
              toast.info('已进入编辑', '修改目标、策略、措施或质量标准后点击保存。')
            }}
          >
            编辑
          </Button>
          <Button
            size="sm"
            variant="secondary"
            icon={<History size={14} />}
            loading={busy === 'history'}
            disabled={!mutable || !selectedId || Boolean(busy)}
            onClick={showHistory}
          >
            {history !== null ? '收起历史' : '查看历史'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon={<RefreshCw size={14} />}
            disabled={!mutable || !selectedId || Boolean(busy)}
            onClick={() => {
              onRefresh()
              toast.info('已刷新', '正在重新读取章节规划。')
            }}
          >
            刷新
          </Button>
        </div>
      </div>

      {batchProgress && (
        <p className="plan-state-line" role="status" aria-live="polite">
          <span className="subtle">
            批量进度 {batchProgress.done}/{batchProgress.total}
            {batchProgress.label ? ` · ${batchProgress.label}` : ''}
          </span>
        </p>
      )}

      {error && (
        <div className="inline-gate" role="alert">
          <AlertTriangle size={15} />
          <span>读取章节规划失败：{error}</span>
        </div>
      )}

      {!selectedId ? (
        <p className="plan-empty">请先生成目录大纲，再为章节生成规划。</p>
      ) : loading && !plan ? (
        <p className="plan-empty" aria-busy="true">正在读取章节规划…</p>
      ) : (
        <>
          <div className="plan-state-line">
            <Badge tone={confirmed ? 'success' : plan ? 'warn' : 'neutral'} subtle>
              {plan ? planStatusLabel(plan.status) : '未生成'}
            </Badge>
            <span>{plan ? (confirmed ? '已确认' : '未确认') : '未生成'}</span>
            <span className="subtle">
              版本 {plan?.version !== undefined ? `v${plan.version}` : '未记录'}
            </span>
            <span className="subtle">
              模式 {mode === 'auto' ? '自动规划' : mode === 'review_strict' ? '严格复核' : '人工复核'}
            </span>
          </div>

          {!plan && (
            <p className="plan-empty">本章规划未生成。点击「生成规划」开始。</p>
          )}

          {gate.blocked && (
            <div className="plan-gate" role="note">
              <AlertTriangle size={14} />
              <span>{gate.reason}</span>
            </div>
          )}
          {gate.warning && (
            <div className="plan-gate is-warning" role="note">
              <AlertTriangle size={14} />
              <span>{gate.warning}</span>
            </div>
          )}

          {plan && (
            <>
              <div className="plan-objective">
                <strong>规划目标</strong>
                <p>{plan.objective?.trim() || '未填写规划目标'}</p>
              </div>

              <dl className="plan-fields">
                {DISPLAY_FIELDS.map(({ key, label }) => {
                  const items = planItems(plan[key] as unknown[] | undefined)
                  return (
                    <div className="plan-field" key={key}>
                      <dt>{label}</dt>
                      <dd>
                        {key === 'knowledge_refs' ? (
                          <KnowledgeRefList refs={plan.knowledge_refs} />
                        ) : items.length > 0 ? (
                          <ul>
                            {items.map((item, index) => (
                              <li key={`${key}-${index}`}>{item}</li>
                            ))}
                          </ul>
                        ) : (
                          <span className="subtle">未填写</span>
                        )}
                      </dd>
                    </div>
                  )
                })}
              </dl>

              {validationErrors.length > 0 && (
                <div className="plan-errors" role="alert">
                  <strong>校验问题（{validationErrors.length}）</strong>
                  <ul>
                    {validationErrors.map((item, index) => (
                      <li key={`error-${index}`}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}

              {editing && (
                <div className="plan-editor">
                  <div className="eyebrow">人工调整</div>
                  {EDITABLE_FIELDS.map(({ key, label, placeholder }) => (
                    <label className="plan-edit-field" key={key}>
                      <span>{label}</span>
                      <textarea
                        className="input"
                        aria-label={label}
                        rows={key === 'objective' ? 2 : 3}
                        placeholder={placeholder}
                        value={draft[key]}
                        onChange={(event) =>
                          setDraft((current) => ({ ...current, [key]: event.target.value }))
                        }
                      />
                    </label>
                  ))}
                  <Button
                    size="sm"
                    variant="primary"
                    icon={<Save size={14} />}
                    loading={busy === 'save'}
                    disabled={!mutable || Boolean(busy)}
                    onClick={save}
                  >
                    保存修改
                  </Button>
                </div>
              )}

              <div className="plan-confirm-row">
                <input
                  className="input"
                  aria-label="规划确认备注"
                  placeholder="确认备注（可选）"
                  value={confirmNote}
                  onChange={(event) => setConfirmNote(event.target.value)}
                  readOnly={!reviewAllowed}
                />
                <Button
                  size="sm"
                  variant={confirmed ? 'secondary' : 'primary'}
                  icon={<Sparkles size={14} />}
                  loading={busy === 'confirm'}
                  disabled={!reviewAllowed || Boolean(busy)}
                  title={!mutable ? '只读成员不能确认规划' : !reviewAllowed ? '仅项目负责人可以确认规划' : undefined}
                  onClick={confirm}
                >
                  确认规划
                </Button>
              </div>

              {history !== null && (
                <div className="plan-history" role="region" aria-label="规划历史">
                  <div className="eyebrow">规划历史</div>
                  {history.length === 0 ? (
                    <p className="subtle">没有历史版本</p>
                  ) : (
                    <ol>
                      {history.map((item, index) => (
                        <li key={item.id || `history-${index}`}>
                          <span className="mono">
                            {item.version !== undefined ? `v${item.version}` : '版本未记录'}
                          </span>
                          <span>{planStatusLabel(item.status)}</span>
                          <span className="subtle">
                            {item.created_at ? formatDate(item.created_at) : '时间未记录'}
                          </span>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              )}
            </>
          )}
        </>
      )}

      <p className="plan-footnote">
        规划用于约束正文编排；确认后可生成正文，修改规划后需重新确认。
        批量生成只补齐缺失规划且不会自动确认；批量确认只处理草稿、已更新或待复核的规划。
      </p>
    </section>
  )
}

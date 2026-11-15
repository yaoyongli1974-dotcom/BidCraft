import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  FileText,
  ListTree,
  Package,
  Plus,
  Save,
  Sparkles,
  Target,
  Trash2,
  Wand2,
  type LucideIcon,
} from 'lucide-react'
import { useWorkspace } from '../WorkspacePage'
import {
  ApiError,
  generateChapter,
  generateOutline,
  getOutline,
  messageOf,
  saveOutline,
} from '../../lib/api'
import type { OutlineChapter } from '../../lib/types'
import {
  chapterStatus,
  className,
  formatNumber,
  formatWords,
  OUTLINE_MODES,
  outlineMode,
  percent,
  projectStats,
} from '../../lib/format'
import { useQuery, useSaveShortcut, useUnsavedGuard } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button, IconButton } from '../../components/Button'
import { ErrorState, Skeleton } from '../../components/Feedback'
import { AutoTextarea, TabHeading } from '../../components/Workbench'
import { useConfirm } from '../../context/ConfirmContext'
import { useToast } from '../../context/ToastContext'

interface EditableChapter {
  uid: string
  id?: string
  heading: string
  intent: string
  target_words: number
  status?: string
  words?: number
  has_body?: boolean
  needs?: string[]
}

const MODE_ICONS: Record<string, LucideIcon> = {
  auto: Wand2,
  package: Package,
  scoring: Target,
  'tender-format': FileText,
}

const WORD_PRESETS = [20000, 30000, 50000, 80000]

let newChapterCounter = 0

function toEditable(chapter: OutlineChapter): EditableChapter {
  return {
    uid: chapter.id,
    id: chapter.id,
    heading: chapter.heading || '',
    intent: chapter.intent || '',
    target_words: chapter.target_words || 0,
    status: chapter.status,
    words: chapter.words,
    has_body: chapter.has_body,
    needs: chapter.needs,
  }
}

function snapshot(chapters: EditableChapter[]): string {
  return JSON.stringify(
    chapters.map((chapter) => ({
      id: chapter.id,
      heading: chapter.heading,
      intent: chapter.intent,
      target_words: chapter.target_words,
    })),
  )
}

export function OutlineTab() {
  const { ref, basePath, project, reloadProject, setUnsaved, canWrite } = useWorkspace()
  const outlineQuery = useQuery(`outline:${ref}`, () => getOutline(ref))
  const confirm = useConfirm()
  const toast = useToast()
  const navigate = useNavigate()
  const [chapters, setChapters] = useState<EditableChapter[]>([])
  const [baseline, setBaseline] = useState('[]')
  const [initialized, setInitialized] = useState(false)
  const [mode, setMode] = useState<string>('auto')
  const [words, setWords] = useState(30000)
  const [generating, setGenerating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [generatingId, setGeneratingId] = useState<string | null>(null)
  const [invalidUids, setInvalidUids] = useState<Set<string>>(new Set())
  const initializedRef = useRef(false)

  const applyChapters = useCallback((list: OutlineChapter[]) => {
    const mapped = list.map(toEditable)
    setChapters(mapped)
    setBaseline(snapshot(mapped))
    setInitialized(true)
    initializedRef.current = true
  }, [])

  useEffect(() => {
    if (!outlineQuery.data) return
    applyChapters(outlineQuery.data)
    if (!initializedRef.current) {
      const total = outlineQuery.data.reduce(
        (sum, chapter) => sum + (chapter.target_words || 0),
        0,
      )
      if (total > 0) setWords(total)
    }
  }, [outlineQuery.data, applyChapters])

  const dirty = initialized && snapshot(chapters) !== baseline
  const hasBody = chapters.some((chapter) => chapter.has_body || (chapter.words || 0) > 0)
  const totalTarget = chapters.reduce((sum, chapter) => sum + (chapter.target_words || 0), 0)
  const stats = projectStats(project)
  const missingRequired = stats.missing_required || (project.missing_required?.length || 0)

  useEffect(() => {
    setUnsaved(dirty)
    return () => setUnsaved(false)
  }, [dirty, setUnsaved])

  useUnsavedGuard(dirty, {
    title: '大纲有未保存的修改',
    description: '章节标题、写作意图或目标字数还没有保存，离开会丢失这些修改。',
    confirmLabel: '放弃修改并离开',
  })

  const handleError = useCallback(
    (error: unknown, title: string) => {
      if (error instanceof ApiError && error.missingRequired.length > 0) {
        toast.error('生成被事实门禁拦截', error.message)
        navigate(`${basePath}/ledger`, {
          state: { highlight: error.missingRequired },
        })
        return
      }
      toast.error(title, messageOf(error))
    },
    [basePath, navigate, toast],
  )

  const save = useCallback(async () => {
    if (!canWrite || saving || generating) return
    const invalid = new Set(
      chapters.filter((chapter) => !chapter.heading.trim()).map((chapter) => chapter.uid),
    )
    if (invalid.size > 0) {
      setInvalidUids(invalid)
      toast.warning('还有章节没有标题', '请为每个章节填写标题后再保存。')
      return
    }
    setSaving(true)
    try {
      const response = await saveOutline(
        ref,
        chapters.map((chapter) => ({
          ...(chapter.id ? { id: chapter.id } : {}),
          heading: chapter.heading.trim(),
          intent: chapter.intent.trim(),
          target_words: Math.max(100, Math.round(chapter.target_words || 0)),
        })),
      )
      if (response.length > 0) {
        applyChapters(response)
      } else {
        const fresh = await getOutline(ref)
        applyChapters(fresh)
      }
      outlineQuery.setData(await getOutline(ref))
      setInvalidUids(new Set())
      await reloadProject()
      toast.success('大纲已保存', `共 ${chapters.length} 个章节。`)
    } catch (error) {
      handleError(error, '保存大纲失败')
    } finally {
      setSaving(false)
    }
  }, [
    canWrite,
    saving,
    generating,
    chapters,
    ref,
    applyChapters,
    outlineQuery,
    reloadProject,
    toast,
    handleError,
  ])

  useSaveShortcut(() => {
    if (dirty) void save()
  }, true)

  const generate = async () => {
    if (!canWrite || generating || saving) return
    if (chapters.length > 0) {
      const accepted = await confirm({
        title: '重新生成目录大纲？',
        description: hasBody
          ? '当前章节结构与已有正文会被替换。已生成的正文将被清空，且无法恢复。'
          : '当前章节结构会被新的编排结果替换，手工调整的标题与目标字数将丢失。',
        note: hasBody
          ? '建议先在「正文」页确认重要内容已经另行留存。'
          : '生成后仍可逐章修改标题、意图和目标字数。',
        confirmLabel: hasBody ? '清空正文并重新生成' : '重新生成大纲',
        tone: 'danger',
      })
      if (!accepted) return
    }
    setGenerating(true)
    try {
      const result = await generateOutline(ref, mode, Math.max(1000, Math.round(words)))
      const fresh = await getOutline(ref)
      outlineQuery.setData(fresh)
      applyChapters(fresh)
      await reloadProject()
      toast.success(
        '目录大纲已生成',
        `${result.chapters?.length || fresh.length || 0} 个章节 · ${outlineMode(result.mode || mode)}`,
      )
    } catch (error) {
      handleError(error, '生成大纲失败')
    } finally {
      setGenerating(false)
    }
  }

  const addChapter = () => {
    if (!canWrite) return
    newChapterCounter += 1
    setChapters((current) => [
      ...current,
      {
        uid: `new-${Date.now()}-${newChapterCounter}`,
        heading: '',
        intent: '',
        target_words: 1500,
        status: 'empty',
        words: 0,
        has_body: false,
        needs: [],
      },
    ])
  }

  const removeChapter = async (chapter: EditableChapter) => {
    if (!canWrite) return
    const accepted = await confirm({
      title: '删除这个章节？',
      description: `「${chapter.heading || '未命名章节'}」将从大纲中移除。`,
      note: chapter.has_body
        ? '该章节已有正文。保存大纲后，正文也会一并删除，无法恢复。'
        : '保存大纲后生效。',
      confirmLabel: '删除章节',
      tone: 'danger',
    })
    if (!accepted) return
    setChapters((current) => current.filter((item) => item.uid !== chapter.uid))
    setInvalidUids((current) => {
      const next = new Set(current)
      next.delete(chapter.uid)
      return next
    })
  }

  const moveChapter = (index: number, direction: -1 | 1) => {
    if (!canWrite) return
    const target = index + direction
    if (target < 0 || target >= chapters.length) return
    setChapters((current) => {
      const next = [...current]
      const [item] = next.splice(index, 1)
      next.splice(target, 0, item)
      return next
    })
  }

  const updateChapter = (uid: string, patch: Partial<EditableChapter>) => {
    setChapters((current) =>
      current.map((chapter) => (chapter.uid === uid ? { ...chapter, ...patch } : chapter)),
    )
    if (patch.heading !== undefined && patch.heading.trim()) {
      setInvalidUids((current) => {
        const next = new Set(current)
        next.delete(uid)
        return next
      })
    }
  }

  const runChapter = async (chapter: EditableChapter) => {
    if (!canWrite || !chapter.id || generatingId) return
    if (dirty) {
      toast.info('请先保存大纲', '有未保存的大纲修改，生成本章会使用已保存的章节设置。')
      return
    }
    if (chapter.has_body) {
      const accepted = await confirm({
        title: '重新生成本章？',
        description: `「${chapter.heading}」已有正文，生成会覆盖当前内容。`,
        note: '当前正文不会被自动备份。',
        confirmLabel: '覆盖并重新生成',
        tone: 'danger',
      })
      if (!accepted) return
    }
    setGeneratingId(chapter.id)
    try {
      const result = await generateChapter(ref, chapter.id)
      const fresh = await getOutline(ref)
      outlineQuery.setData(fresh)
      applyChapters(fresh)
      await reloadProject()
      const details: string[] = []
      if (result.written) details.push(`已写 ${result.written} 章`)
      if (result.fallback) details.push(`${result.fallback} 章使用确定性草稿`)
      if (result.failed) details.push(`${result.failed} 章失败`)
      const suspicious = Array.isArray(result.suspicious_numbers)
        ? result.suspicious_numbers.length
        : Number(result.suspicious_numbers || 0)
      if (suspicious) details.push(`${suspicious} 处数字待复核`)
      if (result.fallback || result.failed) {
        toast.warning('章节已生成，请复核', details.join(' · ') || '部分内容需要人工检查。')
      } else {
        toast.success('章节已生成', details.join(' · ') || `「${chapter.heading}」已写入正文。`)
      }
    } catch (error) {
      handleError(error, '生成本章失败')
    } finally {
      setGeneratingId(null)
    }
  }

  const loading = outlineQuery.loading && !initialized
  const totalWritten = chapters.filter((chapter) => chapter.has_body).length

  return (
    <div className="tab-content outline-tab">
      <TabHeading
        eyebrow="目录大纲"
        title="编排章节，分配字数"
        description="大纲决定正文结构与评分覆盖。重新生成会替换当前章节，并清空已有正文。"
        actions={
          <>
            <span className={className('dirty-flag', dirty && 'is-dirty')}>
              {dirty ? '有未保存修改' : initialized ? '已同步到本机' : ''}
            </span>
            <Button
              variant="primary"
              size="sm"
              icon={<Save size={14} />}
              onClick={() => void save()}
              loading={saving}
              disabled={!canWrite || !dirty || generating}
              title={!canWrite ? '只读成员不能修改大纲' : undefined}
            >
              保存大纲
            </Button>
          </>
        }
      />

      {missingRequired > 0 && (
        <div className="inline-gate">
          <AlertTriangle size={16} />
          <span>
            还有 <strong>{missingRequired}</strong> 项必填事实缺失，生成正文会被直接拒绝。
          </span>
          <button
            type="button"
            className="text-link"
            onClick={() =>
              navigate(`${basePath}/ledger`, {
                state: { highlight: project.missing_required || [] },
              })
            }
          >
            去补齐
          </button>
        </div>
      )}

      <section className="panel outline-config">
        <div className="config-head">
          <div>
            <div className="eyebrow">编排方式</div>
            <h3>{chapters.length > 0 ? '重新编排大纲' : '生成初始大纲'}</h3>
          </div>
          <span className="subtle">
            {chapters.length > 0
              ? hasBody
                ? '已有正文，重新生成会清空'
                : '当前大纲可继续手工调整'
              : '尚无章节'}
          </span>
        </div>

        <div className="mode-grid" role="radiogroup" aria-label="编排方式">
          {OUTLINE_MODES.map((item) => {
            const Icon = MODE_ICONS[item.id] || Wand2
            const selected = mode === item.id
            return (
              <button
                key={item.id}
                type="button"
                role="radio"
                aria-checked={selected}
                className={className('mode-card', selected && 'is-selected')}
                onClick={() => setMode(item.id)}
                disabled={!canWrite}
              >
                <span className="mode-icon" aria-hidden>
                  <Icon size={18} />
                </span>
                <span className="mode-copy">
                  <strong>{item.title}</strong>
                  <span>{item.description}</span>
                </span>
                {selected && (
                  <span className="mode-check" aria-hidden>
                    <Check size={12} />
                  </span>
                )}
              </button>
            )
          })}
        </div>

        <div className="outline-gen-row">
          <div className="words-control">
            <label htmlFor="outline-words">
              {chapters.length > 0 ? '重新生成目标字数' : '目标总字数'}
            </label>
            <div className="words-input-wrap">
              <input
                id="outline-words"
                type="number"
                className="input input--number"
                min={1000}
                max={200000}
                step={1000}
                value={words}
                onChange={(event) => setWords(Number(event.target.value) || 0)}
                onBlur={() => setWords((value) => Math.max(1000, Math.min(200000, value)))}
                disabled={!canWrite}
              />
              <span className="input-suffix">字</span>
            </div>
            <div className="word-presets">
              {WORD_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  className={className('preset-chip', words === preset && 'is-active')}
                  onClick={() => setWords(preset)}
                  disabled={!canWrite}
                >
                  {preset / 10000} 万
                </button>
              ))}
            </div>
          </div>
          <div className="outline-gen-meta">
            <span>
              {chapters.length > 0 ? (
                <>
                  当前大纲 {chapters.length} 章 · 已分配 {formatWords(totalTarget)}；
                  重新生成将按 {formatWords(words)} 重排
                </>
              ) : (
                <>将按 {formatWords(words)} 生成章节骨架</>
              )}
            </span>
            <span className="subtle">按 1,000 字步进调整，生成后可按章节再分配</span>
          </div>
          <Button
            variant="dark"
            icon={<Wand2 size={15} />}
            loading={generating}
            disabled={!canWrite || saving || loading}
            title={!canWrite ? '只读成员不能生成大纲' : undefined}
            onClick={() => void generate()}
          >
            {chapters.length > 0 ? '重新生成大纲' : '生成大纲'}
          </Button>
        </div>
      </section>

      <section className="panel outline-editor">
        <div className="panel-head outline-editor-head">
          <div>
            <div className="eyebrow">章节结构</div>
            <h3>章节结构</h3>
            <p className="panel-sub">
              {chapters.length} 个章节 · 目标 {formatWords(totalTarget)} · 已写 {totalWritten} 章
            </p>
          </div>
          <div className="outline-head-actions">
            {dirty && <span className="unsaved-chip">未保存</span>}
            <Button
              variant="secondary"
              size="sm"
              icon={<Save size={14} />}
              onClick={() => void save()}
              loading={saving}
              disabled={!canWrite || !dirty || generating}
              title={!canWrite ? '只读成员不能保存大纲' : undefined}
            >
              保存
            </Button>
            <Button
              variant="secondary"
              size="sm"
              icon={<Plus size={14} />}
              onClick={addChapter}
              disabled={!canWrite}
              title={!canWrite ? '只读成员不能添加章节' : undefined}
            >
              添加章节
            </Button>
          </div>
        </div>

        {loading ? (
          <div className="outline-loading" aria-busy="true">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="outline-skeleton">
                <Skeleton width={26} height={26} radius={6} />
                <div>
                  <Skeleton width="34%" height={16} />
                  <Skeleton width="72%" height={12} />
                </div>
                <Skeleton width={62} height={34} />
              </div>
            ))}
          </div>
        ) : outlineQuery.error && !initialized ? (
          <ErrorState message={outlineQuery.error} onRetry={outlineQuery.refresh} />
        ) : chapters.length === 0 ? (
          <div className="outline-empty">
            <div className="outline-empty-icon" aria-hidden>
              <ListTree size={26} />
            </div>
            <h3>还没有目录大纲</h3>
            <p>
              选择上方的编排方式与目标字数，生成第一版章节骨架。生成后可逐章调整标题、
              写作意图与目标字数。
            </p>
            <Button
              variant="primary"
              icon={<Wand2 size={15} />}
              loading={generating}
              disabled={!canWrite}
              title={!canWrite ? '只读成员不能生成大纲' : undefined}
              onClick={() => void generate()}
            >
              生成初始大纲
            </Button>
          </div>
        ) : (
          <div className="outline-list">
            {chapters.map((chapter, index) => {
              const status = chapterStatus(chapter)
              const wordsWritten = chapter.words || 0
              const progress = percent(wordsWritten, chapter.target_words || 0)
              const invalid = invalidUids.has(chapter.uid)
              return (
                <article
                  key={chapter.uid}
                  className={className('outline-row', invalid && 'has-error')}
                >
                  <div className="outline-index">
                    <span className="mono">{String(index + 1).padStart(2, '0')}</span>
                    <span className="outline-rail" aria-hidden />
                  </div>

                  <div className="outline-fields">
                    <input
                      className={className(
                        'input outline-heading-input',
                        invalid && 'input--error',
                      )}
                      value={chapter.heading}
                      onChange={(event) =>
                        updateChapter(chapter.uid, { heading: event.target.value })
                      }
                      placeholder="章节标题"
                      aria-label={`第 ${index + 1} 章标题`}
                      readOnly={!canWrite}
                      title={!canWrite ? '只读成员不能修改章节' : undefined}
                    />
                    {invalid && <span className="field-error">章节标题不能为空。</span>}
                    <AutoTextarea
                      className="outline-intent-input"
                      value={chapter.intent}
                      onValueChange={(value) =>
                        updateChapter(chapter.uid, { intent: value })
                      }
                      placeholder="写作意图：这一章要响应什么要求、引用哪些事实（可选）"
                      minRows={1}
                      aria-label={`第 ${index + 1} 章写作意图`}
                      readOnly={!canWrite}
                      title={!canWrite ? '只读成员不能修改章节' : undefined}
                    />
                    {chapter.needs && chapter.needs.length > 0 && (
                      <div className="outline-needs" title={chapter.needs.join('、')}>
                        <span>依赖事实</span>
                        {chapter.needs.slice(0, 4).map((need) => (
                          <code key={need}>{need}</code>
                        ))}
                        {chapter.needs.length > 4 && (
                          <span className="subtle">+{chapter.needs.length - 4}</span>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="outline-plan">
                    <div className="outline-status-line">
                      <Badge tone={status.tone} subtle>
                        {status.label}
                      </Badge>
                      <span className="mono subtle">
                        {formatNumber(wordsWritten)} / {formatNumber(chapter.target_words)}
                      </span>
                    </div>
                    <div className="outline-word-track" aria-hidden>
                      <span style={{ width: `${progress}%` }} />
                    </div>
                    <label className="target-input">
                      <span>目标字数</span>
                      <input
                        type="number"
                        className="input input--number input--sm"
                        min={100}
                        max={100000}
                        step={100}
                        value={chapter.target_words || 0}
                        onChange={(event) =>
                          updateChapter(chapter.uid, {
                            target_words: Number(event.target.value) || 0,
                          })
                        }
                        readOnly={!canWrite}
                        title={!canWrite ? '只读成员不能修改目标字数' : undefined}
                      />
                    </label>
                  </div>

                  <div className="outline-actions">
                    <div className="reorder-group">
                      <IconButton
                        label="上移章节"
                        onClick={() => moveChapter(index, -1)}
                        disabled={!canWrite || index === 0}
                      >
                        <ArrowUp size={14} />
                      </IconButton>
                      <IconButton
                        label="下移章节"
                        onClick={() => moveChapter(index, 1)}
                        disabled={!canWrite || index === chapters.length - 1}
                      >
                        <ArrowDown size={14} />
                      </IconButton>
                    </div>
                    <Button
                      size="sm"
                      variant={chapter.has_body ? 'secondary' : 'ghost'}
                      icon={<Sparkles size={13} />}
                      loading={generatingId === chapter.id}
                      disabled={!canWrite || !chapter.id || dirty || Boolean(generatingId)}
                      title={
                        !canWrite
                          ? '只读成员不能生成本章'
                          : dirty
                            ? '请先保存大纲再生成'
                            : !chapter.id
                              ? '请先保存新章节'
                              : chapter.has_body
                                ? '重新生成本章'
                                : '生成本章'
                      }
                      onClick={() => void runChapter(chapter)}
                    >
                      {chapter.has_body ? '重新生成' : '生成本章'}
                    </Button>
                    <IconButton
                      label="删除章节"
                      tone="danger"
                      disabled={!canWrite}
                      onClick={() => void removeChapter(chapter)}
                    >
                      <Trash2 size={14} />
                    </IconButton>
                  </div>
                </article>
              )
            })}
            {canWrite && (
              <button type="button" className="outline-add-row" onClick={addChapter}>
                <Plus size={15} />
                添加一个章节
              </button>
            )}
          </div>
        )}

        {chapters.length > 0 && (
          <div className="outline-foot">
            <span>
              保存后会同步章节 ID、顺序与目标字数。Ctrl + S 可直接保存。
            </span>
            <span className={className('dirty-flag', dirty && 'is-dirty')}>
              {dirty ? '未保存' : '已保存'}
            </span>
          </div>
        )}
      </section>
    </div>
  )
}

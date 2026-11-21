import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronRight,
  Clock3,
  Coins,
  Eye,
  FileText,
  Image as ImageIcon,
  ImageOff,
  ImagePlus,
  Loader2,
  Pencil,
  RefreshCw,
  Save,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Upload,
  Wand2,
  X,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import { generateChapter, getChapter, getChapterCitations, getOutline, getProjectJobs, reviewChapter, saveChapter, startDocumentGeneration, startImageBatch } from '../../api/generation'
import { applyImagePlan, createImage, deleteImage, getImagePlan, getImagePolicy, listImages, regenerateImage, updateImagePolicy, uploadImage } from '../../api/images'
import { ApiError, chapterConflict, messageOf } from '../../lib/http'
import { watchJob } from '../../hooks/watchJob'
import { canReviewChapter } from '../../lib/permissions'
import type {
  ChapterConflict,
  ChapterDetail,
  GenerationReport,
  ImageAsset,
  ImagePlan,
  ImagePolicy,
  Job,
} from '../../lib/types'
import {
  isChapterFrozen,
  reviewDoneMessage,
  reviewMeta,
  reviewStatus,
  reviewSteps,
  type ReviewAction,
} from '../../lib/review'
import {
  chapterStatus,
  className,
  countWords,
  formatFileSize,
  formatNumber,
  formatRelative,
  formatWords,
  percent,
  placeholders,
  projectStats,
} from '../../lib/format'
import {
  useQuery,
  useSaveShortcut,
  useUnsavedGuard,
} from '../../lib/hooks'
import { MarkdownPreview } from '../../lib/markdown'
import { markTraceHits } from '../../lib/trace'
import { Badge } from '../../components/Badge'
import { Button, IconButton } from '../../components/Button'
import { EmptyState, ErrorState, ProgressBar, Skeleton } from '../../components/Feedback'
import { Modal } from '../../components/Modal'
import { TabHeading } from '../../components/Workbench'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'
import { useAuth } from '../../context/useAuth'

const JOB_STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '生成中',
  done: '已完成',
  failed: '已失败',
}

// 图片工作台的可选风格；空值表示“无”，不发送 style 字段。
const IMAGE_STYLE_PRESETS: Array<{ value: string; label: string }> = [
  { value: '', label: '无（自动）' },
  { value: '工程示意', label: '工程示意' },
  { value: '系统架构', label: '系统架构' },
  { value: '现场照片感', label: '现场照片感' },
  { value: '极简信息图', label: '极简信息图' },
]

// 尺寸预设；auto 表示交给后端按模型配置决定。
const IMAGE_SIZE_PRESETS: Array<{ value: string; label: string }> = [
  { value: 'auto', label: '自动' },
  { value: '1024x1024', label: '方形 1024×1024' },
  { value: '1792x1024', label: '横向 1792×1024' },
  { value: '1024x1792', label: '纵向 1024×1792' },
]

function imageTokensIn(text: string): string[] {
  const matches = text.match(/\{\{\s*图片占位\s*[:：][^}]*\}\}/g) || []
  return Array.from(new Set(matches))
}

function imagePlaceholderLabel(token: string): string {
  const match = /^\{\{\s*图片占位\s*[:：]\s*([\s\S]*?)\s*\}\}$/.exec(token)
  return (match?.[1] || '').trim() || '配图'
}

function imageTokenForLabel(label: string): string {
  return `{{图片占位:${label.trim() || '配图'}}}`
}

function imageAlt(label: string): string {
  return (label || '配图').replace(/[[\]]/g, '')
}

function imageMarkdown(label: string, url: string): string {
  return `![${imageAlt(label)}](${url})`
}

function insertImageAtToken(body: string, token: string, markdown: string): string {
  return body
    .split(token)
    .join(`\n${markdown}\n`)
    .replace(/\n{3,}/g, '\n\n')
}

function appendImage(body: string, markdown: string): string {
  const trimmed = body.replace(/\s+$/, '')
  return `${trimmed}${trimmed ? '\n\n' : ''}${markdown}\n`
}

/**
 * 在光标/选区处插入 Markdown 图片，前后各保留一个空行，
 * 并吞掉选区两端的空白，避免出现连续空行。
 */
function insertImageAtCursor(
  body: string,
  start: number,
  end: number,
  markdown: string,
): string {
  const safeStart = Math.max(0, Math.min(start, body.length))
  const safeEnd = Math.max(safeStart, Math.min(end, body.length))
  const before = body.slice(0, safeStart).replace(/\s+$/, '')
  const after = body.slice(safeEnd).replace(/^\s+/, '')
  const head = before ? `${before}\n\n` : ''
  const tail = after ? `\n\n${after}` : '\n'
  return `${head}${markdown}\n${tail}`
}

function jobProgress(progress?: number): number {
  if (!progress || progress <= 0) return 0
  const normalized = progress <= 1 ? progress * 100 : progress
  return Math.max(0, Math.min(100, Math.round(normalized)))
}

/** 前端按契约 clamp，避免把非法数字发给后端或显示在摘要里。 */
function clampImagePolicy(policy: ImagePolicy): ImagePolicy {
  const maxTotal = Number.isFinite(policy.max_total)
    ? Math.round(Math.min(60, Math.max(0, policy.max_total)))
    : 0
  const width = Number.isFinite(policy.width_cm)
    ? Math.min(19, Math.max(8, Math.round(policy.width_cm * 100) / 100))
    : 15
  const size = policy.size === 'auto' || /^\d{2,4}x\d{2,4}$/i.test(policy.size)
    ? policy.size
    : 'auto'
  return {
    max_total: maxTotal,
    style: (policy.style || '').slice(0, 80),
    size,
    captions: policy.captions,
    caption_prefix: (policy.caption_prefix || '').trim().slice(0, 10) || '图',
    width_cm: width,
  }
}

function policyEquals(left: ImagePolicy, right: ImagePolicy): boolean {
  return (
    left.max_total === right.max_total &&
    left.style === right.style &&
    left.size === right.size &&
    left.captions === right.captions &&
    left.caption_prefix === right.caption_prefix &&
    left.width_cm === right.width_cm
  )
}

/** 折叠状态下用一句话交代策略，省得展开才知道当前设定。 */
function policySummaryText(policy: ImagePolicy): string {
  const budget = policy.max_total > 0 ? `${policy.max_total} 张` : '不限'
  const captions = policy.captions
    ? `图注「${policy.caption_prefix || '图'}n」`
    : '不生成图注'
  return `预算 ${budget} · ${captions} · 宽 ${policy.width_cm}cm`
}

function reportDetails(report?: GenerationReport): string[] {
  if (!report) return []
  const details: string[] = []
  if (report.written) details.push(`写入 ${report.written} 章`)
  if (report.fallback) details.push(`${report.fallback} 章使用确定性草稿`)
  if (report.failed) details.push(`${report.failed} 章失败`)
  const suspicious = Array.isArray(report.suspicious_numbers)
    ? report.suspicious_numbers.length
    : Number(report.suspicious_numbers || 0)
  if (suspicious) details.push(`${suspicious} 处数字待复核`)
  return details
}

type JobEventPayload = Partial<Job> & {
  heading?: string
  index?: number
  total?: number
  target_words?: number
  progress?: number
  done?: number
  words?: number
  source?: string
  failed?: boolean
  text?: string
  error?: string
}

interface DraftFocusTarget {
  nodeId: string
  quote: string
}

const QUOTE_BLOCK_SELECTOR =
  'p, h1, h2, h3, h4, h5, h6, li, td, th, blockquote, pre, .md-placeholder'

function normalizeForSearch(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

/**
 * 在预览 DOM 里按文本查找 quote 所在块：不做标题 id 体系，只认渲染后的文字。
 * 长引文逐级截短重试，尽量命中段落。
 */
function findQuoteBlock(root: HTMLElement, quote: string): HTMLElement | null {
  const target = normalizeForSearch(quote)
  if (target.length < 4) return null
  const blocks = Array.from(root.querySelectorAll<HTMLElement>(QUOTE_BLOCK_SELECTOR))
  const candidates = [target, target.slice(0, 48), target.slice(0, 16)].filter(
    (value, index, list) => value.length >= 4 && list.indexOf(value) === index,
  )
  for (const candidate of candidates) {
    const hit = blocks.find((block) =>
      normalizeForSearch(block.textContent || '').includes(candidate),
    )
    if (hit) return hit
  }
  return null
}

export function DraftTab() {
  const { ref, basePath, project, reloadProject, setUnsaved, canWrite } = useWorkspace()
  const { syncUser } = useAuth()
  const toast = useToast()
  const confirm = useConfirm()
  const navigate = useNavigate()
  const location = useLocation()
  const outlineQuery = useQuery(`outline:${ref}`, () => getOutline(ref))
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const citationsQuery = useQuery(
    `citations:${ref}:${selectedId || 'none'}`,
    () => getChapterCitations(ref, selectedId as string),
    { enabled: Boolean(selectedId) },
  )
  const [activeFactKey, setActiveFactKey] = useState<string | null>(null)
  const [body, setBody] = useState('')
  const [savedBody, setSavedBody] = useState('')
  const [loadedId, setLoadedId] = useState<string | null>(null)
  const [preview, setPreview] = useState(false)
  const [saving, setSaving] = useState(false)
  const [revision, setRevision] = useState<number | null>(null)
  const [conflict, setConflict] = useState<ChapterConflict | null>(null)
  const [resolvingConflict, setResolvingConflict] = useState(false)
  const [generatingChapter, setGeneratingChapter] = useState(false)
  const [reviewBusy, setReviewBusy] = useState(false)
  const [reviewNoteFor, setReviewNoteFor] = useState<ReviewAction | null>(null)
  const [reviewNote, setReviewNote] = useState('')
  const [job, setJob] = useState<Job | null>(null)
  const [streamFallback, setStreamFallback] = useState(false)
  const [streamPreview, setStreamPreview] = useState<{
    heading: string
    text: string
  } | null>(null)
  const [startingJob, setStartingJob] = useState(false)
  const [creditGate, setCreditGate] = useState<string | null>(null)
  const selectedIdRef = useRef<string | null>(null)
  selectedIdRef.current = selectedId
  const jobRef = useRef<Job | null>(null)
  jobRef.current = job

  const chapterQuery = useQuery<ChapterDetail | null>(
    `chapter:${ref}:${selectedId || 'none'}`,
    () => (selectedId ? getChapter(ref, selectedId) : Promise.resolve(null)),
  )
  const imagesQuery = useQuery(
    `images:${ref}:${selectedId || 'none'}`,
    () => (selectedId ? listImages(ref, selectedId) : Promise.resolve([])),
  )
  const projectImagesQuery = useQuery(`images:all:${ref}`, () => listImages(ref))
  const imagePlanQuery = useQuery(`image-plan:${ref}`, () => getImagePlan(ref))
  const imagePolicyQuery = useQuery(`image-policy:${ref}`, () => getImagePolicy(ref))
  const [imageBatchJob, setImageBatchJob] = useState<Job | null>(null)
  const [startingImageBatch, setStartingImageBatch] = useState(false)
  const [applyingImagePlan, setApplyingImagePlan] = useState(false)
  const imageBatchJobRef = useRef<Job | null>(null)
  imageBatchJobRef.current = imageBatchJob
  // 批量生成结束后是否自动接一次「按计划插入」。
  const autoApplyAfterBatch = useRef(false)
  const autoApplyPlanRef = useRef<(() => Promise<void>) | null>(null)
  // 配图策略表单草稿；与已保存值不同即视为未保存。
  const [policyDraft, setPolicyDraft] = useState<ImagePolicy | null>(null)
  const [savingPolicy, setSavingPolicy] = useState(false)
  const [galleryScope, setGalleryScope] = useState<'chapter' | 'project'>('chapter')
  const [imageHistory, setImageHistory] = useState<string[]>([])
  const [generatingToken, setGeneratingToken] = useState<string | null>(null)
  const [deletingImageId, setDeletingImageId] = useState<string | null>(null)
  const [imageError, setImageError] = useState<string | null>(null)
  // 图片工作台：图名 / 画面描述 / 风格 / 尺寸 / 生成后自动插入。
  const [studioLabel, setStudioLabel] = useState('')
  const [studioPrompt, setStudioPrompt] = useState('')
  const [studioStyle, setStudioStyle] = useState('')
  const [studioSize, setStudioSize] = useState('auto')
  const [autoInsertImage, setAutoInsertImage] = useState(false)
  const [generatingStudio, setGeneratingStudio] = useState(false)
  const [uploadingImage, setUploadingImage] = useState(false)
  const [regeneratingImageId, setRegeneratingImageId] = useState<string | null>(null)
  const studioLabelTouched = useRef(false)
  const draftTextareaRef = useRef<HTMLTextAreaElement | null>(null)
  const studioFileInputRef = useRef<HTMLInputElement | null>(null)
  const caretRef = useRef<{ start: number; end: number } | null>(null)
  const pendingCaret = useRef<number | null>(null)
  // 风险总览「跳转修改」带来的定位目标：选中章节后按 quote 在预览里定位。
  const [focusTarget, setFocusTarget] = useState<DraftFocusTarget | null>(null)
  const editorPreviewRef = useRef<HTMLDivElement | null>(null)
  const focusSelectingRef = useRef<string | null>(null)
  const focusTimerRef = useRef<number | null>(null)

  const outline = useMemo(() => outlineQuery.data || [], [outlineQuery.data])
  const selectedChapter = outline.find((chapter) => chapter.id === selectedId) || null
  const dirty = Boolean(selectedId) && loadedId === selectedId && body !== savedBody
  const stats = projectStats(project)
  const missingRequired = stats.missing_required || (project.missing_required?.length || 0)
  const writtenCount = outline.filter((chapter) => chapter.has_body).length
  const totalTarget = outline.reduce((sum, chapter) => sum + (chapter.target_words || 0), 0)
  const totalWrittenWords = outline.reduce((sum, chapter) => sum + (chapter.words || 0), 0)
  const liveWords = countWords(body)
  const targetWords = selectedChapter?.target_words || chapterQuery.data?.target_words || 0
  const placeholdersInBody = useMemo(() => placeholders(body), [body])
  const jobActive = job?.status === 'queued' || job?.status === 'running'
  const imageBatchActive =
    imageBatchJob?.status === 'queued' || imageBatchJob?.status === 'running'
  // 自动插入由轮询回调触发，必须在同一 tick 里读到最新门禁状态，
  // 否则闭包里的 imageBatchActive 仍是上一帧的 true。
  const imageBatchActiveRef = useRef(imageBatchActive)
  imageBatchActiveRef.current = imageBatchActive
  const jobActiveRef = useRef(jobActive)
  jobActiveRef.current = jobActive
  const applyingPlanRef = useRef(applyingImagePlan)
  applyingPlanRef.current = applyingImagePlan
  const images = useMemo(() => imagesQuery.data || [], [imagesQuery.data])
  const projectImages = useMemo(
    () => projectImagesQuery.data || [],
    [projectImagesQuery.data],
  )
  const allImages = useMemo(() => {
    const seen = new Set<string>()
    return [...projectImages, ...images].filter((image) => {
      if (seen.has(image.id)) return false
      seen.add(image.id)
      return true
    })
  }, [projectImages, images])
  const readyImages = useMemo(
    () =>
      [...allImages]
        .filter((image) => image.status === 'ready')
        .sort((left, right) => {
          const a = new Date(left.created_at || 0).getTime()
          const b = new Date(right.created_at || 0).getTime()
          return b - a
        }),
    [allImages],
  )
  const visibleImages = galleryScope === 'project' ? projectImages : images
  const galleryQuery = galleryScope === 'project' ? projectImagesQuery : imagesQuery
  const refreshImageData = () => {
    imagesQuery.refresh()
    projectImagesQuery.refresh()
    imagePlanQuery.refresh()
  }
  const refreshImageDataRef = useRef(refreshImageData)
  refreshImageDataRef.current = refreshImageData
  const missingImageLabels = useMemo(() => {
    const labels = new Set<string>()
    for (const chapter of imagePlanQuery.data?.chapters || []) {
      for (const item of chapter.placeholders) {
        if (item.status === 'missing') labels.add(item.label)
      }
    }
    return labels
  }, [imagePlanQuery.data])
  const failedImageLabels = useMemo(() => {
    const labels = new Set<string>()
    for (const chapter of imagePlanQuery.data?.chapters || []) {
      for (const item of chapter.placeholders) {
        if (item.status === 'failed') labels.add(item.label)
      }
    }
    return labels
  }, [imagePlanQuery.data])
  const currentImageTokens = useMemo(() => imageTokensIn(body), [body])
  const allImageTokens = useMemo(
    () => Array.from(new Set([...imageHistory, ...currentImageTokens])),
    [imageHistory, currentImageTokens],
  )
  const nonImagePlaceholders = useMemo(
    () => placeholdersInBody.filter((token) => !/^\{\{\s*图片占位/.test(token)),
    [placeholdersInBody],
  )
  const insertedImageTokens = allImageTokens.filter((token) => !body.includes(token))
  const pendingImageTokens = allImageTokens.filter((token) => body.includes(token))
  const visiblePlaceholderCount = nonImagePlaceholders.length + allImageTokens.length

  // 策略优先取独立接口；旧后端没有该接口时退回计划响应里的 policy。
  const imagePolicy = imagePolicyQuery.data || imagePlanQuery.data?.policy || null
  const planDocument = imagePlanQuery.data?.document
  const planReady = imagePlanQuery.data?.summary.ready ?? 0
  const planToGenerate =
    (imagePlanQuery.data?.summary.missing ?? 0) +
    (imagePlanQuery.data?.summary.failed ?? 0)
  const policyDirty = Boolean(policyDraft && imagePolicy && !policyEquals(policyDraft, imagePolicy))
  const styleOptions = useMemo(() => {
    const extra = policyDraft?.style
    if (!extra || IMAGE_STYLE_PRESETS.some((preset) => preset.value === extra)) {
      return IMAGE_STYLE_PRESETS
    }
    return [...IMAGE_STYLE_PRESETS, { value: extra, label: extra }]
  }, [policyDraft?.style])
  const sizeOptions = useMemo(() => {
    const extra = policyDraft?.size
    if (!extra || IMAGE_SIZE_PRESETS.some((preset) => preset.value === extra)) {
      return IMAGE_SIZE_PRESETS
    }
    return [...IMAGE_SIZE_PRESETS, { value: extra, label: extra }]
  }, [policyDraft?.size])

  // 切换项目后清空旧草稿与自动插入标记，避免把上个项目的策略带过来。
  useEffect(() => {
    setPolicyDraft(null)
    autoApplyAfterBatch.current = false
  }, [ref])

  // 服务端策略变化时刷新表单；用户正在编辑（dirty）时不覆盖草稿。
  useEffect(() => {
    if (!imagePolicy || policyDirty) return
    setPolicyDraft({ ...imagePolicy })
  }, [imagePolicy, policyDirty])

  useEffect(() => {
    if (!outline.length) {
      if (selectedId) {
        setSelectedId(null)
        setBody('')
        setSavedBody('')
        setLoadedId(null)
        setRevision(null)
      }
      return
    }
    if (!selectedId || !outline.some((chapter) => chapter.id === selectedId)) {
      const first = outline.find((chapter) => chapter.has_body) || outline[0]
      setSelectedId(first.id)
      setBody('')
      setSavedBody('')
      setLoadedId(null)
      setRevision(null)
    }
  }, [outline, selectedId])

  useEffect(() => {
    const data = chapterQuery.data
    if (!data || data.id !== selectedId) return
    setBody(data.body || '')
    setSavedBody(data.body || '')
    setLoadedId(data.id)
    setRevision(typeof data.revision === 'number' ? data.revision : null)
    setImageHistory(imageTokensIn(data.body || ''))
    setImageError(null)
    // Generated / imported chapters open in reading mode. Editing remains an
    // explicit choice via the toolbar toggle.
    setPreview(Boolean((data.body || '').trim()))
  }, [chapterQuery.data, selectedId])

  useEffect(() => {
    if (!currentImageTokens.length) return
    setImageHistory((previous) => {
      const missing = currentImageTokens.filter((token) => !previous.includes(token))
      return missing.length ? [...previous, ...missing] : previous
    })
  }, [currentImageTokens])

  useEffect(() => {
    if (!imagesQuery.data?.length) return
    setImageHistory((previous) => {
      let next = previous
      imagesQuery.data?.forEach((asset) => {
        if (!asset.label) return
        if (next.some((token) => imagePlaceholderLabel(token) === asset.label)) return
        const canonical = imageTokenForLabel(asset.label)
        if (!next.includes(canonical)) next = [...next, canonical]
      })
      return next
    })
  }, [imagesQuery.data])

  // 切换章节时清空上一章的自定义描述，并恢复“跟随占位符”的图名。
  useEffect(() => {
    studioLabelTouched.current = false
    setStudioPrompt('')
    setImageError(null)
  }, [selectedId])

  // 图名默认跟随第一个待插入的占位符；用户手动改过之后不再自动覆盖。
  const firstPendingTokenLabel = useMemo(
    () =>
      (pendingImageTokens[0] ? imagePlaceholderLabel(pendingImageTokens[0]) : '') || '配图',
    [pendingImageTokens],
  )
  useEffect(() => {
    if (studioLabelTouched.current) return
    setStudioLabel(firstPendingTokenLabel)
  }, [firstPendingTokenLabel, selectedId])

  // 插入图片后把光标放回新插入的 Markdown 之后，保持连续编辑。
  useEffect(() => {
    if (pendingCaret.current == null) return
    const position = pendingCaret.current
    pendingCaret.current = null
    if (preview) return
    const textarea = draftTextareaRef.current
    if (!textarea) return
    textarea.focus()
    textarea.setSelectionRange(position, position)
  }, [body, preview])

  const rememberCaret = () => {
    const textarea = draftTextareaRef.current
    if (!textarea) return
    caretRef.current = { start: textarea.selectionStart, end: textarea.selectionEnd }
  }

  useEffect(() => {
    setUnsaved(dirty)
    return () => setUnsaved(false)
  }, [dirty, setUnsaved])

  useUnsavedGuard(dirty, {
    title: '正文有未保存的修改',
    description: '当前章节的改动还没有写入本机，切换章节或离开页面会丢失。',
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
      if (error instanceof ApiError && error.status === 402) {
        setCreditGate(error.message || '点数不足，请先充值后再生成。')
        toast.warning('点数不足', 'AI 生成按章扣点，请充值后再继续。')
        void syncUser()
        return
      }
      toast.error(title, messageOf(error))
    },
    [basePath, navigate, toast, syncUser],
  )

  const applySavedChapter = useCallback(
    (result: ChapterDetail) => {
      setSavedBody(result.body ?? body)
      setLoadedId(selectedId)
      setRevision((current) =>
        typeof result.revision === 'number'
          ? result.revision
          : current === null
            ? null
            : current + 1,
      )
    },
    [body, selectedId],
  )

  const save = useCallback(
    async (baseRevision?: number | null) => {
      if (!canWrite || !selectedId || saving || !dirty) return
      setSaving(true)
      try {
        const result = await saveChapter(
          ref,
          selectedId,
          body,
          typeof baseRevision === 'number' ? baseRevision : revision,
        )
        applySavedChapter(result)
        outlineQuery.refresh()
        imagePlanQuery.refresh()
        await reloadProject()
        toast.success('正文已保存', selectedChapter?.heading || '当前章节')
      } catch (error) {
        const conflictInfo = chapterConflict(error)
        if (conflictInfo) {
          setConflict(conflictInfo)
          return
        }
        handleError(error, '保存正文失败')
      } finally {
        setSaving(false)
      }
    },
    [
      canWrite,
      selectedId,
      saving,
      dirty,
      ref,
      body,
      revision,
      applySavedChapter,
      outlineQuery,
      imagePlanQuery,
      reloadProject,
      toast,
      selectedChapter?.heading,
      handleError,
    ],
  )

  const forceOverwrite = useCallback(async () => {
    if (!conflict || !selectedId || saving || resolvingConflict) return
    setResolvingConflict(true)
    try {
      const result = await saveChapter(ref, selectedId, body, conflict.current_revision)
      applySavedChapter(result)
      setConflict(null)
      outlineQuery.refresh()
      imagePlanQuery.refresh()
      await reloadProject()
      toast.success('已强制覆盖保存', selectedChapter?.heading || '当前章节')
    } catch (error) {
      const next = chapterConflict(error)
      if (next) {
        setConflict(next)
        toast.warning(
          '章节再次被更新',
          '服务器版本又变了，请重新选择载入最新或强制覆盖。',
        )
        return
      }
      handleError(error, '保存正文失败')
    } finally {
      setResolvingConflict(false)
    }
  }, [
    conflict,
    selectedId,
    saving,
    resolvingConflict,
    ref,
    body,
    applySavedChapter,
    outlineQuery,
    imagePlanQuery,
    reloadProject,
    toast,
    selectedChapter?.heading,
    handleError,
  ])

  const loadLatestVersion = useCallback(() => {
    if (!conflict) return
    setBody(conflict.current_body)
    setSavedBody(conflict.current_body)
    setRevision(conflict.current_revision)
    setLoadedId(selectedId)
    setPreview(Boolean(conflict.current_body.trim()))
    setConflict(null)
    outlineQuery.refresh()
    toast.info('已载入最新正文', '未保存的本地修改已丢弃。')
  }, [conflict, selectedId, outlineQuery, toast])

  useSaveShortcut(() => {
    if (dirty) void save()
  }, true)

  const selectChapter = async (id: string) => {
    if (id === selectedId || generatingChapter) return
    if (dirty) {
      const accepted = await confirm({
        title: '切换章节？',
        description: '当前章节有未保存的修改，切换后会丢失。',
        confirmLabel: '放弃修改并切换',
        tone: 'warning',
      })
      if (!accepted) return
    }
    setSelectedId(id)
    setLoadedId(null)
    setBody('')
    setSavedBody('')
    setRevision(null)
  }

  const reloadChapterData = useCallback(async (id: string) => {
    const detail = await getChapter(ref, id)
    if (selectedIdRef.current !== id) return
    setBody(detail.body || '')
    setSavedBody(detail.body || '')
    setLoadedId(id)
    setRevision(typeof detail.revision === 'number' ? detail.revision : null)
    setPreview(Boolean((detail.body || '').trim()))
    return detail
  }, [ref])

  const reviewOwner = canReviewChapter(project)
  const chapterReview = chapterQuery.data?.review_status || selectedChapter?.review_status
  const reviewFrozen = isChapterFrozen(chapterReview)
  const steps = reviewSteps(chapterReview, Boolean(reviewOwner))

  const applyReview = async (action: ReviewAction, note = '') => {
    if (!selectedId) return
    setReviewBusy(true)
    try {
      const updated = await reviewChapter(ref, selectedId, action, note)
      chapterQuery.setData(updated)
      setRevision(updated.revision ?? 0)
      setSavedBody(updated.body || '')
      await outlineQuery.refresh()
      await reloadProject()
      toast.success(reviewDoneMessage(action), selectedChapter?.heading || '当前章节')
      setReviewNoteFor(null)
      setReviewNote('')
    } catch (error) {
      handleError(error, '章节状态流转失败')
    } finally {
      setReviewBusy(false)
    }
  }

  const startReview = (action: ReviewAction, needsNote?: boolean) => {
    if (needsNote) {
      setReviewNote('')
      setReviewNoteFor(action)
      return
    }
    void applyReview(action)
  }

  const runChapter = async () => {
    if (!canWrite || !selectedId || !selectedChapter || generatingChapter) return
    if (dirty) {
      const accepted = await confirm({
        title: '生成会覆盖当前修改',
        description: '当前章节有未保存的修改。生成会直接覆盖正文。',
        confirmLabel: '放弃修改并生成',
        tone: 'danger',
      })
      if (!accepted) return
    } else if (savedBody.trim()) {
      const accepted = await confirm({
        title: '重新生成本章？',
        description: `「${selectedChapter.heading}」已有正文，生成会覆盖当前内容。`,
        note: '当前正文不会被自动备份。',
        confirmLabel: '覆盖并生成',
        tone: 'danger',
      })
      if (!accepted) return
    }
    setGeneratingChapter(true)
    try {
      const report = await generateChapter(ref, selectedId)
      setCreditGate(null)
      void syncUser()
      await reloadChapterData(selectedId)
      const fresh = await getOutline(ref)
      outlineQuery.setData(fresh)
      await reloadProject()
      const details = reportDetails(report)
      if (report.failed || report.fallback) {
        toast.warning('章节已生成，请复核', details.join(' · ') || '部分内容需要人工检查。')
      } else {
        toast.success('章节已生成', details.join(' · ') || '正文已写入当前章节。')
      }
      setPreview(true)
    } catch (error) {
      handleError(error, '生成本章失败')
    } finally {
      setGeneratingChapter(false)
    }
  }

  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty

  // 选中跳转目标章节；沿用切换章节时的未保存确认流程。
  const applyFocusSelection = useCallback(
    async (nodeId: string) => {
      if (selectedIdRef.current === nodeId) return true
      if (dirtyRef.current) {
        const accepted = await confirm({
          title: '切换章节？',
          description: '当前章节有未保存的修改，切换后会丢失。',
          confirmLabel: '放弃修改并切换',
          tone: 'warning',
        })
        if (!accepted) return false
      }
      setSelectedId(nodeId)
      setLoadedId(null)
      setBody('')
      setSavedBody('')
      setRevision(null)
      return true
    },
    [confirm],
  )

  // 1) 消费风险总览带来的定位 state（读一次即清除，刷新不会重复触发）。
  useEffect(() => {
    const state = location.state as { focusNode?: string; quote?: string } | null
    if (!state?.focusNode) return
    const nodeId = String(state.focusNode)
    navigate(location.pathname, { replace: true, state: null })
    setFocusTarget((current) =>
      current?.nodeId === nodeId ? current : { nodeId, quote: state.quote || '' },
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key])

  // 2) 选中目标章节；大纲里已不存在该节点时给出提示并放弃定位。
  useEffect(() => {
    const target = focusTarget
    if (!target) return
    if (outlineQuery.loading) return
    if (outline.length === 0) {
      toast.warning('未找到对应章节', '当前项目还没有大纲，无法定位风险所在章节。')
      setFocusTarget(null)
      return
    }
    if (!outline.some((chapter) => chapter.id === target.nodeId)) {
      toast.warning('未找到对应章节', '大纲中已没有该章节，可能已被调整或删除。')
      setFocusTarget(null)
      return
    }
    if (selectedId === target.nodeId) return
    if (focusSelectingRef.current === target.nodeId) return
    focusSelectingRef.current = target.nodeId
    void (async () => {
      const accepted = await applyFocusSelection(target.nodeId)
      focusSelectingRef.current = null
      if (!accepted) setFocusTarget(null)
    })()
  }, [focusTarget, outline, outlineQuery.loading, selectedId, applyFocusSelection, toast])

  // 3) 正文载入后在预览 DOM 里定位 quote 并临时高亮；找不到则退化为章节提示。
  useEffect(() => {
    const target = focusTarget
    if (!target || loadedId !== target.nodeId) return
    if (!preview) {
      if (!body.trim()) {
        // 空正文章节不会进入预览：直接给出退化提示，避免目标悬挂。
        toast.info('已定位到章节', '本章正文还是空的，请直接补充相关内容。')
        setFocusTarget(null)
        return
      }
      // 用户可能正停在编辑模式；跳转意图是查看位置，先切回预览再定位。
      setPreview(true)
      return
    }
    if (focusTimerRef.current != null) window.clearTimeout(focusTimerRef.current)
    const timer = window.setTimeout(() => {
      focusTimerRef.current = null
      const root = editorPreviewRef.current
      const block = root ? findQuoteBlock(root, target.quote) : null
      if (block) {
        block.scrollIntoView({ behavior: 'smooth', block: 'center' })
        block.classList.add('md-focus-flash')
        window.setTimeout(() => block.classList.remove('md-focus-flash'), 3200)
      } else {
        toast.info('已定位到章节', '没有自动匹配到具体段落，请在本章内查找相关内容。')
      }
      setFocusTarget(null)
    }, 140)
    focusTimerRef.current = timer
    return () => {
      if (focusTimerRef.current != null) {
        window.clearTimeout(focusTimerRef.current)
        focusTimerRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusTarget, loadedId, preview, body])

  const generateImageFor = async (token: string) => {
    if (
      !canWrite ||
      !selectedId ||
      generatingToken ||
      generatingStudio ||
      uploadingImage ||
      regeneratingImageId
    )
      return
    const label = imagePlaceholderLabel(token)
    setGeneratingToken(token)
    setImageError(null)
    try {
      const asset = await createImage(ref, {
        label,
        node_id: selectedId,
        heading: selectedChapter?.heading || '',
        // 占位符生成沿用图片工作台当前选择的风格与尺寸。
        style: studioStyle || undefined,
        size: studioSize || undefined,
      })
      refreshImageData()
      if (asset.status === 'failed') {
        const message = asset.last_error || '图片生成失败，请检查图片模型配置。'
        setImageError(message)
        toast.warning('图片生成失败', message)
      } else {
        toast.success('图片已生成', '可在「本章配图」中插入正文。')
      }
    } catch (error) {
      const message = messageOf(error)
      setImageError(message)
      toast.error('图片生成失败', message)
    } finally {
      setGeneratingToken(null)
    }
  }

  const insertImageAsset = (asset: ImageAsset, token?: string) => {
    if (!canWrite || asset.status !== 'ready' || !asset.url) return
    // 只替换图名完全一致的占位符；否则插入到编辑器光标/选区。
    const matchingToken =
      token ||
      allImageTokens.find(
        (candidate) =>
          body.includes(candidate) && imagePlaceholderLabel(candidate) === asset.label,
      )
    const markdown = imageMarkdown(
      matchingToken ? imagePlaceholderLabel(matchingToken) : asset.label,
      asset.url,
    )
    let nextBody: string
    if (matchingToken && body.includes(matchingToken)) {
      nextBody = insertImageAtToken(body, matchingToken, markdown)
    } else if (preview) {
      // 预览态：转为编辑并在正文末尾插入。
      nextBody = appendImage(body, markdown)
      setPreview(false)
    } else {
      const position = caretRef.current || { start: body.length, end: body.length }
      const safeStart = Math.max(0, Math.min(position.start, body.length))
      const safeEnd = Math.max(safeStart, Math.min(position.end, body.length))
      nextBody = insertImageAtCursor(body, safeStart, safeEnd, markdown)
      const at = nextBody.indexOf(markdown)
      if (at >= 0) pendingCaret.current = at + markdown.length
    }
    setBody(nextBody)
    imagesQuery.refresh()
    toast.success('已插入正文', '请按 Ctrl+S 保存本章。')
  }

  const generateStudioImage = async () => {
    if (
      !canWrite ||
      !selectedId ||
      generatingStudio ||
      generatingToken ||
      uploadingImage ||
      regeneratingImageId
    )
      return
    const label = studioLabel.trim() || '配图'
    setGeneratingStudio(true)
    setImageError(null)
    try {
      const asset = await createImage(ref, {
        label,
        node_id: selectedId,
        heading: selectedChapter?.heading || '',
        prompt: studioPrompt.trim() || undefined,
        style: studioStyle || undefined,
        size: studioSize || undefined,
      })
      refreshImageData()
      if (asset.status === 'failed') {
        const message = asset.last_error || '图片生成失败，请检查图片模型配置。'
        setImageError(message)
        toast.warning('图片生成失败', message)
        return
      }
      toast.success('图片已生成', `「${label}」已加入本章配图。`)
      if (autoInsertImage) insertImageAsset(asset)
    } catch (error) {
      const message = messageOf(error)
      setImageError(message)
      toast.error('图片生成失败', message)
    } finally {
      setGeneratingStudio(false)
    }
  }

  const regenerateImageAsset = async (asset: ImageAsset) => {
    if (
      !canWrite ||
      regeneratingImageId ||
      generatingStudio ||
      generatingToken ||
      uploadingImage
    )
      return
    setRegeneratingImageId(asset.id)
    setImageError(null)
    try {
      const next = await regenerateImage(ref, asset.id)
      refreshImageData()
      if (next.status === 'failed') {
        const message = next.last_error || '重新生成失败，请检查图片模型配置。'
        setImageError(message)
        toast.warning('重新生成失败', message)
        return
      }
      toast.success('已重新生成', `「${next.label || asset.label}」生成了新的配图。`)
      if (autoInsertImage) insertImageAsset(next)
    } catch (error) {
      const message = messageOf(error)
      setImageError(message)
      toast.error('重新生成失败', message)
    } finally {
      setRegeneratingImageId(null)
    }
  }

  const handleStudioFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.target
    const file = input.files?.[0]
    // 先重置 input，同一文件也能重复选择。
    input.value = ''
    if (
      !file ||
      !canWrite ||
      !selectedId ||
      uploadingImage ||
      generatingStudio ||
      generatingToken ||
      regeneratingImageId
    )
      return
    const looksLikeImage =
      /^image\/(png|jpe?g)$/i.test(file.type) || /\.(png|jpe?g)$/i.test(file.name)
    if (!looksLikeImage) {
      const message = '只支持 PNG / JPEG 图片。'
      setImageError(message)
      toast.warning('无法上传该文件', message)
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      const message = '图片不能超过 10MB。'
      setImageError(message)
      toast.warning('无法上传该文件', message)
      return
    }
    const label = studioLabel.trim() || file.name.replace(/\.[^.]+$/, '') || '配图'
    setUploadingImage(true)
    setImageError(null)
    try {
      const asset = await uploadImage(ref, file, { label, node_id: selectedId })
      refreshImageData()
      toast.success('图片已上传', `「${asset.label || label}」已加入本章配图。`)
      if (autoInsertImage && asset.status === 'ready') insertImageAsset(asset)
    } catch (error) {
      const message = messageOf(error)
      setImageError(message)
      toast.error('上传图片失败', message)
    } finally {
      setUploadingImage(false)
    }
  }

  const removeImage = async (asset: ImageAsset) => {
    if (!canWrite || deletingImageId) return
    const accepted = await confirm({
      title: '删除这张配图？',
      description: `「${asset.label || '未命名配图'}」的图片文件会从本机删除。`,
      note: '正文中的 Markdown 图片引用不会自动删除，保存后仍保留原语法。',
      confirmLabel: '删除配图',
      tone: 'danger',
    })
    if (!accepted) return
    setDeletingImageId(asset.id)
    setImageError(null)
    try {
      await deleteImage(ref, asset.id)
      refreshImageData()
      toast.success('配图已删除', asset.label || '未命名配图')
    } catch (error) {
      const message = messageOf(error)
      setImageError(message)
      toast.error('删除配图失败', message)
    } finally {
      setDeletingImageId(null)
    }
  }

  const finishJob = useCallback(
    async (finished: Job) => {
      try {
        const fresh = await getOutline(ref)
        outlineQuery.setData(fresh)
        imagePlanQuery.refresh()
        await reloadProject()
        const current = selectedIdRef.current
        if (current && !dirtyRef.current) await reloadChapterData(current)
        void syncUser()
        const details = reportDetails(finished.result)
        if (finished.result?.failed || finished.result?.fallback) {
          toast.warning('全书生成完成，请复核', details.join(' · ') || '部分章节需要人工检查。')
        } else {
          toast.success('全书生成完成', details.join(' · ') || '可逐章检查并运行合规核对。')
        }
      } catch (error) {
        toast.warning('生成完成，但刷新页面数据失败', messageOf(error))
      }
    },
    [ref, outlineQuery, imagePlanQuery, reloadProject, reloadChapterData, toast, syncUser],
  )
  const finishJobRef = useRef(finishJob)
  finishJobRef.current = finishJob

  // Re-attach to an active generation job after a refresh or tab switch.
  useEffect(() => {
    let cancelled = false
    void getProjectJobs(ref)
      .then((items) => {
        if (cancelled || jobRef.current) return
        const active = items.find(
          (item) => item.status === 'queued' || item.status === 'running',
        )
        if (active) {
          setJob(active)
          toast.info('已恢复生成进度', '正在接续实时流；断线时自动回退到轮询。')
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return
        // Re-attach is best-effort; normal generation still exposes its job id,
        // but the writer should know that progress may not be live.
        toast.warning(
          '未能恢复生成进度',
          `后台任务可能仍在运行，可稍后刷新本章查看结果。${messageOf(error)}`,
        )
      })
    return () => {
      cancelled = true
    }
  }, [ref, toast])

  // Re-attach to an image batch job after refresh or tab switch.
  useEffect(() => {
    let cancelled = false
    void getProjectJobs(ref, 'images')
      .then((items) => {
        if (cancelled || imageBatchJobRef.current) return
        const active = items.find(
          (item) => item.status === 'queued' || item.status === 'running',
        )
        if (active) {
          setImageBatchJob(active)
          toast.info('已恢复配图任务', '正在接续后台生成进度。')
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return
        // Same as above for image batches: tell the writer instead of hiding it.
        toast.warning(
          '未能恢复配图任务',
          `后台配图可能仍在生成，可稍后在配图计划中刷新。${messageOf(error)}`,
        )
      })
    return () => {
      cancelled = true
    }
  }, [ref, toast])

  // Image batches are polled (coarse progress); watchJob owns the polling transport.
  useEffect(() => {
    const current = imageBatchJobRef.current
    if (!current || (current.status !== 'queued' && current.status !== 'running')) return
    const jobId = current.id
    let finished = false
    return watchJob(jobId, {
      onUpdate: (next) => setImageBatchJob(next as Job),
      onDone: (next) => {
        if (finished) return
        finished = true
        refreshImageDataRef.current()
        const failed = next.result?.failed ?? next.payload?.failed ?? 0
        if (autoApplyAfterBatch.current) {
          autoApplyAfterBatch.current = false
          if (failed > 0) {
            toast.warning(
              '配图生成完成，部分失败',
              `${next.result?.succeeded ?? 0} 个成功，${failed} 个失败；正在插入可用配图。`,
            )
          } else {
            toast.info('配图生成完成', '正在按计划自动插入正文…')
          }
          void autoApplyPlanRef.current?.()
          return
        }
        if (failed > 0) {
          toast.warning(
            '配图任务完成，部分失败',
            `${next.result?.succeeded ?? 0} 个成功，${failed} 个失败；可在配图计划中重试。`,
          )
        } else {
          toast.success('配图生成完成', next.message || '配图计划已更新，可逐章插入正文。')
        }
      },
      onFailed: (next) => {
        if (finished) return
        finished = true
        autoApplyAfterBatch.current = false
        refreshImageDataRef.current()
        toast.error('配图任务失败', next.error || next.message || '请检查图片模型后重试。')
      },
      onPollError: (message) => {
        setImageBatchJob((currentJob) =>
          currentJob ? { ...currentJob, status: 'failed', error: message } : currentJob,
        )
        toast.error('无法读取配图进度', message)
      },
    })
  }, [imageBatchJob?.id, toast])

  useEffect(() => {
    const currentJob = jobRef.current
    if (!currentJob || (currentJob.status !== 'queued' && currentJob.status !== 'running')) {
      return
    }
    const jobId = currentJob.id

    const applyJob = (data: Job | JobEventPayload) => {
      if (!data.id) return
      setJob((current) =>
        current && current.id === jobId ? { ...current, ...(data as Job) } : (data as Job),
      )
    }
    const finish = (finished: Job) => {
      setJob(finished)
      if (finished.status === 'failed') {
        toast.error(
          '全书生成失败',
          finished.error || finished.message || '请查看后端日志后重试。',
        )
        return
      }
      void finishJobRef.current(finished)
    }

    return watchJob(jobId, {
      stream: !streamFallback,
      onUpdate: applyJob,
      onEvent: (kind, data) => {
        if (kind === 'chapter_start') {
          const heading = data.heading || ''
          setStreamPreview((current) =>
            current?.heading === heading ? current : { heading, text: '' },
          )
          setJob((current) =>
            current
              ? {
                  ...current,
                  status: 'running',
                  message: data.heading ? `正在生成：${data.heading}` : current.message,
                  payload: {
                    ...current.payload,
                    heading: data.heading,
                    done: Math.max(0, (data.index || 1) - 1),
                    total: data.total || current.payload?.total,
                  },
                }
              : current,
          )
        } else if (kind === 'delta') {
          const heading = data.heading || ''
          const text = data.text || ''
          if (!text) return
          setStreamPreview((current) =>
            current?.heading === heading
              ? { ...current, text: current.text + text }
              : { heading, text },
          )
        } else if (kind === 'chapter') {
          setJob((current) =>
            current
              ? {
                  ...current,
                  progress: data.progress ?? current.progress,
                  message: data.heading
                    ? data.failed
                      ? `本章生成失败：${data.heading}`
                      : `已完成：${data.heading}`
                    : current.message,
                  payload: {
                    ...current.payload,
                    heading: data.heading,
                    done: data.done ?? current.payload?.done,
                    total: data.total ?? current.payload?.total,
                  },
                }
              : current,
          )
        }
      },
      onDone: finish,
      onFailed: finish,
      onSseFallback: () => setStreamFallback(true),
      onPollError: (message) => {
        setJob((current) =>
          current ? { ...current, status: 'failed', error: message } : current,
        )
        toast.error('无法读取生成进度', message)
      },
    })
  }, [job?.id, streamFallback, toast])

  const startFullGeneration = async () => {
    if (!canWrite || startingJob || jobActive || !outline.length) return
    const anyBody = outline.some((chapter) => chapter.has_body || (chapter.words || 0) > 0)
    if (anyBody || dirty) {
      const accepted = await confirm({
        title: anyBody ? '重新生成全书正文？' : '开始生成全书正文？',
        description: anyBody
          ? '已有正文会被覆盖，生成过程按章节推进。'
          : '当前章节的未保存修改会在生成完成后被服务端内容覆盖。',
        note: anyBody
          ? '建议先导出或留存重要内容。生成完成后仍可逐章修改。'
          : '生成使用已保存的大纲与事实台账。',
        confirmLabel: anyBody ? '覆盖并生成' : '开始生成',
        tone: anyBody ? 'danger' : 'primary',
      })
      if (!accepted) return
    }
    setStartingJob(true)
    setStreamFallback(false)
    setStreamPreview(null)
    try {
      const result = await startDocumentGeneration(ref, anyBody)
      setCreditGate(null)
      void syncUser()
      setJob({ id: result.job_id, status: 'queued', progress: 0 })
      toast.info('已开始生成全书', '进度会显示在页面上方，可以继续查看其他章节。')
    } catch (error) {
      handleError(error, '启动全书生成失败')
    } finally {
      setStartingJob(false)
    }
  }

  const startImageBatchGeneration = async (includeFailed: boolean) => {
    if (!canWrite || startingImageBatch || imageBatchActive) return
    const labels = includeFailed ? failedImageLabels : missingImageLabels
    if (!labels.size) return
    const accepted = await confirm({
      title: includeFailed ? '重试失败的配图？' : '生成缺失的配图？',
      description: `将为 ${labels.size} 个图名按顺序生成配图；同一图名只生成一次。`,
      note: '任务在后台运行，可能产生图片供应商费用；完成后仍需逐章确认插入正文。',
      confirmLabel: '开始生成',
      tone: includeFailed ? 'warning' : 'primary',
    })
    if (!accepted) return
    setStartingImageBatch(true)
    try {
      const result = await startImageBatch(ref, includeFailed)
      setImageBatchJob({ id: result.job_id, status: 'queued', progress: 0 })
      toast.info(
        '已开始生成配图',
        `${labels.size} 个图名正在后台生成，可继续编辑其他章节。`,
      )
    } catch (error) {
      toast.error('无法开始配图任务', messageOf(error))
    } finally {
      setStartingImageBatch(false)
    }
  }

  /**
   * 「生成并插入」：先批量生成缺失/失败配图，任务完成后自动接一次
   * 「按计划插入」，省掉两轮来回确认。
   */
  const startBatchAndApply = async () => {
    if (
      !canWrite ||
      startingImageBatch ||
      imageBatchActive ||
      applyingImagePlan ||
      dirty ||
      jobActive
    )
      return
    const summary = imagePlanQuery.data?.summary
    const missing = summary?.missing ?? 0
    const failed = summary?.failed ?? 0
    const toGenerate = missing + failed
    if (!toGenerate) return
    const accepted = await confirm({
      title: '生成缺失配图并自动插入？',
      description: `先为 ${toGenerate} 处缺失或失败的图名批量生成配图，完成后按章节与全文预算自动插入正文。`,
      note: '生成在后台进行，可能产生图片供应商费用；未匹配、失败或超出预算的占位会保留。',
      confirmLabel: '开始生成并插入',
    })
    if (!accepted) return
    setStartingImageBatch(true)
    autoApplyAfterBatch.current = true
    try {
      const result = await startImageBatch(ref, failed > 0)
      setImageBatchJob({ id: result.job_id, status: 'queued', progress: 0 })
      toast.info(
        '已开始生成配图',
        `${toGenerate} 处图名正在后台生成，完成后会自动插入正文。`,
      )
    } catch (error) {
      autoApplyAfterBatch.current = false
      toast.error('无法开始配图任务', messageOf(error))
    } finally {
      setStartingImageBatch(false)
    }
  }

  const applyPlanNow = async (ready: number, skipConfirm: boolean): Promise<boolean> => {
    if (
      !canWrite ||
      applyingPlanRef.current ||
      dirtyRef.current ||
      jobActiveRef.current ||
      imageBatchActiveRef.current ||
      !ready
    )
      return false
    if (!skipConfirm) {
      const accepted = await confirm({
        title: '按计划插入已匹配配图？',
        description: `将把 ${ready} 处已匹配占位替换为 Markdown 图片，并遵守每章与全文配图预算。`,
        note: '未匹配、失败或超出预算的占位会保留；操作会直接修改并保存章节正文。',
        confirmLabel: '插入配图',
      })
      if (!accepted) return false
    }
    applyingPlanRef.current = true
    setApplyingImagePlan(true)
    try {
      const result = await applyImagePlan(ref)
      const skipped =
        result.skipped_missing + result.skipped_failed + result.skipped_over_budget
      const overTotal = result.skipped_over_total || 0
      const parts: string[] = []
      if (result.applied) parts.push(`插入 ${result.applied} 张`)
      if (skipped) parts.push(`跳过 ${skipped} 处`)
      if (overTotal) parts.push(`全文预算跳过 ${overTotal} 处`)
      const detail = parts.length ? `${parts.join('，')}。` : ''
      if (result.applied) {
        toast.success('配图已按计划插入', detail || '章节正文已更新。')
      } else {
        toast.info('没有插入新的配图', detail || '没有可插入的配图。')
      }
      outlineQuery.refresh()
      refreshImageData()
      await reloadProject()
      if (selectedId && result.chapters.includes(selectedId)) {
        await reloadChapterData(selectedId)
      }
      return result.applied > 0
    } catch (error) {
      toast.error('按计划插入失败', messageOf(error))
      return false
    } finally {
      applyingPlanRef.current = false
      setApplyingImagePlan(false)
    }
  }

  const applyMatchedImages = () =>
    applyPlanNow(imagePlanQuery.data?.summary.ready || 0, false)

  /** 批量任务 done 后：先取最新计划，再静默按计划插入（不再二次确认）。 */
  const autoApplyAfterBatchDone = async () => {
    if (dirtyRef.current) {
      toast.info(
        '配图已生成，未自动插入',
        '当前章节有未保存修改，保存后再点「按计划插入」。',
      )
      return
    }
    const fresh: ImagePlan | null = await getImagePlan(ref).catch((error: unknown) => {
      toast.warning('配图已生成，但读取计划失败', messageOf(error))
      return null
    })
    if (!fresh) return
    imagePlanQuery.setData(fresh)
    if (!fresh.summary.ready) {
      toast.info('配图已生成', '暂无可自动插入的配图，可在配图计划中查看失败项。')
      return
    }
    await applyPlanNow(fresh.summary.ready, true)
  }
  autoApplyPlanRef.current = autoApplyAfterBatchDone

  const patchPolicyDraft = (patch: Partial<ImagePolicy>) =>
    setPolicyDraft((current) => (current ? { ...current, ...patch } : current))

  const savePolicy = async () => {
    if (!canWrite || !policyDraft || savingPolicy || !policyDirty) return
    const payload = clampImagePolicy(policyDraft)
    setSavingPolicy(true)
    try {
      const result = await updateImagePolicy(ref, payload)
      imagePolicyQuery.setData(result)
      setPolicyDraft({ ...result })
      imagePlanQuery.refresh()
      toast.success('配图策略已保存', policySummaryText(result))
    } catch (error) {
      toast.error('保存配图策略失败', messageOf(error))
    } finally {
      setSavingPolicy(false)
    }
  }

  const refreshAfterManual = () => {
    outlineQuery.refresh()
    void reloadProject()
    if (selectedId) {
      setLoadedId(null)
      void reloadChapterData(selectedId)
    }
  }

  const currentStatus = selectedChapter
    ? chapterStatus({
        status: chapterQuery.data?.status || selectedChapter.status,
        has_body: Boolean(body),
        words: liveWords,
      })
    : { label: '未选择', tone: 'neutral' as const }

  // 来源追溯：正文里每个命中台账取值的片段都可点开看出处。
  const citations = citationsQuery.data?.citations || []
  const staleCitations = citations.filter((item) => item.stale)
  const activeCitation = activeFactKey
    ? citations.find((item) => item.fact_key === activeFactKey) || null
    : null

  useEffect(() => {
    setActiveFactKey(null)
  }, [selectedId])

  useEffect(() => {
    const root = editorPreviewRef.current
    if (!preview || !root || loadedId !== selectedId) return
    markTraceHits(root, citations)
    // citations 来自 query 缓存，body 变化后需要重包
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preview, loadedId, selectedId, body, citationsQuery.data])

  return (
    <div className="tab-content draft-tab">
      <TabHeading
        eyebrow="正文写作"
        title="逐章写作与生成"
        description="正文只使用事实台账中的取值。没有依据的信息会保留 {{待填:...}} 占位，导出时标红。"
        actions={
          <>
            <Button
              variant="ghost"
              size="sm"
              icon={<RefreshCw size={14} />}
              onClick={refreshAfterManual}
              disabled={jobActive || dirty}
              title={dirty ? '先保存当前章节再刷新' : '重新读取服务器数据'}
            >
              刷新
            </Button>
            <Button
              variant="dark"
              size="sm"
              icon={<Sparkles size={14} />}
              loading={startingJob}
              disabled={!canWrite || !outline.length || jobActive || missingRequired > 0}
              onClick={() => void startFullGeneration()}
              title={
                !canWrite
                  ? '只读成员不能生成正文'
                  : missingRequired > 0
                    ? '先补齐必填事实'
                    : !outline.length
                      ? '先生成目录大纲'
                      : '生成全书正文'
              }
            >
              生成全书正文
            </Button>
          </>
        }
      />

      {creditGate && (
        <div className="credit-gate" role="alert">
          <div className="credit-gate-icon" aria-hidden>
            <Coins size={19} />
          </div>
          <div className="credit-gate-copy">
            <strong>点数不足</strong>
            <span>{creditGate}</span>
          </div>
          <Link to="/billing" className="btn btn--primary btn--sm">
            <Coins size={14} />
            <span className="btn-label">去充值</span>
          </Link>
        </div>
      )}

      {missingRequired > 0 && (
        <div className="inline-gate">
          <AlertTriangle size={16} />
          <span>
            还有 <strong>{missingRequired}</strong> 项必填事实缺失，生成正文会被拒绝。
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

      {job && (
        <section
          className={className('job-strip', `job-strip--${job.status}`)}
          aria-live="polite"
        >
          <div className="job-strip-icon" aria-hidden>
            {job.status === 'done' ? (
              <Check size={18} />
            ) : job.status === 'failed' ? (
              <AlertTriangle size={18} />
            ) : (
              <Loader2 size={18} className="spin" />
            )}
          </div>
          <div className="job-strip-main">
            <div className="job-strip-title">
              <strong>
                {job.status === 'done'
                  ? '全书生成完成'
                  : job.status === 'failed'
                    ? '全书生成失败'
                    : `正在生成全书 · ${JOB_STATUS_LABEL[job.status] || job.status}`}
              </strong>
              {job.payload?.total ? (
                <span className="mono">
                  {job.payload.done || 0} / {job.payload.total} 章
                </span>
              ) : (
                <span className="mono">{jobProgress(job.progress)}%</span>
              )}
            </div>
            <ProgressBar
              value={job.status === 'done' ? 100 : jobProgress(job.progress)}
              tone={job.status === 'failed' ? 'danger' : job.status === 'done' ? 'success' : 'accent'}
              thin
              label="全书生成进度"
            />
            <p className="job-strip-message">
              {job.error ||
                job.message ||
                (job.payload?.heading
                  ? `当前章节：${job.payload.heading}`
                  : '正在准备章节内容…')}
            </p>
          </div>
          {!jobActive && (
            <button
              type="button"
              className="icon-btn"
              onClick={() => {
                setJob(null)
                setStreamPreview(null)
              }}
              aria-label="关闭生成进度"
            >
              <X size={15} />
            </button>
          )}
        </section>
      )}

      {job && streamPreview?.text && (
        <section className="stream-preview" aria-live="polite">
          <div className="stream-preview-head">
            <span className="stream-preview-dot" aria-hidden />
            <strong>{streamPreview.heading || '实时预览'}</strong>
            <span className="mono">{formatWords(countWords(streamPreview.text))}</span>
          </div>
          <pre className="stream-preview-body">
            {streamPreview.text || '等待模型返回…'}
            <span className="stream-caret" aria-hidden />
          </pre>
          <p className="stream-preview-note">
            {streamFallback
              ? '实时流不可用，已回退到章节轮询；完成情况仍会自动刷新。'
              : '实时预览，最终以章节编辑器保存内容为准。'}
          </p>
        </section>
      )}

      {outlineQuery.error && !outline.length ? (
        <div className="panel">
          <ErrorState message={outlineQuery.error} onRetry={outlineQuery.refresh} />
        </div>
      ) : outlineQuery.loading && !outline.length ? (
        <DraftSkeleton />
      ) : outline.length === 0 ? (
        <div className="panel">
          <EmptyState
            icon={FileText}
            title="还没有目录大纲"
            description="正文按大纲章节组织。先编排章节并保存，再回到这里逐章生成或书写。"
            actions={
              <Link to={`${basePath}/outline`} className="btn btn--primary btn--md">
                <span className="btn-label">去编排大纲</span>
                <ArrowRight size={15} />
              </Link>
            }
          />
        </div>
      ) : (
        <div className="draft-layout">
          <aside className="chapter-rail">
            <div className="rail-head">
              <div className="rail-head-title">
                <span className="eyebrow">章节进度</span>
                <strong>
                  章节 <span className="muted mono">{writtenCount}/{outline.length}</span>
                </strong>
              </div>
              <ProgressBar
                value={percent(writtenCount, outline.length)}
                tone="accent"
                thin
                label={`章节进度 ${writtenCount}/${outline.length}`}
              />
              <div className="rail-summary">
                <span>正文 {formatWords(totalWrittenWords)}</span>
                <span>目标 {formatWords(totalTarget)}</span>
              </div>
            </div>
            <div className="chapter-list" role="listbox" aria-label="章节列表">
              {outline.map((chapter, index) => {
                const meta = chapterStatus(chapter)
                const words = chapter.words || 0
                return (
                  <button
                    key={chapter.id}
                    type="button"
                    role="option"
                    aria-selected={chapter.id === selectedId}
                    className={className(
                      'chapter-item',
                      chapter.id === selectedId && 'is-active',
                      chapter.has_body && 'has-body',
                    )}
                    onClick={() => void selectChapter(chapter.id)}
                    disabled={generatingChapter}
                  >
                    <span className="chapter-no mono">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <span className="chapter-item-main">
                      <span className="chapter-item-title">{chapter.heading}</span>
                      <span className="chapter-item-meta">
                        <span className="mono">
                          {formatNumber(words)} / {formatNumber(chapter.target_words)}
                        </span>
                        <span className={className('chapter-dot', chapter.has_body && 'is-on')} />
                      </span>
                      <span className="chapter-item-track" aria-hidden>
                        <span
                          style={{
                            width: `${percent(words, chapter.target_words || 0)}%`,
                          }}
                        />
                      </span>
                    </span>
                    <span className="chapter-item-status">
                      {chapter.citations_stale ? (
                        <Badge tone="warn" subtle>
                          需复核
                        </Badge>
                      ) : null}
                      {reviewStatus(chapter.review_status) !== 'draft' && (
                        <Badge tone={reviewMeta(chapter.review_status).tone} subtle>
                          {reviewMeta(chapter.review_status).label}
                        </Badge>
                      )}
                      <Badge tone={meta.tone} subtle>
                        {meta.label}
                      </Badge>
                    </span>
                  </button>
                )
              })}
            </div>
            <div className="rail-foot">
              <Clock3 size={13} />
              <span>Ctrl + S 保存当前章节</span>
            </div>
          </aside>

          <section className="editor-panel">
            {!selectedChapter ? (
              <EmptyState
                icon={Pencil}
                title="选择一个章节开始写作"
                description="左侧列出了大纲中的全部章节。"
              />
            ) : (
              <>
                <header className="editor-head">
                  <div className="editor-title-copy">
                    <div className="eyebrow">
                      第{' '}
                      {String(outline.indexOf(selectedChapter) + 1).padStart(2, '0')} /{' '}
                      {String(outline.length).padStart(2, '0')}
                    </div>
                    <h2>{selectedChapter.heading}</h2>
                    <div className="editor-meta">
                      <Badge tone={currentStatus.tone} subtle>
                        {currentStatus.label}
                      </Badge>
                      <Badge
                        tone={reviewMeta(chapterReview).tone}
                        title={reviewMeta(chapterReview).hint}
                      >
                        {reviewMeta(chapterReview).label}
                      </Badge>
                      <span>
                        目标 <strong>{formatNumber(targetWords)}</strong> 字
                      </span>
                      <span>
                        当前 <strong>{formatNumber(liveWords)}</strong> 字
                      </span>
                      <span className={liveWords >= targetWords && targetWords > 0 ? 'is-ok' : ''}>
                        {targetWords > 0
                          ? liveWords >= targetWords
                            ? `超出 ${formatNumber(liveWords - targetWords)} 字`
                            : `距目标 ${formatNumber(targetWords - liveWords)} 字`
                          : '未设目标字数'}
                      </span>
                      {dirty && <span className="unsaved-chip">未保存</span>}
                      {citations.length > 0 && (
                        <span
                          className={className(
                            'trace-summary',
                            staleCitations.length > 0 && 'is-stale',
                          )}
                          title="正文中命中事实台账取值的数量"
                        >
                          来源 <strong>{citations.length}</strong> 处
                          {staleCitations.length > 0
                            ? ` · ${staleCitations.length} 处待复核`
                            : ''}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="editor-actions">
                    <div className="segmented" role="group" aria-label="编辑或预览">
                      <button
                        type="button"
                        className={className(!preview && 'is-active')}
                        aria-pressed={!preview}
                        onClick={() => setPreview(false)}
                      >
                        <Pencil size={13} /> 编辑
                      </button>
                      <button
                        type="button"
                        className={className(preview && 'is-active')}
                        aria-pressed={preview}
                        onClick={() => setPreview(true)}
                      >
                        <Eye size={13} /> 预览
                      </button>
                    </div>
                    <Button
                      variant="secondary"
                      size="sm"
                      icon={<Save size={14} />}
                      loading={saving}
                      disabled={!canWrite || !dirty || reviewFrozen}
                      title={
                        reviewFrozen
                          ? '本章已确认，需先撤回为草稿才能修改'
                          : !canWrite
                            ? '只读成员不能保存正文'
                            : undefined
                      }
                      onClick={() => void save()}
                    >
                      保存
                    </Button>
                    <Button
                      variant={savedBody ? 'secondary' : 'primary'}
                      size="sm"
                      icon={<Wand2 size={14} />}
                      loading={generatingChapter}
                      disabled={!canWrite || jobActive || !loadedId || reviewFrozen}
                      title={
                        reviewFrozen
                          ? '本章已确认，需先撤回为草稿才能重新生成'
                          : !canWrite
                            ? '只读成员不能生成正文'
                            : undefined
                      }
                      onClick={() => void runChapter()}
                    >
                      {savedBody ? '重新生成本章' : '生成本章'}
                    </Button>
                  </div>
                </header>

                {(steps.length > 0 || reviewFrozen) && (
                  <div
                    className={className('review-bar', reviewFrozen && 'is-frozen')}
                    role="status"
                  >
                    <div className="review-bar-text">
                      <Badge tone={reviewMeta(chapterReview).tone} subtle>
                        {reviewMeta(chapterReview).label}
                      </Badge>
                      <span>{reviewMeta(chapterReview).hint}</span>
                      {chapterQuery.data?.confirmed_by && (
                        <span className="review-bar-meta">
                          确认人 {chapterQuery.data.confirmed_by}
                          {chapterQuery.data.confirmed_at
                            ? ` · ${formatRelative(chapterQuery.data.confirmed_at)}`
                            : ''}
                        </span>
                      )}
                      {chapterQuery.data?.locked_by && (
                        <span className="review-bar-meta">
                          锁定人 {chapterQuery.data.locked_by}
                        </span>
                      )}
                    </div>
                    <div className="review-bar-actions">
                      {steps.map((step) => (
                        <Button
                          key={step.action}
                          variant={step.variant}
                          size="sm"
                          loading={reviewBusy}
                          disabled={!canWrite || reviewBusy}
                          onClick={() => startReview(step.action, step.needsNote)}
                        >
                          {step.label}
                        </Button>
                      ))}
                    </div>
                  </div>
                )}

                {activeCitation && (
                  <div className="trace-card" role="status">
                    <div className="trace-card-head">
                      <span className="eyebrow">来源追溯</span>
                      <button
                        type="button"
                        className="trace-card-close"
                        onClick={() => setActiveFactKey(null)}
                        aria-label="关闭来源信息"
                      >
                        <X size={13} />
                      </button>
                    </div>
                    <div className="trace-card-title">
                      <strong>{activeCitation.label}</strong>
                      <code className="key-chip">{activeCitation.fact_key}</code>
                      <Badge tone={activeCitation.stale ? 'warn' : 'success'} subtle>
                        {activeCitation.stale ? '正文与台账不一致' : '与台账一致'}
                      </Badge>
                    </div>
                    <dl className="trace-card-fields">
                      <div>
                        <dt>正文引用</dt>
                        <dd>
                          {activeCitation.value}
                          {activeCitation.occurrences > 1
                            ? `（本章 ${activeCitation.occurrences} 处）`
                            : ''}
                        </dd>
                      </div>
                      <div>
                        <dt>台账当前值</dt>
                        <dd>{activeCitation.current_value || '（空）'}</dd>
                      </div>
                      <div>
                        <dt>原件出处</dt>
                        <dd>
                          {activeCitation.source_quote || '（未记录原文）'}
                          {activeCitation.source_page > 0
                            ? ` · 第 ${activeCitation.source_page} 页`
                            : ''}
                        </dd>
                      </div>
                    </dl>
                    {activeCitation.stale ? (
                      <p className="trace-card-hint">
                        台账已改为「{activeCitation.current_value}」，正文仍是旧值。
                        按新值修改正文并保存后，此处会自动恢复一致。
                      </p>
                    ) : null}
                  </div>
                )}

                <section className="image-studio" aria-label="图片工作台">
                  <div className="image-studio-head">
                    <div className="image-studio-title">
                      <span className="eyebrow">图片工作台</span>
                      <p>
                        {pendingImageTokens.length > 0
                          ? `本章有 ${pendingImageTokens.length} 处图片占位待插入，图名一致才能替换。`
                          : '生成或上传配图，插入正文后按 Ctrl+S 保存。'}
                      </p>
                    </div>
                    <label className="image-studio-auto" title="生成成功后自动插入正文">
                      <input
                        type="checkbox"
                        checked={autoInsertImage}
                        disabled={!canWrite}
                        onChange={(event) => setAutoInsertImage(event.target.checked)}
                      />
                      <span>生成后插入正文</span>
                    </label>
                  </div>
                  <div className="image-studio-grid">
                    <label className="image-studio-field">
                      <span>图名</span>
                      <input
                        className="input input--sm"
                        value={studioLabel}
                        maxLength={80}
                        placeholder="配图"
                        disabled={!canWrite}
                        onChange={(event) => {
                          studioLabelTouched.current = true
                          setStudioLabel(event.target.value)
                        }}
                      />
                    </label>
                    <label className="image-studio-field">
                      <span>画面风格</span>
                      <select
                        className="select select--sm"
                        value={studioStyle}
                        disabled={!canWrite}
                        onChange={(event) => setStudioStyle(event.target.value)}
                      >
                        {IMAGE_STYLE_PRESETS.map((preset) => (
                          <option key={preset.value || 'none'} value={preset.value}>
                            {preset.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="image-studio-field">
                      <span>尺寸</span>
                      <select
                        className="select select--sm"
                        value={studioSize}
                        disabled={!canWrite}
                        onChange={(event) => setStudioSize(event.target.value)}
                      >
                        {IMAGE_SIZE_PRESETS.map((preset) => (
                          <option key={preset.value} value={preset.value}>
                            {preset.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="image-studio-field image-studio-field--wide">
                      <span>画面描述（可选，留空按本章内容自动生成）</span>
                      <input
                        className="input input--sm"
                        value={studioPrompt}
                        maxLength={2000}
                        placeholder="例如：摄像机吸顶安装细节示意，深色线稿风格，不出现人名与公司标识"
                        disabled={!canWrite}
                        onChange={(event) => setStudioPrompt(event.target.value)}
                      />
                    </label>
                    <div className="image-studio-actions">
                      <Button
                        size="sm"
                        variant="secondary"
                        icon={<Upload size={13} />}
                        loading={uploadingImage}
                        disabled={
                          !canWrite ||
                          generatingStudio ||
                          uploadingImage ||
                          Boolean(generatingToken) ||
                          Boolean(regeneratingImageId)
                        }
                        title={
                          !canWrite
                            ? '只读成员不能上传图片'
                            : '上传本地 PNG / JPEG 图片（10MB 以内）'
                        }
                        onClick={() => studioFileInputRef.current?.click()}
                      >
                        上传本地图片
                      </Button>
                      <Button
                        size="sm"
                        variant="primary"
                        icon={<ImagePlus size={13} />}
                        loading={generatingStudio}
                        disabled={
                          !canWrite ||
                          uploadingImage ||
                          generatingStudio ||
                          Boolean(generatingToken) ||
                          Boolean(regeneratingImageId)
                        }
                        title={
                          !canWrite
                            ? '只读成员不能生成图片'
                            : generatingToken
                              ? '正在生成占位符配图，请稍候'
                              : `为「${studioLabel.trim() || '配图'}」生成配图`
                        }
                        onClick={() => void generateStudioImage()}
                      >
                        生成配图
                      </Button>
                    </div>
                  </div>
                  <input
                    ref={studioFileInputRef}
                    type="file"
                    accept="image/png,image/jpeg"
                    className="image-studio-file"
                    tabIndex={-1}
                    aria-hidden
                    onChange={(event) => void handleStudioFileChange(event)}
                  />
                </section>

                <details className="image-policy">
                  <summary>
                    <span className="image-policy-title">
                      <SlidersHorizontal size={13} aria-hidden />
                      <strong>整篇配图策略</strong>
                      {policyDirty && (
                        <span className="image-policy-dirty image-policy-dirty--summary">
                          未保存
                        </span>
                      )}
                      <span className="image-policy-summary">
                        {imagePolicy
                          ? policySummaryText(imagePolicy)
                          : '统一导出图注与全文预算'}
                      </span>
                    </span>
                    <span className="image-policy-caret" aria-hidden>
                      <ChevronRight size={14} />
                    </span>
                  </summary>
                  <div className="image-policy-body">
                    {!policyDraft ? (
                      imagePolicyQuery.error ? (
                        <div className="image-policy-message">
                          <span title={imagePolicyQuery.error}>
                            暂时读不到配图策略，可能是服务版本较旧。
                          </span>
                          <button
                            type="button"
                            className="text-btn"
                            onClick={() => imagePolicyQuery.refresh()}
                          >
                            重试
                          </button>
                        </div>
                      ) : (
                        <div className="image-policy-message">正在读取配图策略…</div>
                      )
                    ) : (
                      <>
                        <div className="image-policy-grid">
                          <label className="image-policy-field">
                            <span>全文配图上限</span>
                            <input
                              className="input input--sm input--number"
                              type="number"
                              min={0}
                              max={60}
                              step={1}
                              value={policyDraft.max_total}
                              disabled={!canWrite || savingPolicy}
                              onChange={(event) =>
                                patchPolicyDraft({
                                  max_total:
                                    event.target.value === ''
                                      ? 0
                                      : Number(event.target.value),
                                })
                              }
                              onBlur={() =>
                                setPolicyDraft((current) =>
                                  current ? clampImagePolicy(current) : current,
                                )
                              }
                            />
                            <small>0 = 不限，最多 60 张</small>
                          </label>
                          <label className="image-policy-field">
                            <span>图注编号前缀</span>
                            <input
                              className="input input--sm"
                              value={policyDraft.caption_prefix}
                              maxLength={10}
                              disabled={!canWrite || savingPolicy}
                              onChange={(event) =>
                                patchPolicyDraft({ caption_prefix: event.target.value })
                              }
                              onBlur={() =>
                                setPolicyDraft((current) =>
                                  current ? clampImagePolicy(current) : current,
                                )
                              }
                            />
                            <small>导出时按「前缀 + 序号」编号，例如 图1</small>
                          </label>
                          <label className="image-policy-field">
                            <span>默认画面风格</span>
                            <select
                              className="select select--sm"
                              value={policyDraft.style}
                              disabled={!canWrite || savingPolicy}
                              onChange={(event) =>
                                patchPolicyDraft({ style: event.target.value })
                              }
                            >
                              {styleOptions.map((preset) => (
                                <option key={preset.value || 'none'} value={preset.value}>
                                  {preset.label}
                                </option>
                              ))}
                            </select>
                            <small>批量生成时，未指定风格的配图沿用它</small>
                          </label>
                          <label className="image-policy-field">
                            <span>默认尺寸</span>
                            <select
                              className="select select--sm"
                              value={policyDraft.size}
                              disabled={!canWrite || savingPolicy}
                              onChange={(event) =>
                                patchPolicyDraft({ size: event.target.value })
                              }
                            >
                              {sizeOptions.map((preset) => (
                                <option key={preset.value} value={preset.value}>
                                  {preset.label}
                                </option>
                              ))}
                            </select>
                            <small>「自动」交给图片模型决定</small>
                          </label>
                          <label className="image-policy-field">
                            <span>导出图片宽度（cm）</span>
                            <input
                              className="input input--sm input--number"
                              type="number"
                              min={8}
                              max={19}
                              step={0.5}
                              value={policyDraft.width_cm}
                              disabled={!canWrite || savingPolicy}
                              onChange={(event) =>
                                patchPolicyDraft({
                                  width_cm:
                                    event.target.value === ''
                                      ? 0
                                      : Number(event.target.value),
                                })
                              }
                              onBlur={() =>
                                setPolicyDraft((current) =>
                                  current ? clampImagePolicy(current) : current,
                                )
                              }
                            />
                            <small>8 – 19 cm，按 0.5 递增</small>
                          </label>
                          <div className="image-policy-field">
                            <span>图注</span>
                            <label className="image-policy-toggle">
                              <input
                                type="checkbox"
                                checked={policyDraft.captions}
                                disabled={!canWrite || savingPolicy}
                                onChange={(event) =>
                                  patchPolicyDraft({ captions: event.target.checked })
                                }
                              />
                              <span>导出时自动生成图注</span>
                            </label>
                            <small>关闭后只放图，不加编号段落</small>
                          </div>
                        </div>
                        <div className="image-policy-foot">
                          <span className="image-policy-note">
                            策略作用于导出排版与批量生成的默认风格；已插入正文的图片不受影响。
                          </span>
                          <div className="image-policy-actions">
                            {policyDirty && (
                              <span className="image-policy-dirty">未保存</span>
                            )}
                            <Button
                              size="sm"
                              variant="secondary"
                              icon={<Save size={13} />}
                              loading={savingPolicy}
                              disabled={!canWrite || !policyDirty || savingPolicy}
                              title={
                                !canWrite
                                  ? '只读成员不能修改配图策略'
                                  : !policyDirty
                                    ? '改动后会启用保存'
                                    : undefined
                              }
                              onClick={() => void savePolicy()}
                            >
                              保存策略
                            </Button>
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                </details>

                {imagePlanQuery.data && imagePlanQuery.data.chapters.length > 0 && (
                  <section className="image-plan" aria-label="全文配图计划">
                    <div className="image-plan-head">
                      <div>
                        <span className="eyebrow">全文配图</span>
                        <h3>
                          配图计划
                          <span>{imagePlanQuery.data.summary.placeholders} 处占位</span>
                        </h3>
                      </div>
                      <div className="image-plan-summary">
                        {planDocument?.over_total && (
                          <Badge tone="warn" subtle dot>
                            已达全文上限
                          </Badge>
                        )}
                        <Badge tone="success" subtle>
                          已匹配 {imagePlanQuery.data.summary.ready}
                        </Badge>
                        <Badge tone="danger" subtle>
                          失败 {imagePlanQuery.data.summary.failed}
                        </Badge>
                        <Badge tone="warn" subtle>
                          缺失 {imagePlanQuery.data.summary.missing}
                        </Badge>
                        {imagePlanQuery.data.chapters.some((chapter) => chapter.over_budget) && (
                          <Badge tone="danger" subtle>
                            超预算{' '}
                            {
                              imagePlanQuery.data.chapters.filter(
                                (chapter) => chapter.over_budget,
                              ).length
                            }{' '}
                            章
                          </Badge>
                        )}
                      </div>
                      <div className="image-plan-actions">
                        {planToGenerate > 0 && (
                          <Button
                            size="sm"
                            variant="primary"
                            icon={<Sparkles size={13} />}
                            loading={startingImageBatch || applyingImagePlan}
                            disabled={
                              !canWrite ||
                              startingImageBatch ||
                              applyingImagePlan ||
                              dirty ||
                              jobActive ||
                              imageBatchActive ||
                              generatingStudio ||
                              Boolean(generatingToken)
                            }
                            title={
                              !canWrite
                                ? '只读成员不能生成配图'
                                : dirty
                                  ? '请先保存当前章节'
                                  : '先批量生成缺失配图，完成后按计划自动插入'
                            }
                            onClick={() => void startBatchAndApply()}
                          >
                            生成并插入
                          </Button>
                        )}
                        {planReady > 0 && (
                          <Button
                            size="sm"
                            variant={planToGenerate > 0 ? 'secondary' : 'primary'}
                            icon={<Wand2 size={13} />}
                            loading={applyingImagePlan}
                            disabled={
                              !canWrite ||
                              applyingImagePlan ||
                              startingImageBatch ||
                              dirty ||
                              jobActive ||
                              imageBatchActive
                            }
                            title={
                              !canWrite
                                ? '只读成员不能插入配图'
                                : dirty
                                  ? '请先保存当前章节'
                                  : undefined
                            }
                            onClick={() => void applyMatchedImages()}
                          >
                            按计划插入 {planReady}
                          </Button>
                        )}
                        {missingImageLabels.size > 0 && (
                          <Button
                            size="sm"
                            variant="secondary"
                            loading={startingImageBatch && !imageBatchActive}
                            disabled={
                              !canWrite || startingImageBatch || imageBatchActive
                            }
                            title={!canWrite ? '只读成员不能生成配图' : undefined}
                            onClick={() => void startImageBatchGeneration(false)}
                          >
                            生成缺失 {missingImageLabels.size}
                          </Button>
                        )}
                        {failedImageLabels.size > 0 && (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={
                              !canWrite || startingImageBatch || imageBatchActive
                            }
                            title={!canWrite ? '只读成员不能生成配图' : undefined}
                            onClick={() => void startImageBatchGeneration(true)}
                          >
                            重试失败 {failedImageLabels.size}
                          </Button>
                        )}
                      </div>
                    </div>
                    {planDocument && (
                      <div className="image-plan-doc">
                        <span>
                          全文已用{' '}
                          <strong className="mono">{planDocument.used}</strong> / 预算{' '}
                          <strong className="mono">
                            {planDocument.max_total > 0 ? planDocument.max_total : '不限'}
                          </strong>
                        </span>
                        {planDocument.remaining != null && !planDocument.over_total && (
                          <span className="image-plan-doc-extra">
                            还可插入 {planDocument.remaining} 张
                          </span>
                        )}
                        {planDocument.over_total && (
                          <span className="image-plan-doc-extra is-full">
                            新的配图会因全文上限被跳过
                          </span>
                        )}
                      </div>
                    )}
                    {imageBatchJob &&
                      (imageBatchActive || imageBatchJob.status === 'failed') && (
                        <div className="image-batch-status">
                          <div className="image-batch-line">
                            <span>
                              {imageBatchJob.message ||
                                (imageBatchActive ? '配图任务处理中…' : '配图任务失败')}
                            </span>
                            <span>
                              {imageBatchJob.payload?.done ?? 0}/
                              {imageBatchJob.payload?.total ?? 0}
                            </span>
                          </div>
                          {imageBatchActive ? (
                            <ProgressBar value={imageBatchJob.progress || 0} />
                          ) : (
                            <span className="image-batch-error">
                              {imageBatchJob.error || '请检查图片模型后重试。'}
                            </span>
                          )}
                        </div>
                      )}
                    <div className="image-plan-list">
                      {imagePlanQuery.data.chapters.map((chapter) => (
                        <button
                          type="button"
                          key={chapter.id}
                          className={className(
                            'image-plan-row',
                            chapter.id === selectedId && 'is-current',
                          )}
                          onClick={() => void selectChapter(chapter.id)}
                          title={`切换到「${chapter.heading}」`}
                        >
                          <span className="image-plan-chapter">{chapter.heading}</span>
                          <span className="image-plan-counts">
                            {chapter.total} 处 · 已匹配 {chapter.ready} · 失败 {chapter.failed} · 缺失{' '}
                            {chapter.missing}
                            {chapter.budget != null && ` · 预算 ${chapter.budget}`}
                            {chapter.over_budget ? ' · 超出预算' : ''}
                          </span>
                          <span className="image-plan-tags">
                            {chapter.placeholders.map((item) => (
                              <span
                                key={item.token}
                                className={className('image-plan-tag', `is-${item.status}`)}
                                title={
                                  item.status === 'failed'
                                    ? item.last_error || '生成失败，可重新生成'
                                    : item.status === 'ready'
                                      ? '已有同图名配图，可插入正文'
                                      : '尚未生成同图名配图'
                                }
                              >
                                {item.label}
                                {item.count > 1 ? ` ×${item.count}` : ''}
                              </span>
                            ))}
                          </span>
                        </button>
                      ))}
                    </div>
                    {imagePlanQuery.error && (
                      <div className="image-plan-error">
                        <span>{imagePlanQuery.error}</span>
                        <button
                          type="button"
                          className="text-btn"
                          onClick={() => imagePlanQuery.refresh()}
                        >
                          重试
                        </button>
                      </div>
                    )}
                  </section>
                )}

                {visiblePlaceholderCount > 0 && (
                  <details className="placeholder-note">
                    <summary>
                      <AlertTriangle size={14} />
                      待处理 {nonImagePlaceholders.length} 处待填 ·{' '}
                      {pendingImageTokens.length} 处图片占位
                      {insertedImageTokens.length > 0 && (
                        <span className="placeholder-inserted-count">
                          · 已插入 {insertedImageTokens.length} 处
                        </span>
                      )}
                    </summary>
                    <ul className="placeholder-list">
                      {nonImagePlaceholders.map((placeholder) => (
                        <li key={placeholder} className="placeholder-row">
                          <div className="placeholder-token">
                            <code>{placeholder}</code>
                            <Badge tone="warn" subtle>
                              待填
                            </Badge>
                          </div>
                        </li>
                      ))}
                      {allImageTokens.map((token) => {
                        const inserted = !body.includes(token)
                        const label = imagePlaceholderLabel(token)
                        // 只允许图名完全一致的 ready 图片替换占位符，
                        // 绝不拿最新的其他图片顶替。
                        const matchedAsset =
                          readyImages.find((image) => image.label === label) || null
                        return (
                          <li
                            key={token}
                            className={className(
                              'placeholder-row',
                              inserted && 'is-inserted',
                            )}
                          >
                            <div className="placeholder-token">
                              <code>{token}</code>
                              <Badge tone={inserted ? 'success' : 'accent'} subtle>
                                {inserted ? '已插入' : '图片占位'}
                              </Badge>
                            </div>
                            {!inserted && (
                              <div className="placeholder-actions">
                                <Button
                                  size="sm"
                                  variant="secondary"
                                  icon={<ImagePlus size={13} />}
                                  loading={generatingToken === token}
                                  disabled={
                                    !canWrite || Boolean(generatingToken) || generatingStudio
                                  }
                                  title={!canWrite ? '只读成员不能生成图片' : undefined}
                                  onClick={() => void generateImageFor(token)}
                                >
                                  生成图片
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  icon={<ImageIcon size={13} />}
                                  disabled={!canWrite || !matchedAsset}
                                  title={
                                    !canWrite
                                      ? '只读成员不能插入图片'
                                      : matchedAsset
                                        ? `插入「${label}」配图`
                                        : `先生成同图名配图「${label}」`
                                  }
                                  onClick={() => {
                                    if (matchedAsset) {
                                      insertImageAsset(matchedAsset, token)
                                    }
                                  }}
                                >
                                  插入正文
                                </Button>
                              </div>
                            )}
                          </li>
                        )
                      })}
                    </ul>
                  </details>
                )}

                <section className="chapter-gallery" aria-label="图片素材">
                  <div className="chapter-gallery-head">
                    <div>
                      <div className="eyebrow">图片素材</div>
                      <h3>
                        {galleryScope === 'chapter' ? '本章配图' : '项目图库'}
                        <span>
                          {visibleImages.filter((image) => image.status === 'ready').length} 张可用
                        </span>
                      </h3>
                    </div>
                    <div className="chapter-gallery-tools">
                      <div className="segmented" role="group" aria-label="图片范围">
                        <button
                          type="button"
                          className={className(galleryScope === 'chapter' && 'is-active')}
                          aria-pressed={galleryScope === 'chapter'}
                          onClick={() => setGalleryScope('chapter')}
                        >
                          本章
                        </button>
                        <button
                          type="button"
                          className={className(galleryScope === 'project' && 'is-active')}
                          aria-pressed={galleryScope === 'project'}
                          onClick={() => setGalleryScope('project')}
                        >
                          项目
                        </button>
                      </div>
                      <span className="chapter-gallery-note">
                        {canWrite
                          ? galleryScope === 'chapter'
                            ? '生成后插入正文，保存前可继续调整'
                            : '跨章节复用已有图片，插入到当前章节'
                          : '只读成员可查看，不能生成或删除'}
                      </span>
                    </div>
                  </div>

                  {imageError && (
                    <div className="chapter-gallery-error" role="alert">
                      <AlertTriangle size={14} />
                      <span>{imageError}</span>
                      <button
                        type="button"
                        className="icon-btn"
                        onClick={() => setImageError(null)}
                        aria-label="关闭图片错误提示"
                      >
                        <X size={13} />
                      </button>
                    </div>
                  )}

                  {galleryQuery.loading ? (
                    <div className="gallery-skeleton" aria-busy="true">
                      <Skeleton width={150} height={92} radius={6} />
                      <Skeleton width={150} height={92} radius={6} />
                    </div>
                  ) : galleryQuery.error ? (
                    <div className="chapter-gallery-error" role="alert">
                      <AlertTriangle size={14} />
                      <span>{galleryQuery.error}</span>
                      <button type="button" className="text-btn" onClick={galleryQuery.refresh}>
                        重试
                      </button>
                    </div>
                  ) : visibleImages.length === 0 ? (
                    <div className="chapter-gallery-empty">
                      <ImageOff size={18} />
                      <span>
                        {galleryScope === 'chapter' ? '尚未生成本章图片' : '项目还没有图片素材'}
                      </span>
                      <small>
                        {galleryScope === 'chapter'
                          ? '在上方图片工作台生成或上传本章配图。'
                          : '在任意章节生成或上传图片后，会出现在项目图库。'}
                      </small>
                    </div>
                  ) : (
                    <div className="chapter-gallery-grid">
                      {visibleImages.map((asset) => {
                        const ready = asset.status === 'ready'
                        const isUpload = asset.provider === 'upload'
                        const regenerating = regeneratingImageId === asset.id
                        const nodeHeading =
                          galleryScope === 'project'
                            ? outline.find((chapter) => chapter.id === asset.node_id)?.heading
                            : ''
                        return (
                          <article
                            key={asset.id}
                            className={className('gallery-card', !ready && 'is-failed')}
                          >
                            <div className="gallery-thumb">
                              {ready ? (
                                <img src={asset.url} alt={asset.label} loading="lazy" />
                              ) : (
                                <ImageOff size={20} />
                              )}
                            </div>
                            <div className="gallery-copy">
                              <strong>{asset.label || '未命名配图'}</strong>
                              {nodeHeading && (
                                <span className="gallery-node" title={nodeHeading}>
                                  {nodeHeading}
                                </span>
                              )}
                              <span>
                                {ready
                                  ? `${formatFileSize(asset.size_bytes)} · ${formatRelative(asset.created_at)}`
                                  : '生成失败'}
                              </span>
                              <div className="gallery-meta">
                                <Badge tone={ready ? 'success' : 'danger'} subtle>
                                  {ready ? '可用' : '失败'}
                                </Badge>
                                <span className="gallery-source" title={isUpload ? '本地上传' : asset.model || ''}>
                                  {isUpload ? '上传' : asset.model || 'AI 生成'}
                                </span>
                              </div>
                              {!ready && asset.last_error && (
                                <p className="gallery-failed-note">{asset.last_error}</p>
                              )}
                              {asset.prompt && (
                                <details className="gallery-prompt">
                                  <summary>查看提示词</summary>
                                  <p>{asset.prompt}</p>
                                </details>
                              )}
                            </div>
                            <div className="gallery-actions">
                              {ready && (
                                <>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    disabled={!canWrite}
                                    title={
                                      !canWrite
                                        ? '只读成员不能插入图片'
                                        : `插入「${asset.label}」`
                                    }
                                    onClick={() => insertImageAsset(asset)}
                                  >
                                    插入
                                  </Button>
                                  <IconButton
                                    label={`重新生成配图 ${asset.label || ''}`}
                                    disabled={!canWrite || Boolean(regeneratingImageId)}
                                    onClick={() => void regenerateImageAsset(asset)}
                                  >
                                    {regenerating ? (
                                      <RefreshCw size={13} className="spin" />
                                    ) : (
                                      <RefreshCw size={13} />
                                    )}
                                  </IconButton>
                                </>
                              )}
                              {!ready && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  icon={<RefreshCw size={12} />}
                                  loading={regenerating}
                                  disabled={!canWrite || Boolean(regeneratingImageId)}
                                  title={
                                    !canWrite
                                      ? '只读成员不能重新生成图片'
                                      : '使用原图名与设置重新生成'
                                  }
                                  onClick={() => void regenerateImageAsset(asset)}
                                >
                                  重新生成
                                </Button>
                              )}
                              <IconButton
                                label={`删除配图 ${asset.label || ''}`}
                                tone="danger"
                                disabled={!canWrite || deletingImageId === asset.id}
                                onClick={() => void removeImage(asset)}
                              >
                                {deletingImageId === asset.id ? (
                                  <RefreshCw size={13} className="spin" />
                                ) : (
                                  <Trash2 size={13} />
                                )}
                              </IconButton>
                            </div>
                          </article>
                        )
                      })}
                    </div>
                  )}
                </section>

                {chapterQuery.error ? (
                  <ErrorState
                    message={chapterQuery.error}
                    onRetry={chapterQuery.refresh}
                  />
                ) : chapterQuery.loading || loadedId !== selectedId ? (
                  <div className="editor-loading" aria-busy="true">
                    <Skeleton width="88%" height={16} />
                    <Skeleton width="96%" height={16} />
                    <Skeleton width="72%" height={16} />
                    <Skeleton width="90%" height={16} />
                    <Skeleton width="60%" height={16} />
                  </div>
                ) : preview ? (
                  <div
                    className="editor-preview"
                    ref={editorPreviewRef}
                    onClick={(event) => {
                      const hit = (event.target as HTMLElement).closest(
                        'mark.trace-hit',
                      ) as HTMLElement | null
                      if (hit?.dataset.factKey) setActiveFactKey(hit.dataset.factKey)
                    }}
                  >
                    <MarkdownPreview text={body} />
                  </div>
                ) : (
                  <textarea
                    ref={draftTextareaRef}
                    className="draft-textarea"
                    value={body}
                    onChange={(event) => setBody(event.target.value)}
                    onSelect={rememberCaret}
                    onClick={rememberCaret}
                    onKeyUp={rememberCaret}
                    placeholder="开始书写本章正文。支持 Markdown：# 标题、**加粗**、- 列表、| 表格 |。"
                    spellCheck={false}
                    aria-label={`${selectedChapter.heading} 正文`}
                    readOnly={!canWrite || reviewFrozen}
                    title={
                      reviewFrozen
                        ? '本章已确认，需先撤回为草稿才能编辑'
                        : !canWrite
                          ? '只读成员不能编辑正文'
                          : undefined
                    }
                  />
                )}

                <footer className="editor-statusbar">
                  <span>
                    <span className="mono">{formatNumber(liveWords)}</span> 字 · 目标{' '}
                    <span className="mono">{formatNumber(targetWords)}</span> 字
                  </span>
                  <span className="editor-hint">
                    {preview ? (
                      'Markdown 预览'
                    ) : (
                      <>
                        Markdown 编辑 · 图片语法 ![](/路径) · <kbd>Ctrl</kbd> + <kbd>S</kbd> 保存
                      </>
                    )}
                  </span>
                  <span className={className('dirty-flag', dirty && 'is-dirty')}>
                    {dirty ? '未保存' : '已保存'}
                  </span>
                </footer>
              </>
            )}
          </section>
        </div>
      )}

      <Modal
        open={Boolean(conflict)}
        onClose={() => {
          if (!resolvingConflict) setConflict(null)
        }}
        size="sm"
        eyebrow="保存冲突"
        title="章节已被他人修改"
        className="conflict-modal"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setConflict(null)}
              disabled={resolvingConflict}
            >
              保留我的修改
            </Button>
            <Button variant="secondary" onClick={loadLatestVersion} disabled={resolvingConflict}>
              载入最新
            </Button>
            <Button
              variant="danger"
              loading={resolvingConflict}
              onClick={() => void forceOverwrite()}
            >
              强制覆盖
            </Button>
          </>
        }
      >
        {conflict && (
          <div className="conflict-body">
            <div className="conflict-alert">
              <AlertTriangle size={17} aria-hidden />
              <p>
                {conflict.message || '章节已被他人修改，请载入最新或强制覆盖。'}
              </p>
            </div>
            <dl className="conflict-meta">
              <div>
                <dt>服务器版本</dt>
                <dd className="mono">r{conflict.current_revision}</dd>
              </div>
              <div>
                <dt>服务器字数</dt>
                <dd>{formatNumber(conflict.current_words)} 字</dd>
              </div>
              <div>
                <dt>你的版本</dt>
                <dd>{formatNumber(liveWords)} 字</dd>
              </div>
            </dl>
            <div className="conflict-preview">
              <span className="eyebrow">服务器上的最新正文</span>
              <pre>
                {conflict.current_body.trim()
                  ? `${conflict.current_body.slice(0, 320)}${
                      conflict.current_body.length > 320 ? '…' : ''
                    }`
                  : '（服务器正文为空）'}
              </pre>
            </div>
            <p className="conflict-note">
              载入最新会丢弃你尚未保存的修改；强制覆盖会以服务器最新版本为基准写入你的内容。
            </p>
          </div>
        )}
      </Modal>

      <Modal
        open={reviewNoteFor !== null}
        onClose={() => {
          setReviewNoteFor(null)
          setReviewNote('')
        }}
        title={reviewNoteFor === 'unlock' ? '解锁本章' : '撤回本章'}
        eyebrow="章节复核"
        size="sm"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => {
                setReviewNoteFor(null)
                setReviewNote('')
              }}
            >
              取消
            </Button>
            <Button
              variant="primary"
              loading={reviewBusy}
              disabled={reviewNote.trim().length < 4}
              onClick={() => reviewNoteFor && void applyReview(reviewNoteFor, reviewNote)}
            >
              {reviewNoteFor === 'unlock' ? '确认解锁' : '确认撤回'}
            </Button>
          </>
        }
      >
        <p className="review-note-hint">
          {reviewNoteFor === 'unlock'
            ? '解锁后本章回到「已确认」，正文仍不可直接编辑；要改还需撤回为草稿。'
            : '撤回后本章回到草稿，正文可以继续修改；已确认状态与确认人记录会被清空。'}
        </p>
        <label className="review-note-field">
          <span>原因（至少 4 个字，写入审计）</span>
          <textarea
            value={reviewNote}
            onChange={(event) => setReviewNote(event.target.value)}
            rows={3}
            placeholder="例如：客户要求补充分包说明"
          />
        </label>
      </Modal>
    </div>
  )
}

function DraftSkeleton() {
  return (
    <div className="draft-layout" aria-busy="true">
      <aside className="chapter-rail">
        <div className="rail-head">
          <Skeleton width="55%" height={14} />
          <Skeleton height={5} />
        </div>
        <div className="chapter-list">
          {Array.from({ length: 6 }).map((_, index) => (
            <div key={index} className="chapter-skeleton">
              <Skeleton width={20} height={12} />
              <div>
                <Skeleton width="80%" height={13} />
                <Skeleton width="48%" height={9} />
              </div>
            </div>
          ))}
        </div>
      </aside>
      <section className="editor-panel">
        <div className="editor-loading">
          <Skeleton width="38%" height={24} />
          <Skeleton width="90%" height={14} />
          <Skeleton width="100%" height={320} radius={6} />
        </div>
      </section>
    </div>
  )
}

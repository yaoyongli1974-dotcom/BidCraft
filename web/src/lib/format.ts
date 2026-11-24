import type {
  FactStatus,
  Issue,
  KnowledgeBase,
  OutlineChapter,
  ProjectDetail,
  ProjectStats,
  SourceRef,
} from './types'

export type Tone = 'success' | 'warn' | 'danger' | 'info' | 'neutral' | 'accent'

export interface Meta {
  label: string
  tone: Tone
}

export const ROLE_META: Record<string, Meta> = {
  super_admin: { label: '超级管理员', tone: 'accent' },
  manager: { label: '投标经理', tone: 'info' },
  viewer: { label: '只读成员', tone: 'neutral' },
}

export const ROLE_HINTS: Record<string, string> = {
  super_admin: '管理用户、模型与知识库，拥有全部项目权限',
  manager: '创建和维护项目、生成正文、执行核对与导出',
  viewer: '只读查看项目、台账、正文与核对结果',
}

export function roleMeta(role?: string, fallbackLabel?: string): Meta {
  if (!role) return { label: fallbackLabel || '未知角色', tone: 'neutral' }
  return ROLE_META[role] || { label: fallbackLabel || role, tone: 'neutral' }
}

export function roleHint(role?: string): string {
  return ROLE_HINTS[role || ''] || '权限由超级管理员配置'
}

// ------------------------------------------------------------------ project membership

/** Badge shown on project cards for the caller's role inside that project. */
export const PROJECT_ROLE_META: Record<string, Meta> = {
  owner: { label: '我负责', tone: 'accent' },
  editor: { label: '可编辑', tone: 'info' },
  viewer: { label: '只读', tone: 'neutral' },
  admin: { label: '管理员', tone: 'warn' },
}

export function projectRoleMeta(role?: string): Meta {
  if (!role) return { label: '成员', tone: 'neutral' }
  return PROJECT_ROLE_META[role] || { label: role, tone: 'neutral' }
}

/** Member rows only ever contain the three project roles below. */
export const MEMBER_ROLE_META: Record<string, Meta> = {
  owner: { label: '负责人', tone: 'accent' },
  editor: { label: '可编辑', tone: 'info' },
  viewer: { label: '只读', tone: 'neutral' },
}

export function memberRoleMeta(role?: string): Meta {
  if (!role) return { label: '成员', tone: 'neutral' }
  return MEMBER_ROLE_META[role] || { label: role, tone: 'neutral' }
}

export const MEMBER_ROLE_HINTS: Record<string, string> = {
  owner: '项目负责人，可管理成员与全部内容',
  editor: '可上传、编辑、生成与核对',
  viewer: '只能查看项目内容',
}

export const MODEL_KINDS = [
  {
    id: 'chat',
    label: '对话模型',
    singular: '对话',
    description: '用于大纲与正文生成；生成正文使用对话类型的默认模型。',
    tone: 'accent' as Tone,
  },
  {
    id: 'image',
    label: '图片模型',
    singular: '图片',
    description: '用于正文配图生成；由正文页的图片占位触发，结果保存在项目图片库。',
    tone: 'info' as Tone,
  },
  {
    id: 'embedding',
    label: '向量模型',
    singular: '向量',
    description: '用于语义向量与知识检索能力，不直接生成正文。',
    tone: 'warn' as Tone,
  },
] as const

export function modelKindMeta(kind?: string): Meta {
  return MODEL_KINDS.find((item) => item.id === kind) || {
    label: kind || '未知类型',
    tone: 'neutral',
  }
}

export const MODEL_PROVIDERS: Record<string, string> = {
  openai: 'OpenAI 兼容',
  ollama: 'Ollama',
  custom: '自定义端点',
}

export const KB_PROVIDERS: Record<string, string> = {
  local: '本地目录',
  generic: '通用 API',
  dify: 'Dify',
  ragflow: 'RAGFlow',
}

export function providerLabel(provider?: string): string {
  return MODEL_PROVIDERS[provider || ''] || provider || '未指定'
}

export function kbProviderLabel(provider?: string): string {
  return KB_PROVIDERS[provider || ''] || provider || '未指定'
}

export function kbKindMeta(kind?: string): Meta {
  if (kind === 'api') return { label: '第三方接口', tone: 'info' }
  if (kind === 'local') return { label: '本地目录', tone: 'success' }
  return { label: kind || '未知类型', tone: 'neutral' }
}

/**
 * Vector-index status of a knowledge base. Older servers send none of the
 * `vector_*` fields, so anything unset reads as keyword-only.
 */
export function kbVectorMeta(kb: KnowledgeBase): Meta {
  const total = Math.max(0, kb.vector_total ?? kb.chunk_count ?? 0)
  const embedded = Math.max(0, Math.min(kb.vector_embedded ?? 0, total))
  if (kb.vector_enabled === false || !kb.vector_model || total === 0) {
    return { label: '仅关键词', tone: 'neutral' }
  }
  if (kb.vector_ready === true || embedded >= total) {
    return { label: '语义就绪', tone: 'success' }
  }
  return {
    label: `向量待更新 ${formatNumber(embedded)}/${formatNumber(total)}`,
    tone: 'warn',
  }
}

// ------------------------------------------------------------------ project types

export interface ProjectTypeDef {
  id: string
  label: string
  tone: Tone
  description: string
}

/** Fixed project type taxonomy shared by the admin console and the new project dialog. */
export const PROJECT_TYPES: ProjectTypeDef[] = [
  {
    id: 'weakcurrent',
    label: '弱电智能化',
    tone: 'accent',
    description: '综合布线、安防、楼宇自控与机房工程',
  },
  {
    id: 'fire',
    label: '消防工程',
    tone: 'danger',
    description: '火灾自动报警、喷淋、消火栓与防排烟',
  },
  {
    id: 'installation',
    label: '安装工程',
    tone: 'info',
    description: '机电安装、设备安装与管道工程',
  },
  {
    id: 'civil',
    label: '土建工程',
    tone: 'warn',
    description: '建筑结构、市政与土石方工程',
  },
  {
    id: 'hvac',
    label: '暖通空调',
    tone: 'success',
    description: '供暖、通风、空调与洁净工程',
  },
  {
    id: 'decoration',
    label: '装饰装修',
    tone: 'neutral',
    description: '室内外装饰、幕墙与精装修',
  },
  {
    id: 'other',
    label: '其他',
    tone: 'neutral',
    description: '未归类或其他行业的技术标',
  },
]

const COMMON_PROJECT_TYPE: ProjectTypeDef = {
  id: 'common',
  label: '通用',
  tone: 'neutral',
  description: '仅作为抽象基包的通用投标事实',
}

const PROJECT_TYPE_BY_ID: Record<string, ProjectTypeDef> = Object.fromEntries(
  [COMMON_PROJECT_TYPE, ...PROJECT_TYPES].map((item) => [item.id, item]),
)

/** Includes the abstract `common` base package, for the admin inventory. */
export const ALL_PROJECT_TYPES: ProjectTypeDef[] = [COMMON_PROJECT_TYPE, ...PROJECT_TYPES]

/** Falls back to `other` for missing or unrecognised type keys. */
export function normalizeProjectType(type?: string | null): string {
  const key = String(type || '').trim().toLowerCase()
  return PROJECT_TYPE_BY_ID[key] ? key : 'other'
}

export function projectTypeMeta(type?: string | null): ProjectTypeDef {
  return PROJECT_TYPE_BY_ID[normalizeProjectType(type)] || COMMON_PROJECT_TYPE
}

export function projectTypeLabel(type?: string | null, fallbackLabel?: string | null): string {
  const meta = projectTypeMeta(type)
  if (meta.id !== 'other') return meta.label
  const fallback = String(fallbackLabel || '').trim()
  return fallback || meta.label
}

/** Project types that can be chosen when creating a project (the base package is abstract). */
export const SELECTABLE_PROJECT_TYPES = PROJECT_TYPES.filter((item) => item.id !== 'common')

export function testResultMeta(ok?: boolean | null): Meta {
  if (ok === true) return { label: '测试通过', tone: 'success' }
  if (ok === false) return { label: '测试失败', tone: 'danger' }
  return { label: '未测试', tone: 'neutral' }
}

export function formatLatency(ms?: number): string {
  if (ms === undefined || ms === null || Number.isNaN(ms)) return ''
  return `${Math.max(0, Math.round(ms))} ms`
}

export function formatCents(cents?: number | null): string {
  if (cents === undefined || cents === null || Number.isNaN(cents)) return '—'
  return `¥${(Math.max(0, cents) / 100).toFixed(2)}`
}

export const CREDIT_KIND: Record<string, Meta> = {
  signup: { label: '注册赠送', tone: 'success' },
  recharge: { label: '充值', tone: 'success' },
  redeem: { label: '兑换', tone: 'accent' },
  consume: { label: '生成消耗', tone: 'warn' },
  refund: { label: '失败退回', tone: 'info' },
  admin: { label: '管理员调整', tone: 'neutral' },
}

export function creditKindMeta(kind?: string): Meta {
  return CREDIT_KIND[kind || ''] || { label: kind || '点数变动', tone: 'neutral' }
}

export const ORDER_STATUS: Record<string, Meta> = {
  pending: { label: '待支付', tone: 'warn' },
  paid: { label: '已支付', tone: 'success' },
  closed: { label: '已关闭', tone: 'neutral' },
  expired: { label: '已过期', tone: 'neutral' },
  failed: { label: '支付失败', tone: 'danger' },
}

export function orderStatusMeta(status?: string): Meta {
  return ORDER_STATUS[status || ''] || { label: status || '未知', tone: 'neutral' }
}

export const REDEEM_STATUS: Record<string, Meta> = {
  unused: { label: '未使用', tone: 'success' },
  used: { label: '已使用', tone: 'neutral' },
  disabled: { label: '已停用', tone: 'danger' },
}

export function redeemStatusMeta(status?: string): Meta {
  return REDEEM_STATUS[status || ''] || { label: status || '未知', tone: 'neutral' }
}

export function signedCredits(amount?: number): string {
  if (amount === undefined || amount === null || Number.isNaN(amount)) return '—'
  return `${amount > 0 ? '+' : ''}${formatNumber(amount)}`
}

export function shortId(value?: string | null, length = 8): string {
  if (!value) return '—'
  return value.length > length ? `${value.slice(0, length)}…` : value
}

export const PROJECT_STATUS: Record<string, Meta> = {
  intake: { label: '待导入', tone: 'neutral' },
  new: { label: '新建', tone: 'neutral' },
  analyzed: { label: '已解析', tone: 'info' },
  parsing: { label: '解析中', tone: 'info' },
  drafting: { label: '撰写中', tone: 'accent' },
  writing: { label: '撰写中', tone: 'accent' },
  review: { label: '核对中', tone: 'warn' },
  checking: { label: '核对中', tone: 'warn' },
  ready: { label: '可交付', tone: 'success' },
  exported: { label: '已导出', tone: 'success' },
  delivered: { label: '已交付', tone: 'success' },
  archived: { label: '已归档', tone: 'neutral' },
  failed: { label: '异常', tone: 'danger' },
}

export function projectStatus(status?: string): Meta {
  if (!status) return { label: '未知', tone: 'neutral' }
  return PROJECT_STATUS[status.toLowerCase()] || { label: status, tone: 'neutral' }
}

/** 后台任务状态（bidcraft/models.py 的 Job.status），供任务统计等处复用。 */
export const JOB_STATUS: Record<string, Meta> = {
  queued: { label: '排队中', tone: 'neutral' },
  running: { label: '运行中', tone: 'info' },
  done: { label: '已完成', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
  cancelled: { label: '已取消', tone: 'neutral' },
  waiting_approval: { label: '待审批', tone: 'warn' },
}

export function jobStatus(status?: string): Meta {
  if (!status) return { label: '未知', tone: 'neutral' }
  return JOB_STATUS[status.toLowerCase()] || { label: status, tone: 'neutral' }
}

export const FACT_STATUS: Record<FactStatus, Meta> = {
  extracted: { label: '已提取', tone: 'info' },
  confirmed: { label: '已确认', tone: 'success' },
  manual: { label: '手工录入', tone: 'accent' },
  missing: { label: '缺失', tone: 'danger' },
  conflict: { label: '取值冲突', tone: 'warn' },
}

export function factStatus(status?: string): Meta {
  if (!status) return { label: '未知', tone: 'neutral' }
  return FACT_STATUS[status as FactStatus] || { label: status, tone: 'neutral' }
}

export const PARSE_STATE: Record<string, Meta> = {
  pending: { label: '待解析', tone: 'neutral' },
  parsing: { label: '解析中', tone: 'info' },
  parsed: { label: '已解析', tone: 'success' },
  needs_ocr: { label: '需 OCR', tone: 'warn' },
  failed: { label: '解析失败', tone: 'danger' },
}

export function parseState(state?: string): Meta {
  if (!state) return { label: '待解析', tone: 'neutral' }
  return PARSE_STATE[state.toLowerCase()] || { label: state, tone: 'neutral' }
}

export function chapterStatus(chapter: {
  status?: string
  has_body?: boolean
  words?: number
}): Meta {
  const normalized = chapter.status?.toLowerCase()
  if (normalized === 'failed') return { label: '生成失败', tone: 'danger' }
  if (normalized === 'stale') return { label: '待更新', tone: 'warn' }
  if (chapter.has_body) return { label: '已生成', tone: 'success' }
  if (normalized === 'draft' || normalized === 'drafting' || (chapter.words || 0) > 0) {
    return { label: '草稿', tone: 'warn' }
  }
  if (normalized && ['generated', 'written', 'done'].includes(normalized)) {
    return { label: '已生成', tone: 'success' }
  }
  return { label: '未生成', tone: 'neutral' }
}

export const ISSUE_LEVEL: Record<string, Meta> = {
  blocking: { label: '阻断', tone: 'danger' },
  critical: { label: '严重', tone: 'danger' },
  major: { label: '主要', tone: 'warn' },
  minor: { label: '提示', tone: 'info' },
  info: { label: '提示', tone: 'neutral' },
}

export function issueLevel(level?: string): Meta {
  if (!level) return { label: '提示', tone: 'neutral' }
  return ISSUE_LEVEL[level.toLowerCase()] || { label: level, tone: 'neutral' }
}

export const VERDICT: Record<string, { label: string; tone: Tone; description: string }> = {
  pass: {
    label: '核对通过',
    tone: 'success',
    description: '未发现阻断项或需要关注的问题，可以继续导出。',
  },
  warn: {
    label: '需要关注',
    tone: 'warn',
    description: '存在非阻断问题，建议逐条确认后再交付。',
  },
  blocked: {
    label: '存在阻断项',
    tone: 'danger',
    description: '有阻断级问题未解决，暂不建议交付。',
  },
}

export function verdictMeta(verdict?: string) {
  return (
    VERDICT[verdict || ''] || {
      label: '核对结果',
      tone: 'neutral' as Tone,
      description: '查看下方问题明细。',
    }
  )
}

export function riskLevel(level?: string): Meta {
  const known: Record<string, Meta> = {
    blocking: { label: '废标级', tone: 'danger' },
    critical: { label: '高风险', tone: 'danger' },
    high: { label: '高风险', tone: 'danger' },
    major: { label: '中风险', tone: 'warn' },
    medium: { label: '中风险', tone: 'warn' },
    minor: { label: '提示', tone: 'info' },
    low: { label: '低风险', tone: 'info' },
    info: { label: '提示', tone: 'neutral' },
  }
  if (!level) return { label: '未分级', tone: 'neutral' }
  return known[level.toLowerCase()] || { label: level, tone: 'neutral' }
}

export function requirementKind(kind?: string): string {
  const known: Record<string, string> = {
    qualification: '资格',
    substantive: '实质性',
    mandatory: '实质性',
    format: '格式',
    commercial: '商务',
    technical: '技术',
    other: '其他',
  }
  if (!kind) return '要求'
  return known[kind.toLowerCase()] || kind
}

export const OUTLINE_MODES = [
  {
    id: 'auto',
    title: '自动编排',
    description: '结合规则包与已提取事实，自动平衡章节与字数。',
  },
  {
    id: 'package',
    title: '规则包骨架',
    description: '使用行业规则包内置章节，结构稳定、覆盖完整。',
  },
  {
    id: 'scoring',
    title: '评分导向',
    description: '围绕评分办法组织章节，优先覆盖得分点。',
  },
  {
    id: 'tender-format',
    title: '招标格式',
    description: '对齐招标文件“投标文件组成/格式”规定的顺序。',
  },
] as const

export function outlineMode(mode?: string): string {
  return OUTLINE_MODES.find((item) => item.id === mode)?.title || mode || '自动编排'
}

export function projectStats(project: ProjectDetail): ProjectStats {
  const summary = project.ledger_summary || {}
  const outlineArray = Array.isArray(project.outline) ? project.outline : undefined
  const stats = project.stats || {}
  return {
    sources:
      numberOr(stats.sources, undefined) ??
      project.sources?.length ??
      numberOr(summary.sources, 0) ??
      0,
    facts_total:
      numberOr(stats.facts_total, undefined) ??
      numberOr(summary.fields_total, 0) ??
      0,
    facts_filled:
      numberOr(stats.facts_filled, undefined) ??
      numberOr(summary.fields_filled, 0) ??
      0,
    missing_required:
      numberOr(stats.missing_required, undefined) ??
      numberOr(summary.missing_required, project.missing_required?.length ?? 0) ??
      0,
    outline:
      numberOr(stats.outline, undefined) ??
      (typeof project.outline === 'number' ? project.outline : outlineArray?.length) ??
      0,
    written:
      numberOr(stats.written, undefined) ??
      outlineArray?.filter((chapter) => chapter.has_body || (chapter.words || 0) > 0).length ??
      0,
    issues_open: numberOr(stats.issues_open, 0) ?? 0,
  }
}

function numberOr(value: unknown, fallback: number | undefined): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    return Number(value)
  }
  return fallback
}

export function outlineCount(project: ProjectDetail): number {
  if (Array.isArray(project.outline)) return project.outline.length
  if (typeof project.outline === 'number') return project.outline
  return projectStats(project).outline
}

export function factGroups(key: string): { id: string; label: string; order: number } {
  const prefix = key.split('.')[0]?.toLowerCase() || ''
  if (prefix === 'project' || prefix === 'tender') {
    return { id: 'project', label: '项目信息', order: 0 }
  }
  if (prefix === 'commercial' || prefix === 'scoring') {
    return { id: 'commercial', label: '商务与评分', order: 1 }
  }
  if (prefix === 'qualification' || prefix === 'personnel') {
    return { id: 'qualification', label: '资格与人员', order: 2 }
  }
  if (
    prefix === 'technical' ||
    prefix === 'tech' ||
    prefix === 'cabling' ||
    prefix === 'cctv' ||
    prefix === 'ba' ||
    prefix === 'security' ||
    prefix === 'system'
  ) {
    return { id: 'technical', label: '技术需求', order: 3 }
  }
  if (prefix === 'bidder') return { id: 'bidder', label: '投标人信息', order: 4 }
  return { id: 'other', label: '其他事实', order: 5 }
}

export function sourceLabel(source?: SourceRef): string {
  if (!source || (!source.file && !source.page && !source.quote)) return '无自动出处'
  const page = source.page ? `第 ${source.page} 页` : ''
  if (source.file && page) return `${source.file} · ${page}`
  return source.file || page || '查看原文'
}

export function formatFileSize(bytes?: number): string {
  if (bytes === undefined || bytes === null || Number.isNaN(bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export function parseDate(value?: string | null): Date | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDate(value?: string | null, withTime = true): string {
  const date = parseDate(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    ...(withTime ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}),
  })
    .format(date)
    .replace(/\//g, '-')
}

export function formatRelative(value?: string | null): string {
  const date = parseDate(value)
  if (!date) return '—'
  const diff = Date.now() - date.getTime()
  if (diff < 0) return formatDate(value)
  const minute = 60_000
  const hour = 60 * minute
  const day = 24 * hour
  if (diff < minute) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`
  if (diff < 7 * day) return `${Math.floor(diff / day)} 天前`
  return formatDate(value, false)
}

export function formatNumber(value?: number | string | null): string {
  if (value === undefined || value === null) return '—'
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed) return '—'
    const numeric = Number(trimmed)
    if (!Number.isFinite(numeric)) return trimmed
    return new Intl.NumberFormat('zh-CN').format(numeric)
  }
  if (!Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('zh-CN').format(value)
}

/** 金额（元）：固定小数位、千分位；非数字显示为破折号。 */
export function formatMoney(value?: number | null, digits = 2): string {
  if (value === undefined || value === null || !Number.isFinite(value)) return '—'
  const places = Math.max(0, Math.min(4, Math.round(digits)))
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  }).format(value)
}

export function formatWords(value?: number): string {
  if (value === undefined || value === null) return '—'
  if (value >= 10000) {
    const text = (value / 10000).toFixed(value % 10000 === 0 ? 0 : 1)
    return `${text} 万字`
  }
  return `${formatNumber(value)} 字`
}

export function percent(part: number, total: number): number {
  if (!total || total <= 0) return 0
  return Math.max(0, Math.min(100, Math.round((part / total) * 100)))
}

export function countWords(text: string): number {
  if (!text) return 0
  const cleaned = text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`[^`]*`/g, ' ')
    .replace(/[#>*_~|]/g, ' ')
  const cjk = cleaned.match(/[\u3400-\u9fff\u3040-\u30ff]/g)?.length || 0
  const latin = cleaned.match(/[A-Za-z0-9]+/g)?.length || 0
  return cjk + latin
}

export function placeholders(text: string): string[] {
  if (!text) return []
  const matches = text.match(/\{\{\s*(?:待填|图片占位)[:：][^}]*\}\}/g) || []
  return Array.from(new Set(matches))
}

export function confidenceText(confidence?: number): string {
  if (confidence === undefined || confidence === null || confidence <= 0) return '—'
  const normalized = confidence <= 1 ? confidence * 100 : confidence
  return `${Math.round(normalized)}%`
}

export function confidencePercent(confidence?: number): number {
  if (confidence === undefined || confidence === null || confidence <= 0) return 0
  return Math.max(0, Math.min(100, Math.round(confidence <= 1 ? confidence * 100 : confidence)))
}

export function evidenceText(evidence: unknown): string {
  if (evidence === undefined || evidence === null) return ''
  if (typeof evidence === 'string') return evidence
  if (typeof evidence === 'number' || typeof evidence === 'boolean') return String(evidence)
  try {
    return JSON.stringify(evidence, null, 2)
  } catch {
    return String(evidence)
  }
}

/**
 * 问题处置状态元数据。新契约取值 open / handled / ignored；
 * 旧值 resolved / closed 兼容显示为「已处理」。
 */
export const ISSUE_STATUS: Record<string, Meta> = {
  open: { label: '待处理', tone: 'warn' },
  handled: { label: '已处理', tone: 'success' },
  resolved: { label: '已处理', tone: 'success' },
  closed: { label: '已处理', tone: 'success' },
  ignored: { label: '已忽略', tone: 'neutral' },
}

export function issueStatusMeta(status?: string): Meta {
  if (!status) return { label: '待处理', tone: 'warn' }
  return ISSUE_STATUS[status.toLowerCase()] || { label: status, tone: 'neutral' }
}

/** 一条问题是否已离开「待处理」（含旧值 resolved / closed）。 */
export function issueResolved(status?: string): boolean {
  const normalized = (status || '').toLowerCase()
  return ['handled', 'ignored', 'resolved', 'closed'].includes(normalized)
}

/**
 * 交付前风险总览的四档标签。配色沿用 ISSUE_LEVEL 的 tone，
 * 只是把 minor 与 info 合并成「提示」一档，不另造配色体系。
 */
export const RISK_TIER: Record<string, Meta> = {
  blocking: { label: '废标/阻断', tone: 'danger' },
  critical: { label: '严重', tone: 'danger' },
  major: { label: '主要', tone: 'warn' },
  minor: { label: '提示', tone: 'info' },
}

export function riskTier(level?: string): Meta {
  const normalized = (level || '').toLowerCase()
  // info 并入「提示」档；未知等级也落入提示档，不凭空造出第五档。
  const key = normalized === 'info' ? 'minor' : normalized
  return RISK_TIER[key] || RISK_TIER.minor
}

export function issueStatus(status?: string): string {
  if (!status) return ''
  return ISSUE_STATUS[status.toLowerCase()]?.label || status
}

export function issueTitle(issue: Issue): string {
  return issue.finding || issue.message || issue.code || '核对问题'
}

export function truncate(text: string, length: number): string {
  if (!text) return ''
  return text.length > length ? `${text.slice(0, length)}…` : text
}

export function readStorage<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) return fallback
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

export function writeStorage(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Storage can be disabled in hardened browsers; the UI still works.
  }
}

export function className(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ')
}

export function chapterNeeds(chapter: OutlineChapter): string[] {
  return Array.isArray(chapter.needs) ? chapter.needs : []
}

// ------------------------------------------------------------------ audit

/** Chinese labels for the fixed action keys in the audit contract. */
export const ACTION_LABELS: Record<string, string> = {
  'project.create': '新建项目',
  'project.delete': '删除项目',
  'source.upload': '上传招标文件',
  'source.delete': '删除招标文件',
  'parse.run': '运行解析',
  'fact.update': '更新事实',
  'outline.save': '保存大纲',
  'chapter.save': '保存章节',
  'chapter.generate': '生成章节',
  'project.generate': '生成全书',
  'check.run': '运行核对',
  'export.run': '导出文件',
  'boq.upload': '上传清单',
  'boq.apply': '应用清单变更',
  'boq.price': '设置清单单价',
  'boq.delete': '删除清单',
  'boq.quota_fill': '套用企业定额',
  'boq.adjust': '按系数调价',
  'quota.create': '新增定额',
  'quota.update': '更新定额',
  'quota.delete': '删除定额',
  'term.create': '新增术语',
  'term.update': '更新术语',
  'term.delete': '删除术语',
  'price.policy': '调整调价策略',
  'image.generate': '生成配图',
  'image.upload': '上传配图',
  'image.regenerate': '重新生成配图',
  'image.delete': '删除配图',
  'image.apply_plan': '按计划插入配图',
  'image.batch': '批量生成配图',
  'image.policy': '调整配图策略',
  'member.add': '添加成员',
  'member.update': '调整成员权限',
  'member.remove': '移除成员',
  'package.create': '新建规则包',
  'package.update': '更新规则包',
  'package.delete': '删除规则包',
  'package.restore': '恢复规则包',
  'plugin.create': '新增插件',
  'plugin.update': '更新插件',
  'plugin.delete': '删除插件',
  'plugin.test': '测试插件',
  'plugin.call': '调用插件工具',
  'boq.version_save': '保存报价版本',
  'boq.version_restore': '恢复报价版本',
  'boq.version_delete': '删除报价版本',
  'wechat.config': '更新微信开放平台配置',
}

/** Unknown keys fall back to the raw action key so new backend actions stay visible. */
export function actionLabel(action?: string): string {
  if (!action) return '操作'
  return ACTION_LABELS[action] || action
}

/** Audit rows may be written by the server itself (`username: "system"`). */
export function auditActor(entry: { username?: string; display_name?: string }): string {
  if (!entry.username || entry.username === 'system') {
    return entry.display_name || '系统'
  }
  return entry.display_name || entry.username
}

export function auditActionTone(action?: string): Tone {
  const key = action || ''
  if (key.startsWith('member.')) return 'accent'
  if (key === 'project.delete' || key.endsWith('.delete')) return 'danger'
  if (key === 'check.run') return 'warn'
  if (key === 'boq.adjust' || key === 'price.policy') return 'warn'
  if (key === 'export.run') return 'info'
  if (
    key.startsWith('package.') ||
    key.startsWith('quota.') ||
    key.startsWith('term.')
  ) {
    return 'info'
  }
  if (key.endsWith('.upload') || key.endsWith('.generate')) return 'success'
  return 'neutral'
}

const AUDIT_DETAIL_LABELS: Record<string, string> = {
  filename: '文件',
  heading: '章节',
  name: '名称',
  username: '成员',
  role: '角色',
  count: '数量',
  format: '格式',
  labels: '图名',
  label: '图名',
  notes: '备注',
  key: '字段',
  status: '状态',
  chapters: '章节数',
  total: '合计',
  row: '行',
  unit_price: '单价',
  mode: '方式',
  action: '操作',
  version: '版本',
  factor: '系数',
  rows: '行数',
  applied: '套用',
}

const BOQ_ACTION_LABELS: Record<string, string> = {
  update: '更新台账',
  add: '补入台账',
  ignore: '忽略差异',
  restore: '恢复差异',
}

/** Compact one-line summary of an audit `detail` dictionary; never dumps raw JSON. */
export function auditDetailText(detail?: Record<string, unknown> | null): string {
  if (!detail || typeof detail !== 'object') return ''
  const parts: string[] = []
  for (const [key, value] of Object.entries(detail)) {
    if (parts.length >= 3) break
    if (key === 'id' || key.endsWith('_id')) continue
    let text = ''
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      if (key === 'role') text = memberRoleMeta(String(value)).label
      else if (key === 'username') text = `@${value}`
      else if (key === 'count') text = `${value} 项`
      else if (key === 'action') text = BOQ_ACTION_LABELS[String(value)] || String(value)
      else if (key === 'mode') text = outlineMode(String(value))
      else if (key === 'only_missing') text = value ? '仅补未报价行' : '覆盖已报价行'
      else text = String(value)
    } else if (Array.isArray(value)) {
      text = `${value.length} 项`
    }
    if (!text) continue
    const label = AUDIT_DETAIL_LABELS[key]
    parts.push(label ? `${label} ${text}` : text)
  }
  return parts.join(' · ')
}


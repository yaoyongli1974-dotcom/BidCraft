import { ApiError, asArray, jsonBody, notifyUnauthorized, readError, request } from '../lib/http'
import type {
  ChapterCitations,
  ChapterDetail,
  ChapterPlan,
  ChapterPlanConfirmInput,
  ChapterPlanGenerateInput,
  ChapterPlanListResponse,
  ChapterPlanResponse,
  ChapterPlanUpdateInput,
  GenerationReport,
  Job,
  OutlineChapter,
  OutlineResponse,
  ProjectCitations,
  ScoringCoverageReport,
} from '../lib/types'

// ---------------------------------------------------------------- generation domain
export async function getOutline(ref: string): Promise<OutlineChapter[]> {
  const data = await request<OutlineChapter[] | { chapters?: OutlineChapter[] }>(
    `/projects/${encodeURIComponent(ref)}/outline`,
  )
  return asArray<OutlineChapter>(data, 'chapters')
}

export const generateOutline = (ref: string, mode: string, words: number) =>
  request<OutlineResponse>(
    `/projects/${encodeURIComponent(ref)}/outline`,
    jsonBody({ mode, words }),
  )

/** 评分点覆盖矩阵（只读）：每个评分点是否映射到带正文的章节。 */
export function getScoringCoverage(ref: string): Promise<ScoringCoverageReport> {
  return request<ScoringCoverageReport>(
    `/projects/${encodeURIComponent(ref)}/scoring-coverage`,
  )
}

export async function saveOutline(
  ref: string,
  chapters: Array<{
    id?: string
    heading: string
    intent?: string
    target_words?: number
  }>,
): Promise<OutlineChapter[]> {
  const data = await request<OutlineChapter[] | { chapters?: OutlineChapter[] }>(
    `/projects/${encodeURIComponent(ref)}/outline`,
    { method: 'PUT', body: JSON.stringify({ chapters }) },
  )
  return asArray<OutlineChapter>(data, 'chapters')
}

export async function getChapter(ref: string, nodeId: string): Promise<ChapterDetail> {
  const data = await request<ChapterDetail | { chapter?: ChapterDetail }>(
    `/projects/${encodeURIComponent(ref)}/chapters/${encodeURIComponent(nodeId)}`,
  )
  if (data && typeof data === 'object' && 'chapter' in data && data.chapter) return data.chapter
  return data as ChapterDetail
}

// ------------------------------------------------------------ chapter planning

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

/**
 * 后端同一字段可能用英文键或中文键（历史返回）；按此表逐个取值。
 * 未出现的字段保持 undefined，UI 会显示「未填写」，而不是补造内容。
 */
const PLAN_FIELD_ALIASES: Partial<Record<keyof ChapterPlan, readonly string[]>> = {
  id: ['id', 'plan_id', '规划ID'],
  project_id: ['project_id', '项目ID'],
  node_id: ['node_id', '章节ID'],
  package_id: ['package_id', '规则包ID'],
  version: ['version', '版本'],
  status: ['status', '状态'],
  objective: ['objective', '目标', '章节目标', '写作目标'],
  score_points: ['score_points', '评分点', '得分点'],
  requirements: ['requirements', '要求', '需求'],
  conditions: ['conditions', '条件'],
  challenges: ['challenges', '挑战', '难点'],
  strategy: ['strategy', '策略', '写作策略', '章节策略'],
  measures: ['measures', '措施', '应对措施'],
  knowledge_refs: ['knowledge_refs', '知识引用', '知识点', '知识参考'],
  fact_refs: ['fact_refs', '事实引用', '台账引用'],
  planned_tables: ['planned_tables', '计划表格', '表格'],
  planned_diagrams: ['planned_diagrams', '计划图表', '图表'],
  forbidden_assumptions: ['forbidden_assumptions', '禁止假设', '不得假设'],
  quality_criteria: ['quality_criteria', '质量标准', '质量要求'],
  user_adjustments: ['user_adjustments', '用户调整', '人工调整'],
  validation_errors: ['validation_errors', '校验错误', '验证错误'],
  raw_response: ['raw_response', '原始响应'],
  user_instruction: ['user_instruction', '补充指令'],
  used_at: ['used_at', '使用时间'],
  model_id: ['model_id', '模型ID'],
  prompt_version: ['prompt_version', '提示词版本'],
  created_by: ['created_by', '创建人'],
  updated_by: ['updated_by', '更新人'],
  confirmed_by: ['confirmed_by', '确认人'],
  created_at: ['created_at', '创建时间'],
  updated_at: ['updated_at', '更新时间'],
  confirmed_at: ['confirmed_at', '确认时间'],
}

const TEXT_FIELDS: ReadonlyArray<keyof ChapterPlan> = [
  'id',
  'project_id',
  'node_id',
  'package_id',
  'status',
  'objective',
  'raw_response',
  'user_instruction',
  'used_at',
  'model_id',
  'prompt_version',
  'created_by',
  'updated_by',
  'confirmed_by',
  'created_at',
  'updated_at',
  'confirmed_at',
]

const ARRAY_FIELDS: ReadonlyArray<keyof ChapterPlan> = [
  'score_points',
  'requirements',
  'conditions',
  'challenges',
  'strategy',
  'measures',
  'knowledge_refs',
  'fact_refs',
  'planned_tables',
  'planned_diagrams',
  'forbidden_assumptions',
  'quality_criteria',
  'user_adjustments',
  'validation_errors',
]

function pickPlanField(raw: Record<string, unknown>, field: keyof ChapterPlan): unknown {
  const keys = PLAN_FIELD_ALIASES[field] ?? [field]
  for (const key of keys) {
    if (raw[key] !== undefined) return raw[key]
  }
  return undefined
}

function optionalText(value: unknown): string | null | undefined {
  if (value === undefined) return undefined
  if (value === null) return null
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return undefined
}

function optionalNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() && /^\d+$/.test(value.trim())) {
    return Number(value.trim())
  }
  return undefined
}

/** 把 `{plan: ...}` / `{规划: ...}` / 裸 plan 统一成 ChapterPlan。 */
export function normalizeChapterPlan(value: unknown): ChapterPlan | null {
  const outer = asRecord(value)
  const raw =
    outer.plan !== undefined
      ? asRecord(outer.plan)
      : outer['规划'] !== undefined
        ? asRecord(outer['规划'])
        : outer
  if (Object.keys(raw).length === 0) return null
  const plan: Record<string, unknown> = {}
  for (const field of TEXT_FIELDS) {
    const text = optionalText(pickPlanField(raw, field))
    if (text !== undefined) plan[field] = text
  }
  const version = optionalNumber(pickPlanField(raw, 'version'))
  if (version !== undefined) plan.version = version
  for (const field of ARRAY_FIELDS) {
    const list = pickPlanField(raw, field)
    if (Array.isArray(list)) plan[field] = list
  }
  return Object.keys(plan).length > 0 ? (plan as ChapterPlan) : null
}

export function normalizeChapterPlanResponse(value: unknown): ChapterPlanResponse {
  const raw = asRecord(value)
  return {
    plan: normalizeChapterPlan(value),
    mode: raw.mode === 'review' ? 'review' : 'auto',
  }
}

export function normalizeChapterPlanHistory(value: unknown): ChapterPlan[] {
  const raw = asRecord(value)
  const list = Array.isArray(raw.plans)
    ? raw.plans
    : Array.isArray(raw['历史'])
      ? raw['历史']
      : []
  return list
    .map((item) => normalizeChapterPlan(item))
    .filter((plan): plan is ChapterPlan => plan !== null)
}

function planFromMutation(value: unknown): ChapterPlan {
  const plan = normalizeChapterPlan(value)
  if (!plan) throw new ApiError(0, null, '章节规划响应缺少有效数据')
  return plan
}

function trimOptional(value: string | undefined): string | undefined {
  const text = (value || '').trim()
  return text || undefined
}

function planUpdatePayload(input: ChapterPlanUpdateInput) {
  const payload = planMutationPayload(input)
  // 契约以 updates 字典提交；当前后端 schema 同时接受顶层扁平字段。两者一起发，
  // 老/新后端都能读到人工调整，且不会凭空补造未修改的字段。
  for (const [key, value] of Object.entries(input.updates)) {
    if (!(key in payload)) payload[key] = value
  }
  return payload
}

function planMutationPayload(input: ChapterPlanGenerateInput | ChapterPlanUpdateInput) {
  const payload: Record<string, unknown> = {}
  if ('user_instruction' in input) {
    const instruction = trimOptional(input.user_instruction)
    if (instruction) payload.user_instruction = instruction
  }
  if ('updates' in input) payload.updates = input.updates
  const packageId = trimOptional(input.package_id)
  if (packageId) payload.package_id = packageId
  return payload
}

function planPath(ref: string, nodeId: string, suffix = ''): string {
  return `/projects/${encodeURIComponent(ref)}/chapters/${encodeURIComponent(nodeId)}/plan${suffix}`
}

export function getChapterPlan(ref: string, nodeId: string): Promise<ChapterPlanResponse> {
  return request<unknown>(planPath(ref, nodeId)).then(normalizeChapterPlanResponse)
}

export function generateChapterPlan(
  ref: string,
  nodeId: string,
  input: ChapterPlanGenerateInput = {},
): Promise<ChapterPlan> {
  const body = jsonBody(planMutationPayload(input))
  return request<unknown>(planPath(ref, nodeId, '/generate'), body)
    .then(planFromMutation)
    .catch((error: unknown) => {
      // 契约路径是 /plan/generate；个别后端版本把「首次生成」放在 POST /plan。
      if (error instanceof ApiError && (error.status === 404 || error.status === 405)) {
        return request<unknown>(planPath(ref, nodeId), body).then(planFromMutation)
      }
      throw error
    })
}

export function regenerateChapterPlan(
  ref: string,
  nodeId: string,
  input: ChapterPlanGenerateInput = {},
): Promise<ChapterPlan> {
  return request<unknown>(
    planPath(ref, nodeId, '/regenerate'),
    jsonBody(planMutationPayload(input)),
  ).then(planFromMutation)
}

export function updateChapterPlan(
  ref: string,
  nodeId: string,
  input: ChapterPlanUpdateInput,
): Promise<ChapterPlan> {
  return request<unknown>(planPath(ref, nodeId), {
    method: 'PUT',
    body: JSON.stringify(planUpdatePayload(input)),
  }).then(planFromMutation)
}

export function confirmChapterPlan(
  ref: string,
  nodeId: string,
  input: ChapterPlanConfirmInput = {},
): Promise<ChapterPlan> {
  const payload: Record<string, unknown> = {}
  const note = trimOptional(input.note)
  if (note) payload.note = note
  return request<unknown>(planPath(ref, nodeId, '/confirm'), jsonBody(payload)).then(
    planFromMutation,
  )
}

export function listChapterPlanHistory(ref: string, nodeId: string): Promise<ChapterPlan[]> {
  return request<ChapterPlanListResponse>(planPath(ref, nodeId, '/history')).then((data) =>
    normalizeChapterPlanHistory(data),
  )
}

/** 本章正文的台账来源追溯（读侧，viewer 也可读）。 */

export async function reviewChapter(
  ref: string,
  nodeId: string,
  action: 'submit' | 'confirm' | 'revert' | 'lock' | 'unlock',
  note = '',
): Promise<ChapterDetail> {
  return request<ChapterDetail>(
    `/projects/${encodeURIComponent(ref)}/chapters/${encodeURIComponent(nodeId)}/review`,
    { method: 'POST', body: JSON.stringify({ action, note }) },
  )
}

export async function getChapterCitations(
  ref: string,
  nodeId: string,
): Promise<ChapterCitations> {
  const data = await request<ChapterCitations>(
    `/projects/${encodeURIComponent(ref)}/chapters/${encodeURIComponent(nodeId)}/citations`,
  )
  return {
    node_id: data?.node_id || nodeId,
    heading: data?.heading || '',
    citations: Array.isArray(data?.citations) ? data.citations : [],
    stale: Number(data?.stale || 0),
  }
}

/** 全书来源追溯：哪些章节引用了哪个台账字段、哪些已与台账不一致。 */

export async function getProjectCitations(ref: string): Promise<ProjectCitations> {
  const data = await request<ProjectCitations>(
    `/projects/${encodeURIComponent(ref)}/citations`,
  )
  return {
    project_id: data?.project_id || '',
    summary: {
      citations: Number(data?.summary?.citations || 0),
      facts_cited: Number(data?.summary?.facts_cited || 0),
      stale: Number(data?.summary?.stale || 0),
      sections_citing: Number(data?.summary?.sections_citing || 0),
      sections_needing_review: Number(data?.summary?.sections_needing_review || 0),
    },
    citations: Array.isArray(data?.citations) ? data.citations : [],
    chapters: Array.isArray(data?.chapters) ? data.chapters : [],
  }
}

export async function saveChapter(
  ref: string,
  nodeId: string,
  body: string,
  baseRevision?: number | null,
): Promise<ChapterDetail> {
  const payload: { body: string; base_revision?: number } = { body }
  if (typeof baseRevision === 'number' && Number.isFinite(baseRevision)) {
    payload.base_revision = baseRevision
  }
  const data = await request<ChapterDetail | { chapter?: ChapterDetail }>(
    `/projects/${encodeURIComponent(ref)}/chapters/${encodeURIComponent(nodeId)}`,
    { method: 'PUT', body: JSON.stringify(payload) },
  )
  if (data && typeof data === 'object' && 'chapter' in data && data.chapter) return data.chapter
  return data as ChapterDetail
}

export const generateChapter = (ref: string, nodeId: string) =>
  request<GenerationReport>(
    `/projects/${encodeURIComponent(ref)}/chapters/${encodeURIComponent(nodeId)}/generate`,
    { method: 'POST' },
  )

export const startDocumentGeneration = (ref: string, force = false) =>
  request<{ job_id: string }>(`/projects/${encodeURIComponent(ref)}/generate`, {
    method: 'POST',
    body: JSON.stringify(force ? { force: true } : {}),
  })

export const getJob = (jobId: string) =>
  request<Job>(`/jobs/${encodeURIComponent(jobId)}`)

export const getProjectJobs = (ref: string, kind?: string) => {
  const query = kind ? `?kind=${encodeURIComponent(kind)}` : ''
  return request<Job[]>(`/projects/${encodeURIComponent(ref)}/jobs${query}`)
}

export const startImageBatch = (ref: string, includeFailed = false) =>
  request<{ job_id: string }>(
    `/projects/${encodeURIComponent(ref)}/images/generate-missing`,
    jsonBody({ include_failed: includeFailed }),
  )

export function filenameFromDisposition(disposition: string | null): string | null {
  if (!disposition) return null
  const utf8 = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(disposition)
  if (utf8?.[1]) {
    try {
      return decodeURIComponent(utf8[1].trim().replace(/^"|"$/g, ''))
    } catch {
      return utf8[1].trim()
    }
  }
  const plain = /filename\s*=\s*"([^"]+)"|filename\s*=\s*([^;]+)/i.exec(disposition)
  const value = plain?.[1] || plain?.[2]
  if (!value) return null
  try {
    return decodeURIComponent(value.trim())
  } catch {
    return value.trim()
  }
}

export interface ExportForceOptions {
  /** 跳过导出门禁；仅 owner/admin 可用。 */
  force?: boolean
  /** 强制导出原因；force 时去空白后至少 4 个字符，否则服务端拒绝。 */
  force_reason?: string
}

export async function exportProject(
  ref: string,
  format: string,
  options: ExportForceOptions = {},
): Promise<{ blob: Blob; filename: string }> {
  const payload: Record<string, unknown> = { format }
  if (options.force) payload.force = true
  const reason = (options.force_reason || '').trim()
  if (options.force && reason) payload.force_reason = reason
  let response: Response
  try {
    response = await fetch(`/projects/${encodeURIComponent(ref)}/export`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: '*/*' },
      body: JSON.stringify(payload),
    })
  } catch {
    throw new ApiError(
      0,
      null,
      '无法连接本地服务。请确认 BidCraft 后端已启动，且当前页面由它托管。',
    )
  }
  if (!response.ok) {
    if (response.status === 401) {
      notifyUnauthorized(`/projects/${ref}/export`)
    }
    throw await readError(response)
  }
  const blob = await response.blob()
  const filename =
    filenameFromDisposition(response.headers.get('Content-Disposition')) ||
    `标书.${format === 'md' ? 'md' : format}`
  return { blob, filename }
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 4000)
}

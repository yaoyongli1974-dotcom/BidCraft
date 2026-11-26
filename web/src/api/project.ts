import { ApiError, asArray, jsonBody, request, uploadForm } from '../lib/http'
import type { ApprovalChainEntry, ApprovalRule, ApprovalRuleField, ApprovalRuleOp, ApprovalStage, ApprovalStageInput, BidPackage, BidPackageInput, ExportTemplatePreset, ExportTemplatesResponse, Member, MemberRole, ParseResult, Project, ProjectDetail, ProjectDuplicateInput, ProjectInstantiateInput, ProjectTemplate, SourceFile, UploadResult } from '../lib/types'
import { asText, normalizeApprovalConditionList, normalizeBidPackage, normalizeCurrencyToken, normalizeProjectTemplate } from './shared'

// ---------------------------------------------------------------- project domain
export async function getProjects(): Promise<Project[]> {
  const data = await request<Project[] | { projects?: Project[] }>('/projects')
  return asArray<Project>(data, 'projects')
}

export const createProject = (name: string, pkg: string, bidSection = '') =>
  request<Project>(
    '/projects',
    jsonBody({ name, package: pkg, bid_section: bidSection.trim() }),
  )

/**
 * R22 项目复制（模板复用）：POST /projects/{ref}/duplicate → 201，
 * 返回与 POST /projects 同形状的项目 payload（复制者成为新项目 owner）。
 * name 缺省时后端默认「源名 副本」；include_boq/include_images 缺省 true。
 * 台账/大纲/正文/源文件始终复制；报价版本与审批记录不复制。
 * 400 名称非法（detail 中文）、403 只读成员，错误经 ApiError/messageOf 透出。
 */

export const duplicateProject = (ref: string, input: ProjectDuplicateInput = {}) =>
  request<Project>(
    `/projects/${encodeURIComponent(ref)}/duplicate`,
    jsonBody({
      ...(input.name !== undefined ? { name: input.name } : {}),
      include_boq: input.includeBoq ?? true,
      include_images: input.includeImages ?? true,
    }),
  )

/**
 * R23 模板列表：GET /projects/templates → 平铺数组（仅 is_template=True，
 * updated_at 降序、上限 200，登录即可浏览）。旧后端没有该接口时返回 404，
 * 或 SPA 兜底把未知路径回成 index.html（200 + HTML 导致 JSON 解析失败），
 * 两种情况都按「无模板」降级为空列表。
 */

export async function getProjectTemplates(signal?: AbortSignal): Promise<ProjectTemplate[]> {
  let data: unknown
  try {
    data = await request<unknown>('/projects/templates', undefined, signal)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return []
    if (error instanceof SyntaxError) return []
    throw error
  }
  return asArray<unknown>(data, 'templates')
    .map(normalizeProjectTemplate)
    .filter((item): item is ProjectTemplate => item !== null)
}

/** 归一化一条模板轻量项；缺 id 的脏数据整体丢弃。 */

/**
 * 导出排版模板（区别于 /projects/templates 的项目模板）：GET 列出四套预设与当前选择，
 * PUT 把 id 存进项目设置。真正导出时后端从项目设置读取模板，ExportRequest 不接受
 * template 字段，因此前端不把模板拼进 /export 请求。
 * 旧后端没有这两个接口（404 或 SPA 兜底 HTML）时回退到后端默认 standard 预设。
 */
export const DEFAULT_EXPORT_TEMPLATE_ID = 'standard'

export const DEFAULT_EXPORT_TEMPLATE: ExportTemplatePreset = {
  id: DEFAULT_EXPORT_TEMPLATE_ID,
  name: '默认模板',
  description: '标准投标文件：宋体小四、1.5 倍行距，后端默认排版',
}

function normalizeExportTemplate(value: unknown): ExportTemplatePreset | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const id = asText(raw.id).trim()
  if (!id || id === 'default') return null
  return {
    id,
    name: asText(raw.name) || id,
    description: asText(raw.description),
    ...(typeof raw.body_font === 'string' ? { body_font: raw.body_font } : {}),
    ...(typeof raw.heading_font === 'string' ? { heading_font: raw.heading_font } : {}),
    ...(typeof raw.body_size === 'number' ? { body_size: raw.body_size } : {}),
    ...(typeof raw.line_spacing === 'number' ? { line_spacing: raw.line_spacing } : {}),
    ...(typeof raw.cover === 'boolean' ? { cover: raw.cover } : {}),
    ...(typeof raw.toc === 'boolean' ? { toc: raw.toc } : {}),
    ...(typeof raw.page_numbers === 'boolean' ? { page_numbers: raw.page_numbers } : {}),
  }
}

export function normalizeExportTemplates(value: unknown): ExportTemplatesResponse {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const presets = Array.isArray(raw.presets)
    ? raw.presets.map(normalizeExportTemplate).filter((item): item is ExportTemplatePreset => item !== null)
    : []
  const current = normalizeExportTemplate(raw.current) || DEFAULT_EXPORT_TEMPLATE
  const all = presets.some((item) => item.id === current.id) ? presets : [current, ...presets]
  return { presets: all.length > 0 ? all : [DEFAULT_EXPORT_TEMPLATE], current }
}

export async function getExportTemplates(ref: string): Promise<ExportTemplatesResponse> {
  let data: unknown
  try {
    data = await request<unknown>(`/projects/${encodeURIComponent(ref)}/export-templates`)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return { presets: [DEFAULT_EXPORT_TEMPLATE], current: DEFAULT_EXPORT_TEMPLATE }
    }
    if (error instanceof SyntaxError) {
      return { presets: [DEFAULT_EXPORT_TEMPLATE], current: DEFAULT_EXPORT_TEMPLATE }
    }
    throw error
  }
  return normalizeExportTemplates(data)
}

export async function setExportTemplate(ref: string, id: string): Promise<ExportTemplatesResponse> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/export-template`,
    { method: 'PUT', body: JSON.stringify({ id }) },
  )
  return normalizeExportTemplates(data)
}

export const instantiateTemplate = (id: string, input: ProjectInstantiateInput = {}) =>
  request<Project>(
    `/projects/templates/${encodeURIComponent(id)}/instantiate`,
    jsonBody({
      ...(input.name !== undefined ? { name: input.name } : {}),
      include_boq: input.includeBoq ?? true,
      include_images: input.includeImages ?? true,
    }),
  )

/**
 * Rename a project, change its 标段/包件 or set the quote approval chain.
 * Send only the fields that changed: the server rejects a payload where every
 * key is absent. `approval_chain` is the v2 stage list (≤5 stages, each with
 * 1–5 user ids, an all/any mode and optional R21 `min_amount` / R25
 * `packages` & `currencies` conditions, owner only); an empty array clears the
 * chain back to single-stage owner review. The echo is normalised to v2 even
 * when an older server still replies with the flat R18 format. R23
 * `is_template` toggles the project template flag (owner/admin only);
 * templates are visible to every member.
 */

export const updateProject = async (
  ref: string,
  patch: {
    name?: string
    bid_section?: string
    approval_chain?: ApprovalStageInput[]
    is_template?: boolean
  },
): Promise<Project> => {
  const data = await request<Project>(`/projects/${encodeURIComponent(ref)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
  return { ...data, approval_chain: normalizeApprovalChain(data.approval_chain) }
}

const APPROVAL_RULE_FIELDS: ApprovalRuleField[] = ['total_amount', 'package_id', 'currency']
const APPROVAL_RULE_OPS: ApprovalRuleOp[] = [
  'gte', 'lte', 'eq', 'ne', 'in', 'not_in', 'contains',
]
const APPROVAL_RULE_MAX = 20
const APPROVAL_RULE_VALUE_MAX = 20
const APPROVAL_RULE_TEXT_MAX = 40

function normalizeApprovalRuleValue(
  field: ApprovalRuleField,
  op: ApprovalRuleOp,
  value: unknown,
): ApprovalRule['value'] | null {
  if (op === 'in' || op === 'not_in') {
    return normalizeApprovalConditionList(value).slice(0, APPROVAL_RULE_VALUE_MAX)
  }
  if (field === 'total_amount') {
    return typeof value === 'number' && Number.isFinite(value) ? value : null
  }
  const text = asText(value).trim()
  if (!text || text.length > APPROVAL_RULE_TEXT_MAX) return null
  return field === 'currency' ? normalizeCurrencyToken(text) : text
}

/** 审批链规则条件回显归一化：非法项丢弃，与后端 _stage_rules 同口径。 */
export function normalizeApprovalRules(value: unknown): ApprovalRule[] {
  if (!Array.isArray(value)) return []
  const rules: ApprovalRule[] = []
  for (const entry of value.slice(0, APPROVAL_RULE_MAX)) {
    if (!entry || typeof entry !== 'object') continue
    const record = entry as Record<string, unknown>
    const field = asText(record.field).trim() as ApprovalRuleField
    const op = asText(record.op).trim() as ApprovalRuleOp
    if (!APPROVAL_RULE_FIELDS.includes(field) || !APPROVAL_RULE_OPS.includes(op)) continue
    const normalized = normalizeApprovalRuleValue(field, op, record.value)
    if (normalized === null || (Array.isArray(normalized) && normalized.length === 0)) continue
    rules.push({ field, op, value: normalized })
  }
  return rules
}

function approvalRuleMatches(rule: ApprovalRule, actual: unknown): boolean {
  if (rule.op === 'in' || rule.op === 'not_in') {
    if (typeof actual !== 'string' || !actual.trim()) return true
    const left = rule.field === 'currency' ? normalizeCurrencyToken(actual) : actual.trim()
    const expected = Array.isArray(rule.value) ? rule.value : []
    const present = expected.some((item) =>
      (rule.field === 'currency' ? normalizeCurrencyToken(item) : item.trim()) === left,
    )
    return rule.op === 'in' ? present : !present
  }
  if (rule.op === 'gte' || rule.op === 'lte') {
    if (typeof actual !== 'number' || !Number.isFinite(actual)) return true
    const expected = typeof rule.value === 'number' ? rule.value : Number(rule.value)
    if (!Number.isFinite(expected)) return true
    return rule.op === 'gte' ? actual >= expected : actual <= expected
  }
  if (actual === null || actual === undefined || actual === '') return true
  const left =
    rule.field === 'currency' ? normalizeCurrencyToken(actual) : String(actual).trim()
  const right = String(rule.value)
  if (rule.op === 'contains') return left.toLowerCase().includes(right.toLowerCase())
  return rule.op === 'eq' ? left === right : left !== right
}

/**
 * 审批链回显归一化为 v2 阶段列表：
 * - R18 扁平 `{id,name}` → 单审批人 all 阶段；
 * - v2 `{approvers:[{id,name}],mode}` 原样，mode 白名单外的值回退 all；
 * - 审批人只保留带 id 的条目（级内去重保序），姓名缺失留空串；
 * - 无有效审批人的阶段与非法项整体忽略；
 * - R21 金额门槛 min_amount 仅保留 ≥0 的有限数值，非法/负值丢弃该键（= 恒生效）。
 * - R25 适用包件 packages / 适用币种 currencies 仅保留非空、≤40 字的字符串，
 *   去重保序；非法 / 空列表丢弃该键（= 不限）。
 */

export function normalizeApprovalChain(value: unknown): ApprovalStage[] {
  if (!Array.isArray(value)) return []
  const stages: ApprovalStage[] = []
  for (const item of value) {
    if (!item || typeof item !== 'object') continue
    const record = item as Record<string, unknown>
    const rawApprovers = Array.isArray(record.approvers) ? record.approvers : [record]
    const approvers: ApprovalChainEntry[] = []
    for (const entry of rawApprovers) {
      if (!entry || typeof entry !== 'object') continue
      const entryRecord = entry as Record<string, unknown>
      const id = asText(entryRecord.id).trim()
      if (!id || approvers.some((approver) => approver.id === id)) continue
      approvers.push({ id, name: asText(entryRecord.name) })
    }
    if (approvers.length === 0) continue
    const minAmount = record.min_amount
    const validMinAmount =
      typeof minAmount === 'number' && Number.isFinite(minAmount) && minAmount >= 0
    const packages = normalizeApprovalConditionList(record.packages)
    const currencies = normalizeApprovalConditionList(record.currencies)
    const rules = normalizeApprovalRules(record.rules)
    stages.push({
      approvers,
      mode: record.mode === 'any' ? 'any' : 'all',
      ...(validMinAmount ? { min_amount: minAmount } : {}),
      ...(packages.length > 0 ? { packages } : {}),
      ...(currencies.length > 0 ? { currencies } : {}),
      ...(rules.length > 0 ? { rules } : {}),
    })
  }
  return stages
}

/**
 * R25 阶段生效条件列表归一化：只保留非空、≤40 字字符串，去重保序。
 * 非数组 / 非法元素 / 空列表返回 []（= 该条件不限）。
 */

export interface ApprovalStageContext {
  packageId?: string | null
  currency?: string | null
}

export function applicableStages(
  chain: ApprovalStage[],
  totalAmount: number | null | undefined,
  context?: ApprovalStageContext,
): Array<{ index: number; stage: ApprovalStage }> {
  const amountKnown = typeof totalAmount === 'number' && Number.isFinite(totalAmount)
  const packageId = typeof context?.packageId === 'string' ? context.packageId.trim() : ''
  const currency = normalizeCurrencyToken(context?.currency)
  const result: Array<{ index: number; stage: ApprovalStage }> = []
  chain.forEach((stage, index) => {
    const threshold = stage.min_amount
    const gated = typeof threshold === 'number' && Number.isFinite(threshold) && threshold >= 0
    if (gated && amountKnown && (totalAmount as number) < threshold) return
    if (
      stage.packages &&
      stage.packages.length > 0 &&
      !(packageId && stage.packages.includes(packageId))
    ) {
      return
    }
    if (
      stage.currencies &&
      stage.currencies.length > 0 &&
      !(currency && stage.currencies.some((item) => normalizeCurrencyToken(item) === currency))
    ) {
      return
    }
    for (const rule of stage.rules ?? []) {
      const actual =
        rule.field === 'total_amount'
          ? totalAmount
          : rule.field === 'package_id'
            ? packageId || null
            : currency || null
      if (!approvalRuleMatches(rule, actual)) return
    }
    result.push({ index, stage })
  })
  if (result.length === 0 && chain.length > 0) {
    return chain.map((stage, index) => ({ index, stage }))
  }
  return result
}

export async function getProject(ref: string): Promise<ProjectDetail> {
  const data = await request<ProjectDetail>(`/projects/${encodeURIComponent(ref)}`)
  return {
    ...data,
    missing_required: Array.isArray(data.missing_required) ? data.missing_required : [],
    sources: Array.isArray(data.sources) ? data.sources : undefined,
    approval_chain: normalizeApprovalChain(data.approval_chain),
  }
}

export const deleteProject = (ref: string) =>
  request<{ deleted: boolean }>(`/projects/${encodeURIComponent(ref)}`, { method: 'DELETE' })

export async function getBidPackages(ref: string): Promise<BidPackage[]> {
  const data = await request<unknown>(`/projects/${encodeURIComponent(ref)}/packages`)
  const items = Array.isArray(data) ? data : asArray<unknown>(data, 'packages')
  return items.map(normalizeBidPackage).filter((item) => item.id)
}

export async function createBidPackage(
  ref: string,
  input: BidPackageInput,
): Promise<BidPackage> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/packages`,
    jsonBody(input),
  )
  return normalizeBidPackage(data)
}

export async function updateBidPackage(
  ref: string,
  packageId: string,
  patch: Partial<BidPackageInput>,
): Promise<BidPackage> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/packages/${encodeURIComponent(packageId)}`,
    { method: 'PATCH', body: JSON.stringify(patch) },
  )
  return normalizeBidPackage(data)
}

export const deleteBidPackage = (ref: string, packageId: string) =>
  request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(ref)}/packages/${encodeURIComponent(packageId)}`,
    { method: 'DELETE' },
  )

export async function getMembers(ref: string): Promise<Member[]> {
  const data = await request<Member[] | { members?: Member[] }>(
    `/projects/${encodeURIComponent(ref)}/members`,
  )
  return asArray<Member>(data, 'members').map((item) => ({
    user_id: String(item.user_id ?? ''),
    username: String(item.username ?? ''),
    display_name: String(item.display_name ?? ''),
    role: String(item.role ?? 'viewer'),
    created_at: typeof item.created_at === 'string' ? item.created_at : null,
  }))
}

export const addMember = (ref: string, input: { username: string; role: MemberRole | string }) =>
  request<Member>(`/projects/${encodeURIComponent(ref)}/members`, jsonBody(input))

export const updateMember = (
  ref: string,
  userId: string,
  input: { role: MemberRole | string },
) =>
  request<Member>(
    `/projects/${encodeURIComponent(ref)}/members/${encodeURIComponent(userId)}`,
    { method: 'PATCH', body: JSON.stringify(input) },
  )

export const removeMember = (ref: string, userId: string) =>
  request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(ref)}/members/${encodeURIComponent(userId)}`,
    { method: 'DELETE' },
  )

export async function getSources(ref: string): Promise<SourceFile[]> {
  const data = await request<SourceFile[] | { sources?: SourceFile[] }>(
    `/projects/${encodeURIComponent(ref)}/sources`,
  )
  return asArray<SourceFile>(data, 'sources')
}

export function uploadSource(
  ref: string,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)
  return uploadForm<UploadResult>(
    `/projects/${encodeURIComponent(ref)}/sources`,
    form,
    { onProgress },
  )
}

export const parseProject = (ref: string) =>
  request<ParseResult>(`/projects/${encodeURIComponent(ref)}/parse`, { method: 'POST' })

export const sourceFileUrl = (ref: string, sourceId: string) =>
  `/projects/${encodeURIComponent(ref)}/sources/${encodeURIComponent(sourceId)}/file`

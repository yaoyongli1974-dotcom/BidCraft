import { normalizeProjectType, projectTypeLabel } from '../lib/format'
import { ApiError, asArray, request } from '../lib/http'
import type { AdminRulePackage, AuditEntry, AuditPage, BatchItemResult, BidPackage, BoqLedgerSummary, BoqPayload, BoqPrice, BoqQuoteVersion, BoqRow, BoqSnapshot, BoqVersionDiffChanged, BoqVersionDiffEntry, ImagePlan, ImagePlanDocument, ImagePolicy, KbDocPolicyEntry, KbDocSource, KbVectorResult, KnowledgeBase, PackageInfo, PendingApproval, Personnel, PluginCatalogEntry, PreflightItem, PreflightStatus, PriceCurrency, ProjectTemplate, Qualification, QualificationEvidence, QualificationKind, QualificationStatus, QuotaItem, QuotaProposal, QuoteApprovalRecord, QuoteVersionStatus, RequirementResponse, RequirementResponseStatus, RequirementVerdict, RiskCategorySummary, RiskTierLevel, RiskTierSummary, RulePackageSummary, RulePackageValidation, TermInput, TextQaSettings, UserDoc, WechatConfig } from '../lib/types'
import type { DeletionState } from './account'
import type { InvoiceRequest } from './auth'
import { normalizeBoqQuoteVersion, normalizeBoqSummary } from './boq'
import { normalizeTypoRules } from './compliance'
import { normalizePersonnel, normalizeQualification } from './tender'

// ---------------------------------------------------------------- shared domain
export function normalizeUserDoc(value: unknown): UserDoc | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const id = asText(raw.id)
  if (!id) return null
  return {
    id,
    filename: asText(raw.filename) || '未命名文档',
    size: Math.max(0, Math.round(asNumber(raw.size))),
    mime: asText(raw.mime),
    status: (asText(raw.status, 'ready') || 'ready').toLowerCase(),
    error: asText(raw.error),
    kb_id: asText(raw.kb_id),
    created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
  }
}

/** 当前登录用户上传的个人文档列表（平铺数组，后端只返回本人数据）。 */

export const QUALIFICATION_KIND_OPTIONS: QualificationKind[] = [
  'license',
  'safety',
  'person',
  'performance',
  'credit',
  'other',
]

export function isQualificationKind(value: unknown): value is QualificationKind {
  return (
    typeof value === 'string' &&
    (QUALIFICATION_KIND_OPTIONS as string[]).includes(value)
  )
}

export function normalizeQualificationKind(value: unknown): QualificationKind {
  const text = asText(value).trim().toLowerCase()
  return isQualificationKind(text) ? text : 'other'
}

export function normalizeQualificationStatus(value: unknown): QualificationStatus {
  const text = asText(value).trim().toLowerCase()
  return text === 'valid' || text === 'expiring' || text === 'expired' || text === 'none'
    ? text
    : 'none'
}

export function normalizePreflightStatus(value: unknown): PreflightStatus {
  const text = asText(value).trim().toLowerCase()
  return text === 'ok' || text === 'expiring' || text === 'expired' || text === 'missing'
    ? text
    : 'missing'
}

export function asNullableNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return Math.round(value)
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    return Math.round(Number(value))
  }
  return null
}

export function normalizeQualificationEvidence(value: unknown): QualificationEvidence | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const filename = asText(raw.filename).trim()
  if (!filename) return null
  return {
    filename,
    size: Math.max(0, Math.round(asNumber(raw.size, 0))),
    mime: asText(raw.mime),
    uploaded_at: typeof raw.uploaded_at === 'string' ? raw.uploaded_at : null,
  }
}

/**
 * Normalise one qualification payload. Unknown kinds fall back to `other`,
 * unknown statuses to `none`, and evidence entries without a filename are
 * dropped so the evidence list never renders an empty download link.
 */

export function unwrapQualification(value: unknown): unknown {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const raw = value as Record<string, unknown>
    if (raw.qualification) return raw.qualification
  }
  return value
}

export function requireQualification(value: unknown): Qualification {
  const qualification = normalizeQualification(unwrapQualification(value))
  if (!qualification) {
    throw new ApiError(500, null, '服务端返回的资质信息无法识别，请刷新后重试。')
  }
  return qualification
}

export const qualificationPath = (id: string) =>
  `/my/qualifications/${encodeURIComponent(id)}`

/** 当前用户的资质台账；kind / status 只在非空时拼接查询参数。 */

export function normalizePreflightItem(value: unknown): PreflightItem | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const key = asText(raw.key).trim()
  if (!key) return null
  const kinds = (Array.isArray(raw.kinds) ? raw.kinds : [])
    .map((kind) => asText(kind).trim().toLowerCase())
    .filter(isQualificationKind)
  return {
    key,
    label: asText(raw.label).trim() || key,
    kinds: Array.from(new Set(kinds)),
    hint: asText(raw.hint),
    status: normalizePreflightStatus(raw.status),
    qualification_id: asText(raw.qualification_id) || null,
    name: asText(raw.name),
    valid_to: asText(raw.valid_to) || null,
    evidence_count: Math.max(0, Math.round(asNumber(raw.evidence_count, 0))),
  }
}

/** 归一化自查报告；缺少 summary 时按条目状态回算，保证汇总条始终可用。 */

export const MAX_SOCIAL_MONTHS = 600

/** 兼容 `{ personnel: {...} }` 包装；平铺 payload 原样返回。 */

export function unwrapPersonnel(value: unknown): unknown {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const raw = value as Record<string, unknown>
    if (raw.personnel) return raw.personnel
  }
  return value
}

/**
 * 归一化一条人员记录。缺失的文本字段补空串，社保月数收敛到 0–600，
 * 没有 id 的条目直接丢弃（与资质台账相同的容错策略）。
 */

export function requirePersonnel(value: unknown): Personnel {
  const person = normalizePersonnel(value)
  if (!person) {
    throw new ApiError(500, null, '服务端返回的人员信息无法识别，请刷新后重试。')
  }
  return person
}

export const personnelPath = (id: string) => `/my/personnel/${encodeURIComponent(id)}`

/** 当前用户的人员台账（平铺数组，后端按姓名升序）。 */

export function asText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value
  if (value === undefined || value === null) return fallback
  return String(value)
}

export function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    return Number(value)
  }
  return fallback
}

export function asBool(value: unknown): boolean {
  return value === true || value === 'true'
}

export function asStringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => (typeof item === 'string' ? item.trim() : ''))
      .filter(Boolean)
  }
  if (typeof value === 'string' && value.trim()) {
    return value
      .split(/[,，\s]+/)
      .map((item) => item.trim())
      .filter(Boolean)
  }
  return []
}

export function normalizePackageInfo(value: unknown): PackageInfo {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const name = asText(raw.name)
  const version =
    typeof raw.version === 'number' || typeof raw.version === 'string' ? raw.version : 1
  return {
    name,
    title: asText(raw.title) || name,
    version,
    extends: asStringList(raw.extends),
    path: typeof raw.path === 'string' ? raw.path : undefined,
    project_type: normalizeProjectType(asText(raw.project_type)),
    project_type_label: projectTypeLabel(
      asText(raw.project_type),
      asText(raw.project_type_label),
    ),
    description: asText(raw.description),
    builtin: asBool(raw.builtin),
    overridden: asBool(raw.overridden),
    abstract: asBool(raw.abstract),
  }
}

export function unwrapRulePackage(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const raw = value as Record<string, unknown>
  const nested = raw.package
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
    return { ...(nested as Record<string, unknown>), ...raw, package: undefined }
  }
  return raw
}

export function normalizeRulePackage(value: unknown): AdminRulePackage {
  const raw = unwrapRulePackage(value)
  const base = normalizePackageInfo(raw)
  const usageRaw = raw.usage ?? raw.project_count ?? raw.projects
  return {
    ...base,
    project_type: normalizeProjectType(asText(raw.project_type)),
    project_type_label: projectTypeLabel(
      asText(raw.project_type),
      asText(raw.project_type_label),
    ),
    description: asText(raw.description),
    extends: asStringList(raw.extends),
    abstract: asBool(raw.abstract),
    builtin: asBool(raw.builtin),
    overridden: asBool(raw.overridden),
    hidden: asBool(raw.hidden),
    usage: Math.max(0, Math.round(asNumber(usageRaw, 0))),
    error: asText(raw.error),
    document: typeof raw.document === 'string' ? raw.document : undefined,
  }
}

export function normalizeRulePackageSummary(value: unknown): RulePackageSummary {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return {
    fields: Math.max(0, Math.round(asNumber(raw.fields, 0))),
    outline: Math.max(0, Math.round(asNumber(raw.outline, 0))),
    checks: Math.max(0, Math.round(asNumber(raw.checks, 0))),
  }
}

export function normalizeValidation(value: unknown): RulePackageValidation {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const problems = asStringList(raw.problems)
  const ok = raw.ok === undefined || raw.ok === null ? problems.length === 0 : asBool(raw.ok)
  return {
    ok: ok && problems.length === 0,
    problems,
    summary: normalizeRulePackageSummary(raw.summary),
  }
}

export const normalizeInvoice = (value: unknown): InvoiceRequest | null => {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  if (typeof raw.id !== 'string') return null
  const status = raw.status === 'issued' || raw.status === 'rejected' ? raw.status : 'requested'
  return {
    id: raw.id,
    user_id: typeof raw.user_id === 'string' ? raw.user_id : '',
    username: typeof raw.username === 'string' ? raw.username : '',
    title: typeof raw.title === 'string' ? raw.title : '',
    tax_no: typeof raw.tax_no === 'string' ? raw.tax_no : '',
    email: typeof raw.email === 'string' ? raw.email : '',
    amount_cents: typeof raw.amount_cents === 'number' ? raw.amount_cents : 0,
    note: typeof raw.note === 'string' ? raw.note : '',
    status,
    status_label: typeof raw.status_label === 'string' ? raw.status_label : '',
    invoice_no: typeof raw.invoice_no === 'string' ? raw.invoice_no : '',
    reject_reason: typeof raw.reject_reason === 'string' ? raw.reject_reason : '',
    has_file: Boolean(raw.has_file),
    issued_at: typeof raw.issued_at === 'string' ? raw.issued_at : null,
    created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
  }
}

export function normalizeKbDocPolicyEntry(value: unknown, kind: string): KbDocPolicyEntry {
  const raw =
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {}
  const required = Array.isArray(raw.required)
    ? raw.required.map((item) => asText(item).trim()).filter(Boolean)
    : []
  return {
    label: asText(raw.label).trim() || kind,
    required: Array.from(new Set(required)),
    hint: asText(raw.hint),
  }
}

/**
 * 归一化素材类型策略；空 / 非法值回退到默认策略，并保证 `other` 始终存在，
 * 这样类型下拉框在最老的服务器上也能工作。
 */

export const KB_DOC_RESERVED_KEYS = new Set(['source', 'kind', 'fields', 'ok', 'reason'])

/** 新旧后端兼容：优先读嵌套 `fields`，否则把其余标量键当作字段。 */

export function normalizeKbDocFields(raw: Record<string, unknown>): Record<string, string> {
  const fields: Record<string, string> = {}
  const nested = raw.fields
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
    Object.entries(nested as Record<string, unknown>).forEach(([key, value]) => {
      if (!key.trim()) return
      if (value === undefined || value === null) return
      if (typeof value === 'object') return
      fields[key.trim()] = asText(value)
    })
  }
  Object.entries(raw).forEach(([key, value]) => {
    if (KB_DOC_RESERVED_KEYS.has(key)) return
    if (value === undefined || value === null || typeof value === 'object') return
    if (!(key in fields)) fields[key] = asText(value)
  })
  return fields
}

export function normalizeKbDocSource(value: unknown): KbDocSource | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const source = asText(raw.source).trim()
  if (!source) return null
  return {
    source,
    chunks: Math.max(0, Math.round(asNumber(raw.chunks, 0))),
  }
}

/**
 * 归一化 `GET /admin/kbs/{id}/docs`。`items` / `sources` 中的无效项会被过滤；
 * 老后端没有 `sources` 时用已登记项回填，保证左侧文档列表仍然可用。
 */

export const kbDocsPath = (id: string) => `/admin/kbs/${encodeURIComponent(id)}/docs`

/** 读取某个知识库的素材元数据、策略与全部片段来源。 */

export function normalizeKbVector(value: unknown): KbVectorResult | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const kbRaw = raw.kb
  const kb =
    kbRaw && typeof kbRaw === 'object'
      ? (kbRaw as KnowledgeBase)
      : ({
          id: '',
          name: '',
          kind: 'local',
          provider: 'local',
          location: '',
          enabled: false,
          is_default: false,
          has_api_key: false,
          chunk_count: 0,
        } satisfies KnowledgeBase)
  return {
    embedded: Math.max(0, Math.round(asNumber(raw.embedded, 0))),
    total: Math.max(0, Math.round(asNumber(raw.total, 0))),
    model: asText(raw.model),
    dim: Math.max(0, Math.round(asNumber(raw.dim, 0))),
    error: asText(raw.error),
    kb,
  }
}

export function normalizeProjectTemplate(value: unknown): ProjectTemplate | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const id = asText(raw.id).trim()
  if (!id) return null
  return {
    id,
    name: asText(raw.name) || '未命名模板',
    package: asText(raw.package),
    is_template: asBool(raw.is_template),
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
    owner_name: typeof raw.owner_name === 'string' && raw.owner_name ? raw.owner_name : null,
  }
}

/**
 * R23 从模板创建：POST /projects/templates/{id}/instantiate → 201，
 * 返回与 POST /projects 同形状的项目 payload（创建者成为新项目 owner，
 * 无需模板成员关系）。name 缺省时后端默认「模板名 副本」；
 * include_boq/include_images 缺省 true。台账/大纲/正文/源文件始终复制；
 * 报价版本与审批记录不复制。404 模板不存在或已取消模板、400 名称非法，
 * 错误经 ApiError/messageOf 透出。
 */

export const APPROVAL_CONDITION_MAX = 40

export function normalizeApprovalConditionList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  const result: string[] = []
  for (const item of value) {
    if (typeof item !== 'string') continue
    const text = item.trim()
    if (!text || text.length > APPROVAL_CONDITION_MAX || result.includes(text)) continue
    result.push(text)
  }
  return result
}

/** 币种比较按规范串（去空格 + 大写），与后端的规范化比较保持一致。 */

export function normalizeCurrencyToken(value: unknown): string {
  return typeof value === 'string' ? value.trim().toUpperCase() : ''
}

/**
 * 本地镜像后端 `applicable_stages`：按原链顺序过滤出当前上下文下生效的级，
 * 返回（原链索引, 阶段）列表。无门槛（缺省 / null）恒生效；金额未知
 * （null / undefined / 非有限数）时带门槛级也视为生效（保守，宁多审不漏审）；
 * 金额已知时仅 totalAmount ≥ min_amount 的门槛级生效。
 * R25：packages 非空要求 context.packageId 命中，currencies 非空要求
 * context.currency 命中（不区分大小写）；条件不匹配的级跳过。
 * 过滤后无任何生效级时回退全部阶段，保证审批链不会被条件架空。
 */

export function normalizeBidPackage(value: unknown): BidPackage {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const nested = raw.package
  const source =
    nested && typeof nested === 'object' && !Array.isArray(nested)
      ? (nested as Record<string, unknown>)
      : raw
  return {
    id: asText(source.id),
    name: asText(source.name),
    number: asText(source.number),
    position: Math.max(0, Math.round(asNumber(source.position, 0))),
    has_boq: asBool(source.has_boq),
    created_at: typeof source.created_at === 'string' ? source.created_at : null,
  }
}

export function normalizeAuditPage(data: unknown): AuditPage {
  const raw = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  const items: AuditEntry[] = []
  for (const item of asArray<unknown>(raw, 'items')) {
    if (!item || typeof item !== 'object') continue
    const record = item as Record<string, unknown>
    if (typeof record.id !== 'number' || typeof record.action !== 'string') continue
    items.push({
      id: record.id,
      action: record.action,
      username: typeof record.username === 'string' ? record.username : '',
      display_name: typeof record.display_name === 'string' ? record.display_name : '',
      detail:
        record.detail && typeof record.detail === 'object' && !Array.isArray(record.detail)
          ? (record.detail as Record<string, unknown>)
          : null,
      created_at: typeof record.created_at === 'string' ? record.created_at : null,
      project_id: typeof record.project_id === 'string' ? record.project_id : null,
      project_name: typeof record.project_name === 'string' ? record.project_name : null,
    })
  }
  return {
    items,
    next_before: typeof raw.next_before === 'number' ? raw.next_before : null,
  }
}

export function auditQuery(params: { limit?: number; before?: number | null; project_id?: string | null }) {
  const search = new URLSearchParams()
  search.set('limit', String(params.limit ?? 50))
  if (typeof params.before === 'number') search.set('before', String(params.before))
  if (params.project_id) search.set('project_id', params.project_id)
  return `?${search.toString()}`
}

export function normalizePendingApproval(value: unknown): PendingApproval | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const projectId = asText(raw.project_id).trim()
  const versionId = asText(raw.version_id).trim()
  if (!projectId || !versionId) return null
  const modeRaw = asText(raw.mode).trim()
  const mode: PendingApproval['mode'] =
    modeRaw === 'any' ? 'any' : modeRaw === 'all' ? 'all' : ''
  const stage = Math.max(1, Math.round(asNumber(raw.stage, 1)))
  // 总级数至少不小于当前级，保证「第 X/N 级」永不出现 X>N。
  const stageCount = Math.max(stage, Math.round(asNumber(raw.stage_count, stage)))
  const packageId = asText(raw.package_id).trim()
  const amount = asNumber(raw.total_amount, Number.NaN)
  return {
    project_id: projectId,
    project_name: asText(raw.project_name).slice(0, 120) || '未命名项目',
    version_id: versionId,
    version_name: asText(raw.version_name).slice(0, 120) || '未命名版本',
    package_id: packageId || null,
    stage,
    stage_count: stageCount,
    mode,
    submitted_by: asText(raw.submitted_by).slice(0, 80),
    submitted_at: isoOrNull(raw.submitted_at),
    total_amount: Number.isFinite(amount) ? amount : null,
  }
}

/**
 * 读取当前用户的跨项目审批待办（平铺数组）。旧后端没有该接口时返回 404，
 * 或 SPA 兜底把未知路径回成 index.html（200 + HTML 导致 JSON 解析失败），
 * 两种情况都按「无待办」降级为空列表，而不是把错误抛给顶栏。
 */

export function normalizeRiskTier(value: unknown): RiskTierSummary {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const level = asText(raw.level).toLowerCase() as RiskTierLevel
  const known: RiskTierLevel[] = ['blocking', 'critical', 'major', 'minor']
  return {
    level: known.includes(level) ? level : 'minor',
    label: asText(raw.label) || level,
    total: Math.max(0, Math.round(asNumber(raw.total, 0))),
    open: Math.max(0, Math.round(asNumber(raw.open, 0))),
    handled: Math.max(0, Math.round(asNumber(raw.handled, 0))),
    ignored: Math.max(0, Math.round(asNumber(raw.ignored, 0))),
  }
}

export function normalizeRiskCategory(value: unknown): RiskCategorySummary {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return {
    category: asText(raw.category),
    total: Math.max(0, Math.round(asNumber(raw.total, 0))),
    open: Math.max(0, Math.round(asNumber(raw.open, 0))),
    blocking_open: Math.max(0, Math.round(asNumber(raw.blocking_open, 0))),
  }
}

/**
 * 交付前风险总览：四档聚合 + 导出门禁 + 按严重度排序的问题清单。
 * 字段缺失（旧后端/并行发布窗口）时按空态归一，界面拿到的是安全形状。
 */

export function normalizeRequirement(value: unknown): RequirementResponse | null {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : null
  if (!raw || !asText(raw.id) || !asText(raw.text)) return null
  const status = asText(raw.response_status).toLowerCase()
  return {
    id: asText(raw.id),
    kind: asText(raw.kind) || 'substantive',
    marker: asText(raw.marker),
    requirement_type: asText(raw.requirement_type) || undefined,
    requirement_type_label: asText(raw.requirement_type_label) || undefined,
    text: asText(raw.text),
    mandatory: Boolean(raw.mandatory),
    source_quote: asText(raw.source_quote),
    response_status:
      status === 'responded' || status === 'na' ? (status as RequirementResponseStatus) : 'pending',
    response_note: asText(raw.response_note),
    responded_by: asText(raw.responded_by),
    responded_at: asText(raw.responded_at) || null,
    response_verdict: asText(raw.response_verdict) || undefined,
    response_verdict_label: asText(raw.response_verdict_label) || undefined,
    response_verdict_reason: asText(raw.response_verdict_reason) || undefined,
    section_id: asText(raw.section_id) || null,
    node_id: asText(raw.node_id) || null,
    section_heading: asText(raw.section_heading),
    auto_covered: Boolean(raw.auto_covered),
    page: typeof raw.page === 'number' ? raw.page : 0,
    clause_no: asText(raw.clause_no),
    verdict: (['satisfied', 'unsatisfied', 'partial', 'review'] as const).includes(
      asText(raw.verdict) as 'satisfied',
    )
      ? (raw.verdict as RequirementVerdict)
      : undefined,
    verdict_label: asText(raw.verdict_label) || undefined,
    verdict_reason: asText(raw.verdict_reason) || undefined,
    verdict_level: asText(raw.verdict_level) || undefined,
    verdict_layers: (raw.verdict_layers as Record<string, unknown> | undefined) || undefined,
  }
}

/** 实质性条款及正文响应状态；旧后端没有该端点时按空列表处理（页面不报错）。 */

export function normalizeBatchItem(value: unknown): BatchItemResult | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  return {
    project_id: asText(raw.project_id),
    name: asText(raw.name),
    ok: raw.ok !== false,
    issues: typeof raw.issues === 'number' ? raw.issues : undefined,
    blocking: typeof raw.blocking === 'number' ? raw.blocking : undefined,
    arcname: raw.arcname ? asText(raw.arcname) : undefined,
    error: raw.error ? asText(raw.error) : undefined,
  }
}

/**
 * Read one batch job. `payload` carries running counters; `result` only exists
 * once the job is done, so both shapes are folded into one view model.
 */

export function asLooseBool(value: unknown, fallback: boolean): boolean {
  if (value === undefined || value === null || value === '') return fallback
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0
  if (typeof value === 'string') return !['false', '0', 'no', 'off'].includes(value.toLowerCase())
  return fallback
}

/**
 * 归一化配图策略：缺省取默认值，并按契约 clamp，
 * 保证界面上的数字永远落在后端接受的区间内。
 */

export function normalizeImagePlanDocument(
  value: unknown,
  policy: ImagePolicy | undefined,
  summary: ImagePlan['summary'],
): ImagePlanDocument | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const raw = value as Record<string, unknown>
  const used = asNumber(raw.used, 0)
  const maxTotal = asNumber(raw.max_total, policy?.max_total ?? 0)
  const remainingRaw = raw.remaining
  return {
    used,
    max_total: maxTotal,
    remaining:
      typeof remainingRaw === 'number' && Number.isFinite(remainingRaw)
        ? remainingRaw
        : maxTotal > 0
          ? Math.max(0, maxTotal - used)
          : null,
    placeholders: asNumber(raw.placeholders, summary.placeholders),
    ready: asNumber(raw.ready, summary.ready),
    failed: asNumber(raw.failed, summary.failed),
    missing: asNumber(raw.missing, summary.missing),
    over_total: asLooseBool(raw.over_total, maxTotal > 0 && used >= maxTotal),
  }
}

export function boqNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

/** 可空数值字段：缺失、null 或非有限数一律视为 null（旧后端不带该字段）。 */

export function nullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function normalizeBoqPrice(value: unknown): BoqPrice | null {
  if (!value || typeof value !== 'object') return null
  const source = value as Record<string, unknown>
  return {
    unit_price: typeof source.unit_price === 'number' ? source.unit_price : null,
    amount: typeof source.amount === 'number' ? source.amount : null,
  }
}

export function normalizeBoqSnapshot(value: unknown): BoqSnapshot | null {
  if (!value || typeof value !== 'object') return null
  const source = value as Record<string, unknown>
  return {
    id: String(source.id ?? ''),
    filename: String(source.filename ?? ''),
    sheet: String(source.sheet ?? ''),
    header_row: boqNumber(source.header_row),
    item_count: boqNumber(source.item_count),
    warnings: Array.isArray(source.warnings)
      ? source.warnings.filter((item): item is string => typeof item === 'string')
      : [],
    created_at: typeof source.created_at === 'string' ? source.created_at : null,
  }
}

export function normalizeBoqRow(value: unknown): BoqRow | null {
  if (!value || typeof value !== 'object') return null
  const source = value as Record<string, unknown>
  const ledger =
    source.ledger && typeof source.ledger === 'object'
      ? (source.ledger as BoqRow['ledger'])
      : null
  return {
    key: String(source.key ?? ''),
    boq_row: typeof source.boq_row === 'number' ? source.boq_row : null,
    seq: typeof source.seq === 'string' ? source.seq : String(source.seq ?? ''),
    name: String(source.name ?? ''),
    spec: String(source.spec ?? ''),
    unit: String(source.unit ?? ''),
    qty: typeof source.qty === 'number' ? source.qty : null,
    note: String(source.note ?? ''),
    status: String(source.status ?? ''),
    ignored: source.ignored === true,
    match_score: typeof source.match_score === 'number' ? source.match_score : null,
    ledger,
    diff:
      source.diff && typeof source.diff === 'object'
        ? (source.diff as BoqRow['diff'])
        : null,
    price: normalizeBoqPrice(source.price),
  }
}

export function boqPackageQuery(packageId?: string | null): string {
  const value = (packageId || '').trim()
  return value ? `?package_id=${encodeURIComponent(value)}` : ''
}

/** 统一归一化一份清单响应（GET / apply / adjust 都返回同一形状）。 */

export function normalizeBoqPayload(value: unknown): BoqPayload {
  const data = value && typeof value === 'object' ? (value as Partial<BoqPayload>) : {}
  return {
    snapshot: normalizeBoqSnapshot(data.snapshot),
    rows: Array.isArray(data.rows)
      ? (data.rows.map(normalizeBoqRow).filter(Boolean) as BoqRow[])
      : [],
    summary: normalizeBoqSummary(data.summary),
    ledger_summary: (data.ledger_summary as BoqLedgerSummary) || undefined,
  }
}

export const QUOTE_VERSION_STATUSES: QuoteVersionStatus[] = [
  'draft',
  'submitted',
  'approved',
  'rejected',
]

export function normalizeQuoteVersionStatus(value: unknown): QuoteVersionStatus {
  const status = asText(value).trim()
  return QUOTE_VERSION_STATUSES.includes(status as QuoteVersionStatus)
    ? (status as QuoteVersionStatus)
    : 'draft'
}

export function isoOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

/** 逐级审批记录归一化：只保留决定为 approve/reject 的条目，旧后端缺省为空列表。 */

export function normalizeQuoteApprovals(value: unknown): QuoteApprovalRecord[] {
  if (!Array.isArray(value)) return []
  const records: QuoteApprovalRecord[] = []
  for (const item of value) {
    if (!item || typeof item !== 'object') continue
    const record = item as Record<string, unknown>
    const decision = asText(record.decision).trim()
    if (decision !== 'approve' && decision !== 'reject') continue
    records.push({
      stage: Math.max(0, Math.round(asNumber(record.stage, 0))),
      user_id: asText(record.user_id),
      name: asText(record.name).slice(0, 80),
      decision,
      note: asText(record.note).slice(0, 200),
      at: isoOrNull(record.at),
    })
  }
  return records
}

export async function postQuoteVersionAction(
  ref: string,
  versionId: string,
  action: 'submit' | 'review' | 'unapprove',
  body?: Record<string, unknown>,
): Promise<BoqQuoteVersion> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions/${encodeURIComponent(versionId)}/${action}`,
    {
      method: 'POST',
      ...(body ? { body: JSON.stringify(body) } : {}),
    },
  )
  const version = normalizeBoqQuoteVersion(data)
  if (!version) throw new ApiError(0, null, '报价版本返回了无法识别的数据。')
  return version
}

/** 提交版本审批；只有草稿或已驳回的版本可以提交。 */

export function normalizeBoqVersionDiffEntry(value: unknown): BoqVersionDiffEntry {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return {
    key: asText(raw.key),
    name: asText(raw.name),
    unit_price: asNumber(raw.unit_price, 0),
    amount: raw.amount === null || raw.amount === undefined ? null : asNumber(raw.amount, 0),
  }
}

export function normalizeBoqVersionDiffChanged(value: unknown): BoqVersionDiffChanged {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return {
    key: asText(raw.key),
    name: asText(raw.name),
    from: asNumber(raw.from, 0),
    to: asNumber(raw.to, 0),
    from_amount: raw.from_amount === null || raw.from_amount === undefined ? null : asNumber(raw.from_amount, 0),
    to_amount: raw.to_amount === null || raw.to_amount === undefined ? null : asNumber(raw.to_amount, 0),
  }
}

export function normalizeQuotaProposal(value: unknown): QuotaProposal | null {
  if (!value || typeof value !== 'object') return null
  const source = value as Record<string, unknown>
  return {
    boq_row: typeof source.boq_row === 'number' ? source.boq_row : null,
    name: String(source.name ?? ''),
    quota_id: String(source.quota_id ?? ''),
    quota_name: String(source.quota_name ?? ''),
    unit_price:
      typeof source.unit_price === 'number' && Number.isFinite(source.unit_price)
        ? source.unit_price
        : 0,
    score: typeof source.score === 'number' && Number.isFinite(source.score) ? source.score : 0,
  }
}

/**
 * 预览 / 套用企业定额。`dry_run=true` 只返回提案而不写库；
 * 旧后端没有该接口时调用方按错误提示降级。
 */

export const PRICE_CURRENCIES: readonly PriceCurrency[] = ['CNY', 'USD', 'EUR', 'HKD']

export const EXCHANGE_RATE_MIN = 0.0001

export const EXCHANGE_RATE_MAX = 10000

export function normalizeCurrency(value: unknown): PriceCurrency {
  const text = typeof value === 'string' ? value.trim().toUpperCase() : ''
  return (PRICE_CURRENCIES as readonly string[]).includes(text)
    ? (text as PriceCurrency)
    : 'CNY'
}

export function clampNumber(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** 汇率 clamp：0.0001–10000，缺省 1；币种为 CNY 时恒为 1。 */

export function clampExchangeRate(value: unknown, currency: PriceCurrency): number {
  if (currency === 'CNY') return 1
  return clampNumber(asNumber(value, 1), EXCHANGE_RATE_MIN, EXCHANGE_RATE_MAX)
}

/** 按小数位四舍五入；与后端 `round(value, digits)` 保持一致的预览口径。 */

export function normalizeQuotaItem(value: unknown): QuotaItem {
  const source = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return {
    id: asText(source.id),
    name: asText(source.name),
    spec: asText(source.spec),
    unit: asText(source.unit),
    unit_price: asNumber(source.unit_price, 0),
    category: asText(source.category),
    note: asText(source.note),
    created_at: typeof source.created_at === 'string' ? source.created_at : null,
    updated_at: typeof source.updated_at === 'string' ? source.updated_at : null,
  }
}

export function quotaItems(data: unknown): unknown[] {
  const items = asArray<unknown>(data, 'items')
  if (items.length) return items
  return asArray<unknown>(data, 'quotas')
}

export const TERM_KINDS = ['replace', 'forbidden'] as const

export const TERM_SOURCE_MAX = 120

export const TERM_TARGET_MAX = 120

export const TERM_NOTE_MAX = 200

/** 后端只认 replace / forbidden；旧数据或脏值统一回退到 replace。 */

export function normalizeTermKind(value: unknown): 'replace' | 'forbidden' {
  return value === 'forbidden' ? 'forbidden' : 'replace'
}

/**
 * 归一化一条术语。列表接口返回的是平铺数组，字段可能来自旧版本，
 * 因此这里补齐默认值并按后端列宽截断，避免超长文本被静默拒绝。
 */

export function termItems(data: unknown): unknown[] {
  const items = asArray<unknown>(data, 'items')
  if (items.length) return items
  return asArray<unknown>(data, 'terms')
}

export function termPayload(input: TermInput): Record<string, unknown> {
  return {
    kind: normalizeTermKind(input.kind),
    source: (input.source || '').trim().slice(0, TERM_SOURCE_MAX),
    target: (input.target || '').trim().slice(0, TERM_TARGET_MAX),
    note: (input.note || '').trim().slice(0, TERM_NOTE_MAX),
    enabled: input.enabled ?? true,
  }
}

export function normalizeTextQa(value: unknown): TextQaSettings {
  const raw =
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {}
  const typos = normalizeTypoRules(raw.typos ?? value)
  return {
    typos,
    count: Math.max(0, Math.round(asNumber(raw.count, typos.length))),
    builtin_count: Math.max(0, Math.round(asNumber(raw.builtin_count, 0))),
  }
}

/** 读取企业自定义错别字词库（仅超管）；内置词库只回条数，不回明细。 */

export function normalizePluginArgs(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => (typeof item === 'string' ? item.trim() : ''))
      .filter(Boolean)
  }
  if (typeof value === 'string' && value.trim()) {
    try {
      const parsed = JSON.parse(value)
      return normalizePluginArgs(parsed)
    } catch {
      return value
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean)
    }
  }
  return []
}

export function normalizePluginEnv(value: unknown): Record<string, string> {
  let source = value
  if (typeof source === 'string' && source.trim()) {
    try {
      source = JSON.parse(source)
    } catch {
      return {}
    }
  }
  if (!source || typeof source !== 'object' || Array.isArray(source)) return {}
  const result: Record<string, string> = {}
  for (const [key, raw] of Object.entries(source as Record<string, unknown>)) {
    const name = key.trim()
    if (!name) continue
    if (typeof raw === 'string') result[name] = raw
    else if (typeof raw === 'number' || typeof raw === 'boolean') result[name] = String(raw)
  }
  return result
}

export function normalizePluginTools(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (typeof item === 'string') return item.trim()
      if (item && typeof item === 'object' && 'name' in item) {
        return String((item as { name: unknown }).name ?? '').trim()
      }
      return ''
    })
    .filter(Boolean)
}

/** 插件资源限制的合法区间与缺省值，与后端契约保持一致。 */

export function normalizePluginLimit(
  value: unknown,
  fallback: number,
  min: number,
  max: number,
): number {
  if (value === undefined || value === null || value === '') return fallback
  const parsed = asNumber(value, Number.NaN)
  if (!Number.isFinite(parsed)) return fallback
  return clampNumber(Math.round(parsed), min, max)
}

export function pluginItems(data: unknown): unknown[] {
  const items = asArray<unknown>(data, 'plugins')
  if (items.length) return items
  return asArray<unknown>(data, 'items')
}

export function normalizePluginCatalogEntry(value: unknown): PluginCatalogEntry | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const name = asText(raw.name)
  if (!name) return null
  const transport = asText(raw.transport).toLowerCase()
  return {
    name,
    description: asText(raw.description),
    transport: transport === 'http' ? 'http' : 'stdio',
    command: asText(raw.command),
    args: normalizePluginArgs(raw.args),
    url: asText(raw.url),
  }
}

export function normalizeWechatConfig(value: unknown): WechatConfig {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return {
    app_id: asText(raw.app_id),
    has_secret: asBool(raw.has_secret),
    redirect_uri: asText(raw.redirect_uri),
    enabled: asBool(raw.enabled),
    mock: asBool(raw.mock),
    available: asBool(raw.available),
    source: asText(raw.source, 'env'),
  }
}

export const PROFILE_TEXT_FIELDS = [
  ['company_name', 80],
  ['legal_rep', 40],
  ['project_name', 120],
  ['owner_name', 120],
  ['bid_amount', 40],
  ['duration_days', 20],
  ['warranty_years', 20],
  ['quality_standard', 80],
] as const

export function normalizeDeletionState(value: unknown): DeletionState {
  const raw = (value ?? {}) as Record<string, unknown>
  return {
    pending: Boolean(raw.pending),
    requested_at: typeof raw.requested_at === 'string' ? raw.requested_at : null,
    due_at: typeof raw.due_at === 'string' ? raw.due_at : null,
    days_left: Number(raw.days_left ?? 0),
    cooling_off_days: Number(raw.cooling_off_days ?? 0),
  }
}

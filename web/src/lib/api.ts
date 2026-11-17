import type {
  AdminBillingData,
  AdminBillingSettings,
  AdminRulePackage,
  AuditEntry,
  AuditPage,
  BatchExportFailure,
  BatchExportFile,
  BatchFormat,
  BatchItemResult,
  BatchJob,
  BidPackage,
  BidPackageInput,
  BillingData,
  BoqAction,
  BoqLedgerSummary,
  BoqPayload,
  BoqPrice,
  BoqQuoteVersion,
  BoqRow,
  BoqSnapshot,
  BoqSummary,
  BoqVersionDeleteResult,
  BoqVersionDiff,
  BoqVersionDiffChanged,
  BoqVersionDiffEntry,
  ChapterConflict,
  CreditAdjustResult,
  KnowledgeBase,
  KnowledgeBaseInput,
  KbVectorResult,
  ChapterDetail,
  CheckResult,
  GenerationReport,
  Health,
  ImageAsset,
  ImageCreateInput,
  ImagePlan,
  ImagePlanApplyResult,
  ImagePlanDocument,
  ImagePolicy,
  Issue,
  Job,
  Ledger,
  Member,
  MemberRole,
  MockPayResult,
  ModelConfig,
  ModelInput,
  OutlineChapter,
  OutlineResponse,
  PackageInfo,
  ParseResult,
  Plugin,
  PluginCatalogEntry,
  WechatConfig,
  WechatConfigInput,
  PluginInput,
  PluginTestResult,
  PricePolicy,
  PriceCurrency,
  Project,
  ProjectDetail,
  QuotaFillResult,
  QuotaInput,
  QuotaItem,
  QuotaMatch,
  QuotaProposal,
  RechargeOrder,
  RedeemResult,
  RulePackageSummary,
  RulePackageValidation,
  SourceFile,
  TermInput,
  TermItem,
  TestResult,
  UploadResult,
  User,
  UserInput,
} from './types'
import { normalizeProjectType, projectTypeLabel } from './format'

export const UNAUTHORIZED_EVENT = 'bidcraft:unauthorized'

const AUTH_SKIP_401 = ['/auth/login', '/auth/me', '/auth/logout']

export function notifyUnauthorized(path: string): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT, { detail: { path } }))
}

export class ApiError extends Error {
  status: number
  detail: unknown
  /** Full parsed response body, when the server sent one. */
  payload: unknown
  missingRequired: string[]

  constructor(status: number, detail: unknown, message?: string, payload?: unknown) {
    super(message || errorDetailText(detail) || `请求失败（${status || '网络错误'}）`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.payload = payload ?? null
    const missing =
      detail && typeof detail === 'object' && 'missing_required' in detail
        ? (detail as { missing_required?: unknown }).missing_required
        : undefined
    this.missingRequired = Array.isArray(missing)
      ? missing.filter((item): item is string => typeof item === 'string')
      : []
  }
}

export function errorDetailText(detail: unknown): string {
  if (!detail) return ''
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg: unknown }).msg)
        }
        return JSON.stringify(item)
      })
      .join('；')
  }
  if (typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    if (Array.isArray(record.missing_required)) {
      return `还缺 ${record.missing_required.length} 项必填事实：${record.missing_required
        .slice(0, 4)
        .join('、')}${record.missing_required.length > 4 ? '…' : ''}`
    }
    if (typeof record.message === 'string') return record.message
    if (typeof record.detail === 'string') return record.detail
    try {
      return JSON.stringify(detail)
    } catch {
      return '请求失败'
    }
  }
  return String(detail)
}

export function messageOf(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return '发生未知错误，请重试'
}

/**
 * Extract the optimistic-lock payload from a chapter save 409. The server nests
 * the conflict fields next to `detail`; older proxies may only expose `detail`,
 * so both shapes are accepted.
 */
export function chapterConflict(error: unknown): ChapterConflict | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null
  const candidates: unknown[] = [error.payload, error.detail]
  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== 'object') continue
    const record = candidate as Record<string, unknown>
    if (typeof record.current_revision !== 'number') continue
    return {
      current_revision: record.current_revision,
      current_body:
        typeof record.current_body === 'string' ? record.current_body : '',
      current_words:
        typeof record.current_words === 'number' ? record.current_words : 0,
      message: typeof record.detail === 'string' ? record.detail : undefined,
    }
  }
  return null
}

function asArray<T>(value: unknown, key: string): T[] {
  if (Array.isArray(value)) return value as T[]
  if (value && typeof value === 'object') {
    const nested = (value as Record<string, unknown>)[key]
    if (Array.isArray(nested)) return nested as T[]
  }
  return []
}

async function readError(response: Response): Promise<ApiError> {
  let detail: unknown = response.statusText
  let payload: unknown = null
  const text = await response.text().catch(() => '')
  if (text) {
    try {
      const parsed = JSON.parse(text) as { detail?: unknown; message?: unknown }
      payload = parsed
      detail = parsed.detail ?? parsed.message ?? text
    } catch {
      detail = text
      payload = text
    }
  }
  return new ApiError(response.status, detail, undefined, payload)
}

/** Cancellation must stay distinguishable from a real network failure. */
export function isAbortError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  return (error as { name?: unknown }).name === 'AbortError'
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  signal?: AbortSignal,
): Promise<T> {
  const requestSignal = signal ?? init?.signal ?? undefined
  let response: Response
  try {
    response = await fetch(path, {
      credentials: 'same-origin',
      ...init,
      ...(requestSignal ? { signal: requestSignal } : {}),
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init?.headers || {}),
      },
    })
  } catch (error) {
    // An aborted request is not a server failure: let `useQuery` and callers
    // recognise it and skip the error state.
    if (isAbortError(error)) throw error
    throw new ApiError(
      0,
      null,
      '无法连接本地服务。请确认 BidCraft 后端已启动，且当前页面由它托管。',
    )
  }
  if (!response.ok) {
    if (response.status === 401 && !AUTH_SKIP_401.some((item) => path.startsWith(item))) {
      notifyUnauthorized(path)
    }
    throw await readError(response)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const request = apiFetch

const jsonBody = (data: unknown): RequestInit => ({
  method: 'POST',
  body: JSON.stringify(data),
})

// ---------------------------------------------------------------- upload transport

interface UploadOptions {
  onProgress?: (percent: number) => void
  /** Friendly message used when the request never reaches the server. */
  networkMessage?: string
  /** Cancels the upload; the promise rejects with a cancellation ApiError. */
  signal?: AbortSignal
}

/**
 * Shared multipart upload transport. Uploads cannot use `fetch` (no upload
 * progress), so every multipart call goes through this helper instead of
 * hand-rolling XHR: 401 still triggers the global re-auth event, 403 and other
 * failures surface as the same `ApiError` shape as JSON requests, and progress
 * reporting stays consistent.
 */
export function uploadForm<T>(
  path: string,
  form: FormData,
  options: UploadOptions = {},
): Promise<T> {
  const { onProgress, networkMessage, signal } = options
  if (signal?.aborted) {
    return Promise.reject(new ApiError(0, null, '上传已取消。'))
  }
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const cancelled = () => new ApiError(0, null, '上传已取消。')
    let settled = false
    const handleAbort = () => xhr.abort()
    const cleanup = () => {
      if (signal) signal.removeEventListener('abort', handleAbort)
    }
    const finish = (fn: () => void) => {
      if (settled) return
      settled = true
      cleanup()
      fn()
    }
    if (signal) signal.addEventListener('abort', handleAbort)
    xhr.open('POST', path)
    xhr.responseType = 'text'
    xhr.withCredentials = true
    if (onProgress) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress(Math.round((event.loaded / event.total) * 100))
        }
      }
    }
    xhr.onload = () => {
      const text = xhr.responseText || ''
      const body: unknown = (() => {
        try {
          return text ? JSON.parse(text) : null
        } catch {
          return text
        }
      })()
      if (xhr.status >= 200 && xhr.status < 300) {
        finish(() => resolve((body || {}) as T))
        return
      }
      if (xhr.status === 401) notifyUnauthorized(path)
      const fallback =
        xhr.status === 403 ? '当前账号没有执行此操作的权限。' : xhr.statusText
      const detail =
        body && typeof body === 'object' && 'detail' in body
          ? (body as { detail: unknown }).detail
          : text || fallback
      finish(() => reject(new ApiError(xhr.status, detail)))
    }
    xhr.onerror = () =>
      finish(() =>
        reject(
          new ApiError(
            0,
            null,
            networkMessage || '上传失败：无法连接本地服务，请确认后端已启动。',
          ),
        ),
      )
    xhr.onabort = () => finish(() => reject(cancelled()))
    xhr.send(form)
  })
}

// ---------------------------------------------------------------- health & packages

export const getHealth = () => request<Health>('/health')

function asText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value
  if (value === undefined || value === null) return fallback
  return String(value)
}

function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    return Number(value)
  }
  return fallback
}

function asBool(value: unknown): boolean {
  return value === true || value === 'true'
}

function asStringList(value: unknown): string[] {
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

function normalizePackageInfo(value: unknown): PackageInfo {
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

function unwrapRulePackage(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const raw = value as Record<string, unknown>
  const nested = raw.package
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
    return { ...(nested as Record<string, unknown>), ...raw, package: undefined }
  }
  return raw
}

function normalizeRulePackage(value: unknown): AdminRulePackage {
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

function normalizeRulePackageSummary(value: unknown): RulePackageSummary {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return {
    fields: Math.max(0, Math.round(asNumber(raw.fields, 0))),
    outline: Math.max(0, Math.round(asNumber(raw.outline, 0))),
    checks: Math.max(0, Math.round(asNumber(raw.checks, 0))),
  }
}

function normalizeValidation(value: unknown): RulePackageValidation {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const problems = asStringList(raw.problems)
  const ok = raw.ok === undefined || raw.ok === null ? problems.length === 0 : asBool(raw.ok)
  return {
    ok: ok && problems.length === 0,
    problems,
    summary: normalizeRulePackageSummary(raw.summary),
  }
}

export async function getPackages(): Promise<PackageInfo[]> {
  const data = await request<PackageInfo[] | { packages?: PackageInfo[] }>('/packages')
  return asArray<unknown>(data, 'packages').map(normalizePackageInfo)
}

// ---------------------------------------------------------------- admin: rule packages

export async function getRulePackages(includeHidden = false): Promise<AdminRulePackage[]> {
  const query = includeHidden ? '?include_hidden=true' : ''
  const data = await request<unknown>(`/admin/rule-packages${query}`)
  const items = asArray<unknown>(data, 'packages')
  const list = items.length ? items : asArray<unknown>(data, 'rule_packages')
  return list.map(normalizeRulePackage)
}

export async function getRulePackage(name: string): Promise<AdminRulePackage> {
  const data = await request<unknown>(`/admin/rule-packages/${encodeURIComponent(name)}`)
  return normalizeRulePackage(data)
}

export async function validateRulePackage(input: {
  name?: string
  document: string
}): Promise<RulePackageValidation> {
  const data = await request<unknown>(
    '/admin/rule-packages/validate',
    jsonBody({ ...(input.name ? { name: input.name } : {}), document: input.document }),
  )
  return normalizeValidation(data)
}

export async function createRulePackage(document: string): Promise<AdminRulePackage> {
  const data = await request<unknown>('/admin/rule-packages', jsonBody({ document }))
  return normalizeRulePackage(data)
}

export async function updateRulePackage(
  name: string,
  document: string,
): Promise<AdminRulePackage> {
  const data = await request<unknown>(
    `/admin/rule-packages/${encodeURIComponent(name)}`,
    { method: 'PUT', body: JSON.stringify({ document }) },
  )
  return normalizeRulePackage(data)
}

export const deleteRulePackage = (name: string) =>
  request<{ name?: string; hidden?: boolean }>(
    `/admin/rule-packages/${encodeURIComponent(name)}`,
    { method: 'DELETE' },
  )

export async function restoreRulePackage(
  name: string,
  resetOverride = false,
): Promise<AdminRulePackage> {
  const query = resetOverride ? '?reset_override=true' : ''
  const data = await request<unknown>(
    `/admin/rule-packages/${encodeURIComponent(name)}/restore${query}`,
    { method: 'POST' },
  )
  return normalizeRulePackage(data)
}

// ---------------------------------------------------------------- auth

export const login = (username: string, password: string) =>
  request<{ user: User }>('/auth/login', jsonBody({ username, password }))

export const logout = () =>
  request<{ logged_out: boolean }>('/auth/logout', { method: 'POST' })

export const getMe = () => request<{ user: User }>('/auth/me')

export const register = (username: string, password: string, displayName = '') =>
  request<{ user: User }>(
    '/auth/register',
    jsonBody({ username, password, display_name: displayName }),
  )

export function wechatLoginUrl(health: { wechat_mock?: boolean } | null, next = '/'): string {
  const path = health?.wechat_mock ? '/auth/wechat/mock' : '/auth/wechat/start'
  return `${path}?next=${encodeURIComponent(next)}`
}

export const changePassword = (currentPassword: string, newPassword: string) =>
  request<{ changed: boolean; relogin: boolean }>(
    '/auth/password',
    jsonBody({ current_password: currentPassword, new_password: newPassword }),
  )

// ---------------------------------------------------------------- admin: models

export async function getModels(): Promise<ModelConfig[]> {
  const data = await request<ModelConfig[] | { models?: ModelConfig[] }>('/admin/models')
  return asArray<ModelConfig>(data, 'models')
}

export const createModel = (payload: ModelInput) =>
  request<ModelConfig>('/admin/models', jsonBody(payload))

export const updateModel = (id: string, payload: Partial<ModelInput>) =>
  request<ModelConfig>(`/admin/models/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })

export const deleteModel = (id: string) =>
  request<{ deleted: boolean; id: string }>(`/admin/models/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })

export const testModel = (id: string) =>
  request<TestResult>(`/admin/models/${encodeURIComponent(id)}/test`, { method: 'POST' })

// ---------------------------------------------------------------- admin: knowledge bases

export async function getKnowledgeBases(): Promise<KnowledgeBase[]> {
  const data = await request<KnowledgeBase[] | { knowledge_bases?: KnowledgeBase[] }>(
    '/admin/kbs',
  )
  return asArray<KnowledgeBase>(data, 'knowledge_bases')
}

export const createKnowledgeBase = (payload: KnowledgeBaseInput) =>
  request<KnowledgeBase>('/admin/kbs', jsonBody(payload))

export const updateKnowledgeBase = (id: string, payload: Partial<KnowledgeBaseInput>) =>
  request<KnowledgeBase>(`/admin/kbs/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })

export const deleteKnowledgeBase = (id: string) =>
  request<{ deleted: boolean; id: string }>(`/admin/kbs/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })

export const testKnowledgeBase = (id: string) =>
  request<TestResult>(`/admin/kbs/${encodeURIComponent(id)}/test`, { method: 'POST' })

/** Vector fields are optional: an older server simply reports zeroes. */
function normalizeKbVector(value: unknown): KbVectorResult | null {
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

export interface KbReindexResult {
  indexed: number
  kb: KnowledgeBase
  /** Present when the server rebuilt the vector index too; null otherwise. */
  vector: KbVectorResult | null
}

/**
 * Newer servers accept the rebuild and return only a job id; the result is read
 * later from `GET /jobs/{id}`. An older server still answers synchronously with
 * `{indexed, vector, kb}`, so both shapes are exposed to the caller.
 */
export interface KbReindexStart {
  job_id: string | null
  legacy: KbReindexResult | null
}

/** Normalise a `kb_reindex` job result (or a legacy sync response). */
export function parseKbReindexResult(value: unknown): KbReindexResult {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const kb =
    raw.kb && typeof raw.kb === 'object' && !Array.isArray(raw.kb)
      ? (raw.kb as KnowledgeBase)
      : ({} as KnowledgeBase)
  return {
    indexed: Math.max(0, Math.round(asNumber(raw.indexed, 0))),
    kb,
    vector: normalizeKbVector(raw.vector),
  }
}

export async function reindexKnowledgeBase(id: string): Promise<KbReindexStart> {
  const data = await request<Record<string, unknown>>(
    `/admin/kbs/${encodeURIComponent(id)}/reindex`,
    { method: 'POST' },
  )
  const jobId = asText(data?.job_id)
  if (jobId) return { job_id: jobId, legacy: null }
  return { job_id: null, legacy: parseKbReindexResult(data) }
}

/**
 * Build (or refresh) the semantic vectors of a local knowledge base. A partial
 * build still resolves — check `error` before reporting success.
 */
export async function embedKnowledgeBase(id: string): Promise<KbVectorResult> {
  const data = await request<unknown>(`/admin/kbs/${encodeURIComponent(id)}/embed`, {
    method: 'POST',
  })
  return (
    normalizeKbVector(data) || {
      embedded: 0,
      total: 0,
      model: '',
      dim: 0,
      error: '',
      kb: {} as KnowledgeBase,
    }
  )
}

// ---------------------------------------------------------------- admin: users

export async function getUsers(): Promise<User[]> {
  const data = await request<User[] | { users?: User[] }>('/admin/users')
  return asArray<User>(data, 'users')
}

export const createUser = (payload: UserInput) =>
  request<User>('/admin/users', jsonBody(payload))

export const updateUser = (id: string, payload: Partial<UserInput>) =>
  request<User>(`/admin/users/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })

export const deleteUser = (id: string) =>
  request<{ deleted: boolean; id: string }>(`/admin/users/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })

// ---------------------------------------------------------------- projects

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
 * Rename a project or change its 标段/包件. Send only the fields that changed:
 * the server rejects a payload where every key is absent.
 */
export const updateProject = (
  ref: string,
  patch: { name?: string; bid_section?: string },
) =>
  request<Project>(`/projects/${encodeURIComponent(ref)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })

export async function getProject(ref: string): Promise<ProjectDetail> {
  const data = await request<ProjectDetail>(`/projects/${encodeURIComponent(ref)}`)
  return {
    ...data,
    missing_required: Array.isArray(data.missing_required) ? data.missing_required : [],
    sources: Array.isArray(data.sources) ? data.sources : undefined,
  }
}

export const deleteProject = (ref: string) =>
  request<{ deleted: boolean }>(`/projects/${encodeURIComponent(ref)}`, { method: 'DELETE' })

// ---------------------------------------------------------------- bid packages

function normalizeBidPackage(value: unknown): BidPackage {
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

// ---------------------------------------------------------------- members

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

// ---------------------------------------------------------------- audit

function normalizeAuditPage(data: unknown): AuditPage {
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

function auditQuery(params: { limit?: number; before?: number | null; project_id?: string | null }) {
  const search = new URLSearchParams()
  search.set('limit', String(params.limit ?? 50))
  if (typeof params.before === 'number') search.set('before', String(params.before))
  if (params.project_id) search.set('project_id', params.project_id)
  return `?${search.toString()}`
}

export async function getProjectAudit(
  ref: string,
  params: { limit?: number; before?: number | null } = {},
): Promise<AuditPage> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/audit${auditQuery(params)}`,
  )
  return normalizeAuditPage(data)
}

export async function getAdminAudit(
  params: { limit?: number; before?: number | null; project_id?: string | null } = {},
): Promise<AuditPage> {
  const data = await request<unknown>(`/admin/audit${auditQuery(params)}`)
  return normalizeAuditPage(data)
}


// ---------------------------------------------------------------- sources

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

// ---------------------------------------------------------------- ledger

export async function getLedger(ref: string): Promise<Ledger> {
  const data = await request<Partial<Ledger>>(`/projects/${encodeURIComponent(ref)}/ledger`)
  return {
    summary: data.summary,
    facts: Array.isArray(data.facts) ? data.facts : [],
    materials: Array.isArray(data.materials) ? data.materials : [],
    parameters: Array.isArray(data.parameters) ? data.parameters : [],
    scoring: Array.isArray(data.scoring) ? data.scoring : [],
    requirements: Array.isArray(data.requirements) ? data.requirements : [],
    risks: Array.isArray(data.risks) ? data.risks : [],
    template_sections: Array.isArray(data.template_sections) ? data.template_sections : [],
    project: data.project,
  }
}

export const updateFact = (ref: string, key: string, value: string, status: string) =>
  request<{ key: string; value: string; status: string }>(
    `/projects/${encodeURIComponent(ref)}/facts/${encodeURIComponent(key)}`,
    { method: 'PATCH', body: JSON.stringify({ value, status }) },
  )

// ---------------------------------------------------------------- outline

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

// ---------------------------------------------------------------- chapters & generation

export async function getChapter(ref: string, nodeId: string): Promise<ChapterDetail> {
  const data = await request<ChapterDetail | { chapter?: ChapterDetail }>(
    `/projects/${encodeURIComponent(ref)}/chapters/${encodeURIComponent(nodeId)}`,
  )
  if (data && typeof data === 'object' && 'chapter' in data && data.chapter) return data.chapter
  return data as ChapterDetail
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

// ---------------------------------------------------------------- checks

export const runCheck = (ref: string) =>
  request<CheckResult>(`/projects/${encodeURIComponent(ref)}/check`, { method: 'POST' })

export async function getIssues(ref: string): Promise<Issue[]> {
  const data = await request<Issue[] | { issues?: Issue[] }>(
    `/projects/${encodeURIComponent(ref)}/issues`,
  )
  return asArray<Issue>(data, 'issues')
}

// ---------------------------------------------------------------- export

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

export async function exportProject(
  ref: string,
  format: string,
): Promise<{ blob: Blob; filename: string }> {
  let response: Response
  try {
    response = await fetch(`/projects/${encodeURIComponent(ref)}/export`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: '*/*' },
      body: JSON.stringify({ format }),
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

// ---------------------------------------------------------------- batch operations

export const batchCheck = (ids: string[]) =>
  request<{ job_id: string }>('/projects/batch-check', jsonBody({ ids }))

export const batchExport = (ids: string[], format: BatchFormat = 'docx') =>
  request<{ job_id: string }>('/projects/batch-export', jsonBody({ ids, format }))

function normalizeBatchItem(value: unknown): BatchItemResult | null {
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
export async function getBatchJob(jobId: string): Promise<BatchJob> {
  const data = await request<unknown>(`/jobs/${encodeURIComponent(jobId)}`)
  const raw = data && typeof data === 'object' && !Array.isArray(data)
    ? (data as Record<string, unknown>)
    : {}
  const payload =
    raw.payload && typeof raw.payload === 'object' && !Array.isArray(raw.payload)
      ? (raw.payload as Record<string, unknown>)
      : {}
  const result =
    raw.result && typeof raw.result === 'object' && !Array.isArray(raw.result)
      ? (raw.result as Record<string, unknown>)
      : {}

  const failedRaw = result.failed
  const failedItems = (Array.isArray(failedRaw) ? failedRaw : [])
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const record = item as Record<string, unknown>
      return {
        project_id: asText(record.project_id),
        name: asText(record.name),
        error: asText(record.error),
      }
    })
    .filter((item): item is BatchExportFailure => item !== null)

  const files = asArray<unknown>(result, 'files')
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const record = item as Record<string, unknown>
      return {
        project_id: asText(record.project_id),
        name: asText(record.name),
        arcname: asText(record.arcname),
      }
    })
    .filter((item): item is BatchExportFile => item !== null)

  const resultItems = asArray<unknown>(result, 'results')
  const results = (resultItems.length ? resultItems : asArray<unknown>(payload, 'results'))
    .map(normalizeBatchItem)
    .filter((item): item is BatchItemResult => item !== null)

  const resultTotal = asNumber(result.total, asNumber(payload.total, 0))
  const resultOk = asNumber(result.ok, asNumber(payload.ok, 0))
  const resultFailed = Array.isArray(failedRaw)
    ? failedItems.length
    : asNumber(failedRaw, asNumber(payload.failed, 0))

  return {
    id: asText(raw.id) || jobId,
    kind: asText(raw.kind),
    status: asText(raw.status, 'queued'),
    progress: Math.max(0, Math.min(100, asNumber(raw.progress, 0))),
    message: asText(raw.message),
    error: asText(raw.error) || asText(result.error),
    done: asNumber(payload.done, 0),
    total: asNumber(payload.total, resultTotal),
    okCount: asNumber(payload.ok, resultOk),
    failedCount: asNumber(payload.failed, resultFailed),
    current: asText(payload.current),
    result: {
      total: resultTotal,
      ok: resultOk,
      failed: resultFailed,
      results,
      files,
      failedItems,
      zip_path: asText(result.zip_path),
      error: asText(result.error),
    },
  }
}

/** Download the zip produced by a finished batch export. */
export async function downloadBatchZip(
  jobId: string,
): Promise<{ blob: Blob; filename: string }> {
  const path = `/batch-jobs/${encodeURIComponent(jobId)}/download`
  let response: Response
  try {
    response = await fetch(path, { credentials: 'same-origin', headers: { Accept: '*/*' } })
  } catch {
    throw new ApiError(
      0,
      null,
      '无法连接本地服务。请确认 BidCraft 后端已启动，且当前页面由它托管。',
    )
  }
  if (!response.ok) {
    if (response.status === 401) notifyUnauthorized(path)
    throw await readError(response)
  }
  const blob = await response.blob()
  const filename =
    filenameFromDisposition(response.headers.get('Content-Disposition')) ||
    `batch-${jobId.slice(0, 8)}.zip`
  return { blob, filename }
}


// ---------------------------------------------------------------- billing

export const getBillingSummary = () => request<BillingData>('/billing/summary')

export const createBillingOrder = (packageId: string) =>
  request<RechargeOrder>('/billing/orders', jsonBody({ package_id: packageId }))

export const getBillingOrder = (orderId: string) =>
  request<RechargeOrder>(`/billing/orders/${encodeURIComponent(orderId)}`)

export const mockPayOrder = (orderId: string) =>
  request<MockPayResult>(`/billing/orders/${encodeURIComponent(orderId)}/mock-pay`, {
    method: 'POST',
  })

export const redeemCreditCode = (code: string) =>
  request<RedeemResult>('/billing/redeem', jsonBody({ code }))

export const billingOrderQrUrl = (orderId: string) =>
  `/billing/orders/${encodeURIComponent(orderId)}/qr`

export const getAdminBilling = () => request<AdminBillingData>('/admin/billing')

export const saveAdminBilling = (payload: AdminBillingSettings) =>
  request<{ settings: AdminBillingSettings; stats: AdminBillingData['stats'] }>(
    '/admin/billing',
    { method: 'PUT', body: JSON.stringify(payload) },
  )

export const generateRedeemCodes = (payload: {
  count: number
  credits: number
  amount_cents: number
  days: number
}) => request<{ codes: string[] }>('/admin/redeem-codes', jsonBody(payload))

export const adjustUserCredits = (userId: string, amount: number, note = '') =>
  request<CreditAdjustResult>(`/admin/users/${encodeURIComponent(userId)}/credits`, {
    method: 'POST',
    body: JSON.stringify({ amount, note }),
  })

export function wechatFullPageLogin(health: { wechat_mock?: boolean } | null, next = '/') {
  window.location.assign(wechatLoginUrl(health, next))
}



// ---------------------------------------------------------------- images

export async function listImages(ref: string, nodeId?: string): Promise<ImageAsset[]> {
  const query = nodeId ? `?node_id=${encodeURIComponent(nodeId)}` : ''
  const data = await request<ImageAsset[] | { images?: ImageAsset[] }>(
    `/projects/${encodeURIComponent(ref)}/images${query}`,
  )
  return asArray<ImageAsset>(data, 'images')
}

/** 与后端 DEFAULT_POLICY 对齐；字段缺省时用它兜底，旧后端也不会崩。 */
export const DEFAULT_IMAGE_POLICY: ImagePolicy = {
  max_total: 12,
  style: '',
  size: 'auto',
  captions: true,
  caption_prefix: '图',
  width_cm: 15,
}

function asLooseBool(value: unknown, fallback: boolean): boolean {
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
export function normalizeImagePolicy(value: unknown): ImagePolicy {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const maxTotal = Math.round(
    Math.min(60, Math.max(0, asNumber(raw.max_total, DEFAULT_IMAGE_POLICY.max_total))),
  )
  const widthRaw = asNumber(raw.width_cm, DEFAULT_IMAGE_POLICY.width_cm)
  const width = Math.min(19, Math.max(8, Math.round(widthRaw * 100) / 100))
  const size = asText(raw.size, DEFAULT_IMAGE_POLICY.size)
  return {
    max_total: maxTotal,
    style: asText(raw.style, DEFAULT_IMAGE_POLICY.style).slice(0, 80),
    size: size === 'auto' || /^\d{2,4}x\d{2,4}$/i.test(size) ? size : 'auto',
    captions: asLooseBool(raw.captions, DEFAULT_IMAGE_POLICY.captions),
    caption_prefix: asText(raw.caption_prefix, '').slice(0, 10) || DEFAULT_IMAGE_POLICY.caption_prefix,
    width_cm: width,
  }
}

function normalizeImagePlanDocument(
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

export async function getImagePolicy(ref: string): Promise<ImagePolicy> {
  const data = await request<Partial<ImagePolicy>>(
    `/projects/${encodeURIComponent(ref)}/image-policy`,
  )
  return normalizeImagePolicy(data)
}

export async function updateImagePolicy(
  ref: string,
  patch: Partial<ImagePolicy>,
): Promise<ImagePolicy> {
  const data = await request<Partial<ImagePolicy>>(
    `/projects/${encodeURIComponent(ref)}/image-policy`,
    { method: 'PUT', body: JSON.stringify(patch) },
  )
  return normalizeImagePolicy(data)
}

export async function getImagePlan(ref: string): Promise<ImagePlan> {
  const data = await request<Partial<ImagePlan>>(
    `/projects/${encodeURIComponent(ref)}/images/plan`,
  )
  const summary = {
    placeholders: data.summary?.placeholders ?? 0,
    ready: data.summary?.ready ?? 0,
    failed: data.summary?.failed ?? 0,
    missing: data.summary?.missing ?? 0,
  }
  const plan: ImagePlan = {
    chapters: Array.isArray(data.chapters) ? data.chapters : [],
    summary,
  }
  if (data.policy !== undefined && data.policy !== null) {
    plan.policy = normalizeImagePolicy(data.policy)
  }
  const document = normalizeImagePlanDocument(data.document, plan.policy, summary)
  if (document) plan.document = document
  return plan
}

export async function applyImagePlan(ref: string): Promise<ImagePlanApplyResult> {
  const data = await request<Partial<ImagePlanApplyResult>>(
    `/projects/${encodeURIComponent(ref)}/images/apply-plan`,
    { method: 'POST' },
  )
  return {
    applied: data.applied ?? 0,
    skipped_missing: data.skipped_missing ?? 0,
    skipped_failed: data.skipped_failed ?? 0,
    skipped_over_budget: data.skipped_over_budget ?? 0,
    skipped_over_total: data.skipped_over_total ?? 0,
    applied_labels: Array.isArray(data.applied_labels) ? data.applied_labels : [],
    chapters: Array.isArray(data.chapters) ? data.chapters : [],
  }
}

export const createImage = (ref: string, input: ImageCreateInput) =>
  request<ImageAsset>(`/projects/${encodeURIComponent(ref)}/images`, jsonBody(input))

export const regenerateImage = (ref: string, imageId: string) =>
  request<ImageAsset>(
    `/projects/${encodeURIComponent(ref)}/images/${encodeURIComponent(imageId)}/regenerate`,
    { method: 'POST' },
  )

/**
 * 上传本地图片作为配图。multipart 传输使用 XHR，以便复用全局 401/ApiError 处理。
 */
export function uploadImage(
  ref: string,
  file: File,
  meta: { label: string; node_id?: string },
): Promise<ImageAsset> {
  const form = new FormData()
  form.append('file', file)
  form.append('label', meta.label)
  if (meta.node_id) form.append('node_id', meta.node_id)
  return uploadForm<ImageAsset>(
    `/projects/${encodeURIComponent(ref)}/images/upload`,
    form,
  )
}

export const deleteImage = (ref: string, imageId: string) =>
  request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(ref)}/images/${encodeURIComponent(imageId)}`,
    { method: 'DELETE' },
  )


// ---------------------------------------------------------------- boq 清单对齐

function boqNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

/** 可空数值字段：缺失、null 或非有限数一律视为 null（旧后端不带该字段）。 */
function nullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function normalizeBoqSummary(value: unknown): BoqSummary {
  const source = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return {
    boq_rows: boqNumber(source.boq_rows),
    matched: boqNumber(source.matched),
    qty_diff: boqNumber(source.qty_diff),
    unit_diff: boqNumber(source.unit_diff),
    spec_diff: boqNumber(source.spec_diff),
    missing_in_ledger: boqNumber(source.missing_in_ledger),
    missing_in_boq: boqNumber(source.missing_in_boq),
    ignored: boqNumber(source.ignored),
    total_diffs: boqNumber(source.total_diffs),
    match_rate: boqNumber(source.match_rate),
    priced: boqNumber(source.priced),
    unpriced: boqNumber(source.unpriced),
    quantity_missing: boqNumber(source.quantity_missing),
    total_amount: boqNumber(source.total_amount),
    // 旧后端没有调价策略，字段缺失即视为「无调整后合计」，界面照常显示原合计。
    adjusted_total:
      typeof source.adjusted_total === 'number' && Number.isFinite(source.adjusted_total)
        ? source.adjusted_total
        : null,
    // 税额与含税合计仅在税率为正时由后端返回；缺失时保持 null，避免误显 0。
    tax_amount: nullableNumber(source.tax_amount),
    total_with_tax: nullableNumber(source.total_with_tax),
    // 人民币含税合计由新后端按汇率折算；旧后端缺失时界面用本地兜底。
    cny_total_with_tax: nullableNumber(source.cny_total_with_tax),
  }
}

function normalizeBoqPrice(value: unknown): BoqPrice | null {
  if (!value || typeof value !== 'object') return null
  const source = value as Record<string, unknown>
  return {
    unit_price: typeof source.unit_price === 'number' ? source.unit_price : null,
    amount: typeof source.amount === 'number' ? source.amount : null,
  }
}

function normalizeBoqSnapshot(value: unknown): BoqSnapshot | null {
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

function normalizeBoqRow(value: unknown): BoqRow | null {
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

function boqPackageQuery(packageId?: string | null): string {
  const value = (packageId || '').trim()
  return value ? `?package_id=${encodeURIComponent(value)}` : ''
}

/** 统一归一化一份清单响应（GET / apply / adjust 都返回同一形状）。 */
function normalizeBoqPayload(value: unknown): BoqPayload {
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

export async function getBoq(
  ref: string,
  packageId?: string | null,
): Promise<BoqPayload> {
  const data = await request<Partial<BoqPayload>>(
    `/projects/${encodeURIComponent(ref)}/boq${boqPackageQuery(packageId)}`,
  )
  return normalizeBoqPayload(data)
}

/**
 * 上传工程量清单工作簿。`sheet` 为可选工作表名；后端解析失败返回 400 中文错误。
 */
export function uploadBoq(
  ref: string,
  file: File,
  sheet?: string,
  onProgress?: (percent: number) => void,
  packageId?: string | null,
): Promise<BoqPayload> {
  const form = new FormData()
  form.append('file', file)
  const sheetName = (sheet || '').trim()
  if (sheetName) form.append('sheet', sheetName)
  const packageValue = (packageId || '').trim()
  if (packageValue) form.append('package_id', packageValue)
  return uploadForm<Partial<BoqPayload>>(
    `/projects/${encodeURIComponent(ref)}/boq`,
    form,
    { onProgress },
  ).then(normalizeBoqPayload)
}

export const applyBoq = (
  ref: string,
  input: { boq_row: number; action: BoqAction; package_id?: string | null },
): Promise<BoqPayload> =>
  request<BoqPayload>(`/projects/${encodeURIComponent(ref)}/boq/apply`, {
    method: 'POST',
    body: JSON.stringify(input),
  })

export const setBoqPrice = (
  ref: string,
  input: { boq_row: number; unit_price: number | null; package_id?: string | null },
): Promise<BoqPayload> =>
  request<BoqPayload>(`/projects/${encodeURIComponent(ref)}/boq/price`, {
    method: 'POST',
    body: JSON.stringify(input),
  })

export const deleteBoq = (ref: string, packageId?: string | null) =>
  request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(ref)}/boq${boqPackageQuery(packageId)}`,
    { method: 'DELETE' },
  )

// ---------------------------------------------------------------- 报价版本

/**
 * 归一化一个报价版本。列表接口不含 prices，详情接口才带回单价快照，
 * 因此 prices 缺失时保持 undefined 而不是空对象（便于区分「没有单价」与「未返回」）。
 */
export function normalizeBoqQuoteVersion(value: unknown): BoqQuoteVersion | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const source = value as Record<string, unknown>
  const id = asText(source.id).trim()
  if (!id) return null
  const rawPrices = source.prices
  let prices: Record<string, number> | undefined
  if (rawPrices && typeof rawPrices === 'object' && !Array.isArray(rawPrices)) {
    prices = {}
    for (const [key, raw] of Object.entries(rawPrices as Record<string, unknown>)) {
      if (typeof raw === 'number' && Number.isFinite(raw)) prices[key] = raw
    }
  }
  const packageId = asText(source.package_id).trim()
  return {
    id,
    name: asText(source.name),
    note: asText(source.note),
    package_id: packageId || null,
    summary: normalizeBoqSummary(source.summary),
    created_at: typeof source.created_at === 'string' ? source.created_at : null,
    price_count: boqNumber(source.price_count),
    ...(prices ? { prices } : {}),
  }
}

/** 列出项目（或指定包件）的报价版本，最新的在前。 */
export async function listBoqVersions(
  ref: string,
  packageId?: string | null,
): Promise<BoqQuoteVersion[]> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions${boqPackageQuery(packageId)}`,
  )
  const list = Array.isArray(data) ? data : asArray<unknown>(data, 'versions')
  return list
    .map(normalizeBoqQuoteVersion)
    .filter((item): item is BoqQuoteVersion => item !== null)
}

/** 把当前清单单价与汇总保存为一个版本。 */
export async function createBoqVersion(
  ref: string,
  input: { name: string; note?: string; package_id?: string | null },
): Promise<BoqQuoteVersion> {
  const packageId = (input.package_id || '').trim()
  const note = (input.note || '').trim()
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions`,
    {
      method: 'POST',
      body: JSON.stringify({
        name: input.name.trim(),
        ...(note ? { note } : {}),
        ...(packageId ? { package_id: packageId } : {}),
      }),
    },
  )
  return (
    normalizeBoqQuoteVersion(data) ?? {
      id: '',
      name: input.name.trim(),
      note,
      package_id: packageId || null,
      summary: normalizeBoqSummary(null),
      created_at: null,
      price_count: 0,
    }
  )
}

/** 读取版本详情（含单价快照）。 */
export async function getBoqVersion(
  ref: string,
  versionId: string,
): Promise<BoqQuoteVersion> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions/${encodeURIComponent(versionId)}`,
  )
  const version = normalizeBoqQuoteVersion(data)
  if (!version) throw new ApiError(0, null, '报价版本返回了无法识别的数据。')
  return version
}

/** 把版本单价写回当前清单，返回最新的 BOQ payload。 */
export async function restoreBoqVersion(
  ref: string,
  versionId: string,
): Promise<BoqPayload> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions/${encodeURIComponent(versionId)}/restore`,
    { method: 'POST' },
  )
  return normalizeBoqPayload(data)
}

export async function deleteBoqVersion(
  ref: string,
  versionId: string,
): Promise<BoqVersionDeleteResult> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions/${encodeURIComponent(versionId)}`,
    { method: 'DELETE' },
  )
  const source = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  return {
    deleted: asBool(source.deleted),
    id: asText(source.id) || versionId,
  }
}

function normalizeBoqVersionDiffEntry(value: unknown): BoqVersionDiffEntry {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return {
    key: asText(raw.key),
    name: asText(raw.name),
    unit_price: asNumber(raw.unit_price, 0),
    amount: raw.amount === null || raw.amount === undefined ? null : asNumber(raw.amount, 0),
  }
}

function normalizeBoqVersionDiffChanged(value: unknown): BoqVersionDiffChanged {
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

export async function getBoqVersionDiff(
  ref: string,
  versionId: string,
  other?: string | null,
): Promise<BoqVersionDiff> {
  const query = other && other !== 'current' ? `?other=${encodeURIComponent(other)}` : ''
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions/${encodeURIComponent(versionId)}/diff${query}`,
  )
  const raw = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  return {
    version_id: asText(raw.version_id),
    other_id: asText(raw.other_id),
    added: asArray<unknown>(raw.added, 'added').map(normalizeBoqVersionDiffEntry),
    removed: asArray<unknown>(raw.removed, 'removed').map(normalizeBoqVersionDiffEntry),
    changed: asArray<unknown>(raw.changed, 'changed').map(normalizeBoqVersionDiffChanged),
    summary_a: normalizeBoqSummary(raw.summary_a),
    summary_b: normalizeBoqSummary(raw.summary_b),
    count_a: asNumber(raw.count_a, 0),
    count_b: asNumber(raw.count_b, 0),
  }
}

export async function exportBoq(
  ref: string,
  packageId?: string | null,
): Promise<{ blob: Blob; filename: string }> {
  let response: Response
  try {
    response = await fetch(
      `/projects/${encodeURIComponent(ref)}/boq/export${boqPackageQuery(packageId)}`,
      {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: '*/*' },
      },
    )
  } catch {
    throw new ApiError(
      0,
      null,
      '无法连接本地服务。请确认 BidCraft 后端已启动，且当前页面由它托管。',
    )
  }
  if (!response.ok) {
    if (response.status === 401) notifyUnauthorized(`/projects/${ref}/boq/export`)
    throw await readError(response)
  }
  const blob = await response.blob()
  const filename =
    filenameFromDisposition(response.headers.get('Content-Disposition')) || '清单响应表.xlsx'
  return { blob, filename }
}

// ---------------------------------------------------------------- 清单定额套用

function normalizeQuotaProposal(value: unknown): QuotaProposal | null {
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
export async function quotaFill(
  ref: string,
  input: {
    package_id?: string | null
    only_missing?: boolean
    dry_run?: boolean
  } = {},
): Promise<QuotaFillResult> {
  const packageId = (input.package_id || '').trim()
  const data = await request<{ proposals?: unknown; applied?: unknown }>(
    `/projects/${encodeURIComponent(ref)}/boq/quota-fill`,
    jsonBody({
      ...(packageId ? { package_id: packageId } : {}),
      only_missing: input.only_missing !== false,
      dry_run: input.dry_run !== false,
    }),
  )
  const proposals = Array.isArray(data?.proposals)
    ? (data.proposals.map(normalizeQuotaProposal).filter(Boolean) as QuotaProposal[])
    : []
  return {
    proposals,
    applied: typeof data?.applied === 'number' ? data.applied : 0,
  }
}

// ---------------------------------------------------------------- 调价策略

export const DEFAULT_PRICE_POLICY: PricePolicy = {
  enabled: false,
  factor: 1,
  round_digits: 2,
  tax_rate: 0,
  currency: 'CNY',
  exchange_rate: 1,
}

const PRICE_CURRENCIES: readonly PriceCurrency[] = ['CNY', 'USD', 'EUR', 'HKD']
const EXCHANGE_RATE_MIN = 0.0001
const EXCHANGE_RATE_MAX = 10000

function normalizeCurrency(value: unknown): PriceCurrency {
  const text = typeof value === 'string' ? value.trim().toUpperCase() : ''
  return (PRICE_CURRENCIES as readonly string[]).includes(text)
    ? (text as PriceCurrency)
    : 'CNY'
}

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** 汇率 clamp：0.0001–10000，缺省 1；币种为 CNY 时恒为 1。 */
function clampExchangeRate(value: unknown, currency: PriceCurrency): number {
  if (currency === 'CNY') return 1
  return clampNumber(asNumber(value, 1), EXCHANGE_RATE_MIN, EXCHANGE_RATE_MAX)
}

/** 按小数位四舍五入；与后端 `round(value, digits)` 保持一致的预览口径。 */
export function roundTo(value: number, digits: number): number {
  const places = Math.round(clampNumber(digits, 0, 4))
  const factor = 10 ** places
  return Math.round((value + Number.EPSILON) * factor) / factor
}

/**
 * 人民币含税合计：优先使用后端折算的 `cny_total_with_tax`；
 * 旧后端缺失该字段时，按含税合计 × 汇率在本地兜底（与后端同一四舍五入口径）。
 */
export function cnyTotalWithTax(
  cnyTotal: number | null | undefined,
  totalWithTax: number | null | undefined,
  exchangeRate: number | null | undefined,
): number | null {
  if (typeof cnyTotal === 'number' && Number.isFinite(cnyTotal)) return cnyTotal
  if (typeof totalWithTax !== 'number' || !Number.isFinite(totalWithTax)) return null
  const rate =
    typeof exchangeRate === 'number' && Number.isFinite(exchangeRate)
      ? clampNumber(exchangeRate, EXCHANGE_RATE_MIN, EXCHANGE_RATE_MAX)
      : 1
  return roundTo(totalWithTax * rate, 2)
}

/**
 * 归一化调价策略：缺省取默认值并按契约 clamp，
 * 保证界面上的数字始终落在后端接受的区间内。
 */
export function normalizePricePolicy(value: unknown): PricePolicy {
  const outer = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const nested = outer.price_policy
  const source =
    nested && typeof nested === 'object' && !Array.isArray(nested)
      ? (nested as Record<string, unknown>)
      : outer
  const factor = Math.round(clampNumber(asNumber(source.factor, 1), 0.1, 10) * 100) / 100
  const currency = normalizeCurrency(source.currency)
  return {
    enabled: asLooseBool(source.enabled, false),
    factor,
    round_digits: Math.round(clampNumber(asNumber(source.round_digits, 2), 0, 4)),
    // 税率按后端 0–0.5 的区间 clamp；非法值回退到 0（免税）。
    tax_rate: Math.round(clampNumber(asNumber(source.tax_rate, 0), 0, 0.5) * 10000) / 10000,
    currency,
    // 仅非人民币币种保留汇率输入；CNY 强制为 1，避免保存出无意义的折算。
    exchange_rate: clampExchangeRate(source.exchange_rate, currency),
  }
}

export async function getPricePolicy(ref: string): Promise<PricePolicy> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/price-policy`,
  )
  return normalizePricePolicy(data)
}

export async function updatePricePolicy(
  ref: string,
  policy: PricePolicy,
): Promise<PricePolicy> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/price-policy`,
    { method: 'PUT', body: JSON.stringify(normalizePricePolicy(policy)) },
  )
  return normalizePricePolicy(data)
}

/** 把该包件所有已录入单价乘以系数并写回；无已录入单价时后端返回 409。 */
export async function adjustBoq(
  ref: string,
  packageId?: string | null,
): Promise<BoqPayload> {
  const value = (packageId || '').trim()
  const data = await request<unknown>(`/projects/${encodeURIComponent(ref)}/boq/adjust`, {
    method: 'POST',
    body: JSON.stringify(value ? { package_id: value } : {}),
  })
  return normalizeBoqPayload(data)
}

// ---------------------------------------------------------------- admin: 企业定额库

function normalizeQuotaItem(value: unknown): QuotaItem {
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

function quotaItems(data: unknown): unknown[] {
  const items = asArray<unknown>(data, 'items')
  if (items.length) return items
  return asArray<unknown>(data, 'quotas')
}

export async function getQuotas(search = '', limit = 100): Promise<QuotaItem[]> {
  const params = new URLSearchParams()
  const query = search.trim()
  if (query) params.set('search', query)
  params.set('limit', String(limit))
  const data = await request<unknown>(`/admin/quotas?${params.toString()}`)
  return quotaItems(data)
    .map(normalizeQuotaItem)
    .filter((item) => item.id && item.name)
}

export async function createQuota(input: QuotaInput): Promise<QuotaItem> {
  const data = await request<unknown>('/admin/quotas', jsonBody(input))
  return normalizeQuotaItem(data)
}

export async function updateQuota(
  id: string,
  patch: Partial<QuotaInput>,
): Promise<QuotaItem> {
  const data = await request<unknown>(`/admin/quotas/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
  return normalizeQuotaItem(data)
}

export const deleteQuota = (id: string) =>
  request<{ deleted?: boolean; id?: string }>(
    `/admin/quotas/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  )

/** 用清单名称/规格试匹配定额库，返回按相似度降序的前 5 条。 */
export async function matchQuotas(name: string, spec = ''): Promise<QuotaMatch[]> {
  const params = new URLSearchParams()
  params.set('name', name.trim())
  if (spec.trim()) params.set('spec', spec.trim())
  const data = await request<unknown>(`/admin/quotas/match?${params.toString()}`)
  const items = asArray<unknown>(data, 'matches')
  const list = items.length ? items : quotaItems(data)
  return list
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const source = item as Record<string, unknown>
      const match: QuotaMatch = {
        id: asText(source.id),
        name: asText(source.name),
        spec: asText(source.spec),
        unit: asText(source.unit),
        unit_price: asNumber(source.unit_price, 0),
        score: asNumber(source.score, 0),
      }
      return match.id ? match : null
    })
    .filter((item): item is QuotaMatch => item !== null)
}

// ---------------------------------------------------------------- admin: 企业术语库

const TERM_KINDS = ['replace', 'forbidden'] as const
const TERM_SOURCE_MAX = 120
const TERM_TARGET_MAX = 120
const TERM_NOTE_MAX = 200

/** 后端只认 replace / forbidden；旧数据或脏值统一回退到 replace。 */
function normalizeTermKind(value: unknown): 'replace' | 'forbidden' {
  return value === 'forbidden' ? 'forbidden' : 'replace'
}

/**
 * 归一化一条术语。列表接口返回的是平铺数组，字段可能来自旧版本，
 * 因此这里补齐默认值并按后端列宽截断，避免超长文本被静默拒绝。
 */
export function normalizeTerm(value: unknown): TermItem {
  const source = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return {
    id: asText(source.id),
    kind: normalizeTermKind(source.kind),
    source: asText(source.source).slice(0, TERM_SOURCE_MAX),
    target: asText(source.target).slice(0, TERM_TARGET_MAX),
    note: asText(source.note).slice(0, TERM_NOTE_MAX),
    enabled: asLooseBool(source.enabled, true),
    created_at: typeof source.created_at === 'string' ? source.created_at : null,
    updated_at: typeof source.updated_at === 'string' ? source.updated_at : null,
  }
}

function termItems(data: unknown): unknown[] {
  const items = asArray<unknown>(data, 'items')
  if (items.length) return items
  return asArray<unknown>(data, 'terms')
}

export interface TermQuery {
  search?: string
  kind?: string
  limit?: number
  offset?: number
}

export async function getTerms(query: TermQuery = {}): Promise<TermItem[]> {
  const params = new URLSearchParams()
  const search = (query.search || '').trim()
  if (search) params.set('search', search)
  const kind = (query.kind || '').trim()
  if (TERM_KINDS.includes(kind as (typeof TERM_KINDS)[number])) params.set('kind', kind)
  params.set('limit', String(query.limit ?? 200))
  if (query.offset !== undefined && query.offset > 0) {
    params.set('offset', String(Math.round(query.offset)))
  }
  const data = await request<unknown>(`/admin/terms?${params.toString()}`)
  return termItems(data)
    .map(normalizeTerm)
    .filter((item) => item.id && item.source)
}

function termPayload(input: TermInput): Record<string, unknown> {
  return {
    kind: normalizeTermKind(input.kind),
    source: (input.source || '').trim().slice(0, TERM_SOURCE_MAX),
    target: (input.target || '').trim().slice(0, TERM_TARGET_MAX),
    note: (input.note || '').trim().slice(0, TERM_NOTE_MAX),
    enabled: input.enabled ?? true,
  }
}

export async function createTerm(input: TermInput): Promise<TermItem> {
  const data = await request<unknown>('/admin/terms', jsonBody(termPayload(input)))
  return normalizeTerm(data)
}

/** PATCH 只提交调用方给出的字段，未传的字段保持后端原值。 */
export async function updateTerm(
  id: string,
  patch: Partial<TermInput>,
): Promise<TermItem> {
  const body: Record<string, unknown> = {}
  if (patch.kind !== undefined) body.kind = normalizeTermKind(patch.kind)
  if (patch.source !== undefined) {
    body.source = patch.source.trim().slice(0, TERM_SOURCE_MAX)
  }
  if (patch.target !== undefined) {
    body.target = patch.target.trim().slice(0, TERM_TARGET_MAX)
  }
  if (patch.note !== undefined) body.note = patch.note.trim().slice(0, TERM_NOTE_MAX)
  if (patch.enabled !== undefined) body.enabled = patch.enabled
  const data = await request<unknown>(`/admin/terms/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
  return normalizeTerm(data)
}

export const deleteTerm = (id: string) =>
  request<{ deleted?: boolean; id?: string }>(
    `/admin/terms/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  )

// ---------------------------------------------------------------- admin: 插件市场（MCP 客户端）

/** 参数/环境变量可能是数组或 JSON 文本，列表里缺字段时一律降级为空。 */
function normalizePluginArgs(value: unknown): string[] {
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

function normalizePluginEnv(value: unknown): Record<string, string> {
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

function normalizePluginTools(value: unknown): string[] {
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

function normalizePlugin(value: unknown): Plugin {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const command = asText(raw.command)
  const url = asText(raw.url)
  const declared = asText(raw.transport).toLowerCase()
  // 旧后端或手写数据可能漏掉 transport：有 url 无 command 时按 HTTP 处理更安全。
  const transport: Plugin['transport'] =
    declared === 'http' || declared === 'stdio'
      ? declared
      : url && !command
        ? 'http'
        : 'stdio'
  return {
    id: asText(raw.id),
    name: asText(raw.name),
    transport,
    command,
    args: normalizePluginArgs(raw.args),
    env: normalizePluginEnv(raw.env),
    url,
    enabled: raw.enabled === undefined || raw.enabled === null ? true : asBool(raw.enabled),
    tool_allowlist: asStringList(raw.tool_allowlist),
    last_test_at: typeof raw.last_test_at === 'string' ? raw.last_test_at : null,
    last_test_ok:
      typeof raw.last_test_ok === 'boolean'
        ? raw.last_test_ok
        : raw.last_test_ok === undefined || raw.last_test_ok === null
          ? null
          : asBool(raw.last_test_ok),
    last_test_message: asText(raw.last_test_message),
    fail_streak: asNumber(raw.fail_streak, 0),
    auto_disabled: asBool(raw.auto_disabled),
    tools: normalizePluginTools(raw.tools),
    created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
  }
}

function pluginItems(data: unknown): unknown[] {
  const items = asArray<unknown>(data, 'plugins')
  if (items.length) return items
  return asArray<unknown>(data, 'items')
}

export async function getPlugins(): Promise<Plugin[]> {
  let data: unknown
  try {
    data = await request<unknown>('/admin/plugins')
  } catch (err) {
    // 旧后端的 SPA 兜底会把未知路径返回 index.html（200 + HTML），
    // JSON 解析失败时按「接口未就绪」处理，而不是把英文解析错误抛给界面。
    if (err instanceof ApiError) throw err
    throw new ApiError(404, '插件接口未就绪', '当前后端没有插件接口，请升级后重试。')
  }
  return pluginItems(data)
    .map(normalizePlugin)
    .filter((item) => item.id && item.name)
}

export async function createPlugin(input: PluginInput): Promise<Plugin> {
  const data = await request<unknown>('/admin/plugins', jsonBody(input))
  return normalizePlugin(data)
}

export async function updatePlugin(
  id: string,
  patch: Partial<PluginInput>,
): Promise<Plugin> {
  const data = await request<unknown>(`/admin/plugins/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
  return normalizePlugin(data)
}

export const deletePlugin = (id: string) =>
  request<{ deleted?: boolean; id?: string }>(`/admin/plugins/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })

/** 真实握手一次：失败也返回 200（ok=false + 中文 message），不抛异常。 */
export async function testPlugin(id: string): Promise<PluginTestResult> {
  const startedAt = typeof performance !== 'undefined' ? performance.now() : Date.now()
  const data = await request<unknown>(`/admin/plugins/${encodeURIComponent(id)}/test`, {
    method: 'POST',
  })
  const elapsed = (typeof performance !== 'undefined' ? performance.now() : Date.now()) - startedAt
  const raw = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  const latency = raw.latency_ms ?? raw.elapsed_ms ?? raw.latency
  return {
    ok: raw.ok === undefined || raw.ok === null ? false : asBool(raw.ok),
    message: asText(raw.message) || asText(raw.detail),
    tools: normalizePluginTools(raw.tools),
    // 契约未强制返回耗时：后端没有时用一次往返时间兜底，界面始终能给出反馈。
    latency_ms:
      typeof latency === 'number' && Number.isFinite(latency)
        ? Math.max(0, latency)
        : Math.max(0, Math.round(elapsed)),
  }
}

function normalizePluginCatalogEntry(value: unknown): PluginCatalogEntry | null {
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

export async function getPluginCatalog(): Promise<PluginCatalogEntry[]> {
  let data: unknown
  try {
    data = await request<unknown>('/admin/plugins/catalog')
  } catch (err) {
    if (err instanceof ApiError) throw err
    throw new ApiError(404, '预设目录未就绪', '当前后端没有插件预设目录，请升级后重试。')
  }
  const list =
    ['catalog', 'presets', 'items']
      .map((key) => asArray<unknown>(data, key))
      .find((items) => items.length > 0) ?? []
  return list
    .map(normalizePluginCatalogEntry)
    .filter((item): item is PluginCatalogEntry => item !== null)
}

// ---------------------------------------------------------------- admin: wechat

function normalizeWechatConfig(value: unknown): WechatConfig {
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

export async function getWechatConfig(): Promise<WechatConfig> {
  return normalizeWechatConfig(await request<unknown>('/admin/wechat'))
}

export async function updateWechatConfig(input: WechatConfigInput): Promise<WechatConfig> {
  return normalizeWechatConfig(await request<unknown>('/admin/wechat', jsonBody(input)))
}

export async function testWechatConfig(): Promise<{ ok: boolean; message: string }> {
  const raw = await request<unknown>('/admin/wechat/test', { method: 'POST' })
  const data = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return { ok: asBool(data.ok), message: asText(data.message) }
}

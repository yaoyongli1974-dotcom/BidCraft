import { ApiError, asArray, jsonBody, request } from '../lib/http'
import type { KbDocMetaInput, KbDocMetaItem, KbDocMetaResponse, KbDocPolicy, KbDocSource, KbDocumentItem, KbSearchPassage, KbVectorResult, KnowledgeAsset, KnowledgeAssetChunk, KnowledgeAssetDecision, KnowledgeAssetDuplicateDecision, KnowledgeAssetDuplicateGroup, KnowledgeAssetDuplicateMember, KnowledgeBase, KnowledgeBaseInput, TermInput, TermItem, TestResult } from '../lib/types'
import { TERM_KINDS, TERM_NOTE_MAX, TERM_SOURCE_MAX, TERM_TARGET_MAX, asBool, asLooseBool, asNumber, asText, isoOrNull, kbDocsPath, normalizeKbDocFields, normalizeKbDocPolicyEntry, normalizeKbDocSource, normalizeKbVector, normalizeTermKind, termItems, termPayload } from './shared'

// ---------------------------------------------------------------- knowledge domain
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

/** 检索预览：返回带 document_id / page / heading 的片段。 */
export async function previewKnowledgeBase(
  id: string,
  query: string,
  limit = 5,
): Promise<KbSearchPassage[]> {
  return previewKbRetrieval(id, query, limit)
}

// ---------------------------------------------------------------- 文档实体与检索预览

/** 文档实体列表行：后端原字段全部保留，另补展示用的 filename / chunk_count。 */
export interface KbDocumentRow extends KbDocumentItem {
  filename: string
  chunk_count: number
  [key: string]: unknown
}

function nullablePage(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const page = asNumber(value, Number.NaN)
  return Number.isFinite(page) ? Math.max(0, Math.round(page)) : null
}

/**
 * 归一化 `GET /admin/kbs/{id}/documents` 的一条记录。
 * 状态统一成小写（缺省 ready），数值/数组字段容错；未知字段原样保留，便于溯源核对。
 */
export function normalizeKbDocument(value: unknown): KbDocumentRow | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const id = asText(raw.id).trim()
  const source = asText(raw.source).trim()
  if (!id && !source) return null
  return {
    ...raw,
    id: id || source,
    kb_id: asText(raw.kb_id),
    source,
    filename: asText(raw.filename).trim() || source || id,
    sha256: asText(raw.sha256),
    size: Math.max(0, Math.round(asNumber(raw.size, 0))),
    mime: asText(raw.mime),
    pages: Math.max(0, Math.round(asNumber(raw.pages, 0))),
    status: asText(raw.status, 'ready').trim().toLowerCase() || 'ready',
    version: Math.max(1, Math.round(asNumber(raw.version, 1))),
    kind: asText(raw.kind, 'other').trim() || 'other',
    tags: Array.isArray(raw.tags)
      ? raw.tags.filter((tag): tag is string => typeof tag === 'string')
      : [],
    valid_to: typeof raw.valid_to === 'string' && raw.valid_to ? raw.valid_to : null,
    // 后端不同版本可能叫 chunk_count / chunks / chunkCount。
    chunk_count: Math.max(
      0,
      Math.round(asNumber(raw.chunk_count ?? raw.chunks ?? raw.chunkCount, 0)),
    ),
    error: asText(raw.error),
    created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
  }
}

/** 归一化 `GET /admin/kbs/{id}/preview` 的片段；document/page/heading 缺失时留空。 */
export function normalizeKbPassage(value: unknown): KbSearchPassage | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const source = asText(raw.source)
  const text = asText(raw.text)
  if (!source && !text) return null
  return {
    ...raw,
    kb_id: asText(raw.kb_id),
    source,
    text,
    score: asNumber(raw.score, 0),
    document_id:
      typeof raw.document_id === 'string' && raw.document_id ? raw.document_id : null,
    page: nullablePage(raw.page),
    heading: asText(raw.heading),
  }
}

export async function listKnowledgeDocuments(id: string): Promise<KbDocumentItem[]> {
  return listKbDocuments(id)
}

/** 文档实体清单（后端 batch6 的 /documents 端点）。 */
export async function listKbDocuments(kbId: string): Promise<KbDocumentRow[]> {
  const data = await request<unknown>(
    `/admin/kbs/${encodeURIComponent(kbId)}/documents`,
  )
  if (data && typeof data === 'object' && !Array.isArray(data) && !('items' in data)) {
    throw new ApiError(500, null, '服务端返回的文档清单无法识别，请刷新后重试。')
  }
  return asArray<unknown>(data, 'items')
    .map(normalizeKbDocument)
    .filter((item): item is KbDocumentRow => item !== null)
}

/** 检索预览（后端 batch6 的 /preview 端点），携带 q / limit 查询参数。 */
export async function previewKbRetrieval(
  kbId: string,
  query: string,
  limit = 5,
): Promise<KbSearchPassage[]> {
  const q = query.trim()
  if (!q) throw new ApiError(400, null, '检索内容不能为空')
  const bounded = Math.max(1, Math.min(20, Math.round(limit)))
  const params = new URLSearchParams({ q, limit: String(bounded) })
  const data = await request<unknown>(
    `/admin/kbs/${encodeURIComponent(kbId)}/preview?${params.toString()}`,
  )
  if (data && typeof data === 'object' && !Array.isArray(data) && !('items' in data)) {
    throw new ApiError(500, null, '服务端返回的检索预览无法识别，请刷新后重试。')
  }
  return asArray<unknown>(data, 'items')
    .map(normalizeKbPassage)
    .filter((item): item is KbSearchPassage => item !== null)
}

export const DEFAULT_KB_DOC_POLICY: KbDocPolicy = {
  qualification: {
    label: '资质证书',
    required: ['cert_no', 'valid_to'],
    hint: '证书编号与有效期',
  },
  performance: {
    label: '业绩合同',
    required: ['contract_no', 'owner', 'amount', 'acceptance'],
    hint: '合同编号、业主、金额、验收结论',
  },
  personnel: {
    label: '人员证书',
    required: ['cert_no', 'social_months', 'locked'],
    hint: '证书号、社保月份、是否在建锁定',
  },
  standard: {
    label: '标准规范',
    required: ['source_url'],
    hint: '官方出处',
  },
  other: {
    label: '其他素材',
    required: ['source_url'],
    hint: '官方出处或来源链接',
  },
}

export function normalizeKbDocPolicy(value: unknown): KbDocPolicy {
  const raw =
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {}
  const policy: KbDocPolicy = {}
  Object.entries(raw).forEach(([rawKind, entry]) => {
    const kind = rawKind.trim()
    if (!kind) return
    policy[kind] = normalizeKbDocPolicyEntry(entry, kind)
  })
  if (Object.keys(policy).length === 0) {
    return Object.fromEntries(
      Object.entries(DEFAULT_KB_DOC_POLICY).map(([kind, entry]) => [kind, { ...entry }]),
    )
  }
  if (!policy.other) policy.other = { ...DEFAULT_KB_DOC_POLICY.other }
  return policy
}

export function normalizeKbDocItem(
  value: unknown,
  policy: KbDocPolicy = DEFAULT_KB_DOC_POLICY,
): KbDocMetaItem | null {
  const payload = value
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null
  const raw = payload as Record<string, unknown>
  const source = asText(raw.source).trim()
  if (!source) return null
  const rawKind = asText(raw.kind).trim().toLowerCase() || 'other'
  const kind = policy[rawKind] ? rawKind : 'other'
  const reason = asText(raw.reason)
  const ok =
    raw.ok === undefined || raw.ok === null ? reason === '' : asBool(raw.ok)
  return {
    source,
    kind,
    fields: normalizeKbDocFields(raw),
    ok,
    reason,
  }
}

export function normalizeKbDocs(value: unknown): KbDocMetaResponse {
  const raw =
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {}
  const policy = normalizeKbDocPolicy(raw.policy)
  const items = asArray<unknown>(raw.items, 'items')
    .map((item) => normalizeKbDocItem(item, policy))
    .filter((item): item is KbDocMetaItem => item !== null)

  const rawSources = asArray<unknown>(raw.sources, 'sources')
    .map(normalizeKbDocSource)
    .filter((item): item is KbDocSource => item !== null)

  let sources = rawSources
  if (sources.length === 0 && items.length > 0) {
    sources = items.map((item) => ({ source: item.source, chunks: 0 }))
  }
  const deduped = new Map<string, KbDocSource>()
  sources.forEach((entry) => {
    const existing = deduped.get(entry.source)
    if (!existing || entry.chunks > existing.chunks) deduped.set(entry.source, entry)
  })

  return { policy, items, sources: Array.from(deduped.values()) }
}

export async function getKbDocs(id: string): Promise<KbDocMetaResponse> {
  const data = await request<unknown>(kbDocsPath(id))
  return normalizeKbDocs(data)
}

/** 新增 / 覆盖登记一个来源的素材元数据；返回归一化后的条目。 */

export async function updateKbDoc(
  id: string,
  input: KbDocMetaInput,
): Promise<KbDocMetaItem> {
  const data = await request<unknown>(kbDocsPath(id), {
    method: 'PUT',
    body: JSON.stringify(input),
  })
  const raw =
    data && typeof data === 'object' && !Array.isArray(data)
      ? (data as Record<string, unknown>)
      : {}
  // 允许服务端自定义素材类型：把本次提交的类型补进策略，避免回退为 other。
  const policy: KbDocPolicy = {
    ...DEFAULT_KB_DOC_POLICY,
    [input.kind]: DEFAULT_KB_DOC_POLICY[input.kind] || {
      label: input.kind,
      required: [],
      hint: '',
    },
  }
  const item = normalizeKbDocItem(
    {
      ...raw,
      source: input.source,
      kind: raw.kind ?? input.kind,
      fields: raw.fields ?? input.fields,
    },
    policy,
  )
  if (!item) {
    throw new ApiError(500, null, '服务端返回的素材元数据无法识别，请刷新后重试。')
  }
  return item
}

/** 删除一个来源的登记（按 source 查询参数）。 */

export async function deleteKbDoc(
  id: string,
  source: string,
): Promise<{ deleted: boolean; source: string }> {
  const data = await request<unknown>(
    `${kbDocsPath(id)}?source=${encodeURIComponent(source)}`,
    { method: 'DELETE' },
  )
  const raw = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  return { deleted: asBool(raw.deleted), source: asText(raw.source) || source }
}

/** Vector fields are optional: an older server simply reports zeroes. */

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

// ---------------------------------------------------------------- 知识资产（admin）

/** 可空数值：缺失/非有限值一律为 null，不把 0 当成真实页码或相似度。 */
function nullableFinite(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    return Number(value)
  }
  return null
}

/** 可空布尔：值为 undefined/null 时保持 undefined，不伪造 ok=true。 */
function optionalBool(value: unknown): boolean | undefined {
  if (typeof value === 'boolean') return value
  if (value === 'true') return true
  if (value === 'false') return false
  return undefined
}

function nullableText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

const ASSET_UNREADABLE = '服务端返回的知识资产无法识别，请刷新后重试。'
const DUPLICATE_UNREADABLE = '服务端返回的重复资产组无法识别，请刷新后重试。'

/**
 * 归一化一条资产。id 缺失时沿用服务端真实返回的 document_id 作为行标识
 * （旧后端只发 document_id）；两者都缺失才算无法识别并抛 ApiError。
 * 未知字段原样保留，便于溯源核对；缺失的 review_status 保持 null，
 * 绝不回写「草稿」冒充已知状态。
 */
export function normalizeKnowledgeAsset(value: unknown): KnowledgeAsset {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ApiError(500, null, ASSET_UNREADABLE)
  }
  const raw = value as Record<string, unknown>
  const id = asText(raw.id).trim() || asText(raw.document_id).trim()
  if (!id) throw new ApiError(500, null, ASSET_UNREADABLE)
  const fields =
    raw.fields && typeof raw.fields === 'object' && !Array.isArray(raw.fields)
      ? { ...(raw.fields as Record<string, unknown>) }
      : {}
  const chunks = Array.isArray(raw.chunks)
    ? raw.chunks
        .map(normalizeKnowledgeAssetChunk)
        .filter((chunk): chunk is KnowledgeAssetChunk => chunk !== null)
    : []
  const status =
    raw.review_status === null || raw.review_status === undefined
      ? null
      : asText(raw.review_status).trim().toLowerCase() || null
  return {
    ...raw,
    id,
    document_id: nullableText(raw.document_id),
    kb_id: nullableText(raw.kb_id),
    source: asText(raw.source),
    sha256: nullableText(raw.sha256),
    review_status: status,
    fields,
    chunks,
    source_file_id: nullableText(raw.source_file_id),
    reviewed_by: nullableText(raw.reviewed_by),
    reviewed_at: isoOrNull(raw.reviewed_at),
    published_at: isoOrNull(raw.published_at),
    rejected_reason: nullableText(raw.rejected_reason),
    ok: optionalBool(raw.ok),
    reason: asText(raw.reason),
  }
}

function normalizeKnowledgeAssetChunk(value: unknown): KnowledgeAssetChunk | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const id = asText(raw.id).trim()
  const heading = asText(raw.heading)
  if (!id && !heading) return null
  return {
    ...raw,
    id: id || undefined,
    page: nullableFinite(raw.page),
    heading,
  }
}

/** 归一化一组重复资产组；无 id 的条目直接丢弃，未知字段原样保留。 */
export function normalizeKnowledgeAssetDuplicateGroup(value: unknown): KnowledgeAssetDuplicateGroup {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ApiError(500, null, DUPLICATE_UNREADABLE)
  }
  const raw = value as Record<string, unknown>
  const id = asText(raw.id).trim()
  if (!id) throw new ApiError(500, null, DUPLICATE_UNREADABLE)
  const members = Array.isArray(raw.members)
    ? raw.members
        .map(normalizeDuplicateMember)
        .filter((member): member is KnowledgeAssetDuplicateMember => member !== null)
    : []
  return {
    ...raw,
    id,
    kb_id: nullableText(raw.kb_id),
    fingerprint: nullableText(raw.fingerprint),
    similarity: nullableFinite(raw.similarity),
    status: nullableText(raw.status),
    reason: asText(raw.reason),
    members,
  }
}

function normalizeDuplicateMember(value: unknown): KnowledgeAssetDuplicateMember | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const id = asText(raw.id).trim()
  const documentId = asText(raw.document_id).trim()
  if (!id && !documentId) return null
  return {
    ...raw,
    id: id || undefined,
    document_id: documentId || null,
    score: nullableFinite(raw.score),
    role: asText(raw.role),
    source: asText(raw.source),
  }
}

/** 列表响应必须是 `{items: [...]}`；无法识别时报错，不返回空数组冒充成功。 */
export function knowledgeAssetItems(value: unknown, label = ASSET_UNREADABLE): KnowledgeAsset[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ApiError(500, null, label)
  }
  const items = (value as Record<string, unknown>).items
  if (!Array.isArray(items)) throw new ApiError(500, null, label)
  return items.map(normalizeKnowledgeAsset)
}

export function knowledgeAssetDuplicateItems(
  value: unknown,
  label = DUPLICATE_UNREADABLE,
): KnowledgeAssetDuplicateGroup[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ApiError(500, null, label)
  }
  const items = (value as Record<string, unknown>).items
  if (!Array.isArray(items)) throw new ApiError(500, null, label)
  return items.map(normalizeKnowledgeAssetDuplicateGroup)
}

export interface KnowledgeAssetReviewInput {
  decision: KnowledgeAssetDecision
  note?: string
}

export interface KnowledgeAssetDuplicateResolveInput {
  decision: KnowledgeAssetDuplicateDecision
  note?: string
}

/** 出站请求体只含后端接受的 decision / note，note 统一收敛为字符串。 */
export const knowledgeAssetReviewPayload = (input: KnowledgeAssetReviewInput) => ({
  decision: input.decision,
  note: asText(input.note).trim(),
})

export const knowledgeAssetDuplicateResolvePayload = (
  input: KnowledgeAssetDuplicateResolveInput,
) => ({
  decision: input.decision,
  note: asText(input.note).trim(),
})

export async function listKnowledgeAssets(kbId: string): Promise<KnowledgeAsset[]> {
  const data = await request<unknown>(`/admin/kbs/${encodeURIComponent(kbId)}/assets`)
  return knowledgeAssetItems(data)
}

export async function extractKnowledgeAsset(
  kbId: string,
  documentId: string,
): Promise<KnowledgeAsset> {
  const data = await request<unknown>(
    `/admin/kbs/${encodeURIComponent(kbId)}/assets/${encodeURIComponent(documentId)}/extract`,
    { method: 'POST' },
  )
  return normalizeKnowledgeAsset(data)
}

export async function reviewKnowledgeAsset(
  kbId: string,
  documentId: string,
  input: KnowledgeAssetReviewInput,
): Promise<KnowledgeAsset> {
  const data = await request<unknown>(
    `/admin/kbs/${encodeURIComponent(kbId)}/assets/${encodeURIComponent(documentId)}/review`,
    jsonBody(knowledgeAssetReviewPayload(input)),
  )
  return normalizeKnowledgeAsset(data)
}

export async function listKnowledgeAssetDuplicates(
  kbId: string,
): Promise<KnowledgeAssetDuplicateGroup[]> {
  const data = await request<unknown>(`/admin/kbs/${encodeURIComponent(kbId)}/assets/duplicates`)
  return knowledgeAssetDuplicateItems(data)
}

export async function resolveKnowledgeAssetDuplicate(
  kbId: string,
  groupId: string,
  input: KnowledgeAssetDuplicateResolveInput,
): Promise<KnowledgeAssetDuplicateGroup> {
  const data = await request<unknown>(
    `/admin/kbs/${encodeURIComponent(kbId)}/assets/duplicates/${encodeURIComponent(groupId)}/resolve`,
    jsonBody(knowledgeAssetDuplicateResolvePayload(input)),
  )
  return normalizeKnowledgeAssetDuplicateGroup(data)
}

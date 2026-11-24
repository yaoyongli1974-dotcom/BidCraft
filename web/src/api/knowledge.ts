import { ApiError, asArray, jsonBody, request } from '../lib/http'
import type { KbDocMetaInput, KbDocMetaItem, KbDocMetaResponse, KbDocPolicy, KbDocSource, KbDocumentItem, KbSearchPassage, KbVectorResult, KnowledgeBase, KnowledgeBaseInput, TermInput, TermItem, TestResult } from '../lib/types'
import { TERM_KINDS, TERM_NOTE_MAX, TERM_SOURCE_MAX, TERM_TARGET_MAX, asBool, asLooseBool, asNumber, asText, kbDocsPath, normalizeKbDocFields, normalizeKbDocPolicyEntry, normalizeKbDocSource, normalizeKbVector, normalizeTermKind, termItems, termPayload } from './shared'

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

/** 文档实体清单（含页码/版本/有效期/状态）。 */
export async function listKnowledgeDocuments(id: string): Promise<KbDocumentItem[]> {
  const data = await request<{ items?: KbDocumentItem[] } | KbDocumentItem[]>(
    `/admin/kbs/${encodeURIComponent(id)}/documents`,
  )
  return asArray<KbDocumentItem>(data, 'items')
}

/** 检索预览：返回带 document_id / page / heading 的片段。 */
export async function previewKnowledgeBase(
  id: string,
  query: string,
  limit = 5,
): Promise<KbSearchPassage[]> {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  const data = await request<{ items?: KbSearchPassage[] } | KbSearchPassage[]>(
    `/admin/kbs/${encodeURIComponent(id)}/preview?${params.toString()}`,
  )
  return asArray<KbSearchPassage>(data, 'items')
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

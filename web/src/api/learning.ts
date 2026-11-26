import { request } from '../lib/http'
import type { Lesson, LessonPolicy, LessonsResponse } from '../lib/types'

/** `GET /projects/{ref}/lessons` 的查询参数；只发后端接受的 node_id / limit / include_revoked。 */
export interface LessonQuery {
  node_id?: string | null
  limit?: number
  /** 治理视图：把已撤销条目一起取回（仍过滤过期与低信任）。 */
  include_revoked?: boolean
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function asBool(value: unknown): boolean {
  return value === true
}

function asStringMap(value: unknown): Record<string, string> {
  const raw = asRecord(value)
  const result: Record<string, string> = {}
  for (const [key, item] of Object.entries(raw)) {
    if (typeof item === 'string') result[key] = item
  }
  return result
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  const seen = new Set<string>()
  const result: string[] = []
  for (const item of value) {
    if (typeof item !== 'string' || !item || seen.has(item)) continue
    seen.add(item)
    result.push(item)
  }
  return result
}

export function lessonQuery(params: LessonQuery = {}): string {
  const search = new URLSearchParams()
  const nodeId = typeof params.node_id === 'string' ? params.node_id.trim() : ''
  if (nodeId) search.set('node_id', nodeId)
  if (typeof params.limit === 'number' && Number.isFinite(params.limit) && params.limit > 0) {
    search.set('limit', String(Math.max(1, Math.round(params.limit))))
  }
  if (params.include_revoked === true) search.set('include_revoked', 'true')
  const query = search.toString()
  return query ? `?${query}` : ''
}

/** 单条建议；治理字段缺值时用空串 / null / false 兜底，绝不把建议伪装成已验证事实。 */
export function normalizeLesson(value: unknown): Lesson | null {
  const raw = asRecord(value)
  const id = asText(raw.id).trim()
  if (!id) return null
  return {
    id,
    source: asText(raw.source),
    kind: asText(raw.kind),
    text: asText(raw.text),
    node_id:
      typeof raw.node_id === 'string' && raw.node_id.trim() ? raw.node_id : null,
    created_at: typeof raw.created_at === 'string' && raw.created_at.trim() ? raw.created_at : null,
    author: asText(raw.author),
    status: asText(raw.status),
    grade: asText(raw.grade),
    trust: asText(raw.trust),
    expires_at:
      typeof raw.expires_at === 'string' && raw.expires_at.trim() ? raw.expires_at : null,
    revoked: asBool(raw.revoked),
  }
}

export function normalizeLessonPolicy(value: unknown): LessonPolicy {
  const raw = asRecord(value)
  const ttl = raw.ttl_days
  return {
    grades: asStringMap(raw.grades),
    trust_levels: asStringMap(raw.trust_levels),
    min_trust: typeof raw.min_trust === 'string' && raw.min_trust ? raw.min_trust : null,
    ttl_days: typeof ttl === 'number' && Number.isFinite(ttl) ? Math.max(0, Math.round(ttl)) : 0,
    revoked_ids: asStringList(raw.revoked_ids),
  }
}

export function normalizeLessonsResponse(value: unknown): LessonsResponse {
  const raw = asRecord(value)
  const items = Array.isArray(raw.items)
    ? (raw.items.map(normalizeLesson).filter(Boolean) as Lesson[])
    : []
  return {
    items,
    node_id: asText(raw.node_id),
    status: asText(raw.status) || '未审核建议经验',
    policy: normalizeLessonPolicy(raw.policy),
  }
}

/** 项目级建议经验（只读）。后端未上线时调用方可按空列表降级。 */
export async function getProjectLessons(
  ref: string,
  params: LessonQuery = {},
): Promise<LessonsResponse> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/lessons${lessonQuery(params)}`,
  )
  return normalizeLessonsResponse(data)
}

/**
 * owner-only：撤销一条建议经验。只写项目 meta，不改来源行；
 * 返回归一化后的治理策略（含新的 revoked_ids）。
 */
export async function revokeProjectLesson(
  ref: string,
  lessonId: string,
): Promise<{ lesson_id: string; revoked: boolean; policy: LessonPolicy }> {
  const data = await request<Record<string, unknown>>(
    `/projects/${encodeURIComponent(ref)}/lessons/${encodeURIComponent(lessonId)}`,
    { method: 'PATCH' },
  )
  return {
    lesson_id: asText(data.lesson_id),
    revoked: asBool(data.revoked),
    policy: normalizeLessonPolicy(data.policy),
  }
}

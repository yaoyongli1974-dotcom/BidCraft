import { request } from '../lib/http'
import type { Lesson, LessonsResponse } from '../lib/types'

/** `GET /projects/{ref}/lessons` 的查询参数；只发后端接受的 node_id / limit。 */
export interface LessonQuery {
  node_id?: string | null
  limit?: number
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

export function lessonQuery(params: LessonQuery = {}): string {
  const search = new URLSearchParams()
  const nodeId = typeof params.node_id === 'string' ? params.node_id.trim() : ''
  if (nodeId) search.set('node_id', nodeId)
  if (typeof params.limit === 'number' && Number.isFinite(params.limit) && params.limit > 0) {
    search.set('limit', String(Math.max(1, Math.round(params.limit))))
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

/** 单条建议经验；缺字段用空串 / null 兜底，绝不把建议伪装成已验证事实。 */
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

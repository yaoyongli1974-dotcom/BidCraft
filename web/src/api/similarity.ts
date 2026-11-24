import { asArray, jsonBody, request, requestBlob } from '../lib/http'
import type {
  SimilarityEvidence,
  SimilarityHit,
  SimilarityHitStatus,
  SimilarityReport,
  SimilarityStats,
} from '../lib/types'
import { asNumber, asText, isoOrNull } from './shared'

// ---------------------------------------------------------------- similarity domain

export interface SimilarityCheckInput {
  scope: string
  mode: string
  threshold: number
  source_project_id?: string
}

export function normalizeSimilarityStats(raw: unknown): SimilarityStats {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const byKind =
    source.by_kind && typeof source.by_kind === 'object' && !Array.isArray(source.by_kind)
      ? Object.fromEntries(
          Object.entries(source.by_kind as Record<string, unknown>).map(([key, value]) => [
            key,
            asNumber(value),
          ]),
        )
      : undefined
  // 后端 stats 只输出 hits/by_kind/modes/common_candidates/degraded/models 等
  // 原始统计（bidcraft/similarity/service.py:308-320），不输出 checked/reviewed/
  // pending/total/skipped。未知键原样透传，展示用的 checked/reviewed 由前端派生。
  return {
    ...source,
    ...(source.hits !== undefined ? { hits: asNumber(source.hits) } : {}),
    ...(byKind ? { by_kind: byKind } : {}),
  }
}

/** 统计磁贴的展示值；checked/reviewed 来自命中明细，不是后端 stats 字段。 */
export interface SimilarityTileValues {
  checked: number
  hits: number
  reviewed: number
  skipped: number | string
}

export function similarityTileValues(
  hits: readonly { review_status?: string | null }[],
  stats: SimilarityStats,
  skippedText = '',
): SimilarityTileValues {
  return {
    checked: hits.length,
    hits: typeof stats.hits === 'number' ? stats.hits : hits.length,
    reviewed: hits.filter((hit) => hit.review_status !== 'pending').length,
    skipped: stats.skipped ?? (skippedText || '—'),
  }
}

/** 后端 skipped 可能是字符串，也可能是 {mode, path, reason} 对象。 */
function skippedText(item: unknown): string {
  if (item && typeof item === 'object') {
    const obj = item as Record<string, unknown>
    const reason = asText(obj.reason)
    const mode = asText(obj.mode)
    const path = asText(obj.path)
    if (reason) return [mode || path, reason].filter(Boolean).join('：')
    return [mode, path].filter(Boolean).join('：')
  }
  return asText(item)
}

export function normalizeSimilarityReport(raw: unknown): SimilarityReport {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const skipped = Array.isArray(source.skipped)
    ? source.skipped.map(skippedText).filter(Boolean)
    : source.skipped !== undefined && source.skipped !== null
      ? asNumber(source.skipped)
      : undefined
  return {
    id: asText(source.id ?? source.report_id),
    scope: asText(source.scope),
    mode: asText(source.mode),
    threshold: asNumber(source.threshold),
    status: asText(source.status, 'unknown') || 'unknown',
    stats: normalizeSimilarityStats(source.stats),
    ...(skipped !== undefined ? { skipped } : {}),
    source_project_id: source.source_project_id ? asText(source.source_project_id) : null,
    created_at: isoOrNull(source.created_at),
    // 后端字段是 completed_at；旧前端只看 finished_at 会永远显示「未完成」。
    finished_at: isoOrNull(source.finished_at ?? source.completed_at),
  }
}

export function normalizeSimilarityEvidence(raw: unknown): SimilarityEvidence | undefined {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined
  const data = raw as Record<string, unknown>
  return {
    ...data,
    common: data.common === true || data.reasonable_duplicate === true,
    reasonable_duplicate: data.reasonable_duplicate === true || data.common === true,
    degraded: data.degraded === true,
    ...(data.vector_similarity !== undefined
      ? { vector_similarity: asNumber(data.vector_similarity) }
      : {}),
    ...(data.rerank_score !== undefined ? { rerank_score: asNumber(data.rerank_score) } : {}),
  }
}

export function normalizeSimilarityHit(raw: unknown): SimilarityHit {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const evidence = normalizeSimilarityEvidence(source.evidence)
  const rerankRaw = source.rerank_score ?? evidence?.rerank_score
  return {
    id: asText(source.id),
    kind: asText(source.kind) || 'text',
    source_a: asText(source.source_a),
    source_b: asText(source.source_b),
    loc_a: asText(source.loc_a),
    loc_b: asText(source.loc_b),
    excerpt_a: asText(source.excerpt_a),
    excerpt_b: asText(source.excerpt_b),
    similarity: asNumber(source.similarity),
    ...(rerankRaw !== undefined && rerankRaw !== null
      ? { rerank_score: asNumber(rerankRaw) }
      : {}),
    ...(evidence ? { evidence } : {}),
    review_status: (asText(source.review_status, 'pending') || 'pending') as SimilarityHitStatus,
    review_note: asText(source.review_note),
  }
}

/** 启动查重任务；服务端返回 202 与报告 id，结果需轮询报告接口。 */
export async function checkSimilarity(
  ref: string,
  input: SimilarityCheckInput,
): Promise<{ report_id: string; job_id?: string; status?: string }> {
  const data = await request<{
    report_id?: string
    id?: string
    job_id?: string
    status?: string
  }>(
    `/projects/${encodeURIComponent(ref)}/similarity/check`,
    jsonBody({
      scope: input.scope,
      mode: input.mode,
      threshold: input.threshold,
      ...(input.source_project_id ? { source_project_id: input.source_project_id } : {}),
    }),
  )
  return {
    report_id: asText(data?.report_id ?? data?.id),
    ...(data?.job_id ? { job_id: asText(data.job_id) } : {}),
    ...(data?.status ? { status: asText(data.status) } : {}),
  }
}

export async function listSimilarityReports(ref: string): Promise<SimilarityReport[]> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/similarity/reports`,
  )
  return asArray<unknown>(data, 'reports').map(normalizeSimilarityReport)
}

export async function getSimilarityReport(
  ref: string,
  reportId: string,
): Promise<SimilarityReport> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/similarity/reports/${encodeURIComponent(reportId)}`,
  )
  return normalizeSimilarityReport(data)
}

export async function getSimilarityHits(
  ref: string,
  reportId: string,
): Promise<SimilarityHit[]> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/similarity/reports/${encodeURIComponent(reportId)}/hits`,
  )
  return asArray<unknown>(data, 'hits').map(normalizeSimilarityHit)
}

export async function updateSimilarityHit(
  ref: string,
  hitId: string,
  status: SimilarityHitStatus,
  note?: string,
): Promise<SimilarityHit> {
  const trimmed = (note || '').trim()
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/similarity/hits/${encodeURIComponent(hitId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify({ status, ...(trimmed ? { note: trimmed } : {}) }),
    },
  )
  return normalizeSimilarityHit(data)
}

/** 导出 Markdown 查重报告。 */
export function exportSimilarityReport(
  ref: string,
  reportId: string,
): Promise<{ blob: Blob; filename: string }> {
  return requestBlob(
    `/projects/${encodeURIComponent(ref)}/similarity/reports/${encodeURIComponent(reportId)}/export`,
    undefined,
    '查重报告.md',
  )
}

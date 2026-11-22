import { ApiError, asArray, jsonBody, notifyUnauthorized, readError, request } from '../lib/http'
import type { ChapterCitations, ChapterDetail, GenerationReport, Job, OutlineChapter, OutlineResponse, ProjectCitations, ScoringCoverageReport } from '../lib/types'

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

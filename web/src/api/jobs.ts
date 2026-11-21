import { ApiError, asArray, jsonBody, notifyUnauthorized, readError, request } from '../lib/http'
import type { AuditPage, BatchExportFailure, BatchExportFile, BatchFormat, BatchItemResult, BatchJob, PendingApproval } from '../lib/types'
import { filenameFromDisposition } from './generation'
import { asNumber, asText, auditQuery, normalizeAuditPage, normalizeBatchItem, normalizePendingApproval } from './shared'

// ---------------------------------------------------------------- jobs domain
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

export async function getPendingApprovals(signal?: AbortSignal): Promise<PendingApproval[]> {
  let data: unknown
  try {
    data = await request<unknown>('/approvals/pending', undefined, signal)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return []
    if (error instanceof SyntaxError) return []
    throw error
  }
  const list = Array.isArray(data) ? data : asArray<unknown>(data, 'items')
  return list
    .map(normalizePendingApproval)
    .filter((item): item is PendingApproval => item !== null)
}

export const batchCheck = (ids: string[]) =>
  request<{ job_id: string }>('/projects/batch-check', jsonBody({ ids }))

export const batchExport = (ids: string[], format: BatchFormat = 'docx') =>
  request<{ job_id: string }>('/projects/batch-export', jsonBody({ ids, format }))

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

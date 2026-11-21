import { ApiError, asArray, jsonBody, request, uploadForm } from '../lib/http'
import type { Health, PackageInfo, Personnel, PersonnelInput, PreflightItem, PreflightReport, Qualification, QualificationEvidence, QualificationInput, UserDoc, UserDocUploadResult } from '../lib/types'
import { MAX_SOCIAL_MONTHS, asBool, asNullableNumber, asNumber, asText, normalizePackageInfo, normalizePreflightItem, normalizeQualificationEvidence, normalizeQualificationKind, normalizeQualificationStatus, normalizeUserDoc, personnelPath, qualificationPath, requirePersonnel, requireQualification, unwrapPersonnel } from './shared'

// ---------------------------------------------------------------- tender domain
export async function getUserDocs(): Promise<UserDoc[]> {
  const data = await request<unknown>('/my/docs')
  const list = asArray<unknown>(data, 'docs')
  const items = list.length ? list : asArray<unknown>(data, 'items')
  return items
    .map(normalizeUserDoc)
    .filter((item): item is UserDoc => item !== null)
}

/** multipart 上传一份个人文档；202 返回 `{ doc, job_id }`。 */

export async function createUserDoc(
  file: File,
  onProgress?: (percent: number) => void,
): Promise<UserDocUploadResult> {
  const form = new FormData()
  form.append('file', file)
  const payload = await uploadForm<unknown>('/my/docs', form, {
    onProgress,
    networkMessage: '上传失败：无法连接本地服务，请确认后端已启动。',
  })
  const raw =
    payload && typeof payload === 'object' && !Array.isArray(payload)
      ? (payload as Record<string, unknown>)
      : {}
  const doc = normalizeUserDoc(raw.doc ?? payload)
  if (!doc) throw new ApiError(500, null, '服务端返回的文档信息无法识别，请刷新后重试。')
  return { doc, job_id: asText(raw.job_id) }
}

/** 删除本人上传的文档；后端会同时移除落盘文件与索引记录。 */

export async function deleteUserDoc(id: string): Promise<{ deleted: boolean; id: string }> {
  const data = await request<unknown>(`/my/docs/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
  const raw = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  return { deleted: asBool(raw.deleted), id: asText(raw.id) || id }
}

export const userDocFileUrl = (id: string) =>
  `/my/docs/${encodeURIComponent(id)}/file`

export function normalizeQualification(value: unknown): Qualification | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const id = asText(raw.id).trim()
  if (!id) return null
  return {
    id,
    kind: normalizeQualificationKind(raw.kind),
    name: asText(raw.name).trim() || '未命名资质',
    holder: asText(raw.holder),
    cert_no: asText(raw.cert_no),
    issuer: asText(raw.issuer),
    valid_from: asText(raw.valid_from) || null,
    valid_to: asText(raw.valid_to) || null,
    note: asText(raw.note),
    evidence: asArray<unknown>(raw.evidence, 'evidence')
      .map(normalizeQualificationEvidence)
      .filter((item): item is QualificationEvidence => item !== null),
    status: normalizeQualificationStatus(raw.status),
    days_left: asNullableNumber(raw.days_left),
    created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
  }
}

/** 兼容 `{ qualification: {...} }` 包装；平铺 payload 原样返回。 */

export async function getQualifications(
  params: { kind?: string; status?: string } = {},
): Promise<Qualification[]> {
  const search = new URLSearchParams()
  const kind = (params.kind || '').trim()
  const status = (params.status || '').trim()
  if (kind) search.set('kind', kind)
  if (status) search.set('status', status)
  const query = search.toString()
  const data = await request<unknown>(`/my/qualifications${query ? `?${query}` : ''}`)
  const list = asArray<unknown>(data, 'qualifications')
  const items = list.length ? list : asArray<unknown>(data, 'items')
  return items
    .map(normalizeQualification)
    .filter((item): item is Qualification => item !== null)
}

export async function createQualification(
  input: QualificationInput,
): Promise<Qualification> {
  const data = await request<unknown>('/my/qualifications', jsonBody(input))
  return requireQualification(data)
}

export async function updateQualification(
  id: string,
  input: Partial<QualificationInput>,
): Promise<Qualification> {
  const data = await request<unknown>(qualificationPath(id), {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
  return requireQualification(data)
}

export async function deleteQualification(
  id: string,
): Promise<{ deleted: boolean; id: string }> {
  const data = await request<unknown>(qualificationPath(id), { method: 'DELETE' })
  const raw = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  return { deleted: asBool(raw.deleted), id: asText(raw.id) || id }
}

/** multipart 上传存证文件；返回更新后的资质 payload。 */

export async function uploadQualificationEvidence(
  id: string,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<Qualification> {
  const form = new FormData()
  form.append('file', file)
  const payload = await uploadForm<unknown>(`${qualificationPath(id)}/evidence`, form, {
    onProgress,
    networkMessage: '上传证据失败：无法连接本地服务，请确认后端已启动。',
  })
  return requireQualification(payload)
}

/**
 * 删除一条存证。后端契约若未实现该路由，调用会抛 404/405，界面会提示
 * “后端暂不支持删除证据”，不影响其他操作。
 */

export async function deleteQualificationEvidence(
  id: string,
  filename: string,
): Promise<{ deleted: boolean; id: string; filename: string }> {
  const data = await request<unknown>(qualificationEvidenceUrl(id, filename), {
    method: 'DELETE',
  })
  if (!data) return { deleted: true, id, filename }
  const raw = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  return {
    deleted: asBool(raw.deleted),
    id: asText(raw.id) || id,
    filename: asText(raw.filename) || filename,
  }
}

export const qualificationEvidenceUrl = (id: string, filename: string) =>
  `${qualificationPath(id)}/evidence/${encodeURIComponent(filename)}`

export function normalizePreflight(
  value: unknown,
  fallbackDeadline = '',
): PreflightReport {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const items = asArray<unknown>(raw.items, 'items')
    .map(normalizePreflightItem)
    .filter((item): item is PreflightItem => item !== null)
  const summaryRaw =
    raw.summary && typeof raw.summary === 'object'
      ? (raw.summary as Record<string, unknown>)
      : {}
  const computed = {
    ok: items.filter((item) => item.status === 'ok').length,
    warn: items.filter((item) => item.status === 'expiring').length,
    missing: items.filter(
      (item) => item.status === 'missing' || item.status === 'expired',
    ).length,
  }
  const ok = Math.max(0, Math.round(asNumber(summaryRaw.ok, computed.ok)))
  const warn = Math.max(0, Math.round(asNumber(summaryRaw.warn, computed.warn)))
  const missing = Math.max(0, Math.round(asNumber(summaryRaw.missing, computed.missing)))
  return {
    deadline: asText(raw.deadline) || fallbackDeadline,
    items,
    summary: {
      ok,
      warn,
      missing,
      total: Math.max(0, Math.round(asNumber(summaryRaw.total, items.length))),
    },
  }
}

/** 按开标日生成自查清单；deadline 缺省时后端使用今天。 */

export async function getPreflight(deadline?: string | null): Promise<PreflightReport> {
  const value = (deadline || '').trim()
  const data = await request<unknown>(
    `/my/preflight${value ? `?deadline=${encodeURIComponent(value)}` : ''}`,
  )
  return normalizePreflight(data, value)
}

export function normalizePersonnel(value: unknown): Personnel | null {
  const payload = unwrapPersonnel(value)
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null
  const raw = payload as Record<string, unknown>
  const id = asText(raw.id).trim()
  if (!id) return null
  const months = Math.round(asNumber(raw.social_months, 0))
  return {
    id,
    name: asText(raw.name).trim() || '未命名人员',
    title: asText(raw.title),
    specialty: asText(raw.specialty),
    cert_no: asText(raw.cert_no),
    social_months: Math.max(0, Math.min(MAX_SOCIAL_MONTHS, months)),
    locked: asBool(raw.locked),
    note: asText(raw.note),
    created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
  }
}

export async function getPersonnel(): Promise<Personnel[]> {
  const data = await request<unknown>('/my/personnel')
  const list = asArray<unknown>(data, 'personnel')
  const items = list.length ? list : asArray<unknown>(data, 'items')
  return items
    .map(normalizePersonnel)
    .filter((item): item is Personnel => item !== null)
}

export async function createPersonnel(input: PersonnelInput): Promise<Personnel> {
  const data = await request<unknown>('/my/personnel', jsonBody(input))
  return requirePersonnel(data)
}

export async function updatePersonnel(
  id: string,
  input: Partial<PersonnelInput>,
): Promise<Personnel> {
  const data = await request<unknown>(personnelPath(id), {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
  return requirePersonnel(data)
}

export async function deletePersonnel(
  id: string,
): Promise<{ deleted: boolean; id: string }> {
  const data = await request<unknown>(personnelPath(id), { method: 'DELETE' })
  const raw = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  return { deleted: asBool(raw.deleted), id: asText(raw.id) || id }
}

export const getHealth = () => request<Health>('/health')

export async function getPackages(): Promise<PackageInfo[]> {
  const data = await request<PackageInfo[] | { packages?: PackageInfo[] }>('/packages')
  return asArray<unknown>(data, 'packages').map(normalizePackageInfo)
}

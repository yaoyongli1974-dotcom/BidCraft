import { asArray, jsonBody, request, requestBlob } from '../lib/http'
import type {
  CommercialDoc,
  CommercialDocInput,
  CommercialExportFormat,
  CommercialSection,
  CommercialStatus,
  CommercialValidation,
  CommercialValidationIssue,
} from '../lib/types'
import { asText, isoOrNull } from './shared'

// ---------------------------------------------------------------- commercial domain

const STATUSES: CommercialStatus[] = ['draft', 'submitted', 'approved', 'rejected']

export function normalizeCommercialStatus(value: unknown): CommercialStatus {
  const text = asText(value).toLowerCase()
  return (STATUSES as string[]).includes(text) ? (text as CommercialStatus) : 'draft'
}

function normalizeSection(raw: unknown): CommercialSection {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return {
    key: source.key ? asText(source.key) : undefined,
    title: asText(source.title ?? source.name),
    content: source.content !== undefined ? asText(source.content) : undefined,
    status: source.status ? asText(source.status) : undefined,
    note: source.note ? asText(source.note) : undefined,
  }
}

function normalizeIssue(raw: unknown): CommercialValidationIssue {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return {
    code: source.code ? asText(source.code) : undefined,
    message: asText(source.message ?? source.detail ?? source.text) || '未说明的校验问题',
    severity: source.severity ? asText(source.severity) : undefined,
    section: source.section ? asText(source.section) : undefined,
  }
}

export function normalizeCommercialValidation(raw: unknown): CommercialValidation {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const issues = asArray<unknown>(source, 'issues').map(normalizeIssue)
  // 后端 save 后存的是 {consistency:{ok,errors,warnings}, completeness:{ok,missing}, quote,...}
  // 即时 build_commercial_payload 的 validation 则直接是 {ok, errors, warnings}。
  const consistency = source.consistency && typeof source.consistency === 'object'
    ? (source.consistency as Record<string, unknown>)
    : null
  if (consistency) {
    for (const text of asArray<unknown>(consistency, 'errors')) {
      issues.push({ message: asText(text) || '校验未通过', severity: 'error' })
    }
    for (const text of asArray<unknown>(consistency, 'warnings')) {
      issues.push({ message: asText(text) || '校验提示', severity: 'warning' })
    }
    const completeness = source.completeness && typeof source.completeness === 'object'
      ? (source.completeness as Record<string, unknown>)
      : null
    if (completeness) {
      for (const text of asArray<unknown>(completeness, 'missing')) {
        issues.push({ message: `缺少：${asText(text)}`, severity: 'warning', section: 'completeness' })
      }
    }
  }
  // 即时 payload 的 errors/warnings（字符串数组）也映射为 issues
  if (!consistency) {
    for (const text of asArray<unknown>(source, 'errors')) {
      issues.push({ message: asText(text) || '校验未通过', severity: 'error' })
    }
    for (const text of asArray<unknown>(source, 'warnings')) {
      issues.push({ message: asText(text) || '校验提示', severity: 'warning' })
    }
  }
  const explicit = typeof source.ok === 'boolean'
    ? source.ok
    : consistency && typeof consistency.ok === 'boolean'
      ? consistency.ok
      : undefined
  return {
    ok: typeof explicit === 'boolean' ? explicit : issues.length === 0,
    issues,
  }
}

/** 后端 sections 是 {bid_letter:{title,fields,rows}, ...} 字典；也兼容数组。 */
function normalizeSections(raw: unknown): CommercialSection[] {
  if (Array.isArray(raw)) return raw.map(normalizeSection)
  if (raw && typeof raw === 'object') {
    return Object.entries(raw as Record<string, unknown>).map(([key, value]) => {
      const section = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
      const fields = Array.isArray(section.fields) ? section.fields : []
      const rows = Array.isArray(section.rows) ? section.rows : []
      const contentBits = [
        ...fields.map((f) => {
          const field = f && typeof f === 'object' ? (f as Record<string, unknown>) : {}
          return `${asText(field.label)} ${asText(field.value)}`.trim()
        }),
        ...rows.map((r) => {
          const row = r && typeof r === 'object' ? (r as Record<string, unknown>) : {}
          return asText(row.description ?? row.name ?? row.item)
        }),
      ].filter(Boolean)
      return {
        key: asText(section.key ?? key),
        title: asText(section.title ?? key),
        content: contentBits.join('；') || undefined,
        status: section.status ? asText(section.status) : undefined,
        note: section.note ? asText(section.note) : undefined,
      }
    })
  }
  return []
}

export function normalizeCommercialDoc(raw: unknown): CommercialDoc {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return {
    id: asText(source.id),
    name: asText(source.name),
    quote_version_id: asText(source.quote_version_id ?? source.version_id),
    package_id: source.package_id ? asText(source.package_id) : null,
    status: normalizeCommercialStatus(source.status),
    sections: normalizeSections(source.sections),
    validation: normalizeCommercialValidation(source.validation),
    submit_note: source.submit_note ? asText(source.submit_note) : '',
    submitted_by: source.submitted_by ? asText(source.submitted_by) : '',
    submitted_at: isoOrNull(source.submitted_at),
    review_note: source.review_note ? asText(source.review_note) : '',
    reviewed_by: source.reviewed_by ? asText(source.reviewed_by) : '',
    reviewed_at: isoOrNull(source.reviewed_at),
    created_at: isoOrNull(source.created_at),
    updated_at: isoOrNull(source.updated_at),
  }
}

export async function listCommercialDocs(ref: string): Promise<CommercialDoc[]> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/commercial`,
  )
  return asArray<unknown>(data, 'documents').map(normalizeCommercialDoc)
}

export async function createCommercialDoc(
  ref: string,
  input: CommercialDocInput,
): Promise<CommercialDoc> {
  const packageId = (input.package_id || '').trim()
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/commercial`,
    jsonBody({
      name: input.name.trim(),
      quote_version_id: input.quote_version_id,
      ...(packageId ? { package_id: packageId } : {}),
    }),
  )
  return normalizeCommercialDoc(data)
}

export async function getCommercialDoc(
  ref: string,
  docId: string,
): Promise<CommercialDoc> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/commercial/${encodeURIComponent(docId)}`,
  )
  return normalizeCommercialDoc(data)
}

export async function updateCommercialDoc(
  ref: string,
  docId: string,
  input: Partial<CommercialDocInput> & { name?: string },
): Promise<CommercialDoc> {
  const payload: Record<string, unknown> = {}
  if (input.name !== undefined) payload.name = input.name.trim()
  if (input.quote_version_id) payload.quote_version_id = input.quote_version_id
  if (input.package_id !== undefined) payload.package_id = input.package_id || null
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/commercial/${encodeURIComponent(docId)}`,
    { method: 'PUT', body: JSON.stringify(payload) },
  )
  return normalizeCommercialDoc(data)
}

export function deleteCommercialDoc(ref: string, docId: string): Promise<unknown> {
  return request(
    `/projects/${encodeURIComponent(ref)}/commercial/${encodeURIComponent(docId)}`,
    { method: 'DELETE' },
  )
}

async function action(
  ref: string,
  docId: string,
  name: string,
  payload: Record<string, unknown>,
): Promise<CommercialDoc> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/commercial/${encodeURIComponent(docId)}/${name}`,
    jsonBody(payload),
  )
  return normalizeCommercialDoc(data)
}

export function submitCommercialDoc(
  ref: string,
  docId: string,
  note?: string,
  requireComplete = false,
): Promise<CommercialDoc> {
  const trimmed = (note || '').trim()
  const body: Record<string, unknown> = trimmed ? { note: trimmed } : {}
  if (requireComplete) body.require_complete = true
  return action(ref, docId, 'submit', body)
}

export function reviewCommercialDoc(
  ref: string,
  docId: string,
  decision: 'approve' | 'reject',
  note?: string,
): Promise<CommercialDoc> {
  const trimmed = (note || '').trim()
  return action(ref, docId, 'review', {
    action: decision,
    ...(trimmed ? { note: trimmed } : {}),
  })
}

export function unapproveCommercialDoc(
  ref: string,
  docId: string,
  note?: string,
): Promise<CommercialDoc> {
  const trimmed = (note || '').trim()
  return action(ref, docId, 'unapprove', trimmed ? { note: trimmed } : {})
}

export function exportCommercialDoc(
  ref: string,
  docId: string,
  format: CommercialExportFormat,
): Promise<{ blob: Blob; filename: string }> {
  return requestBlob(
    `/projects/${encodeURIComponent(ref)}/commercial/${encodeURIComponent(docId)}/export?format=${encodeURIComponent(format)}`,
    { method: 'POST' },
    `商务标.${format}`,
  )
}

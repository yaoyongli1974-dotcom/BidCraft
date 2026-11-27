import { request } from '../lib/http'
import type { Fact, FactEvidence, FactUpdateResult, Ledger } from '../lib/types'

// ---------------------------------------------------------------- ledger domain

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function nullableText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function nullableNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    return Number(value)
  }
  return null
}

function provenanceKind(value: unknown): 'tender_extract' | 'user_supplied' {
  return value === 'user_supplied' ? 'user_supplied' : 'tender_extract'
}

/**
 * 读取一条事实的来源证据。新契约的 source_* 字段优先，缺字段时回退到旧契约
 * 的嵌套 source；null / undefined 一律归一成 null，绝不编造文件名或页码。
 */
export function normalizeFactEvidence(value: unknown): FactEvidence {
  const raw = asRecord(value)
  const source = asRecord(raw.source)
  const page = raw.source_page ?? source.page
  return {
    source_file_id: nullableText(raw.source_file_id),
    source_quote: nullableText(raw.source_quote) ?? nullableText(source.quote),
    source_page: nullableNumber(page),
    provenance_kind: provenanceKind(raw.provenance_kind),
    supplier_note: nullableText(raw.supplier_note),
  }
}

export function normalizeFact(value: unknown): Fact | null {
  const raw = asRecord(value)
  const key = typeof raw.key === 'string' ? raw.key.trim() : ''
  if (!key) return null
  const source = asRecord(raw.source)
  const legacySource = Object.keys(source).length
    ? {
        file: typeof source.file === 'string' ? source.file : undefined,
        page:
          typeof source.page === 'number' || typeof source.page === 'string'
            ? source.page
            : null,
        quote: typeof source.quote === 'string' ? source.quote : undefined,
      }
    : undefined
  return {
    key,
    label: typeof raw.label === 'string' ? raw.label : '',
    value: typeof raw.value === 'string' ? raw.value : '',
    unit: typeof raw.unit === 'string' ? raw.unit : undefined,
    status: typeof raw.status === 'string' ? raw.status : 'extracted',
    confidence:
      typeof raw.confidence === 'number' && Number.isFinite(raw.confidence)
        ? raw.confidence
        : undefined,
    required: raw.required === true,
    source: legacySource,
    ...normalizeFactEvidence(raw),
    source_file_name:
      nullableText(raw.source_file_name) ?? nullableText(source.file),
    confirmed_by: nullableText(raw.confirmed_by),
    confirmed_at: nullableText(raw.confirmed_at),
  }
}

/** PATCH 响应归一：确认信息由服务端盖章，前端只读取。 */
export function normalizeFactUpdate(value: unknown): FactUpdateResult | null {
  const raw = asRecord(value)
  const key = typeof raw.key === 'string' ? raw.key.trim() : ''
  if (!key) return null
  const stale = Array.isArray(raw.stale_sections)
    ? raw.stale_sections
        .filter((item) => item && typeof item === 'object')
        .map((item) => {
          const row = asRecord(item)
          return {
            node_id: typeof row.node_id === 'string' ? row.node_id : '',
            heading: typeof row.heading === 'string' ? row.heading : '',
            stale_value: typeof row.stale_value === 'string' ? row.stale_value : '',
          }
        })
    : []
  return {
    key,
    label: typeof raw.label === 'string' ? raw.label : undefined,
    value: typeof raw.value === 'string' ? raw.value : '',
    status: typeof raw.status === 'string' ? raw.status : 'extracted',
    required: typeof raw.required === 'boolean' ? raw.required : undefined,
    ...normalizeFactEvidence(raw),
    source_file_name: nullableText(raw.source_file_name),
    confirmed_by: nullableText(raw.confirmed_by),
    confirmed_at: nullableText(raw.confirmed_at),
    provenance_kind: provenanceKind(raw.provenance_kind),
    supplier_note: nullableText(raw.supplier_note),
    stale_sections: stale,
  }
}

const SERVER_STAMPED_FIELDS = [
  'confirmed_by',
  'confirmed_at',
  'source_file_id',
  'source_file_name',
  'source_quote',
  'source_page',
  'provenance_kind',
  'supplier_note',
] as const

/** 把 PATCH 响应合并回本地台账；旧后端未返回的字段保留原值。 */
export function mergeFactUpdate(fact: Fact, update: FactUpdateResult): Fact {
  const next: Fact = { ...fact, value: update.value, status: update.status }
  if (update.label !== undefined) next.label = update.label
  if (update.required !== undefined) next.required = update.required
  for (const field of SERVER_STAMPED_FIELDS) {
    const value = update[field]
    if (value !== undefined && value !== null) {
      Object.assign(next, { [field]: value })
    } else if (value === null) {
      Object.assign(next, { [field]: null })
    }
  }
  return next
}

export async function getLedger(ref: string): Promise<Ledger> {
  const data = await request<Partial<Ledger>>(`/projects/${encodeURIComponent(ref)}/ledger`)
  return {
    summary: data.summary,
    facts: Array.isArray(data.facts)
      ? (data.facts.map(normalizeFact).filter(Boolean) as Fact[])
      : [],
    materials: Array.isArray(data.materials) ? data.materials : [],
    parameters: Array.isArray(data.parameters) ? data.parameters : [],
    scoring: Array.isArray(data.scoring) ? data.scoring : [],
    requirements: Array.isArray(data.requirements) ? data.requirements : [],
    risks: Array.isArray(data.risks) ? data.risks : [],
    template_sections: Array.isArray(data.template_sections) ? data.template_sections : [],
    project: data.project,
  }
}

/**
 * 改写事实。出站体只包含后端接受的 value / status / 来源字段；
 * 确认人与确认时间由服务端盖章，前端永不发送。
 */
export async function updateFact(
  ref: string,
  key: string,
  value: string,
  status: string,
  evidence: FactEvidence = {},
): Promise<FactUpdateResult> {
  const body: Record<string, unknown> = { value, status }
  const provenance = provenanceKind(evidence.provenance_kind)
  body.provenance_kind = provenance
  const note =
    typeof evidence.supplier_note === 'string' ? evidence.supplier_note.trim() : ''
  if (note) body.supplier_note = note
  const fileId =
    typeof evidence.source_file_id === 'string' ? evidence.source_file_id.trim() : ''
  const quote = typeof evidence.source_quote === 'string' ? evidence.source_quote.trim() : ''
  const page = evidence.source_page
  if (fileId) body.source_file_id = fileId
  if (quote) body.source_quote = quote
  if (typeof page === 'number' && Number.isFinite(page)) body.source_page = page
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/facts/${encodeURIComponent(key)}`,
    { method: 'PATCH', body: JSON.stringify(body) },
  )
  const updated = normalizeFactUpdate(data)
  if (!updated) throw new Error('事实更新响应无效')
  return updated
}

import { jsonBody, readError, request } from '../lib/http'
import type { ProjectProfileResponse, ScoreAnalysis, WechatConfig, WechatConfigInput, WritingProfile } from '../lib/types'
import { downloadBlob } from './generation'
import { PROFILE_TEXT_FIELDS, asBool, asText, normalizeDeletionState, normalizeWechatConfig } from './shared'

// ---------------------------------------------------------------- account domain
export async function getWechatConfig(): Promise<WechatConfig> {
  return normalizeWechatConfig(await request<unknown>('/admin/wechat'))
}

export async function updateWechatConfig(input: WechatConfigInput): Promise<WechatConfig> {
  return normalizeWechatConfig(await request<unknown>('/admin/wechat', jsonBody(input)))
}

export async function testWechatConfig(): Promise<{ ok: boolean; message: string }> {
  const raw = await request<unknown>('/admin/wechat/test', { method: 'POST' })
  const data = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return { ok: asBool(data.ok), message: asText(data.message) }
}

export const PROFILE_TONES = ['稳健', '简洁', '技术', '亲和'] as const

export function normalizeWritingProfile(value: unknown): WritingProfile {
  const source = (value ?? {}) as Record<string, unknown>
  const profile: WritingProfile = {
    company_name: '',
    legal_rep: '',
    project_name: '',
    owner_name: '',
    bid_amount: '',
    duration_days: '',
    warranty_years: '',
    quality_standard: '',
    tone: '稳健',
    forbidden_phrases: [],
    extra_facts: [],
  }
  for (const [field, limit] of PROFILE_TEXT_FIELDS) {
    profile[field] = String(source[field] ?? '').trim().slice(0, limit)
  }
  const tone = String(source.tone ?? '稳健')
  profile.tone = (PROFILE_TONES as readonly string[]).includes(tone)
    ? (tone as WritingProfile['tone'])
    : '稳健'

  const phrases = Array.isArray(source.forbidden_phrases) ? source.forbidden_phrases : []
  const seen = new Set<string>()
  for (const item of phrases) {
    const text = String(item ?? '').trim().slice(0, 40)
    if (text && !seen.has(text)) {
      seen.add(text)
      profile.forbidden_phrases.push(text)
    }
  }

  const extras = Array.isArray(source.extra_facts) ? source.extra_facts : []
  const keys = new Set<string>()
  for (const item of extras) {
    const row = (item ?? {}) as Record<string, unknown>
    const key = String(row.key ?? '').trim().slice(0, 40)
    const val = String(row.value ?? '').trim().slice(0, 200)
    if (key && val && !keys.has(key)) {
      keys.add(key)
      profile.extra_facts.push({ key, value: val })
    }
  }
  return profile
}

export function normalizeProjectProfile(value: unknown): ProjectProfileResponse {
  const source = (value ?? {}) as Record<string, unknown>
  return {
    profile: normalizeWritingProfile(source.profile),
    anchors: (source.anchors ?? {}) as Record<string, string>,
    facts_block: String(source.facts_block ?? ''),
  }
}

export async function getProjectProfile(ref: string): Promise<ProjectProfileResponse> {
  const data = await request<unknown>(`/projects/${encodeURIComponent(ref)}/profile`)
  return normalizeProjectProfile(data)
}

export async function saveProjectProfile(
  ref: string,
  profile: WritingProfile,
): Promise<ProjectProfileResponse> {
  const data = await request<unknown>(`/projects/${encodeURIComponent(ref)}/profile`, {
    method: 'PUT',
    body: JSON.stringify({ profile: normalizeWritingProfile(profile) }),
  })
  return normalizeProjectProfile(data)
}

export function normalizeScoreAnalysis(value: unknown): ScoreAnalysis {
  const raw = (value ?? {}) as Record<string, unknown>
  const list = (input: unknown): ScoreAnalysis['items'] =>
    Array.isArray(input)
      ? input
          .map((entry) => {
            const row = (entry ?? {}) as Record<string, unknown>
            if (!row.id) return null
            const num = (v: unknown) => {
              const parsed = typeof v === 'number' ? v : Number(v)
              return Number.isFinite(parsed) ? parsed : null
            }
            return {
              id: String(row.id),
              name: String(row.name ?? '').slice(0, 255),
              score: num(row.score),
              category: String(row.category ?? '').slice(0, 80),
              criteria: String(row.criteria ?? ''),
              section_hint: String(row.section_hint ?? '').slice(0, 255),
              actual_score: num(row.actual_score),
              actual_bidder: String(row.actual_bidder ?? '').slice(0, 80),
              score_note: String(row.score_note ?? '').slice(0, 200),
              rate: num(row.rate),
              loss: num(row.loss),
            }
          })
          .filter((row): row is ScoreAnalysis['items'][number] => row !== null)
      : []
  const items = list(raw.items)
  const losses = Array.isArray(raw.losses) ? list(raw.losses) : items.filter((row) => (row.loss ?? 0) > 0)
  const number = (v: unknown) => {
    const parsed = typeof v === 'number' ? v : Number(v)
    return Number.isFinite(parsed) ? parsed : 0
  }
  const optional = (v: unknown) => {
    const parsed = typeof v === 'number' ? v : Number(v)
    return Number.isFinite(parsed) ? parsed : null
  }
  return {
    project_id: String(raw.project_id ?? ''),
    total_items: number(raw.total_items) || items.length,
    filled: number(raw.filled),
    total_max: number(raw.total_max),
    total_actual: number(raw.total_actual),
    rate: optional(raw.rate),
    dimensions: Array.isArray(raw.dimensions)
      ? raw.dimensions.map((entry) => {
          const row = (entry ?? {}) as Record<string, unknown>
          return {
            category: String(row.category ?? '未分类'),
            count: number(row.count),
            max: number(row.max),
            actual: number(row.actual),
            rate: optional(row.rate),
          }
        })
      : [],
    losses,
    items,
  }
}

export async function getProjectScores(projectId: string): Promise<ScoreAnalysis> {
  const data = await request<unknown>(`/projects/${encodeURIComponent(projectId)}/scores`)
  return normalizeScoreAnalysis(data)
}

export interface ScorePatch {
  id: string
  actual_score: number | null
  actual_bidder?: string
  score_note?: string
}

export async function saveProjectScores(
  projectId: string,
  rows: ScorePatch[],
): Promise<ScoreAnalysis> {
  const data = await request<unknown>(`/projects/${encodeURIComponent(projectId)}/scores`, {
    method: 'PUT',
    body: JSON.stringify({ rows }),
  })
  return normalizeScoreAnalysis(data)
}

export interface LegalDoc {
  name: string
  text: string
}

export async function getLegalDoc(name: string): Promise<LegalDoc> {
  const data = await request<{ name?: string; text?: string }>(
    `/legal/${encodeURIComponent(name)}`,
  )
  return { name: name, text: String(data?.text ?? '') }
}

export interface BillingProfile {
  title: string
  tax_no: string
  email: string
}

export async function getBillingProfile(): Promise<BillingProfile> {
  const data = await request<Partial<BillingProfile>>('/account/billing-profile')
  return {
    title: String(data?.title ?? ''),
    tax_no: String(data?.tax_no ?? ''),
    email: String(data?.email ?? ''),
  }
}

export async function saveBillingProfile(input: BillingProfile): Promise<BillingProfile> {
  return request<BillingProfile>('/account/billing-profile', {
    method: 'PUT',
    body: JSON.stringify(input),
  })
}

export async function downloadAccountExport(): Promise<void> {
  const response = await fetch('/account/export', {
    credentials: 'same-origin',
    headers: { Accept: 'application/zip' },
  })
  if (!response.ok) throw await readError(response)
  downloadBlob(await response.blob(), 'bidcraft-export.zip')
}

export async function downloadStatement(start = '', end = ''): Promise<void> {
  const query = new URLSearchParams()
  if (start) query.set('start', start)
  if (end) query.set('end', end)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  const response = await fetch(`/billing/statement${suffix}`, {
    credentials: 'same-origin',
    headers: { Accept: 'text/csv' },
  })
  if (!response.ok) throw await readError(response)
  downloadBlob(await response.blob(), 'bidcraft-statement.csv')
}

export interface DeletionState {
  pending: boolean
  requested_at: string | null
  due_at: string | null
  days_left: number
  cooling_off_days: number
}

export async function getDeletionState(): Promise<DeletionState> {
  return normalizeDeletionState(await request<unknown>('/account/deletion'))
}

export async function deleteAccount(password: string, confirm: string): Promise<DeletionState> {
  return normalizeDeletionState(
    await request<unknown>('/account/delete', {
      method: 'POST',
      body: JSON.stringify({ password, confirm }),
    }),
  )
}

export async function cancelAccountDeletion(): Promise<DeletionState> {
  return normalizeDeletionState(
    await request<unknown>('/account/delete/cancel', { method: 'POST' }),
  )
}

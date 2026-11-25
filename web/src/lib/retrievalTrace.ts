import type { Tone } from './format'
import type { KnowledgeRef, RetrievalTrace } from './types'

/**
 * 检索溯源信号的统一归一化（v0.32 Phase 3）。
 *
 * 后端分版本补齐这些字段；缺失/非法值一律归一成 null，由 UI 显示「未知」。
 * 绝不把缺省伪装成 0 分、trusted 档位或未降级，避免把没证据的结果说成可信。
 */

const TRUST_TIER_LABELS: Readonly<Record<string, string>> = {
  full: '全文注入',
  excerpt: '有限摘录',
  title_only: '仅题录',
  suppressed: '已抑制',
}

const TRUST_TIER_TONES: Readonly<Record<string, Tone>> = {
  full: 'success',
  excerpt: 'info',
  title_only: 'warn',
  suppressed: 'danger',
}

export function trustTierLabel(tier: string | null | undefined): string {
  if (!tier) return '信任档位未知'
  return TRUST_TIER_LABELS[tier.toLowerCase()] || tier
}

export function trustTierTone(tier: string | null | undefined): Tone {
  if (!tier) return 'neutral'
  return TRUST_TIER_TONES[tier.toLowerCase()] || 'neutral'
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

/** 可空数值：有限数字或数字字符串保留，其余（含 null/undefined）为 null。 */
export function traceNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

/** 可空布尔：只认真实布尔值与字符串 true/false，其余保持 null。 */
export function traceBool(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value
  if (value === 'true') return true
  if (value === 'false') return false
  return null
}

/** 可空文本：空白串、null、undefined 一律为 null。 */
export function traceText(value: unknown): string | null {
  if (typeof value === 'string') return value.trim() ? value : null
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return null
}

export function traceDiagnostics(
  value: unknown,
): Record<string, unknown> | unknown[] | null {
  if (isRecord(value)) return { ...value }
  if (Array.isArray(value)) return [...value]
  return null
}

/**
 * 从任意后端记录中抽取溯源信号。兼容下游别名 tier / downgraded，
 * 未知字段原样留在调用方对象里，不在这里伪造默认值。
 */
export function normalizeRetrievalTrace(raw: Record<string, unknown>): RetrievalTrace {
  return {
    rerank_score: traceNumber(raw.rerank_score),
    trust_tier: traceText(raw.trust_tier) ?? traceText(raw.tier),
    degraded: traceBool(raw.degraded !== undefined ? raw.degraded : raw.downgraded),
    chunk_id: traceText(raw.chunk_id),
    chunk_index: traceNumber(raw.chunk_index),
    diagnostics: traceDiagnostics(raw.diagnostics),
    score_source: traceText(raw.score_source),
  }
}

/** 一条知识引用：真实 id / 文本 + 溯源信号；两者都没有的条目直接丢弃。 */
export function normalizeKnowledgeRef(value: unknown): KnowledgeRef | null {
  if (typeof value === 'string') {
    const text = value.trim()
    return text ? { text, snippet: text } : null
  }
  if (!isRecord(value)) return null
  const trace = normalizeRetrievalTrace(value)
  const id =
    traceText(value.id) ??
    trace.chunk_id ??
    traceText(value.document_id) ??
    traceText(value.chunk)
  const text =
    traceText(value.text) ??
    traceText(value.snippet) ??
    traceText(value.heading) ??
    traceText(value.source)
  if (!id && !text) return null
  return {
    ...value,
    ...trace,
    id: id ?? undefined,
    kind: traceText(value.kind),
    kb_id: traceText(value.kb_id),
    document_id: traceText(value.document_id),
    heading: traceText(value.heading),
    source: traceText(value.source),
    snippet: traceText(value.snippet),
    text: traceText(value.text),
    page: traceNumber(value.page ?? value.page_no),
    score: traceNumber(value.score),
  }
}

/** 规划引用数组：字符串保留，对象归一化后仍是对象（不丢真实 id 与信号）。 */
export function normalizeKnowledgeRefs(value: unknown): Array<KnowledgeRef | string> {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => (typeof item === 'string' ? item.trim() || null : normalizeKnowledgeRef(item)))
    .filter((item): item is KnowledgeRef | string => item !== null)
}

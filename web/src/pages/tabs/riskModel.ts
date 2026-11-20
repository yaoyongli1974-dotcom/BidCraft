import type { Issue, RiskTierLevel } from '../../lib/types'
import { issueResolved } from '../../lib/format'

/**
 * 交付前风险总览的纯数据管道。
 *
 * 独立于组件文件，便于下一轮接入「实质性条款响应」
 * （GET /projects/{ref}/requirements）时复用同一套筛选与分组逻辑。
 */

export const TIER_ORDER: RiskTierLevel[] = ['blocking', 'critical', 'major', 'minor']

export const TIER_NOTES: Record<RiskTierLevel, string> = {
  blocking: '必须处置后才能导出',
  critical: '建议立即逐条处理',
  major: '交付前逐条确认',
  minor: '不影响交付',
}

/** 把单条问题的原始 level 归入四档之一（info 并入提示档）。 */
export function tierOfIssue(level?: string): RiskTierLevel {
  const normalized = (level || '').toLowerCase()
  if (normalized === 'blocking') return 'blocking'
  if (normalized === 'critical') return 'critical'
  if (normalized === 'major') return 'major'
  return 'minor'
}

export interface RiskItemFilters {
  tier: RiskTierLevel | 'all'
  category: string
  query: string
  onlyOpen: boolean
}

export function filterRiskItems(items: Issue[], filters: RiskItemFilters): Issue[] {
  const keyword = filters.query.trim().toLowerCase()
  return items.filter((issue) => {
    if (filters.tier !== 'all' && tierOfIssue(issue.level) !== filters.tier) return false
    if (filters.category !== 'all' && issue.category !== filters.category) return false
    if (filters.onlyOpen && issueResolved(issue.status)) return false
    if (!keyword) return true
    return [
      issue.code,
      issue.finding,
      issue.message,
      issue.location,
      issue.heading,
      issue.recommendation,
      issue.category,
      issue.resolution_note,
    ]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(keyword))
  })
}

export interface RiskGroup {
  tier: RiskTierLevel
  items: Issue[]
  open: number
  handled: number
}

export function groupRiskItems(items: Issue[]): RiskGroup[] {
  const buckets = new Map<RiskTierLevel, Issue[]>()
  TIER_ORDER.forEach((tier) => buckets.set(tier, []))
  items.forEach((issue) => buckets.get(tierOfIssue(issue.level))?.push(issue))
  return TIER_ORDER.map((tier) => {
    const groupItems = buckets.get(tier) || []
    const open = groupItems.filter((issue) => !issueResolved(issue.status)).length
    return { tier, items: groupItems, open, handled: groupItems.length - open }
  })
}

/** 跳转正文时用于定位的片段：优先证据里的 quote，其次 location。 */
export function jumpQuote(issue: Issue): string {
  const evidence = issue.evidence
  if (evidence && typeof evidence === 'object') {
    const record = evidence as Record<string, unknown>
    const quote = record.quote ?? record.snippet
    if (typeof quote === 'string' && quote.trim()) return quote.trim()
  }
  if (typeof evidence === 'string' && evidence.trim()) return evidence.trim().slice(0, 160)
  return (issue.location || '').trim()
}

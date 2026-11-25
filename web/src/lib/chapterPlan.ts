import type { ChapterPlan } from './types'

/** 规划模式来自 GET plan 响应；缺省按自动处理，不改变后端语义。 */
export type ChapterPlanMode = 'auto' | 'review' | 'review_strict'

export interface ChapterPlanGate {
  blocked: boolean
  /** 被拦截时展示给用户的解释。 */
  reason?: string
  /** 自动模式下未人工确认时的提醒。 */
  warning?: string
}

const CONFIRMED_STATUSES = new Set(['CONFIRMED', 'USED_FOR_GENERATION'])

export function isConfirmedPlan(plan: ChapterPlan | null | undefined): boolean {
  return Boolean(plan && plan.status && CONFIRMED_STATUSES.has(plan.status))
}

/**
 * 正文生成前的纯前端门禁提示：review 模式必须确认本章规划；
 * auto 模式不拦截，但明确告知规划未经人工确认。
 */
export function chapterPlanGate(
  mode: ChapterPlanMode,
  plan: ChapterPlan | null | undefined,
  heading = '',
): ChapterPlanGate {
  const label = heading ? `「${heading}」` : '本章'
  if (mode === 'review' || mode === 'review_strict') {
    if (!plan) {
      return {
        blocked: true,
        reason: `${label}还没有章节规划。请先在「目录大纲」的章节规划面板生成并确认规划，再生成正文。`,
      }
    }
    if (!isConfirmedPlan(plan)) {
      return {
        blocked: true,
        reason: `${label}的章节规划尚未确认（当前为草稿或已修改）。请在章节规划面板确认后再生成正文。`,
      }
    }
    return { blocked: false }
  }
  if (!isConfirmedPlan(plan)) {
    return {
      blocked: false,
      warning: '当前为自动规划模式，章节规划未经人工确认；正文将按自动生成的规划编排。',
    }
  }
  return { blocked: false }
}

const STATUS_LABELS: Record<string, string> = {
  DRAFT: '草稿',
  REVIEW_REQUIRED: '待复核',
  CONFIRMED: '已确认',
  USED_FOR_GENERATION: '已用于生成',
  UPDATED: '已更新待确认',
  REGENERATING: '重新生成中',
}

export function planStatusLabel(status: string | undefined): string {
  if (!status) return '状态未记录'
  return STATUS_LABELS[status] || status
}

/** 数组字段的元素可能是中文字符串或对象；统一转成可展示的纯文本。 */
export function planItemText(item: unknown): string | null {
  if (typeof item === 'string') return item.trim() || null
  if (typeof item === 'number' && Number.isFinite(item)) return String(item)
  if (!item || typeof item !== 'object') return null
  const record = item as Record<string, unknown>
  const preferred = [
    'text',
    'title',
    'label',
    'name',
    'content',
    'description',
    'summary',
    'point',
    '内容',
    '标题',
    '名称',
    '说明',
    '描述',
    '要点',
    '条款',
    '文本',
  ]
  for (const key of preferred) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  const extras = Object.entries(record)
    .filter(([key, value]) => typeof value === 'number' && Number.isFinite(value) && !key.startsWith('_'))
    .map(([key, value]) => `${key} ${String(value)}`)
  const strings = Object.values(record)
    .filter((value): value is string => typeof value === 'string' && Boolean(value.trim()))
    .map((value) => value.trim())
  const parts = [...strings, ...extras]
  return parts.length > 0 ? parts.join(' · ') : null
}

/** 缺失字段返回空数组；调用方据此显示「未填写」，不生成占位内容。 */
export function planItems(list: unknown[] | undefined): string[] {
  if (!list) return []
  return list.map(planItemText).filter((text): text is string => text !== null)
}

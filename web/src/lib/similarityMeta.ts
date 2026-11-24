import type { Tone } from './format'

export const SIMILARITY_KIND_META: Record<string, { label: string; tone: Tone }> = {
  text: { label: '文本重合', tone: 'danger' },
  logic: { label: '逻辑结构', tone: 'warn' },
  data: { label: '数据引用', tone: 'info' },
  figure: { label: '图表图片', tone: 'accent' },
  image: { label: '图表图片', tone: 'accent' },
  structure: { label: '逻辑结构', tone: 'warn' },
}

export function similarityKindMeta(kind: string): { label: string; tone: Tone } {
  const key = (kind || '').toLowerCase()
  return SIMILARITY_KIND_META[key] || { label: kind || '其他', tone: 'neutral' }
}

export const SIMILARITY_STATUS_META: Record<string, { label: string; tone: Tone }> = {
  pending: { label: '待复核', tone: 'info' },
  // 后端 review_status 还接受 normal/suspicious（service.py:26、schemas.py:584-586）。
  normal: { label: '正常', tone: 'neutral' },
  suspicious: { label: '疑似重复', tone: 'warn' },
  confirmed: { label: '已确认重复', tone: 'danger' },
  ignored: { label: '已标记原创', tone: 'neutral' },
}

/** 除 pending 外，后端允许的人工复核终态均可再次修改/重置。 */
export const SIMILARITY_REVIEWABLE_STATUSES: Record<string, true> = {
  normal: true,
  suspicious: true,
  confirmed: true,
  ignored: true,
}

export function isSimilarityReviewable(status: string): boolean {
  return SIMILARITY_REVIEWABLE_STATUSES[(status || '').toLowerCase()] === true
}

export function similarityStatusMeta(status: string): { label: string; tone: Tone } {
  return SIMILARITY_STATUS_META[(status || 'pending').toLowerCase()] || {
    label: status || '待复核',
    tone: 'neutral',
  }
}

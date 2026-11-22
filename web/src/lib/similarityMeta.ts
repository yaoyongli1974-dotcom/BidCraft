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
  confirmed: { label: '已确认重复', tone: 'danger' },
  ignored: { label: '已标记原创', tone: 'neutral' },
}

export function similarityStatusMeta(status: string): { label: string; tone: Tone } {
  return SIMILARITY_STATUS_META[(status || 'pending').toLowerCase()] || {
    label: status || '待复核',
    tone: 'neutral',
  }
}

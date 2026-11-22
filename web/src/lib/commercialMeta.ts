import type { Tone } from './format'
import type { CommercialStatus } from './types'

export const COMMERCIAL_STATUS_META: Record<
  CommercialStatus,
  { label: string; tone: Tone }
> = {
  draft: { label: '草稿', tone: 'neutral' },
  submitted: { label: '待审批', tone: 'warn' },
  approved: { label: '已定稿', tone: 'success' },
  rejected: { label: '已驳回', tone: 'danger' },
}

export type CommercialAction = 'submit' | 'approve' | 'reject' | 'unapprove'

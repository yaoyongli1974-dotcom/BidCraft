import type { Tone } from './format'

/** 章节复核流转：草稿 → 待复核 → 已确认 → 已锁定（服务端口径一致）。 */
export type ReviewStatus = 'draft' | 'review' | 'confirmed' | 'locked'
export type ReviewAction = 'submit' | 'confirm' | 'revert' | 'lock' | 'unlock'

export interface ReviewMeta {
  label: string
  tone: Tone
  hint: string
}

export const REVIEW_STATUS: Record<ReviewStatus, ReviewMeta> = {
  draft: {
    label: '草稿',
    tone: 'neutral',
    hint: '可自由修改；提交复核后由负责人确认。',
  },
  review: {
    label: '待复核',
    tone: 'warn',
    hint: '已提交复核。此时再改正文会自动退回草稿，复核对象不会悄悄变化。',
  },
  confirmed: {
    label: '已确认',
    tone: 'success',
    hint: '正文已冻结：导出只认已确认的章节；要改先撤回为草稿并说明原因。',
  },
  locked: {
    label: '已锁定',
    tone: 'info',
    hint: '最终交付状态：正文完全不可改；解锁需负责人说明原因。',
  },
}

const ALIASES: Record<string, ReviewStatus> = {
  draft: 'draft',
  review: 'review',
  confirmed: 'confirmed',
  locked: 'locked',
}

export function reviewStatus(value?: string | null): ReviewStatus {
  return (value && ALIASES[value]) || 'draft'
}

export function reviewMeta(value?: string | null): ReviewMeta {
  return REVIEW_STATUS[reviewStatus(value)]
}

/** 已确认/已锁定的正文不可编辑、不可重生成。 */
export function isChapterFrozen(value?: string | null): boolean {
  const status = reviewStatus(value)
  return status === 'confirmed' || status === 'locked'
}

/** 导出只读取已确认（含已锁定）的章节。 */
export function isChapterExportable(value?: string | null): boolean {
  return isChapterFrozen(value)
}

export interface ReviewStep {
  action: ReviewAction
  label: string
  variant: 'primary' | 'secondary' | 'danger'
  /** 需要填写原因（服务端要求 ≥4 字）。 */
  needsNote?: boolean
  /** 仅项目负责人/超管可执行。 */
  ownerOnly?: boolean
}

/** 当前状态下可执行的流转动作；`owner` 决定是否给出负责人动作。 */
export function reviewSteps(value: string | null | undefined, owner: boolean): ReviewStep[] {
  switch (reviewStatus(value)) {
    case 'draft':
      return [{ action: 'submit', label: '提交复核', variant: 'primary' }]
    case 'review':
      return [
        ...(owner
          ? [{ action: 'confirm', label: '确认通过', variant: 'primary' } as ReviewStep]
          : []),
        { action: 'revert', label: '撤回', variant: 'secondary', needsNote: true },
      ]
    case 'confirmed':
      return [
        ...(owner
          ? [{ action: 'lock', label: '锁定', variant: 'secondary' } as ReviewStep]
          : []),
        { action: 'revert', label: '撤回为草稿', variant: 'danger', needsNote: true, ownerOnly: true },
      ]
    default:
      return owner
        ? [{ action: 'unlock', label: '解锁', variant: 'secondary', needsNote: true, ownerOnly: true }]
        : []
  }
}

/** 流转后提示语，用于 toast。 */
export function reviewDoneMessage(action: ReviewAction): string {
  switch (action) {
    case 'submit':
      return '已提交复核'
    case 'confirm':
      return '已确认本章'
    case 'revert':
      return '已撤回为草稿'
    case 'lock':
      return '已锁定本章'
    default:
      return '已解锁，本章恢复为已确认'
  }
}

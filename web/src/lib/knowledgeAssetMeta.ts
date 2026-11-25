import type { Tone } from './format'
import type {
  KnowledgeAssetDecision,
  KnowledgeAssetReviewStatus,
} from './types'

/** 状态标签与语气；未知/缺失状态由 knowledgeAssetStatusMeta 兜底，不冒充已知状态。 */
export interface KnowledgeAssetStatusMeta {
  label: string
  tone: Tone
  hint: string
}

export const KNOWLEDGE_ASSET_STATUS_META: Record<
  KnowledgeAssetReviewStatus,
  KnowledgeAssetStatusMeta
> = {
  draft: {
    label: '草稿',
    tone: 'neutral',
    hint: '已抽取但尚未送审，可提交复核或直接驳回。',
  },
  review: {
    label: '待审核',
    tone: 'warn',
    hint: '等待人工判断；可通过、驳回或退回草稿。',
  },
  approved: {
    label: '已通过',
    tone: 'info',
    hint: '审核通过，可发布；发现问题时仍可驳回。',
  },
  published: {
    label: '已发布',
    tone: 'success',
    hint: '已进入可用状态；需要下架时只能归档。',
  },
  rejected: {
    label: '已驳回',
    tone: 'danger',
    hint: '未通过审核；服务端可重新打开为草稿后再送审。',
  },
  archived: {
    label: '已归档',
    tone: 'neutral',
    hint: '已下架的历史资产；服务端可重新打开为草稿。',
  },
}

export const KNOWLEDGE_ASSET_UNSET_META: KnowledgeAssetStatusMeta = {
  label: '未审核',
  tone: 'neutral',
  hint: '服务端未给出审核状态，按未审核展示，不假定为草稿。',
}

/** 审核动作：label 给按钮文案，target 是服务端最终写入的状态。 */
export interface KnowledgeAssetDecisionMeta {
  label: string
  variant: 'primary' | 'secondary' | 'danger'
  target: KnowledgeAssetReviewStatus
}

export const KNOWLEDGE_ASSET_DECISION_META: Record<
  KnowledgeAssetDecision,
  KnowledgeAssetDecisionMeta
> = {
  review: { label: '提交复核', variant: 'primary', target: 'review' },
  approve: { label: '审核通过', variant: 'primary', target: 'approved' },
  publish: { label: '发布', variant: 'primary', target: 'published' },
  reject: { label: '驳回', variant: 'danger', target: 'rejected' },
  archive: { label: '归档', variant: 'secondary', target: 'archived' },
}

export const KNOWLEDGE_ASSET_DECISIONS = [
  'review',
  'approve',
  'publish',
  'reject',
  'archive',
] as const satisfies readonly KnowledgeAssetDecision[]

/**
 * 状态机镜像后端 `knowledge_assets.VALID_TRANSITIONS`：
 * draft → review/rejected；review → approved/rejected/draft；
 * approved → published/rejected；published → archived；
 * rejected/archived → draft。
 */
export const KNOWLEDGE_ASSET_TRANSITIONS: Record<
  KnowledgeAssetReviewStatus,
  readonly KnowledgeAssetReviewStatus[]
> = {
  draft: ['review', 'rejected'],
  review: ['approved', 'rejected', 'draft'],
  approved: ['published', 'rejected'],
  published: ['archived'],
  rejected: ['draft'],
  archived: ['draft'],
}

const KNOWN_STATUSES = new Set<string>(Object.keys(KNOWLEDGE_ASSET_STATUS_META))

/** 已知状态返回枚举值；缺失或未知值返回 null（不默认值，避免伪造状态）。 */
export function knowledgeAssetStatus(value?: string | null): KnowledgeAssetReviewStatus | null {
  const text = (value || '').trim().toLowerCase()
  return text && KNOWN_STATUSES.has(text) ? (text as KnowledgeAssetReviewStatus) : null
}

export function knowledgeAssetStatusMeta(value?: string | null): KnowledgeAssetStatusMeta {
  const status = knowledgeAssetStatus(value)
  if (status) return { ...KNOWLEDGE_ASSET_STATUS_META[status] }
  const text = (value || '').trim()
  return text
    ? { label: text, tone: 'neutral', hint: '未知审核状态，以服务端返回为准。' }
    : { ...KNOWLEDGE_ASSET_UNSET_META }
}

/**
 * 后端把 null 当草稿处理状态流转；界面标签仍显示「未审核」，
 * 因此这里单独把 null 映射到 draft 的下一状态集合。
 */
export function allowedNextStatuses(
  value?: string | null,
): readonly KnowledgeAssetReviewStatus[] {
  return KNOWLEDGE_ASSET_TRANSITIONS[knowledgeAssetStatus(value) ?? 'draft']
}

/** 非法流转返回 false，供按钮 disabled 使用；未传资产时不允许任何动作。 */
export function isDecisionAllowed(
  value: string | null | undefined,
  decision: KnowledgeAssetDecision | undefined,
): boolean {
  const target = decision ? KNOWLEDGE_ASSET_DECISION_META[decision]?.target : undefined
  return Boolean(target && allowedNextStatuses(value).includes(target))
}

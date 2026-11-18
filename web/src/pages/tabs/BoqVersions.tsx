import { useEffect, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronRight,
  GitCompare,
  History,
  ListOrdered,
  Plus,
  RotateCcw,
  Save,
  Send,
  ShieldCheck,
  Trash2,
  Undo2,
  X,
} from 'lucide-react'
import type {
  ApprovalChainEntry,
  ApprovalStage,
  BoqPayload,
  BoqQuoteVersion,
  BoqSummary,
  BoqVersionDiff,
  Member,
  QuoteApprovalRecord,
  QuoteVersionStatus,
} from '../../lib/types'
import {
  ApiError,
  createBoqVersion,
  deleteBoqVersion,
  getBoqVersionDiff,
  getMembers,
  listBoqVersions,
  messageOf,
  restoreBoqVersion,
  reviewBoqVersion,
  submitBoqVersion,
  unapproveBoqVersion,
  updateProject,
} from '../../lib/api'
import { className, formatDate, formatMoney, memberRoleMeta, type Tone } from '../../lib/format'
import { useQuery } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button, IconButton } from '../../components/Button'
import { Modal } from '../../components/Modal'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'

const NAME_MAX = 80
const NOTE_MAX = 200
/** 审批链最多 5 级，与后端 PATCH 校验一致。 */
const CHAIN_MAX = 5
/** 每级最多 5 名审批人，与后端 PATCH 校验一致。 */
const APPROVERS_MAX = 5

/** 审批人名单展示：姓名快照优先，缺失时回退用户 id。 */
function namesOf(entries: ApprovalChainEntry[]): string {
  return entries.map((entry) => entry.name || entry.id).join('、')
}

/** 一级的通过模式标签。 */
function modeLabel(mode: ApprovalStage['mode']): string {
  return mode === 'any' ? '或签' : '会签'
}

const STATUS_META: Record<QuoteVersionStatus, { label: string; tone: Tone }> = {
  draft: { label: '草稿', tone: 'neutral' },
  submitted: { label: '待审', tone: 'info' },
  approved: { label: '已定稿', tone: 'success' },
  rejected: { label: '已驳回', tone: 'danger' },
}

/** 优先展示含税合计；旧版本快照只有原价合计。 */
function versionTotal(version: BoqQuoteVersion): { label: string; value: number } {
  if (
    typeof version.summary.total_with_tax === 'number' &&
    Number.isFinite(version.summary.total_with_tax)
  ) {
    return { label: '含税合计', value: version.summary.total_with_tax }
  }
  return { label: '合计', value: version.summary.total_amount }
}

function statusMeta(status: QuoteVersionStatus) {
  return STATUS_META[status] ?? STATUS_META.draft
}

/** 待审/已审版本的流转痕迹，作为版本名下方的小字。 */
function VersionAudit({ version }: { version: BoqQuoteVersion }) {
  if (version.status === 'draft') return null
  const reviewed = version.status === 'approved' || version.status === 'rejected'
  return (
    <>
      {version.submitted_by && (
        <span>
          提交：{version.submitted_by} · {formatDate(version.submitted_at)}
        </span>
      )}
      {version.submit_note && <span className="boq-version-audit-note">说明：{version.submit_note}</span>}
      {reviewed && version.reviewed_by && (
        <span>
          审批：{version.reviewed_by} · {formatDate(version.reviewed_at)}
        </span>
      )}
      {reviewed && version.review_note && (
        <span className="boq-version-audit-note">意见：{version.review_note}</span>
      )}
    </>
  )
}

/** 逐级审批时间线：通过为绿、驳回为红，展示各级审批人、决定、意见与时间。 */
function ApprovalTimeline({ approvals }: { approvals: QuoteApprovalRecord[] }) {
  if (approvals.length === 0) return null
  return (
    <ol className="boq-approval-timeline" aria-label="逐级审批记录">
      {approvals.map((record, index) => (
        <li
          key={`${record.stage}:${record.user_id}:${index}`}
          className={record.decision === 'approve' ? 'is-approve' : 'is-reject'}
        >
          <span className="boq-approval-step">第 {record.stage + 1} 级</span>
          <span className="boq-approval-name">{record.name || record.user_id}</span>
          <span className="boq-approval-decision">
            {record.decision === 'approve' ? '通过' : '驳回'}
          </span>
          {record.at && <span>{formatDate(record.at)}</span>}
          {record.note && <span className="boq-approval-note">意见：{record.note}</span>}
        </li>
      ))}
    </ol>
  )
}

/** 级内添加审批人：候选为尚未加入本级的负责人 / 可编辑成员。 */
function StageApproverAdd({
  candidates,
  disabled,
  onAdd,
}: {
  candidates: Member[]
  disabled: boolean
  onAdd: (userId: string) => void
}) {
  const [candidateId, setCandidateId] = useState('')
  const effectiveCandidate = candidates.some((member) => member.user_id === candidateId)
    ? candidateId
    : (candidates[0]?.user_id ?? '')
  const blocked = disabled || candidates.length === 0
  return (
    <div className="boq-chain-add">
      <select
        value={effectiveCandidate}
        disabled={blocked}
        onChange={(event) => setCandidateId(event.target.value)}
        aria-label="选择要加入本级的成员"
      >
        {candidates.length === 0 ? (
          <option value="">没有可添加的成员</option>
        ) : (
          candidates.map((member) => (
            <option key={member.user_id} value={member.user_id}>
              {member.display_name || member.username}（{memberRoleMeta(member.role).label}）
            </option>
          ))
        )}
      </select>
      <Button
        size="sm"
        variant="secondary"
        icon={<Plus size={13} />}
        disabled={blocked}
        title={
          candidates.length === 0
            ? '没有可添加的成员'
            : disabled
              ? `每级最多 ${APPROVERS_MAX} 名审批人`
              : '把所选成员加入本级审批'
        }
        onClick={() => {
          if (effectiveCandidate) onAdd(effectiveCandidate)
          setCandidateId('')
        }}
      >
        添加审批人
      </Button>
    </div>
  )
}

/**
 * 审批链设置弹窗（仅负责人可见）：≤5 级、每级 1–5 名审批人（候选为负责人与可编辑成员），
 * 每级可选会签（全部通过）/ 或签（任一通过）；阶段支持上移 / 下移 / 移除。
 * 保存走 PATCH approval_chain（v2 阶段格式），空链即回到负责人单级审批。
 */
function ApprovalChainModal({
  projectRef,
  chain,
  onClose,
  onSaved,
}: {
  projectRef: string
  chain: ApprovalStage[]
  onClose: () => void
  onSaved: () => void
}) {
  const toast = useToast()
  const membersQuery = useQuery(`members:${projectRef}`, () => getMembers(projectRef))
  const [draft, setDraft] = useState<ApprovalStage[]>(chain)
  const [saving, setSaving] = useState(false)

  const candidates = (membersQuery.data ?? []).filter(
    (member) => member.role === 'owner' || member.role === 'editor',
  )
  const membersReady = !membersQuery.loading && !(membersQuery.error && !membersQuery.data)
  const full = draft.length >= CHAIN_MAX

  const updateStage = (index: number, patch: Partial<ApprovalStage>) => {
    setDraft(draft.map((stage, i) => (i === index ? { ...stage, ...patch } : stage)))
  }

  const addStage = () => {
    if (full) return
    setDraft([...draft, { approvers: [], mode: 'all' }])
  }

  const moveStage = (index: number, delta: number) => {
    const target = index + delta
    if (target < 0 || target >= draft.length) return
    const next = [...draft]
    const [stage] = next.splice(index, 1)
    next.splice(target, 0, stage)
    setDraft(next)
  }

  const addApprover = (index: number, userId: string) => {
    const stage = draft[index]
    const member = candidates.find((item) => item.user_id === userId)
    if (!stage || !member || stage.approvers.length >= APPROVERS_MAX) return
    if (stage.approvers.some((entry) => entry.id === userId)) return
    updateStage(index, {
      approvers: [
        ...stage.approvers,
        { id: member.user_id, name: member.display_name || member.username },
      ],
    })
  }

  const save = async () => {
    if (saving) return
    if (draft.some((stage) => stage.approvers.length === 0)) {
      toast.warning('审批链未填写完整', '每级至少需要 1 名审批人；请添加审批人或移除空级。')
      return
    }
    setSaving(true)
    try {
      await updateProject(projectRef, {
        approval_chain: draft.map((stage) => ({
          approvers: stage.approvers.map((entry) => entry.id),
          mode: stage.mode,
        })),
      })
      onSaved()
      toast.success(
        draft.length > 0 ? '审批链已保存' : '审批链已清空',
        draft.length > 0
          ? `报价提交后将按 ${draft.length} 级逐级审批，每级按会签/或签规则通过后才定稿。`
          : '已恢复为负责人单级审批。',
      )
      onClose()
    } catch (error) {
      toast.error('保存审批链失败', messageOf(error))
      setSaving(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="报价审批链设置"
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button variant="primary" loading={saving} onClick={() => void save()}>
            保存审批链
          </Button>
        </>
      }
    >
      <div className="boq-chain-modal">
        <p className="boq-chain-hint">
          提交报价后按下列顺序逐级审批：会签级需全部审批人通过，或签级任一通过即进入下一级；
          任一级驳回即退回修改，重新提交从第 1 级重走。最多 {CHAIN_MAX} 级、每级最多{' '}
          {APPROVERS_MAX} 人，候选为负责人与可编辑成员。未设置（保存空链）时为负责人单级审批。
        </p>

        {draft.length === 0 ? (
          <p className="boq-chain-empty">尚未设置审批链：当前为负责人单级审批。</p>
        ) : (
          <ol className="boq-chain-list" aria-label="审批链顺序">
            {draft.map((stage, index) => (
              <li key={index} className="boq-chain-item">
                <div className="boq-chain-stage-head">
                  <span className="boq-chain-stage">第 {index + 1} 级</span>
                  <select
                    className="boq-chain-mode"
                    value={stage.mode}
                    disabled={saving}
                    onChange={(event) =>
                      updateStage(index, { mode: event.target.value === 'any' ? 'any' : 'all' })
                    }
                    aria-label={`第 ${index + 1} 级审批模式`}
                  >
                    <option value="all">会签（全部通过）</option>
                    <option value="any">或签（任一通过）</option>
                  </select>
                  <span className="boq-chain-actions">
                    <IconButton
                      label={`上移第 ${index + 1} 级`}
                      disabled={saving || index === 0}
                      onClick={() => moveStage(index, -1)}
                    >
                      <ArrowUp size={13} />
                    </IconButton>
                    <IconButton
                      label={`下移第 ${index + 1} 级`}
                      disabled={saving || index === draft.length - 1}
                      onClick={() => moveStage(index, 1)}
                    >
                      <ArrowDown size={13} />
                    </IconButton>
                    <IconButton
                      label={`移除第 ${index + 1} 级`}
                      tone="danger"
                      disabled={saving}
                      onClick={() => setDraft(draft.filter((_, i) => i !== index))}
                    >
                      <X size={13} />
                    </IconButton>
                  </span>
                </div>
                {stage.approvers.length === 0 ? (
                  <p className="boq-chain-warn boq-chain-empty-stage">
                    尚未添加审批人：保存前每级至少 1 人。
                  </p>
                ) : (
                  <ul className="boq-chain-approvers" aria-label={`第 ${index + 1} 级审批人`}>
                    {stage.approvers.map((entry) => {
                      const member = candidates.find((item) => item.user_id === entry.id)
                      return (
                        <li key={entry.id} className="boq-chain-approver">
                          <span className="boq-chain-name" title={entry.name || entry.id}>
                            {entry.name || entry.id}
                          </span>
                          {member ? (
                            <span className="boq-chain-role">
                              {memberRoleMeta(member.role).label}
                            </span>
                          ) : (
                            <span className="boq-chain-warn">已非可写成员</span>
                          )}
                          <IconButton
                            label={`从第 ${index + 1} 级移除「${entry.name || entry.id}」`}
                            tone="danger"
                            disabled={saving}
                            onClick={() =>
                              updateStage(index, {
                                approvers: stage.approvers.filter(
                                  (item) => item.id !== entry.id,
                                ),
                              })
                            }
                          >
                            <X size={13} />
                          </IconButton>
                        </li>
                      )
                    })}
                  </ul>
                )}
                {membersReady && (
                  <StageApproverAdd
                    candidates={candidates.filter(
                      (member) => !stage.approvers.some((entry) => entry.id === member.user_id),
                    )}
                    disabled={saving || stage.approvers.length >= APPROVERS_MAX}
                    onAdd={(userId) => addApprover(index, userId)}
                  />
                )}
              </li>
            ))}
          </ol>
        )}

        <div>
          <Button
            size="sm"
            variant="secondary"
            icon={<Plus size={13} />}
            disabled={saving || full}
            title={full ? `审批链最多 ${CHAIN_MAX} 级` : '追加一个审批级（默认会签）'}
            onClick={addStage}
          >
            添加审批级
          </Button>
        </div>

        {membersQuery.loading ? (
          <p className="price-policy-message">正在读取项目成员…</p>
        ) : membersQuery.error && !membersQuery.data ? (
          <div className="price-policy-message">
            <span>暂时读不到项目成员，无法添加审批人。</span>
            <button type="button" className="text-btn" onClick={membersQuery.refresh}>
              重试
            </button>
          </div>
        ) : null}

        <p className="boq-chain-hint boq-chain-hint--quiet">
          审批人姓名在保存时快照，之后改名不会追更；移除成员不会自动改动已保存的审批链。
        </p>
      </div>
    </Modal>
  )
}

// ------------------------------------------------ 并排审阅的汇总与行级差异

/** 金额类汇总字段的展示顺序；未知的金额字段按后端实际键追加在后。 */
const SUMMARY_ORDER = [
  'total_amount',
  'adjusted_total',
  'tax_amount',
  'total_with_tax',
  'cny_total_with_tax',
] as const

const SUMMARY_LABELS: Record<string, string> = {
  total_amount: '总价',
  adjusted_total: '调价后合计',
  tax_amount: '税额',
  total_with_tax: '含税合计',
  cny_total_with_tax: '人民币含税合计',
}

interface SummaryField {
  key: string
  label: string
  value: number
}

/**
 * 只挑汇总里的金额字段展示，各栏顺序一致，便于左右对齐比较。
 * 后端新增金额字段时无需改前端：未知但名字像金额的键会追加显示。
 */
function summaryFields(summary: BoqSummary): SummaryField[] {
  const record = summary as unknown as Record<string, unknown>
  const isMoney = (key: string) =>
    typeof record[key] === 'number' && Number.isFinite(record[key] as number)
  const known = SUMMARY_ORDER.filter(isMoney)
  const extra = Object.keys(record).filter(
    (key) =>
      isMoney(key) &&
      !known.includes(key as (typeof SUMMARY_ORDER)[number]) &&
      /(amount|total|tax)/i.test(key),
  )
  return [...known, ...extra].map((key) => ({
    key,
    label: SUMMARY_LABELS[key] || key,
    value: record[key] as number,
  }))
}

interface ReviewRow {
  key: string
  name: string
  tone: 'added' | 'changed' | 'removed'
  leftPrice: number | null
  leftAmount: number | null
  rightPrice: number | null
  rightAmount: number | null
}

function reviewRows(diff: BoqVersionDiff): ReviewRow[] {
  const added: ReviewRow[] = diff.added.map((item) => ({
    key: item.key,
    name: item.name || item.key,
    tone: 'added',
    leftPrice: item.unit_price,
    leftAmount: item.amount,
    rightPrice: null,
    rightAmount: null,
  }))
  const changed: ReviewRow[] = diff.changed.map((item) => ({
    key: item.key,
    name: item.name || item.key,
    tone: 'changed',
    leftPrice: item.from,
    leftAmount: item.from_amount,
    rightPrice: item.to,
    rightAmount: item.to_amount,
  }))
  const removed: ReviewRow[] = diff.removed.map((item) => ({
    key: item.key,
    name: item.name || item.key,
    tone: 'removed',
    leftPrice: null,
    leftAmount: null,
    rightPrice: item.unit_price,
    rightAmount: item.amount,
  }))
  return [...added, ...changed, ...removed]
}

function priceDiffLabel(row: ReviewRow): string {
  if (row.tone === 'added') return '仅左侧'
  if (row.tone === 'removed') return '仅右侧'
  if (typeof row.leftPrice === 'number' && typeof row.rightPrice === 'number') {
    const delta = row.rightPrice - row.leftPrice
    if (delta === 0) return '一致'
    const sign = delta > 0 ? '+' : '−'
    return `${sign}¥${formatMoney(Math.abs(delta))}`
  }
  return '变动'
}

function money(value: number | null): string {
  return value === null || value === undefined ? '—' : `¥${formatMoney(value)}`
}

function CompareSide({
  title,
  status,
  summary,
  count,
}: {
  title: string
  status?: QuoteVersionStatus
  summary: BoqSummary | null
  count: number
}) {
  const meta = status ? statusMeta(status) : null
  const fields = summary ? summaryFields(summary) : []
  return (
    <section className="boq-review-side">
      <header className="boq-review-side-head">
        <strong>{title}</strong>
        {meta && <Badge tone={meta.tone}>{meta.label}</Badge>}
      </header>
      <dl className="boq-review-fields">
        {fields.map((field) => (
          <div key={field.key} className="boq-review-field">
            <dt>{field.label}</dt>
            <dd className="mono">¥{formatMoney(field.value)}</dd>
          </div>
        ))}
        <div className="boq-review-field">
          <dt>已报价</dt>
          <dd>{count} 条</dd>
        </div>
      </dl>
    </section>
  )
}

export function QuoteVersionsPanel({
  projectRef,
  packageId,
  packageName,
  canWrite,
  canReview,
  approvalChain,
  isOwner,
  currentUserId,
  isSuperAdmin,
  onChainSaved,
  onRestored,
}: {
  projectRef: string
  packageId: string | null
  packageName?: string
  canWrite: boolean
  canReview: boolean
  /** 项目审批链（v2 阶段列表，有序）；空数组 = 负责人单级审批。 */
  approvalChain: ApprovalStage[]
  /** 仅负责人可设置审批链。 */
  isOwner: boolean
  currentUserId: string
  isSuperAdmin: boolean
  onChainSaved: () => void
  onRestored: (payload: BoqPayload) => void
}) {
  const toast = useToast()
  const confirm = useConfirm()

  // ------------------------------------------------------------ 审批链与逐级审批
  const [chainOpen, setChainOpen] = useState(false)
  const chainLength = approvalChain.length
  const chainActive = chainLength > 0

  /** 待审级序号（0 起）；链被缩短等异常情况下夹取到最后一级，避免越界。 */
  const stageOf = (version: BoqQuoteVersion) =>
    Math.min(Math.max(version.approval_stage, 0), Math.max(chainLength - 1, 0))

  /** 某级已通过的审批记录数（会签完成度）。 */
  const approvedCountAt = (version: BoqQuoteVersion, stageIndex: number) =>
    version.approvals.filter(
      (record) => record.stage === stageIndex && record.decision === 'approve',
    ).length

  /** 当前级待批审批人：会签 = 本级尚未通过者，或签 = 全部审批人。 */
  const pendingApprovers = (version: BoqQuoteVersion): ApprovalChainEntry[] => {
    const stageIndex = stageOf(version)
    const stage = approvalChain[stageIndex]
    if (!stage) return []
    if (stage.mode === 'any') return stage.approvers
    const approvedIds = new Set(
      version.approvals
        .filter((record) => record.stage === stageIndex && record.decision === 'approve')
        .map((record) => record.user_id),
    )
    return stage.approvers.filter((entry) => !approvedIds.has(entry.id))
  }

  /** 本次通过后是否直接定稿：末级且（或签任一即可，或会签只差最后一条通过记录）。 */
  const willFinalize = (version: BoqQuoteVersion) => {
    if (!chainActive) return true
    const stageIndex = stageOf(version)
    const stage = approvalChain[stageIndex]
    if (!stage || stageIndex + 1 < chainLength) return false
    if (stage.mode === 'any') return true
    return stage.approvers.length - approvedCountAt(version, stageIndex) <= 1
  }

  /**
   * 定稿/驳回可见性：链非空 → 当前级审批人（会签级我已批过则隐藏，后端会 403
   * 「您已完成本级审批」）或超管；链空 → 沿用 canReview。
   */
  const canReviewVersion = (version: BoqQuoteVersion) => {
    if (!chainActive) return canReview
    if (isSuperAdmin) return true
    if (!currentUserId) return false
    const stageIndex = stageOf(version)
    const stage = approvalChain[stageIndex]
    if (!stage?.approvers.some((entry) => entry.id === currentUserId)) return false
    if (stage.mode === 'all') {
      return !version.approvals.some(
        (record) =>
          record.stage === stageIndex &&
          record.decision === 'approve' &&
          record.user_id === currentUserId,
      )
    }
    return true
  }

  const versionsQuery = useQuery(
    `boq-versions:${projectRef}:${packageId ?? 'single'}`,
    () => listBoqVersions(projectRef, packageId),
  )
  const versions = versionsQuery.data ?? []

  /** 审批链变更后同步项目数据与版本列表（stage 语义随链变化）。 */
  const handleChainSaved = () => {
    onChainSaved()
    versionsQuery.refresh()
  }

  const [name, setName] = useState('')
  const [nameTouched, setNameTouched] = useState(false)
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)

  // 切换包件等于切换到另一组版本：清空尚未保存的名称与备注。
  useEffect(() => {
    setName('')
    setNameTouched(false)
    setNote('')
    setBusyId(null)
  }, [packageId, projectRef])

  const defaultName = `版本 ${versions.length + 1}`
  const effectiveName = nameTouched ? name : defaultName
  const busy = saving || busyId !== null

  // ------------------------------------------------------------ 并排审阅状态

  const [compare, setCompare] = useState<{ a: string; b: string } | null>(null)
  const [diffData, setDiffData] = useState<BoqVersionDiff | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)
  const [diffError, setDiffError] = useState<string | null>(null)

  const loadDiff = async (versionId: string, other: string) => {
    setDiffLoading(true)
    setDiffError(null)
    try {
      const result = await getBoqVersionDiff(
        projectRef,
        versionId,
        other === 'current' ? undefined : other,
      )
      setDiffData(result)
    } catch (error) {
      setDiffData(null)
      setDiffError(messageOf(error))
    } finally {
      setDiffLoading(false)
    }
  }

  /** 打开并排审阅；默认左侧为所选版本、右侧为当前清单。 */
  const openCompare = (versionId?: string) => {
    const fallback = versionId || versions[0]?.id
    if (!fallback) return
    const other = 'current'
    setCompare({ a: fallback, b: other })
    setDiffData(null)
    void loadDiff(fallback, other)
  }

  const closeCompare = () => {
    setCompare(null)
    setDiffData(null)
    setDiffError(null)
    setDiffLoading(false)
  }

  const changeCompare = (next: { a: string; b: string }) => {
    const b = next.b === next.a ? 'current' : next.b
    setCompare({ a: next.a, b })
    void loadDiff(next.a, b)
  }

  const versionA = compare ? versions.find((item) => item.id === compare.a) ?? null : null
  const versionB =
    compare && compare.b !== 'current'
      ? versions.find((item) => item.id === compare.b) ?? null
      : null
  const rightLabel = compare?.b === 'current' ? '当前清单' : versionB?.name || '对比版本'

  /** 提交/审批后刷新列表；本地先落返回的版本，再拉取整组状态（定稿会联动旧版本）。 */
  const syncAfterChange = (updated: BoqQuoteVersion) => {
    versionsQuery.setData((current) =>
      (current ?? []).map((item) => (item.id === updated.id ? updated : item)),
    )
    versionsQuery.refresh()
    if (compare) void loadDiff(compare.a, compare.b)
  }

  // ------------------------------------------------------------ 保存 / 恢复 / 删除

  const save = async () => {
    if (!canWrite || busy) return
    const trimmed = effectiveName.trim()
    if (!trimmed) {
      toast.warning('请填写版本名称', '例如「投标报价 V1」，便于之后恢复。')
      return
    }
    setSaving(true)
    try {
      const created = await createBoqVersion(projectRef, {
        name: trimmed,
        note,
        package_id: packageId,
      })
      versionsQuery.setData((current) => [created, ...(current ?? [])])
      setName('')
      setNameTouched(false)
      setNote('')
      const scope = packageName ? `包件「${packageName}」` : '当前清单'
      toast.success('报价版本已保存', `${scope}已记录「${created.name}」的单价快照。`)
    } catch (error) {
      toast.error('保存报价版本失败', messageOf(error))
    } finally {
      setSaving(false)
    }
  }

  const restore = async (version: BoqQuoteVersion) => {
    if (!canWrite || busy) return
    const accepted = await confirm({
      title: `恢复到「${version.name}」？`,
      description:
        '将把该版本保存的单价写回当前清单；当前尚未保存为版本的报价改动会被覆盖。',
      note: '台账材料与清单对齐结果不受影响，恢复后可以再保存一个新版本。',
      confirmLabel: '恢复版本',
      tone: 'warning',
    })
    if (!accepted) return
    setBusyId(version.id)
    try {
      const payload = await restoreBoqVersion(projectRef, version.id)
      onRestored(payload)
      toast.success('已恢复报价版本', `「${version.name}」的单价已写回当前清单。`)
    } catch (error) {
      toast.error('恢复报价版本失败', messageOf(error))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (version: BoqQuoteVersion) => {
    if (!canWrite || busy) return
    const accepted = await confirm({
      title: `删除版本「${version.name}」？`,
      description: '删除后该版本的单价快照将无法恢复，当前清单不受影响。',
      confirmLabel: '删除版本',
      tone: 'danger',
    })
    if (!accepted) return
    setBusyId(version.id)
    try {
      await deleteBoqVersion(projectRef, version.id)
      versionsQuery.setData((current) =>
        (current ?? []).filter((item) => item.id !== version.id),
      )
      if (compare && (compare.a === version.id || compare.b === version.id)) closeCompare()
      toast.info('版本已删除', `「${version.name}」已从报价版本列表移除。`)
    } catch (error) {
      toast.error('删除版本失败', messageOf(error))
    } finally {
      setBusyId(null)
    }
  }

  // ------------------------------------------------------------ 提交 / 审批弹窗

  type ApprovalDraft = {
    kind: 'submit' | 'approve' | 'reject'
    version: BoqQuoteVersion
  }

  const [approval, setApproval] = useState<ApprovalDraft | null>(null)
  const [approvalNote, setApprovalNote] = useState('')
  const [approvalBusy, setApprovalBusy] = useState(false)

  const openApproval = (kind: ApprovalDraft['kind'], version: BoqQuoteVersion) => {
    setApproval({ kind, version })
    setApprovalNote('')
  }

  const closeApproval = () => {
    setApproval(null)
    setApprovalNote('')
    setApprovalBusy(false)
  }

  const approvalStage = approval ? stageOf(approval.version) : 0
  const approvalIsFinalStage = approvalStage + 1 >= chainLength
  const approvalStageInfo = approval ? approvalChain[approvalStage] : undefined
  const approvalWillFinalize = approval ? willFinalize(approval.version) : true
  /** 会签级除我之外还差几人通过（超管代批时即全部待批者）。 */
  const approvalRemaining =
    approval && approvalStageInfo?.mode === 'all'
      ? approvalStageInfo.approvers.length - approvedCountAt(approval.version, approvalStage)
      : 0
  const approvalOtherNames = approval
    ? namesOf(pendingApprovers(approval.version).filter((entry) => entry.id !== currentUserId))
    : ''
  const approvalCopy = approval
    ? approval.kind === 'submit'
      ? {
          title: `提交「${approval.version.name}」审批？`,
          hint: chainActive
            ? `提交后按审批链逐级审批（共 ${chainLength} 级），每级按会签/或签规则通过后才定稿。可附上报价口径说明。`
            : '提交后版本进入待审状态，审批通过前不能再提交。可附上报价口径说明。',
          field: '提交说明',
          confirm: '提交审批',
        }
      : approval.kind === 'approve'
        ? {
            title: chainActive && !approvalWillFinalize
              ? `通过「${approval.version.name}」的第 ${approvalStage + 1} 级审批？`
              : `定稿「${approval.version.name}」？`,
            hint: chainActive
              ? approvalWillFinalize
                ? '这是最后一级审批：通过后该版本定稿为本包件的现行报价；同一包件的旧定稿版本会自动退回草稿。'
                : approvalIsFinalStage
                  ? `这是最后一级（会签）：你通过后还需「${approvalOtherNames || '其他审批人'}」通过，版本才定稿为本包件现行报价。`
                  : approvalStageInfo?.mode === 'all' && approvalRemaining > 1
                    ? `本级为会签：你通过后还需「${approvalOtherNames || '其他审批人'}」通过，才进入第 ${approvalStage + 2}/${chainLength} 级。`
                    : `通过后进入第 ${approvalStage + 2}/${chainLength} 级，由「${namesOf(approvalChain[approvalStage + 1]?.approvers ?? []) || '下一级审批人'}」继续审批；全部通过后才定稿。`
              : '定稿后该版本成为本包件的现行报价；同一包件的旧定稿版本会自动退回草稿。',
            field: '审批意见',
            confirm: chainActive && !approvalWillFinalize ? '通过本级' : '定稿',
          }
        : {
            title: `驳回「${approval.version.name}」？`,
            hint: chainActive
              ? '任一级驳回都会终止本次审批，版本退回可修改状态；重新提交后从第 1 级重走审批链。'
              : '驳回后版本退回可修改状态，报价人员可调整后再提交。',
            field: '驳回原因',
            confirm: '驳回',
          }
    : null

  const runApproval = async () => {
    if (!approval || approvalBusy) return
    setApprovalBusy(true)
    try {
      const updated =
        approval.kind === 'submit'
          ? await submitBoqVersion(projectRef, approval.version.id, approvalNote)
          : await reviewBoqVersion(
              projectRef,
              approval.version.id,
              approval.kind,
              approvalNote,
            )
      syncAfterChange(updated)
      if (approval.kind === 'approve' && updated.status === 'submitted') {
        // 通过但版本仍待审：会签级可能仍停在原地等其他审批人，否则进入下一级。
        const nextStage = Math.min(updated.approval_stage, Math.max(chainLength - 1, 0))
        const nextNames = namesOf(pendingApprovers(updated)) || '当前级审批人'
        if (nextStage === approvalStage && approvalChain[nextStage]?.mode === 'all') {
          toast.success(
            '本级审批已通过',
            `「${approval.version.name}」第 ${nextStage + 1}/${chainLength} 级会签进行中，还需「${nextNames}」通过。`,
          )
        } else {
          toast.success(
            '本级审批已通过',
            `「${approval.version.name}」进入第 ${nextStage + 1}/${chainLength} 级，等待「${nextNames}」审批。`,
          )
        }
      } else {
        toast.success(
          approval.kind === 'submit'
            ? '已提交审批'
            : approval.kind === 'approve'
              ? '版本已定稿'
              : '版本已驳回',
          `「${approval.version.name}」${
            approval.kind === 'submit'
              ? chainActive
                ? `已按审批链进入第 1/${chainLength} 级审批。`
                : '等待负责人审批。'
              : approval.kind === 'approve'
                ? '已生效为本包件现行报价。'
                : '已退回修改。'
          }`,
        )
      }
      closeApproval()
    } catch (error) {
      // 403 = 非当前级审批人或会签级已批过（detail 原样提示）；级次可能已被他人推进，刷新列表。
      if (error instanceof ApiError && error.status === 403) versionsQuery.refresh()
      toast.error(
        approval.kind === 'submit'
          ? '提交审批失败'
          : approval.kind === 'approve'
            ? '审批失败'
            : '驳回失败',
        messageOf(error),
      )
      setApprovalBusy(false)
    }
  }

  const unapprove = async (version: BoqQuoteVersion) => {
    if (!canWrite || busy) return
    const accepted = await confirm({
      title: `撤销「${version.name}」的定稿？`,
      description: '撤销后版本退回草稿，可继续修改单价或重新提交审批。',
      confirmLabel: '撤销定稿',
      tone: 'warning',
    })
    if (!accepted) return
    setBusyId(version.id)
    try {
      const updated = await unapproveBoqVersion(projectRef, version.id)
      syncAfterChange(updated)
      toast.info('已撤销定稿', `「${version.name}」已退回草稿。`)
    } catch (error) {
      toast.error('撤销定稿失败', messageOf(error))
    } finally {
      setBusyId(null)
    }
  }

  const pendingCount = versions.filter((item) => item.status === 'submitted').length
  const approvedCount = versions.filter((item) => item.status === 'approved').length

  return (
    <details className="price-policy boq-versions">
      <summary>
        <span className="price-policy-title">
          <History size={13} aria-hidden />
          <strong>报价版本</strong>
          <span className="price-policy-summary">
            {versionsQuery.loading
              ? '正在读取版本…'
              : versions.length > 0
                ? `${versions.length} 个版本${pendingCount ? ` · ${pendingCount} 个待审` : ''}${approvedCount ? ` · ${approvedCount} 个已定稿` : ''}`
                : '保存当前单价快照'}
          </span>
        </span>
        <span className="price-policy-caret" aria-hidden>
          <ChevronRight size={14} />
        </span>
      </summary>

      <div className="price-policy-body">
        <div className="boq-version-save">
          <label className="price-policy-field">
            <span>版本名称</span>
            <input
              className="input input--sm"
              value={effectiveName}
              maxLength={NAME_MAX}
              disabled={!canWrite || saving}
              onChange={(event) => {
                setName(event.target.value)
                setNameTouched(true)
              }}
              aria-label="版本名称"
            />
            <small>默认「版本 N」，最多 {NAME_MAX} 字</small>
          </label>
          <label className="price-policy-field">
            <span>备注</span>
            <input
              className="input input--sm"
              value={note}
              maxLength={NOTE_MAX}
              placeholder="可选，例如「含税报价」「下浮 5%」"
              disabled={!canWrite || saving}
              onChange={(event) => setNote(event.target.value)}
              aria-label="版本备注（可选）"
            />
            <small>可选，最多 {NOTE_MAX} 字</small>
          </label>
          <Button
            size="sm"
            variant="secondary"
            icon={<Save size={13} />}
            loading={saving}
            disabled={!canWrite || busy}
            title={
              !canWrite ? '只读成员不能保存版本' : '记录当前包件的单价与汇总快照'
            }
            onClick={() => void save()}
          >
            保存当前为版本
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon={<GitCompare size={13} />}
            disabled={versions.length === 0}
            title={
              versions.length === 0
                ? '先保存至少一个版本才能并排审阅'
                : '左右两栏对比两个版本（或当前清单）的汇总与逐行单价'
            }
            onClick={() => openCompare()}
          >
            并排对比
          </Button>
          {isOwner && (
            <Button
              size="sm"
              variant="ghost"
              icon={<ListOrdered size={13} />}
              title="设置报价审批链：逐级会签/或签，最多 5 级、每级 5 人；未设置时为负责人单级审批"
              onClick={() => setChainOpen(true)}
            >
              审批链{chainActive ? `（${chainLength} 级）` : ''}
            </Button>
          )}
        </div>

        {chainActive && (
          <p className="boq-chain-summary">
            <ListOrdered size={12} aria-hidden />
            <span>
              审批链：
              {approvalChain.map((stage, index) => (
                <span key={index} className="boq-chain-summary-stage">
                  {index > 0 && ' → '}
                  {namesOf(stage.approvers)}
                  <em className="boq-chain-mode-tag">（{modeLabel(stage.mode)}）</em>
                </span>
              ))}
              （共 {chainLength} 级，逐级通过后才定稿）
            </span>
          </p>
        )}

        {versionsQuery.loading ? (
          <p className="price-policy-message">正在读取报价版本…</p>
        ) : versionsQuery.error && !versionsQuery.data ? (
          <div className="price-policy-message">
            <span>暂时读不到报价版本，可能是服务版本较旧：当前清单仍可正常报价。</span>
            <button type="button" className="text-btn" onClick={versionsQuery.refresh}>
              重试
            </button>
          </div>
        ) : versions.length === 0 ? (
          <p className="price-policy-message">
            还没有保存过版本。保存后会记录当前全部单价与汇总，可随时恢复。
          </p>
        ) : (
          <ul className="boq-version-list">
            {versions.map((version) => {
              const total = versionTotal(version)
              const meta = statusMeta(version.status)
              const submitted = version.status === 'submitted'
              const editable = version.status === 'draft' || version.status === 'rejected'
              const reviewable = canReviewVersion(version)
              const stage = stageOf(version)
              const stageInfo = approvalChain[stage]
              const stagePendingNames = submitted ? namesOf(pendingApprovers(version)) : ''
              const finalizes = willFinalize(version)
              return (
                <li key={version.id} className="boq-version-item">
                  <div className="boq-version-main">
                    <div className="boq-version-title">
                      <strong>{version.name}</strong>
                      <Badge tone={meta.tone} dot={submitted}>
                        {meta.label}
                      </Badge>
                      {submitted && chainActive && (
                        <>
                          <Badge
                            tone="info"
                            subtle
                            title={`第 ${stage + 1}/${chainLength} 级（${modeLabel(stageInfo?.mode ?? 'all')}），待批：${stagePendingNames || '当前级审批人'}`}
                          >
                            第 {stage + 1}/{chainLength} 级待审
                          </Badge>
                          <span className="boq-version-stage-pending">
                            待批：{stagePendingNames || '当前级审批人'}
                          </span>
                        </>
                      )}
                    </div>
                    <span className="boq-version-meta">
                      <span>{formatDate(version.created_at)}</span>
                      <span>
                        {total.label}{' '}
                        <span className="mono">¥{formatMoney(total.value)}</span>
                      </span>
                      <span>{version.price_count} 条已报价</span>
                      {version.package_id === null && <span>项目级</span>}
                      <VersionAudit version={version} />
                    </span>
                    {version.note && <p className="boq-version-note">{version.note}</p>}
                    <ApprovalTimeline approvals={version.approvals} />
                  </div>
                  <div
                    className={className(
                      'boq-version-actions',
                      busyId === version.id && 'is-busy',
                    )}
                  >
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<GitCompare size={12} />}
                      title="并排对比：左右两栏查看汇总与逐行单价（只读可用）"
                      onClick={() => openCompare(version.id)}
                    >
                      对比
                    </Button>
                    {editable && canWrite && (
                      <Button
                        size="sm"
                        variant="secondary"
                        icon={<Send size={12} />}
                        disabled={busy}
                        title={
                          version.status === 'rejected'
                            ? chainActive
                              ? '修改后重新提交，从第 1 级重走审批链'
                              : '修改后重新提交审批'
                            : chainActive
                              ? `提交后按审批链逐级审批（共 ${chainLength} 级）`
                              : '提交给项目负责人审批'
                        }
                        onClick={() => openApproval('submit', version)}
                      >
                        {version.status === 'rejected' ? '重新提交' : '提交审批'}
                      </Button>
                    )}
                    {submitted && reviewable && (
                      <>
                        <Button
                          size="sm"
                          variant="primary"
                          icon={<Check size={12} />}
                          disabled={busy}
                          title={
                            chainActive && !finalizes
                              ? `通过第 ${stage + 1}/${chainLength} 级审批${stage + 1 < chainLength ? '，交由下一级继续审批' : '（会签还需其他审批人通过）'}`
                              : '定稿为本包件现行报价；同包件旧定稿版本自动退回草稿'
                          }
                          onClick={() => openApproval('approve', version)}
                        >
                          {chainActive && !finalizes ? '通过本级' : '定稿'}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<X size={12} />}
                          disabled={busy}
                          title="驳回并退回修改"
                          onClick={() => openApproval('reject', version)}
                        >
                          驳回
                        </Button>
                      </>
                    )}
                    {submitted && !reviewable && (
                      <span
                        className="boq-version-pending"
                        title={
                          chainActive
                            ? `第 ${stage + 1}/${chainLength} 级（${modeLabel(stageInfo?.mode ?? 'all')}）等待「${stagePendingNames || '当前级审批人'}」审批`
                            : '等待项目负责人或超级管理员审批'
                        }
                      >
                        <ShieldCheck size={12} aria-hidden />
                        {chainActive ? `等待「${stagePendingNames || '审批人'}」` : '待审批'}
                      </span>
                    )}
                    {version.status === 'approved' && canWrite && (
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={<Undo2 size={12} />}
                        loading={busyId === version.id}
                        disabled={busy}
                        title="撤销定稿，退回草稿后可继续修改"
                        onClick={() => void unapprove(version)}
                      >
                        撤销定稿
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="secondary"
                      icon={<RotateCcw size={12} />}
                      loading={busyId === version.id}
                      disabled={!canWrite || busy}
                      title={
                        !canWrite
                          ? '只读成员不能恢复版本'
                          : '把该版本保存的单价写回当前清单'
                      }
                      onClick={() => void restore(version)}
                    >
                      恢复
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<Trash2 size={12} />}
                      disabled={!canWrite || busy}
                      title={!canWrite ? '只读成员不能删除版本' : '删除该版本快照'}
                      onClick={() => void remove(version)}
                    >
                      删除
                    </Button>
                  </div>
                </li>
              )
            })}
          </ul>
        )}

        <p className="price-policy-note">
          版本只记录当前包件的单价与汇总；恢复会覆盖当前单价，但不改动台账与清单对齐结果。草稿与已驳回的版本可提交审批，
          {chainActive ? '按审批链逐级通过后定稿，等同现行报价' : '负责人定稿后等同现行报价'}
          。最多保留 20 个版本。
        </p>
      </div>

      {approval && approvalCopy && (
        <Modal
          open
          onClose={closeApproval}
          title={approvalCopy.title}
          size="sm"
          footer={
            <>
              <Button variant="ghost" onClick={closeApproval} disabled={approvalBusy}>
                取消
              </Button>
              <Button
                variant={approval.kind === 'reject' ? 'danger' : 'primary'}
                loading={approvalBusy}
                onClick={() => void runApproval()}
              >
                {approvalCopy.confirm}
              </Button>
            </>
          }
        >
          <div className="boq-version-approval">
            <p className="boq-version-approval-hint">{approvalCopy.hint}</p>
            {approval.kind !== 'submit' && (
              <ApprovalTimeline approvals={approval.version.approvals} />
            )}
            <label className="price-policy-field">
              <span>{approvalCopy.field}</span>
              <textarea
                className="input"
                rows={3}
                maxLength={NOTE_MAX}
                value={approvalNote}
                placeholder={
                  approval.kind === 'submit'
                    ? '可选，例如「按最终下浮率调整完毕」'
                    : approval.kind === 'approve'
                      ? '可选，例如「含税口径确认，同意定稿」'
                      : '可选，说明需要修改的报价项'
                }
                disabled={approvalBusy}
                data-autofocus
                onChange={(event) => setApprovalNote(event.target.value)}
                aria-label={approvalCopy.field}
              />
              <small>
                {approvalNote.length} / {NOTE_MAX} 字
              </small>
            </label>
          </div>
        </Modal>
      )}

      {chainOpen && isOwner && (
        <ApprovalChainModal
          projectRef={projectRef}
          chain={approvalChain}
          onClose={() => setChainOpen(false)}
          onSaved={handleChainSaved}
        />
      )}

      {compare && versionA && (
        <Modal
          open
          onClose={closeCompare}
          title="报价版本并排审阅"
          size="lg"
          footer={
            <Button variant="ghost" onClick={closeCompare}>
              关闭
            </Button>
          }
        >
          <div className="boq-review">
            <div className="boq-review-controls">
              <label className="price-policy-field">
                <span>左侧版本</span>
                <select
                  value={compare.a}
                  onChange={(event) => changeCompare({ ...compare, a: event.target.value })}
                  aria-label="左侧版本"
                >
                  {versions.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <span className="boq-review-vs" aria-hidden>
                对比
              </span>
              <label className="price-policy-field">
                <span>右侧对象</span>
                <select
                  value={compare.b}
                  onChange={(event) => changeCompare({ ...compare, b: event.target.value })}
                  aria-label="右侧对象"
                >
                  <option value="current">当前清单</option>
                  {versions
                    .filter((item) => item.id !== compare.a)
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                </select>
              </label>
            </div>

            {diffLoading ? (
              <p className="price-policy-message">正在计算差异…</p>
            ) : diffError ? (
              <div className="price-policy-message">
                <span>{diffError}</span>
                <button
                  type="button"
                  className="text-btn"
                  onClick={() => void loadDiff(compare.a, compare.b)}
                >
                  重试
                </button>
              </div>
            ) : diffData ? (
              <>
                <div className="boq-review-grid">
                  <CompareSide
                    title={versionA.name}
                    status={versionA.status}
                    summary={diffData.summary_a}
                    count={diffData.count_a}
                  />
                  <CompareSide
                    title={rightLabel}
                    status={versionB?.status}
                    summary={diffData.summary_b}
                    count={diffData.count_b}
                  />
                </div>

                {(() => {
                  const rows = reviewRows(diffData)
                  if (rows.length === 0) {
                    return <p className="price-policy-message">两侧单价一致，无差异。</p>
                  }
                  return (
                    <div className="boq-review-table-wrap">
                      <table className="boq-review-table">
                        <thead>
                          <tr>
                            <th scope="col">清单条目</th>
                            <th scope="col">左侧单价</th>
                            <th scope="col">左侧合价</th>
                            <th scope="col">右侧单价</th>
                            <th scope="col">右侧合价</th>
                            <th scope="col">差异</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((row) => (
                            <tr key={`${row.tone}:${row.key}`} className={`is-${row.tone}`}>
                              <th scope="row">{row.name}</th>
                              <td className="mono">{money(row.leftPrice)}</td>
                              <td className="mono">{money(row.leftAmount)}</td>
                              <td className="mono">{money(row.rightPrice)}</td>
                              <td className="mono">{money(row.rightAmount)}</td>
                              <td className="boq-review-delta">{priceDiffLabel(row)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )
                })()}

                <p className="boq-review-legend">
                  仅一侧有价的条目分别标注「仅左侧」「仅右侧」；两侧单价不同的行并列显示 A/B 单价与合价。
                </p>
              </>
            ) : null}
          </div>
        </Modal>
      )}
    </details>
  )
}

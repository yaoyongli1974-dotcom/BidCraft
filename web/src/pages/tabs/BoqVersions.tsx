import { useEffect, useState } from 'react'
import {
  Check,
  ChevronRight,
  GitCompare,
  History,
  RotateCcw,
  Save,
  Send,
  ShieldCheck,
  Trash2,
  Undo2,
  X,
} from 'lucide-react'
import type {
  BoqPayload,
  BoqQuoteVersion,
  BoqSummary,
  BoqVersionDiff,
  QuoteVersionStatus,
} from '../../lib/types'
import {
  createBoqVersion,
  deleteBoqVersion,
  getBoqVersionDiff,
  listBoqVersions,
  messageOf,
  restoreBoqVersion,
  reviewBoqVersion,
  submitBoqVersion,
  unapproveBoqVersion,
} from '../../lib/api'
import { className, formatDate, formatMoney, type Tone } from '../../lib/format'
import { useQuery } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { Modal } from '../../components/Modal'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'

const NAME_MAX = 80
const NOTE_MAX = 200

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
  onRestored,
}: {
  projectRef: string
  packageId: string | null
  packageName?: string
  canWrite: boolean
  canReview: boolean
  onRestored: (payload: BoqPayload) => void
}) {
  const toast = useToast()
  const confirm = useConfirm()

  const versionsQuery = useQuery(
    `boq-versions:${projectRef}:${packageId ?? 'single'}`,
    () => listBoqVersions(projectRef, packageId),
  )
  const versions = versionsQuery.data ?? []

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

  const approvalCopy = approval
    ? approval.kind === 'submit'
      ? {
          title: `提交「${approval.version.name}」审批？`,
          hint: '提交后版本进入待审状态，审批通过前不能再提交。可附上报价口径说明。',
          field: '提交说明',
          confirm: '提交审批',
        }
      : approval.kind === 'approve'
        ? {
            title: `定稿「${approval.version.name}」？`,
            hint: '定稿后该版本成为本包件的现行报价；同一包件的旧定稿版本会自动退回草稿。',
            field: '审批意见',
            confirm: '定稿',
          }
        : {
            title: `驳回「${approval.version.name}」？`,
            hint: '驳回后版本退回可修改状态，报价人员可调整后再提交。',
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
      toast.success(
        approval.kind === 'submit'
          ? '已提交审批'
          : approval.kind === 'approve'
            ? '版本已定稿'
            : '版本已驳回',
        `「${approval.version.name}」${approval.kind === 'submit' ? '等待负责人审批。' : approval.kind === 'approve' ? '已生效为本包件现行报价。' : '已退回修改。'}`,
      )
      closeApproval()
    } catch (error) {
      toast.error(
        approval.kind === 'submit'
          ? '提交审批失败'
          : approval.kind === 'approve'
            ? '定稿失败'
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
        </div>

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
              return (
                <li key={version.id} className="boq-version-item">
                  <div className="boq-version-main">
                    <div className="boq-version-title">
                      <strong>{version.name}</strong>
                      <Badge tone={meta.tone} dot={submitted}>
                        {meta.label}
                      </Badge>
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
                            ? '修改后重新提交审批'
                            : '提交给项目负责人审批'
                        }
                        onClick={() => openApproval('submit', version)}
                      >
                        {version.status === 'rejected' ? '重新提交' : '提交审批'}
                      </Button>
                    )}
                    {submitted && canReview && (
                      <>
                        <Button
                          size="sm"
                          variant="primary"
                          icon={<Check size={12} />}
                          disabled={busy}
                          title="定稿为本包件现行报价；同包件旧定稿版本自动退回草稿"
                          onClick={() => openApproval('approve', version)}
                        >
                          定稿
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
                    {submitted && !canReview && (
                      <span className="boq-version-pending" title="等待项目负责人或超级管理员审批">
                        <ShieldCheck size={12} aria-hidden />
                        待审批
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
          版本只记录当前包件的单价与汇总；恢复会覆盖当前单价，但不改动台账与清单对齐结果。草稿与已驳回的版本可提交审批，负责人定稿后等同现行报价。最多保留 20 个版本。
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

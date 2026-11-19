import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BadgeCheck,
  CalendarDays,
  CheckCircle2,
  ClipboardCheck,
  ExternalLink,
  FileText,
  Info,
  Link2,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Upload,
  UploadCloud,
  UserPlus,
  Users,
  XCircle,
} from 'lucide-react'
import {
  ApiError,
  createPersonnel,
  createQualification,
  deletePersonnel,
  deleteQualification,
  deleteQualificationEvidence,
  getPersonnel,
  getPreflight,
  getQualifications,
  messageOf,
  qualificationEvidenceUrl,
  updatePersonnel,
  updateQualification,
  uploadQualificationEvidence,
} from '../lib/api'
import type {
  Personnel,
  PersonnelInput,
  PreflightItem,
  PreflightStatus,
  Qualification,
  QualificationInput,
  QualificationKind,
  QualificationStatus,
} from '../lib/types'
import {
  className,
  formatDate,
  formatFileSize,
  parseDate,
  truncate,
  type Tone,
} from '../lib/format'
import { useDocumentTitle, useQuery } from '../lib/hooks'
import { Badge } from '../components/Badge'
import { Button, IconButton } from '../components/Button'
import { EmptyState, ErrorState, ProgressBar, Skeleton } from '../components/Feedback'
import { Modal } from '../components/Modal'
import { TabHeading } from '../components/Workbench'
import { useConfirm } from '../context/useConfirm'
import { useToast } from '../context/useToast'

const KIND_LABELS: Record<QualificationKind, string> = {
  license: '营业执照 / 资质证书',
  safety: '安全生产许可证',
  person: '人员注册证书',
  performance: '类似业绩证明',
  credit: '信用记录',
  other: '其他资质',
}

const KIND_OPTIONS = (Object.keys(KIND_LABELS) as QualificationKind[]).map((value) => ({
  value,
  label: KIND_LABELS[value],
}))

const QUALIFICATION_STATUS_META: Record<
  QualificationStatus,
  { label: string; tone: Tone }
> = {
  valid: { label: '有效', tone: 'success' },
  expiring: { label: '临期', tone: 'warn' },
  expired: { label: '已过期', tone: 'danger' },
  none: { label: '未填到期日', tone: 'neutral' },
}

const PREFLIGHT_STATUS_META: Record<PreflightStatus, { label: string; tone: Tone }> = {
  ok: { label: '可满足', tone: 'success' },
  expiring: { label: '临期', tone: 'warn' },
  expired: { label: '已过期', tone: 'danger' },
  missing: { label: '缺失', tone: 'neutral' },
}

const EVIDENCE_SUFFIXES = ['png', 'jpg', 'jpeg', 'webp', 'pdf']
const EVIDENCE_ACCEPT = '.png,.jpg,.jpeg,.webp,.pdf'
const MAX_EVIDENCE_BYTES = 10 * 1024 * 1024

type QualificationModalState =
  | { mode: 'create'; kind: QualificationKind }
  | { mode: 'edit'; qualification: Qualification }

type PersonnelModalState =
  | { mode: 'create' }
  | { mode: 'edit'; person: Personnel }

function pad2(value: number): string {
  return String(value).padStart(2, '0')
}

function todayValue(): string {
  const now = new Date()
  return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`
}

/** Date inputs need YYYY-MM-DD; server timestamps may include a time part. */
function dateInputValue(value?: string | null): string {
  if (!value) return ''
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (match) return `${match[1]}-${match[2]}-${match[3]}`
  const date = parseDate(value)
  if (!date) return ''
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

function displayDate(value?: string | null): string {
  if (!value) return '未填写'
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (match) return `${match[1]}-${match[2]}-${match[3]}`
  return formatDate(value, false)
}

function kindLabel(kind: QualificationKind): string {
  return KIND_LABELS[kind] || KIND_LABELS.other
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
}

function expiryValue(value: string | null): number {
  if (!value) return Number.POSITIVE_INFINITY
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? Number.POSITIVE_INFINITY : parsed
}

function statusHint(qualification: Qualification): string {
  const days = qualification.days_left
  if (qualification.status === 'expired') {
    return days === null ? '证照已过期' : `已过期 ${Math.abs(days)} 天`
  }
  if (qualification.status === 'expiring') {
    return days === null ? '即将到期' : `${days} 天后到期`
  }
  if (qualification.status === 'valid') {
    return days === null ? '长期有效' : `还剩 ${days} 天`
  }
  return '未填到期日'
}

function eligibleQualifications(
  item: PreflightItem,
  qualifications: Qualification[],
): Qualification[] {
  return qualifications.filter((qualification) => item.kinds.includes(qualification.kind))
}

/**
 * 资质台账 + 投标前自查。所有有效期判断都按用户选择的开标日期从后端读取，
 * 前端只负责展示与录入；官方查询仍然需要人工完成并用证据文件存证。
 */
export function QualificationsPage() {
  useDocumentTitle('资质与自查')
  const toast = useToast()
  const confirm = useConfirm()
  const [deadline, setDeadline] = useState(todayValue)
  const effectiveDeadline = deadline || todayValue()
  const [modal, setModal] = useState<QualificationModalState | null>(null)
  const [personnelModal, setPersonnelModal] = useState<PersonnelModalState | null>(null)
  const [deletingPersonnelId, setDeletingPersonnelId] = useState<string | null>(null)
  const [evidenceId, setEvidenceId] = useState<string | null>(null)
  const [pickerItem, setPickerItem] = useState<PreflightItem | null>(null)
  const [focusId, setFocusId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const qualificationsQuery = useQuery('qualifications', () => getQualifications())
  const personnelQuery = useQuery('personnel', () => getPersonnel())
  const preflightQuery = useQuery(`preflight:${effectiveDeadline}`, () =>
    getPreflight(effectiveDeadline),
  )

  const qualifications = useMemo(() => {
    const list = qualificationsQuery.data || []
    return [...list].sort((left, right) => {
      const leftExpiry = expiryValue(left.valid_to)
      const rightExpiry = expiryValue(right.valid_to)
      if (leftExpiry !== rightExpiry) return leftExpiry < rightExpiry ? -1 : 1
      return left.name.localeCompare(right.name, 'zh-CN')
    })
  }, [qualificationsQuery.data])

  const personnel = useMemo(() => {
    const list = personnelQuery.data || []
    return [...list].sort((left, right) =>
      left.name.localeCompare(right.name, 'zh-CN'),
    )
  }, [personnelQuery.data])
  const lockedPersonnelCount = personnel.filter((person) => person.locked).length

  const preflightItems = preflightQuery.data?.items || []
  const summary = preflightQuery.data?.summary
  const evidenceTarget = evidenceId
    ? qualifications.find((qualification) => qualification.id === evidenceId)
    : undefined

  useEffect(() => {
    if (!focusId) return
    const timer = window.setTimeout(() => setFocusId(null), 2600)
    return () => window.clearTimeout(timer)
  }, [focusId])

  const refreshAll = () => {
    qualificationsQuery.refresh()
    personnelQuery.refresh()
    preflightQuery.refresh()
  }

  const mergeQualification = (next: Qualification) => {
    qualificationsQuery.setData((current) => {
      const list = (current || []).filter((item) => item.id !== next.id)
      return [...list, next]
    })
    preflightQuery.refresh()
  }

  const openCreate = (kind: QualificationKind = 'license') => {
    setModal({ mode: 'create', kind })
  }

  const openEvidenceForItem = (item: PreflightItem) => {
    const target = item.qualification_id
      ? qualifications.find((qualification) => qualification.id === item.qualification_id)
      : undefined
    if (target) {
      setEvidenceId(target.id)
      return
    }
    toast.info('请先登记资质', '该自查项还没有可关联的资质记录，登记后即可上传证据。')
    openCreate(item.kinds[0] || 'license')
  }

  const pickQualification = (qualification: Qualification) => {
    setPickerItem(null)
    setFocusId(qualification.id)
    window.requestAnimationFrame(() => {
      document
        .getElementById(`qual-row-${qualification.id}`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
  }

  const removeQualification = async (qualification: Qualification) => {
    if (deletingId) return
    const accepted = await confirm({
      title: `删除资质「${qualification.name}」？`,
      description: (
        <>
          将删除 <strong>{qualification.name}</strong>
          {qualification.evidence.length > 0
            ? ` 及其 ${qualification.evidence.length} 份证据文件`
            : ''}
          。
        </>
      ),
      note: '删除后无法恢复；自查清单会重新按剩余资质计算。',
      confirmLabel: '删除资质',
      tone: 'danger',
    })
    if (!accepted) return
    setDeletingId(qualification.id)
    try {
      await deleteQualification(qualification.id)
      qualificationsQuery.setData((current) =>
        (current || []).filter((item) => item.id !== qualification.id),
      )
      if (evidenceId === qualification.id) setEvidenceId(null)
      preflightQuery.refresh()
      toast.success('资质已删除', qualification.name)
    } catch (error) {
      toast.error('删除资质失败', messageOf(error))
    } finally {
      setDeletingId(null)
    }
  }

  const onSaved = (saved: Qualification, created: boolean) => {
    setModal(null)
    mergeQualification(saved)
    toast.success(created ? '资质已登记' : '资质已保存', saved.name)
  }

  const mergePersonnel = (next: Personnel) => {
    personnelQuery.setData((current) => {
      const list = (current || []).filter((item) => item.id !== next.id)
      return [...list, next]
    })
  }

  const onPersonnelSaved = (saved: Personnel, created: boolean) => {
    setPersonnelModal(null)
    mergePersonnel(saved)
    toast.success(created ? '人员已添加' : '人员已保存', saved.name)
  }

  const removePersonnel = async (person: Personnel) => {
    if (deletingPersonnelId) return
    const accepted = await confirm({
      title: `删除人员「${person.name}」？`,
      description: (
        <>
          将删除 <strong>{person.name}</strong>
          {person.locked ? ' 的在建锁定信息' : ''}。
        </>
      ),
      note: '删除后无法恢复；已上传的资质与证据文件不受影响。',
      confirmLabel: '删除人员',
      tone: 'danger',
    })
    if (!accepted) return
    setDeletingPersonnelId(person.id)
    try {
      await deletePersonnel(person.id)
      personnelQuery.setData((current) =>
        (current || []).filter((item) => item.id !== person.id),
      )
      toast.success('人员已删除', person.name)
    } catch (error) {
      toast.error('删除人员失败', messageOf(error))
    } finally {
      setDeletingPersonnelId(null)
    }
  }

  const qualsError = qualificationsQuery.error
  const preflightError = preflightQuery.error
  const refreshing = qualificationsQuery.refreshing || preflightQuery.refreshing

  return (
    <div className="qualifications-page">
      <TabHeading
        eyebrow="投标资质"
        title="资质与自查"
        description="用结构化台账记录证照有效期，上传官方查询截图存证；按开标日自动生成投标前自查清单。程序不联网查询官方系统，最终结果仍以开标当日现场查询为准。"
        actions={
          <button
            type="button"
            className="text-btn"
            onClick={refreshAll}
            disabled={refreshing}
          >
            <RefreshCw size={13} className={refreshing ? 'spin' : undefined} />
            刷新
          </button>
        }
      />

      <section className="panel qual-topbar" aria-label="开标日期与自查汇总">
        <div className="qual-deadline">
          <span className="qual-deadline-icon" aria-hidden>
            <CalendarDays size={17} />
          </span>
          <label htmlFor="qual-deadline">开标日期</label>
          <input
            id="qual-deadline"
            className="input qual-date-input"
            type="date"
            value={deadline}
            onChange={(event) => setDeadline(event.target.value)}
          />
          <span className="qual-deadline-hint">
            按当日判定有效期；留空则按今天
          </span>
          {deadline !== todayValue() && (
            <button
              type="button"
              className="text-btn"
              onClick={() => setDeadline(todayValue())}
            >
              回到今天
            </button>
          )}
        </div>

        <div
          className={className('qual-summary', !summary && 'is-loading')}
          aria-live="polite"
          aria-busy={preflightQuery.loading && !summary}
        >
          <span className="qual-summary-chip qual-summary-chip--ok">
            <CheckCircle2 size={13} aria-hidden />
            <strong>{summary ? summary.ok : '—'}</strong>
            <span>可满足</span>
          </span>
          <span
            className="qual-summary-chip qual-summary-chip--warn"
            title="含已过期项"
          >
            <ShieldAlert size={13} aria-hidden />
            <strong>{summary ? summary.warn : '—'}</strong>
            <span>临期</span>
          </span>
          <span className="qual-summary-chip qual-summary-chip--missing">
            <XCircle size={13} aria-hidden />
            <strong>{summary ? summary.missing : '—'}</strong>
            <span>缺失</span>
          </span>
          <span className="qual-summary-total">
            共 {summary ? summary.total : '—'} 项
          </span>
        </div>
      </section>

      <section className="panel qual-panel" aria-labelledby="qual-preflight-heading">
        <div className="panel-head">
          <div>
            <div className="eyebrow">投标前自查</div>
            <h3 id="qual-preflight-heading">按开标日核验</h3>
          </div>
          <span className="subtle">
            开标日 {displayDate(preflightQuery.data?.deadline || effectiveDeadline)}
          </span>
        </div>

        {preflightError && !preflightQuery.data ? (
          <div className="qual-panel-body">
            <ErrorState message={preflightError} onRetry={preflightQuery.refresh} />
          </div>
        ) : preflightQuery.loading && !preflightQuery.data ? (
          <QualTableSkeleton rows={4} />
        ) : preflightItems.length === 0 ? (
          <EmptyState
            icon={ClipboardCheck}
            title="没有可生成的自查项"
            description="后端没有返回自查清单。请确认服务端版本，然后刷新重试。"
            actions={
              <Button variant="secondary" onClick={preflightQuery.refresh}>
                重新读取
              </Button>
            }
          />
        ) : (
          <div className="table-scroll">
            <table className="data-table qual-preflight-table">
              <thead>
                <tr>
                  <th>项目</th>
                  <th>要求提示</th>
                  <th>状态</th>
                  <th>关联资质</th>
                  <th>证据数</th>
                  <th className="qual-col-actions">操作</th>
                </tr>
              </thead>
              <tbody>
                {preflightItems.map((item) => {
                  const meta = PREFLIGHT_STATUS_META[item.status]
                  const eligible = eligibleQualifications(item, qualifications)
                  return (
                    <tr
                      key={item.key}
                      className={className(
                        'qual-preflight-row',
                        item.status === 'expiring' && 'is-expiring',
                        item.status === 'expired' && 'is-expired',
                      )}
                    >
                      <td>
                        <strong>{item.label}</strong>
                      </td>
                      <td className="qual-hint-cell">{item.hint || '—'}</td>
                      <td>
                        <Badge tone={meta.tone} dot subtle>
                          {meta.label}
                        </Badge>
                      </td>
                      <td>
                        {item.name ? (
                          <div className="qual-linked">
                            <span>{item.name}</span>
                            <span className="cell-sub">
                              有效期至 {displayDate(item.valid_to)}
                            </span>
                          </div>
                        ) : (
                          <span className="subtle">未登记</span>
                        )}
                      </td>
                      <td>
                        <span
                          className={className(
                            'qual-evidence-count',
                            item.evidence_count === 0 && 'is-zero',
                          )}
                        >
                          {item.evidence_count} 份
                        </span>
                      </td>
                      <td className="qual-col-actions">
                        <div className="qual-row-actions">
                          <Button
                            variant="ghost"
                            size="sm"
                            icon={<Link2 size={12} />}
                            onClick={() => setPickerItem(item)}
                            title={
                              eligible.length
                                ? `从 ${eligible.length} 条同类型资质中选择`
                                : '登记一条同类型资质'
                            }
                          >
                            关联
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            icon={<Upload size={12} />}
                            onClick={() => openEvidenceForItem(item)}
                          >
                            上传证据
                          </Button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="qual-panel-foot">
          <Info size={13} aria-hidden />
          <span>
            程序不联网查询官方系统。请在开标当日到信用中国、中国政府采购网、中国执行信息公开网查询并截图，作为证据上传。
          </span>
        </div>
      </section>

      <section className="panel qual-panel" aria-labelledby="qual-ledger-heading">
        <div className="panel-head">
          <div>
            <div className="eyebrow">资质台账</div>
            <h3 id="qual-ledger-heading">证照与业绩</h3>
          </div>
          <div className="qual-panel-actions">
            <span className="subtle">
              {qualificationsQuery.loading && !qualificationsQuery.data
                ? '正在读取…'
                : `${qualifications.length} 条`}
            </span>
            <Button
              variant="primary"
              size="sm"
              icon={<Plus size={14} />}
              onClick={() => openCreate()}
            >
              新增资质
            </Button>
          </div>
        </div>

        {qualsError && !qualificationsQuery.data ? (
          <div className="qual-panel-body">
            <ErrorState message={qualsError} onRetry={qualificationsQuery.refresh} />
          </div>
        ) : qualificationsQuery.loading && !qualificationsQuery.data ? (
          <QualTableSkeleton rows={3} />
        ) : qualifications.length === 0 ? (
          <EmptyState
            icon={BadgeCheck}
            title="还没有资质记录"
            description="登记营业执照、资质证书、安全生产许可证、人员证书与类似业绩，系统会按开标日提示临期与缺失。"
            actions={
              <Button
                variant="primary"
                icon={<Plus size={15} />}
                onClick={() => openCreate()}
              >
                登记第一条资质
              </Button>
            }
          />
        ) : (
          <div className="table-scroll">
            <table className="data-table qual-ledger-table">
              <thead>
                <tr>
                  <th>类型</th>
                  <th>名称</th>
                  <th>持有人</th>
                  <th>证书号</th>
                  <th>有效期</th>
                  <th>状态</th>
                  <th>证据</th>
                  <th className="qual-col-actions">操作</th>
                </tr>
              </thead>
              <tbody>
                {qualifications.map((qualification) => {
                  const meta = QUALIFICATION_STATUS_META[qualification.status]
                  return (
                    <tr
                      key={qualification.id}
                      id={`qual-row-${qualification.id}`}
                      className={className(
                        'qual-ledger-row',
                        qualification.status === 'expiring' && 'is-expiring',
                        qualification.status === 'expired' && 'is-expired',
                        focusId === qualification.id && 'is-linked',
                      )}
                    >
                      <td>
                        <span className="qual-kind">{kindLabel(qualification.kind)}</span>
                      </td>
                      <td>
                        <div className="qual-name-cell">
                          <strong>{qualification.name}</strong>
                          {qualification.note && (
                            <span className="cell-sub" title={qualification.note}>
                              {truncate(qualification.note, 48)}
                            </span>
                          )}
                        </div>
                      </td>
                      <td>{qualification.holder || <span className="subtle">—</span>}</td>
                      <td>
                        <div className="qual-name-cell">
                          <span className="mono">{qualification.cert_no || '—'}</span>
                          {qualification.issuer && (
                            <span className="cell-sub">
                              发证：{qualification.issuer}
                            </span>
                          )}
                        </div>
                      </td>
                      <td>
                        {qualification.valid_from || qualification.valid_to ? (
                          <div className="qual-validity">
                            <span>{displayDate(qualification.valid_from)}</span>
                            <span className="qual-validity-sep" aria-hidden>
                              →
                            </span>
                            <span>{displayDate(qualification.valid_to)}</span>
                          </div>
                        ) : (
                          <span className="subtle">未填有效期</span>
                        )}
                      </td>
                      <td>
                        <Badge tone={meta.tone} dot subtle>
                          {meta.label}
                        </Badge>
                        <span className="cell-sub">{statusHint(qualification)}</span>
                      </td>
                      <td>
                        <button
                          type="button"
                          className="text-btn qual-evidence-link"
                          onClick={() => setEvidenceId(qualification.id)}
                          title={`管理 ${qualification.name} 的证据`}
                        >
                          <FileText size={12} aria-hidden />
                          {qualification.evidence.length > 0
                            ? `${qualification.evidence.length} 份`
                            : '上传'}
                        </button>
                      </td>
                      <td className="qual-col-actions">
                        <div className="qual-row-actions">
                          <Button
                            variant="ghost"
                            size="sm"
                            icon={<Pencil size={12} />}
                            disabled={deletingId !== null}
                            onClick={() =>
                              setModal({ mode: 'edit', qualification })
                            }
                          >
                            编辑
                          </Button>
                          <IconButton
                            label={`删除资质 ${qualification.name}`}
                            tone="danger"
                            disabled={deletingId !== null}
                            onClick={() => void removeQualification(qualification)}
                          >
                            {deletingId === qualification.id ? (
                              <Loader2 size={14} className="spin" />
                            ) : (
                              <Trash2 size={14} />
                            )}
                          </IconButton>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel qual-panel" aria-labelledby="qual-personnel-heading">
        <div className="panel-head">
          <div>
            <div className="eyebrow">投标团队</div>
            <h3 id="qual-personnel-heading">人员</h3>
          </div>
          <div className="qual-panel-actions">
            <span className="qual-personnel-summary">
              <span className="qual-personnel-stat">
                <span>总人数</span>
                <strong>
                  {personnelQuery.loading && !personnelQuery.data
                    ? '—'
                    : personnel.length}
                </strong>
              </span>
              <span className="qual-personnel-stat qual-personnel-stat--locked">
                <span>在建锁定</span>
                <strong>
                  {personnelQuery.loading && !personnelQuery.data
                    ? '—'
                    : lockedPersonnelCount}
                </strong>
              </span>
            </span>
            <Button
              variant="primary"
              size="sm"
              icon={<UserPlus size={14} />}
              onClick={() => setPersonnelModal({ mode: 'create' })}
            >
              新增人员
            </Button>
          </div>
        </div>

        {personnelQuery.error && !personnelQuery.data ? (
          <div className="qual-panel-body">
            <ErrorState
              message={personnelQuery.error}
              onRetry={personnelQuery.refresh}
            />
          </div>
        ) : personnelQuery.loading && !personnelQuery.data ? (
          <QualTableSkeleton rows={3} />
        ) : personnel.length === 0 ? (
          <EmptyState
            icon={Users}
            title="还没有人员记录"
            description="登记注册建造师、职称人员与关键岗位人员；在建锁定的成员会在汇总中单独统计，便于配置投标团队时避开冲突。"
            actions={
              <Button
                variant="primary"
                icon={<UserPlus size={15} />}
                onClick={() => setPersonnelModal({ mode: 'create' })}
              >
                添加第一名人员
              </Button>
            }
          />
        ) : (
          <div className="table-scroll">
            <table className="data-table qual-personnel-table">
              <thead>
                <tr>
                  <th>姓名</th>
                  <th>职务</th>
                  <th>注册专业</th>
                  <th>证书号</th>
                  <th>社保月数</th>
                  <th>在建状态</th>
                  <th>备注</th>
                  <th className="qual-col-actions">操作</th>
                </tr>
              </thead>
              <tbody>
                {personnel.map((person) => (
                  <tr
                    key={person.id}
                    className={className(
                      'qual-personnel-row',
                      person.locked && 'is-locked',
                    )}
                  >
                    <td>
                      <strong>{person.name}</strong>
                    </td>
                    <td>{person.title || <span className="subtle">—</span>}</td>
                    <td>
                      {person.specialty || <span className="subtle">—</span>}
                    </td>
                    <td>
                      <span className="mono">{person.cert_no || '—'}</span>
                    </td>
                    <td>
                      <span className="qual-personnel-months">
                        {person.social_months} 个月
                      </span>
                    </td>
                    <td>
                      <Badge
                        tone={person.locked ? 'warn' : 'success'}
                        dot
                        subtle
                      >
                        {person.locked ? '在建锁定' : '正常'}
                      </Badge>
                    </td>
                    <td className="qual-hint-cell" title={person.note || undefined}>
                      {person.note ? truncate(person.note, 36) : (
                        <span className="subtle">—</span>
                      )}
                    </td>
                    <td className="qual-col-actions">
                      <div className="qual-row-actions">
                        <Button
                          variant="ghost"
                          size="sm"
                          icon={<Pencil size={12} />}
                          disabled={deletingPersonnelId !== null}
                          onClick={() =>
                            setPersonnelModal({ mode: 'edit', person })
                          }
                        >
                          编辑
                        </Button>
                        <IconButton
                          label={`删除人员 ${person.name}`}
                          tone="danger"
                          disabled={deletingPersonnelId !== null}
                          onClick={() => void removePersonnel(person)}
                        >
                          {deletingPersonnelId === person.id ? (
                            <Loader2 size={14} className="spin" />
                          ) : (
                            <Trash2 size={14} />
                          )}
                        </IconButton>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="qual-panel-foot">
          <Info size={13} aria-hidden />
          <span>
            「在建状态」由人工维护：锁定表示该人员当前已在其他项目任职，配置新项目团队时请避开。
          </span>
        </div>
      </section>

      {modal && (
        <QualificationModal
          open
          state={modal}
          onClose={() => setModal(null)}
          onSaved={onSaved}
        />
      )}

      {personnelModal && (
        <PersonnelModal
          open
          state={personnelModal}
          onClose={() => setPersonnelModal(null)}
          onSaved={onPersonnelSaved}
        />
      )}

      {pickerItem && (
        <LinkQualificationModal
          open
          item={pickerItem}
          qualifications={eligibleQualifications(pickerItem, qualifications)}
          onClose={() => setPickerItem(null)}
          onPick={pickQualification}
          onCreate={() => {
            setPickerItem(null)
            openCreate(pickerItem.kinds[0] || 'license')
          }}
        />
      )}

      {evidenceTarget && (
        <EvidenceModal
          key={evidenceTarget.id}
          open
          qualification={evidenceTarget}
          onClose={() => setEvidenceId(null)}
          onChanged={mergeQualification}
        />
      )}
    </div>
  )
}

function QualTableSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="qual-skeleton" aria-busy="true" aria-label="正在加载">
      {Array.from({ length: rows }).map((_, index) => (
        <div className="qual-skeleton-row" key={index}>
          <Skeleton width="18%" height={12} />
          <Skeleton width="34%" height={12} />
          <Skeleton width="14%" height={12} />
          <Skeleton width={78} height={22} radius={5} />
        </div>
      ))}
    </div>
  )
}

/** 选择一条同类型资质，并在台账里定位（自查本身按类型自动匹配）。 */
function LinkQualificationModal({
  open,
  item,
  qualifications,
  onClose,
  onPick,
  onCreate,
}: {
  open: boolean
  item: PreflightItem
  qualifications: Qualification[]
  onClose: () => void
  onPick: (qualification: Qualification) => void
  onCreate: () => void
}) {
  const kindText = item.kinds.length
    ? item.kinds.map(kindLabel).join(' / ')
    : '任意类型'
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="关联资质"
      eyebrow={item.label}
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            关闭
          </Button>
          <Button variant="secondary" icon={<Plus size={14} />} onClick={onCreate}>
            登记此类资质
          </Button>
        </>
      }
    >
      <div className="qual-link-modal">
        <div className="qual-modal-note">
          <Link2 size={15} aria-hidden />
          <span>
            自查项会按资质类型自动匹配最晚到期的一条记录；这里选择的记录只用于在台账中定位查看。
          </span>
        </div>
        <div className="qual-link-meta">
          <span>可匹配类型：{kindText}</span>
          <span>{qualifications.length} 条</span>
        </div>
        {qualifications.length === 0 ? (
          <EmptyState
            icon={Link2}
            compact
            title="没有可关联的资质"
            description={`先登记一条「${kindText}」类型的记录，再回到自查清单上传证据。`}
            actions={
              <Button variant="primary" icon={<Plus size={14} />} onClick={onCreate}>
                登记资质
              </Button>
            }
          />
        ) : (
          <div className="qual-picker-list">
            {qualifications.map((qualification) => {
              const meta = QUALIFICATION_STATUS_META[qualification.status]
              return (
                <button
                  key={qualification.id}
                  type="button"
                  className="qual-picker-row"
                  onClick={() => onPick(qualification)}
                >
                  <span className="qual-picker-main">
                    <strong>{qualification.name}</strong>
                    <span className="cell-sub">
                      {kindLabel(qualification.kind)} ·{' '}
                      {qualification.holder || '未填持有人'}
                    </span>
                  </span>
                  <Badge tone={meta.tone} dot subtle>
                    {meta.label}
                  </Badge>
                  <span className="qual-picker-date">
                    至 {displayDate(qualification.valid_to)}
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </Modal>
  )
}

function QualificationModal({
  open,
  state,
  onClose,
  onSaved,
}: {
  open: boolean
  state: QualificationModalState
  onClose: () => void
  onSaved: (qualification: Qualification, created: boolean) => void
}) {
  const editing = state.mode === 'edit' ? state.qualification : undefined
  const initialKind = state.mode === 'create' ? state.kind : 'license'
  const [kind, setKind] = useState<QualificationKind>(initialKind)
  const [name, setName] = useState('')
  const [holder, setHolder] = useState('')
  const [certNo, setCertNo] = useState('')
  const [issuer, setIssuer] = useState('')
  const [validFrom, setValidFrom] = useState('')
  const [validTo, setValidTo] = useState('')
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) return
    setSaving(false)
    setError(null)
    setTouched(false)
    setKind(editing?.kind ?? initialKind)
    setName(editing?.name ?? '')
    setHolder(editing?.holder ?? '')
    setCertNo(editing?.cert_no ?? '')
    setIssuer(editing?.issuer ?? '')
    setValidFrom(dateInputValue(editing?.valid_from))
    setValidTo(dateInputValue(editing?.valid_to))
    setNote(editing?.note ?? '')
  }, [open, editing, initialKind])

  const nameMissing = !name.trim()
  const invalidRange = Boolean(validFrom && validTo && validTo < validFrom)

  const submit = async () => {
    setTouched(true)
    if (nameMissing) return
    if (invalidRange) {
      setError('到期日期不能早于起始日期。')
      return
    }
    const payload: QualificationInput = {
      kind,
      name: name.trim(),
      holder: holder.trim(),
      cert_no: certNo.trim(),
      issuer: issuer.trim(),
      valid_from: validFrom || null,
      valid_to: validTo || null,
      note: note.trim(),
    }
    setSaving(true)
    setError(null)
    try {
      const saved = editing
        ? await updateQualification(editing.id, payload)
        : await createQualification(payload)
      onSaved(saved, !editing)
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={saving ? () => undefined : onClose}
      title={editing ? '编辑资质' : '新增资质'}
      eyebrow="资质台账"
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="qualification-form"
            loading={saving}
            disabled={touched && nameMissing}
          >
            {editing ? '保存修改' : '登记资质'}
          </Button>
        </>
      }
    >
      <form
        id="qualification-form"
        className="qual-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="qual-modal-note">
          <ShieldCheck size={15} aria-hidden />
          <span>
            台账只保存结构化信息与证据文件；到期前 30 天会在自查清单与台账中标记为临期。日期留空表示不适用。
          </span>
        </div>

        <div className="qual-form-grid">
          <div className="form-field">
            <label htmlFor="qual-kind">
              类型 <span className="required-mark">必填</span>
            </label>
            <select
              id="qual-kind"
              className="input"
              value={kind}
              onChange={(event) => {
                setKind(event.target.value as QualificationKind)
                if (error) setError(null)
              }}
              disabled={saving}
            >
              {KIND_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="qual-name">
              名称 <span className="required-mark">必填</span>
            </label>
            <input
              id="qual-name"
              className={className(
                'input',
                touched && nameMissing && 'input--error',
              )}
              value={name}
              onChange={(event) => {
                setName(event.target.value)
                if (error) setError(null)
              }}
              placeholder="例如：电子与智能化工程专业承包二级"
              maxLength={120}
              autoComplete="off"
              data-autofocus
              disabled={saving}
            />
            {touched && nameMissing && (
              <span className="field-error">请填写资质名称。</span>
            )}
          </div>

          <div className="form-field">
            <label htmlFor="qual-holder">
              持有人 <span className="optional-mark">可选</span>
            </label>
            <input
              id="qual-holder"
              className="input"
              value={holder}
              onChange={(event) => setHolder(event.target.value)}
              placeholder="单位或个人姓名"
              maxLength={120}
              autoComplete="off"
              disabled={saving}
            />
          </div>

          <div className="form-field">
            <label htmlFor="qual-cert-no">
              证书号 <span className="optional-mark">可选</span>
            </label>
            <input
              id="qual-cert-no"
              className="input"
              value={certNo}
              onChange={(event) => setCertNo(event.target.value)}
              placeholder="证书编号"
              maxLength={80}
              autoComplete="off"
              disabled={saving}
            />
            <p className="field-hint">不超过 80 字。</p>
          </div>

          <div className="form-field qual-form-span">
            <label htmlFor="qual-issuer">
              发证机关 <span className="optional-mark">可选</span>
            </label>
            <input
              id="qual-issuer"
              className="input"
              value={issuer}
              onChange={(event) => setIssuer(event.target.value)}
              placeholder="例如：省住房和城乡建设厅"
              maxLength={120}
              autoComplete="off"
              disabled={saving}
            />
          </div>

          <div className="form-field">
            <label htmlFor="qual-valid-from">
              有效期开始 <span className="optional-mark">可选</span>
            </label>
            <input
              id="qual-valid-from"
              className="input"
              type="date"
              value={validFrom}
              max={validTo || undefined}
              onChange={(event) => {
                setValidFrom(event.target.value)
                if (error) setError(null)
              }}
              disabled={saving}
            />
          </div>

          <div className="form-field">
            <label htmlFor="qual-valid-to">
              有效期结束 <span className="optional-mark">可选</span>
            </label>
            <input
              id="qual-valid-to"
              className={className(
                'input',
                invalidRange && 'input--error',
              )}
              type="date"
              value={validTo}
              min={validFrom || undefined}
              onChange={(event) => {
                setValidTo(event.target.value)
                if (error) setError(null)
              }}
              disabled={saving}
            />
            {invalidRange && (
              <span className="field-error">结束日期不能早于开始日期。</span>
            )}
          </div>

          <div className="form-field qual-form-span">
            <label htmlFor="qual-note">
              备注 <span className="optional-mark">可选</span>
            </label>
            <textarea
              id="qual-note"
              className="textarea"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="等级、承包范围、注册单位等需要提醒的要点"
              maxLength={500}
              rows={3}
              disabled={saving}
            />
          </div>
        </div>

        {error && (
          <div className="form-alert" role="alert">
            {error}
          </div>
        )}
      </form>
    </Modal>
  )
}

/** 证据管理：上传 multipart、新窗口打开、逐条删除。 */
/** 新增 / 编辑人员：姓名必填，社保月数 0–600，锁定状态决定投标配置提示。 */
function PersonnelModal({
  open,
  state,
  onClose,
  onSaved,
}: {
  open: boolean
  state: PersonnelModalState
  onClose: () => void
  onSaved: (person: Personnel, created: boolean) => void
}) {
  const editing = state.mode === 'edit' ? state.person : undefined
  const [name, setName] = useState('')
  const [title, setTitle] = useState('')
  const [specialty, setSpecialty] = useState('')
  const [certNo, setCertNo] = useState('')
  const [socialMonths, setSocialMonths] = useState('0')
  const [locked, setLocked] = useState(false)
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) return
    setSaving(false)
    setError(null)
    setTouched(false)
    setName(editing?.name ?? '')
    setTitle(editing?.title ?? '')
    setSpecialty(editing?.specialty ?? '')
    setCertNo(editing?.cert_no ?? '')
    setSocialMonths(String(editing?.social_months ?? 0))
    setLocked(Boolean(editing?.locked))
    setNote(editing?.note ?? '')
  }, [open, editing])

  const nameMissing = !name.trim()
  const monthsValue = Number(socialMonths)
  const monthsInvalid =
    !socialMonths.trim() ||
    !Number.isInteger(monthsValue) ||
    monthsValue < 0 ||
    monthsValue > 600

  const submit = async () => {
    setTouched(true)
    if (nameMissing) return
    if (monthsInvalid) {
      setError('社保月数必须是 0–600 之间的整数。')
      return
    }
    const payload: PersonnelInput = {
      name: name.trim(),
      title: title.trim(),
      specialty: specialty.trim(),
      cert_no: certNo.trim(),
      social_months: monthsValue,
      locked,
      note: note.trim(),
    }
    setSaving(true)
    setError(null)
    try {
      const saved = editing
        ? await updatePersonnel(editing.id, payload)
        : await createPersonnel(payload)
      onSaved(saved, !editing)
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={saving ? () => undefined : onClose}
      title={editing ? '编辑人员' : '新增人员'}
      eyebrow="投标团队"
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="personnel-form"
            loading={saving}
            disabled={touched && (nameMissing || monthsInvalid)}
          >
            {editing ? '保存修改' : '添加人员'}
          </Button>
        </>
      }
    >
      <form
        id="personnel-form"
        className="qual-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="qual-modal-note">
          <Users size={15} aria-hidden />
          <span>
            人员信息只用于投标团队配置；在建锁定为人工标记，不会自动同步其他项目。
          </span>
        </div>

        <div className="qual-form-grid">
          <div className="form-field">
            <label htmlFor="person-name">
              姓名 <span className="required-mark">必填</span>
            </label>
            <input
              id="person-name"
              className={className('input', touched && nameMissing && 'input--error')}
              value={name}
              onChange={(event) => {
                setName(event.target.value)
                if (error) setError(null)
              }}
              placeholder="例如：张工"
              maxLength={80}
              autoComplete="off"
              data-autofocus
              disabled={saving}
            />
            {touched && nameMissing && (
              <span className="field-error">请填写姓名。</span>
            )}
          </div>

          <div className="form-field">
            <label htmlFor="person-title">
              职务 <span className="optional-mark">可选</span>
            </label>
            <input
              id="person-title"
              className="input"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="例如：项目经理"
              maxLength={80}
              autoComplete="off"
              disabled={saving}
            />
          </div>

          <div className="form-field">
            <label htmlFor="person-specialty">
              注册专业 <span className="optional-mark">可选</span>
            </label>
            <input
              id="person-specialty"
              className="input"
              value={specialty}
              onChange={(event) => setSpecialty(event.target.value)}
              placeholder="例如：机电工程"
              maxLength={80}
              autoComplete="off"
              disabled={saving}
            />
          </div>

          <div className="form-field">
            <label htmlFor="person-cert-no">
              证书号 <span className="optional-mark">可选</span>
            </label>
            <input
              id="person-cert-no"
              className="input mono"
              value={certNo}
              onChange={(event) => setCertNo(event.target.value)}
              placeholder="注册证书编号"
              maxLength={80}
              autoComplete="off"
              disabled={saving}
            />
          </div>

          <div className="form-field">
            <label htmlFor="person-social-months">
              社保月数 <span className="optional-mark">可选</span>
            </label>
            <input
              id="person-social-months"
              className={className(
                'input',
                touched && monthsInvalid && 'input--error',
              )}
              type="number"
              min={0}
              max={600}
              step={1}
              value={socialMonths}
              onChange={(event) => {
                setSocialMonths(event.target.value)
                if (error) setError(null)
              }}
              disabled={saving}
            />
            <p className="field-hint">连续缴纳月数，0–600；新建默认 0。</p>
            {touched && monthsInvalid && (
              <span className="field-error">请输入 0–600 之间的整数。</span>
            )}
          </div>

          <div className="form-field qual-form-span">
            <label className="qual-check" htmlFor="person-locked">
              <input
                id="person-locked"
                type="checkbox"
                checked={locked}
                onChange={(event) => setLocked(event.target.checked)}
                disabled={saving}
              />
              <span>
                <strong>在建锁定</strong>
                <small>该人员当前已在其他项目任职，配置新团队时避开。</small>
              </span>
            </label>
          </div>

          <div className="form-field qual-form-span">
            <label htmlFor="person-note">
              备注 <span className="optional-mark">可选</span>
            </label>
            <textarea
              id="person-note"
              className="textarea"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="职称、注册单位、可用时间等需要提醒的要点"
              maxLength={200}
              rows={3}
              disabled={saving}
            />
            <p className="field-hint">不超过 200 字。</p>
          </div>
        </div>

        {error && (
          <div className="form-alert" role="alert">
            {error}
          </div>
        )}
      </form>
    </Modal>
  )
}

function EvidenceModal({
  open,
  qualification,
  onClose,
  onChanged,
}: {
  open: boolean
  qualification: Qualification
  onClose: () => void
  onChanged: (qualification: Qualification) => void
}) {
  const toast = useToast()
  const confirm = useConfirm()
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const chooseFile = () => {
    if (uploading) return
    inputRef.current?.click()
  }

  const upload = async (file: File) => {
    if (uploading) return
    const suffix = extensionOf(file.name)
    if (!EVIDENCE_SUFFIXES.includes(suffix)) {
      setError('仅支持 PNG、JPG、WEBP 或 PDF 文件。')
      return
    }
    if (file.size > MAX_EVIDENCE_BYTES) {
      setError('单个证据文件不能超过 10MB。')
      return
    }
    if (file.size === 0) {
      setError('证据文件不能为空。')
      return
    }
    setUploading(true)
    setProgress(0)
    setError(null)
    try {
      const updated = await uploadQualificationEvidence(
        qualification.id,
        file,
        setProgress,
      )
      onChanged(updated)
      toast.success('证据已上传', file.name)
    } catch (err) {
      const message = messageOf(err)
      setError(message)
      toast.error('上传证据失败', message)
    } finally {
      setUploading(false)
      setProgress(0)
    }
  }

  const remove = async (filename: string) => {
    if (deleting) return
    const accepted = await confirm({
      title: '删除这份证据？',
      description: (
        <>
          即将删除 <strong>{filename}</strong>。
        </>
      ),
      note: '删除后无法恢复；资质记录本身不受影响。',
      confirmLabel: '删除证据',
      tone: 'danger',
    })
    if (!accepted) return
    setDeleting(filename)
    setError(null)
    try {
      await deleteQualificationEvidence(qualification.id, filename)
      onChanged({
        ...qualification,
        evidence: qualification.evidence.filter((item) => item.filename !== filename),
      })
      toast.success('证据已删除', filename)
    } catch (err) {
      const message =
        err instanceof ApiError && (err.status === 404 || err.status === 405)
          ? '后端暂不支持删除证据，请升级服务端后重试。'
          : messageOf(err)
      setError(message)
      toast.error('删除证据失败', message)
    } finally {
      setDeleting(null)
    }
  }

  return (
    <Modal
      open={open}
      onClose={uploading ? () => undefined : onClose}
      title="证据与存证"
      eyebrow={qualification.name}
      size="lg"
      footer={
        <Button variant="ghost" onClick={onClose} disabled={uploading}>
          关闭
        </Button>
      }
    >
      <div className="qual-evidence-modal">
        <div className="qual-modal-note">
          <ShieldCheck size={15} aria-hidden />
          <span>
            官方查询截图请保留查询日期与网址；扫描件需清晰可读。支持 PNG、JPG、WEBP、PDF，单份不超过
            10MB。
          </span>
        </div>

        <div className="qual-evidence-toolbar">
          <div className="qual-evidence-heading">
            <strong>存证文件</strong>
            <span className="cell-sub">
              {kindLabel(qualification.kind)} · 有效期至{' '}
              {displayDate(qualification.valid_to)}
            </span>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept={EVIDENCE_ACCEPT}
            className="sr-only"
            aria-label="选择证据文件"
            onChange={(event) => {
              const file = event.target.files?.[0]
              event.target.value = ''
              if (file) void upload(file)
            }}
          />
          <Button
            variant="secondary"
            size="sm"
            icon={<Upload size={14} />}
            loading={uploading}
            onClick={chooseFile}
          >
            上传证据
          </Button>
        </div>

        {uploading && (
          <div className="qual-upload-progress">
            <ProgressBar value={progress} tone="accent" label="证据上传进度" />
            <span className="mono">{progress}%</span>
          </div>
        )}

        {error && (
          <div className="form-alert" role="alert">
            {error}
          </div>
        )}

        {qualification.evidence.length === 0 ? (
          <EmptyState
            icon={FileText}
            compact
            title="还没有存证文件"
            description="上传证照扫描件或官方查询截图，自查清单会显示每个资质的证据数量。"
            actions={
              <Button
                variant="secondary"
                icon={<UploadCloud size={14} />}
                onClick={chooseFile}
                disabled={uploading}
              >
                选择文件
              </Button>
            }
          />
        ) : (
          <div className="qual-evidence-list">
            {qualification.evidence.map((item) => (
              <div className="qual-evidence-row" key={item.filename}>
                <span className="qual-evidence-icon" aria-hidden>
                  <FileText size={16} />
                </span>
                <div className="qual-evidence-main">
                  <span className="qual-evidence-name" title={item.filename}>
                    {item.filename}
                  </span>
                  <span className="qual-evidence-meta">
                    {formatFileSize(item.size)}
                    {item.mime ? ` · ${item.mime}` : ''}
                    {item.uploaded_at
                      ? ` · 上传于 ${formatDate(item.uploaded_at)}`
                      : ''}
                  </span>
                </div>
                <div className="qual-evidence-actions">
                  <a
                    className="btn btn--secondary btn--sm"
                    href={qualificationEvidenceUrl(
                      qualification.id,
                      item.filename,
                    )}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={`打开 ${item.filename}`}
                  >
                    <ExternalLink size={13} />
                    <span className="btn-label">打开</span>
                  </a>
                  <IconButton
                    label={`删除证据 ${item.filename}`}
                    tone="danger"
                    disabled={deleting !== null}
                    onClick={() => void remove(item.filename)}
                  >
                    {deleting === item.filename ? (
                      <Loader2 size={14} className="spin" />
                    ) : (
                      <Trash2 size={14} />
                    )}
                  </IconButton>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Modal>
  )
}

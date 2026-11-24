import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  FileCheck2,
  Plus,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import {
  createCommercialDoc,
  deleteCommercialDoc,
  exportCommercialDoc,
  listCommercialDocs,
  reviewCommercialDoc,
  submitCommercialDoc,
  unapproveCommercialDoc,
} from '../../api/commercial'
import { listBoqVersions } from '../../api/boq'
import { getBidPackages } from '../../api/project'
import { downloadBlob } from '../../api/generation'
import { messageOf } from '../../lib/http'
import { isProjectAdmin } from '../../lib/permissions'
import type {
  BidPackage,
  BoqQuoteVersion,
  CommercialDoc,
  CommercialExportFormat,
} from '../../lib/types'
import { formatDate } from '../../lib/format'
import { useQuery } from '../../lib/hooks'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, Skeleton } from '../../components/Feedback'
import { Modal } from '../../components/Modal'
import { TabHeading } from '../../components/Workbench'
import { CommercialDocCard } from '../../components/CommercialDocCard'
import type { CommercialAction } from '../../lib/commercialMeta'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'

type NoteAction = 'submit' | 'reject'

const ACTION_LABEL: Record<CommercialAction, string> = {
  submit: '提交审批',
  approve: '审批通过',
  reject: '驳回',
  unapprove: '撤回定稿',
}

export function CommercialTab() {
  const { ref, basePath, project, canWrite } = useWorkspace()
  const toast = useToast()
  const confirm = useConfirm()
  const canReview = isProjectAdmin(project)

  const packagesQuery = useQuery(`commercial-packages:${ref}`, () => getBidPackages(ref))
  const packages = useMemo<BidPackage[]>(() => packagesQuery.data || [], [packagesQuery.data])
  const packagesReady = packagesQuery.data !== undefined
  const [selectedPackageId, setSelectedPackageId] = useState<string | null>(null)
  // 包件模式必须显式传 package_id：后端 resolve_package 会拒绝「已启用包件但未指定」。
  const packageId = useMemo(() => {
    if (packages.length === 0) return null
    if (selectedPackageId && packages.some((item) => item.id === selectedPackageId)) {
      return selectedPackageId
    }
    return packages[0]?.id ?? null
  }, [packages, selectedPackageId])

  const docsQuery = useQuery(
    `commercial:${ref}:${packageId ?? 'single'}`,
    () => listCommercialDocs(ref, packageId),
    { enabled: packagesReady },
  )
  const versionsQuery = useQuery(
    `commercial-approved-versions:${ref}:${packageId ?? 'single'}`,
    () => listBoqVersions(ref, packageId),
    { enabled: packagesReady },
  )
  const docs = useMemo(() => docsQuery.data || [], [docsQuery.data])
  const approvedVersions = useMemo(
    () => (versionsQuery.data || []).filter((version) => version.status === 'approved'),
    [versionsQuery.data],
  )

  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState('')
  const [versionId, setVersionId] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const [noteTarget, setNoteTarget] = useState<{
    doc: CommercialDoc
    action: NoteAction
  } | null>(null)
  const [note, setNote] = useState('')
  const [requireComplete, setRequireComplete] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [exporting, setExporting] = useState<string | null>(null)

  useEffect(() => {
    // 切换包件后旧版本不属于当前包件，必须重新挑选已定稿版本。
    if (!createOpen) return
    if (!approvedVersions.some((version) => version.id === versionId)) {
      setVersionId(approvedVersions[0]?.id || '')
    }
  }, [createOpen, versionId, approvedVersions])

  const openCreate = () => {
    if (!canWrite || approvedVersions.length === 0) return
    setName('')
    setVersionId(approvedVersions[0]?.id || '')
    setCreateError(null)
    setCreateOpen(true)
  }

  const create = async () => {
    if (!canWrite || creating) return
    const trimmed = name.trim()
    if (!trimmed) {
      setCreateError('请填写商务标名称。')
      return
    }
    if (!versionId) {
      setCreateError('请选择一个已定稿的报价版本。')
      return
    }
    setCreating(true)
    setCreateError(null)
    try {
      const created = await createCommercialDoc(ref, {
        name: trimmed,
        quote_version_id: versionId,
        package_id: packageId,
      })
      docsQuery.setData((current) => [created, ...(current || [])])
      setCreateOpen(false)
      toast.success('商务标已创建', created.name || trimmed)
    } catch (error) {
      const message = messageOf(error)
      setCreateError(message)
      toast.error('创建商务标失败', message)
    } finally {
      setCreating(false)
    }
  }

  const runAction = async (
    doc: CommercialDoc,
    action: CommercialAction,
    message?: string,
    strictComplete = false,
  ) => {
    if (!canWrite || busyId) return
    if (action !== 'submit' && !canReview) return
    setBusyId(doc.id)
    try {
      const next =
        action === 'submit'
          ? await submitCommercialDoc(ref, doc.id, message, strictComplete)
          : action === 'approve'
            ? await reviewCommercialDoc(ref, doc.id, 'approve', message)
            : action === 'reject'
              ? await reviewCommercialDoc(ref, doc.id, 'reject', message)
              : await unapproveCommercialDoc(ref, doc.id, message)
      if (next.id) {
        docsQuery.setData((current) =>
          (current || []).map((item) => (item.id === next.id ? next : item)),
        )
      } else {
        docsQuery.refresh()
      }
      toast.success(`${ACTION_LABEL[action]}成功`, doc.name)
    } catch (error) {
      toast.error(`${ACTION_LABEL[action]}失败`, messageOf(error))
    } finally {
      setBusyId(null)
    }
  }

  const openNote = (doc: CommercialDoc, action: NoteAction) => {
    if (!canWrite) return
    if (action === 'reject' && !canReview) return
    setNote('')
    setRequireComplete(false)
    setNoteTarget({ doc, action })
  }

  const submitNote = async () => {
    if (!noteTarget) return
    const target = noteTarget
    const strictComplete = target.action === 'submit' && requireComplete
    setNoteTarget(null)
    await runAction(target.doc, target.action, note.trim() || undefined, strictComplete)
  }

  const remove = async (doc: CommercialDoc) => {
    if (!canWrite || busyId) return
    const accepted = await confirm({
      title: `删除商务标「${doc.name}」？`,
      description: '删除后无法恢复，绑定的报价版本不受影响。',
      confirmLabel: '删除商务标',
      tone: 'danger',
    })
    if (!accepted) return
    setBusyId(doc.id)
    try {
      await deleteCommercialDoc(ref, doc.id)
      docsQuery.setData((current) => (current || []).filter((item) => item.id !== doc.id))
      toast.success('商务标已删除', doc.name)
    } catch (error) {
      toast.error('删除商务标失败', messageOf(error))
    } finally {
      setBusyId(null)
    }
  }

  const exportDoc = async (doc: CommercialDoc, format: CommercialExportFormat) => {
    const key = `${doc.id}:${format}`
    if (exporting) return
    setExporting(key)
    try {
      const { blob, filename } = await exportCommercialDoc(ref, doc.id, format)
      downloadBlob(blob, filename)
      toast.success('商务标已导出', filename)
    } catch (error) {
      toast.error('导出商务标失败', messageOf(error))
    } finally {
      setExporting(null)
    }
  }

  const versionsError = packagesQuery.error || versionsQuery.error
  const noApproved =
    !packagesQuery.loading &&
    !versionsQuery.loading &&
    !versionsError &&
    approvedVersions.length === 0
  const versionLabel = (version: BoqQuoteVersion) =>
    `${version.name}${version.created_at ? ` · ${formatDate(version.created_at, false)}` : ''}`

  return (
    <div className="tab-content com-tab">
      <TabHeading
        eyebrow="商务标"
        title="商务标编制与审批"
        description="以已定稿的报价版本生成商务标，提交审批后定稿并导出。商务标只读引用报价数据，不重复录入。"
        actions={
          <div className="com-head-actions">
            <Button
              size="sm"
              variant="secondary"
              icon={<RefreshCw size={13} className={docsQuery.refreshing ? 'spin' : undefined} />}
              disabled={docsQuery.refreshing}
              onClick={() => {
                packagesQuery.refresh()
                docsQuery.refresh()
                versionsQuery.refresh()
              }}
            >
              刷新
            </Button>
            <Button
              size="sm"
              variant="primary"
              icon={<Plus size={14} />}
              disabled={!canWrite || approvedVersions.length === 0}
              title={
                !canWrite
                  ? '只读成员不能创建商务标'
                  : approvedVersions.length === 0
                    ? '需要先在清单对齐中定稿一个报价版本'
                    : undefined
              }
              onClick={openCreate}
            >
              新建商务标
            </Button>
          </div>
        }
      />

      {packages.length > 0 && (
        <div className="panel com-package-switch">
          <label className="form-field">
            <span>包件</span>
            <select
              className="select"
              aria-label="切换商务标包件"
              value={packageId ?? ''}
              onChange={(event) => {
                setSelectedPackageId(event.target.value)
                setCreateOpen(false)
              }}
            >
              {packages.map((pkg) => (
                <option key={pkg.id} value={pkg.id}>
                  {pkg.name}
                  {pkg.number ? ` · ${pkg.number}` : ''}
                </option>
              ))}
            </select>
            <span className="field-hint">
              商务标按包件分别编制；切换后只显示该包件的已定稿报价版本与商务标。
            </span>
          </label>
        </div>
      )}

      {noApproved && (
        <div className="com-gate" role="status">
          <span className="com-gate-icon" aria-hidden>
            <AlertTriangle size={19} />
          </span>
          <div>
            <strong>尚无已定稿的报价版本，不能新建或定稿商务标</strong>
            <p>
              商务标必须绑定已审批通过的报价版本。请先在清单对齐中提交并定稿报价版本，再回到这里编制商务标。
            </p>
          </div>
          <Link to={`${basePath}/boq`} className="text-link">
            去清单对齐 <ArrowRight size={11} />
          </Link>
        </div>
      )}

      {versionsError && (
        <div className="com-gate com-gate--error" role="alert">
          <span className="com-gate-icon" aria-hidden>
            <ShieldAlert size={19} />
          </span>
          <div>
            <strong>无法读取包件或报价版本</strong>
            <p>{versionsError}</p>
          </div>
          <Button size="sm" variant="secondary" onClick={versionsQuery.refresh}>
            重试
          </Button>
        </div>
      )}

      {docsQuery.loading && !docsQuery.data ? (
        <div className="com-doc-list" aria-busy="true">
          <Skeleton height={190} radius={12} />
          <Skeleton height={190} radius={12} />
        </div>
      ) : docsQuery.error ? (
        <ErrorState message={docsQuery.error} onRetry={docsQuery.refresh} />
      ) : docs.length === 0 ? (
        <div className="panel">
          <EmptyState
            icon={FileCheck2}
            title="还没有商务标"
            description={
              approvedVersions.length > 0
                ? '选择一个已定稿的报价版本，创建第一份商务标。'
                : '先在清单对齐中定稿报价版本，之后即可创建商务标。'
            }
            actions={
              approvedVersions.length > 0 && canWrite ? (
                <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
                  新建商务标
                </Button>
              ) : undefined
            }
          />
        </div>
      ) : (
        <div className="com-doc-list">
          {docs.map((doc) => (
            <CommercialDocCard
              key={doc.id}
              doc={doc}
              canWrite={canWrite}
              canReview={canReview}
              busy={busyId === doc.id}
              busyExport={
                exporting?.startsWith(`${doc.id}:`)
                  ? (exporting.split(':')[1] as CommercialExportFormat)
                  : null
              }
              onAction={(action) => {
                if (action === 'submit' || action === 'reject') {
                  openNote(doc, action)
                } else {
                  void runAction(doc, action)
                }
              }}
              onExport={(format) => void exportDoc(doc, format)}
              onDelete={() => void remove(doc)}
            />
          ))}
        </div>
      )}

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        size="sm"
        eyebrow="新建商务标"
        title="绑定已定稿报价版本"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button
              variant="primary"
              loading={creating}
              disabled={!name.trim() || !versionId}
              onClick={() => void create()}
            >
              创建
            </Button>
          </>
        }
      >
        <div className="com-modal-body">
          <label className="form-field">
            <span>商务标名称</span>
            <input
              className="input"
              value={name}
              maxLength={80}
              placeholder="例如：XX 项目商务标"
              data-autofocus
              onChange={(event) => {
                setName(event.target.value)
                if (createError) setCreateError(null)
              }}
            />
          </label>
          <label className="form-field">
            <span>已定稿报价版本</span>
            <select
              className="select"
              value={versionId}
              onChange={(event) => {
                setVersionId(event.target.value)
                if (createError) setCreateError(null)
              }}
            >
              {approvedVersions.map((version) => (
                <option key={version.id} value={version.id}>
                  {versionLabel(version)}
                </option>
              ))}
            </select>
            <span className="field-hint">仅列出已审批通过的报价版本。</span>
          </label>
          {createError && (
            <p className="field-error" role="alert">
              {createError}
            </p>
          )}
        </div>
      </Modal>

      <Modal
        open={Boolean(noteTarget)}
        onClose={() => setNoteTarget(null)}
        size="sm"
        eyebrow={noteTarget ? ACTION_LABEL[noteTarget.action] : '审批'}
        title={noteTarget?.doc.name || '商务标'}
        footer={
          <>
            <Button variant="ghost" onClick={() => setNoteTarget(null)}>
              取消
            </Button>
            <Button
              variant={noteTarget?.action === 'reject' ? 'danger' : 'primary'}
              onClick={() => void submitNote()}
            >
              确认{noteTarget ? ACTION_LABEL[noteTarget.action] : ''}
            </Button>
          </>
        }
      >
        <label className="form-field">
          <span>
            {noteTarget?.action === 'reject' ? '驳回原因' : '提交说明'}
            <em className="subtle">（可选）</em>
          </span>
          <textarea
            className="textarea"
            rows={3}
            value={note}
            maxLength={200}
            data-autofocus
            placeholder={
              noteTarget?.action === 'reject'
                ? '说明需要修改的章节或数据。'
                : '说明本次提交的版本与注意事项。'
            }
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
        {noteTarget?.action === 'submit' && (
          <div className="form-field">
            <div className="admin-check-row">
              <label className="admin-check">
                <input
                  type="checkbox"
                  checked={requireComplete}
                  onChange={(event) => setRequireComplete(event.target.checked)}
                />
                <span>
                  <strong>严格校验完整性</strong>
                  <small>必填项未填齐时拒绝送审（质量评估仍会记录完整性风险）</small>
                </span>
              </label>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}

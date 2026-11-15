import { useEffect, useState } from 'react'
import {
  ArrowRightLeft,
  Check,
  Info,
  Package as PackageIcon,
  PackagePlus,
  Pencil,
  Settings2,
  Trash2,
  X,
} from 'lucide-react'
import {
  createBidPackage,
  deleteBidPackage,
  getBidPackages,
  messageOf,
  updateBidPackage,
} from '../../lib/api'
import type { BidPackage, BidPackageInput } from '../../lib/types'
import { className } from '../../lib/format'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { Modal } from '../../components/Modal'
import { useConfirm } from '../../context/ConfirmContext'
import { useToast } from '../../context/ToastContext'

export interface BoqPackageState {
  supported: boolean
  packages: BidPackage[]
}

/**
 * 旧后端没有 `/projects/{ref}/packages`：要么返回 404，要么被 SPA 兜底成
 * 一份 HTML（JSON 解析失败）。两种情况都退回到单份清单的旧界面。
 */
export async function loadBoqPackageState(ref: string): Promise<BoqPackageState> {
  try {
    return { supported: true, packages: await getBidPackages(ref) }
  } catch {
    // 404 / HTML 兜底 / 暂时读取失败都先按旧界面呈现，避免整页被阻断；
    // 清单自身的请求会照常给出具体错误。
    return { supported: false, packages: [] }
  }
}

// ---------------------------------------------------------------- package bar

export function BoqPackageBar({
  packages,
  current,
  itemCount,
  canWrite,
  busy,
  onSelect,
  onEnable,
  onManage,
}: {
  packages: BidPackage[]
  current: BidPackage | null
  itemCount: number | null
  canWrite: boolean
  busy: boolean
  onSelect: (id: string) => void
  onEnable: () => void
  onManage: () => void
}) {
  const hasPackages = packages.length > 0
  const hasSnapshot = Boolean(current?.has_boq) || itemCount !== null
  const countLabel =
    itemCount !== null
      ? `${itemCount} 条清单`
      : current?.has_boq
        ? '已有清单'
        : '未上传清单'

  return (
    <section className="boq-package-bar" aria-label="包件切换">
      <div className="boq-package-main">
        <span className="boq-package-eyebrow">
          <PackageIcon size={13} aria-hidden />
          包件
        </span>

        {hasPackages ? (
          <>
            <label className="select-field select-field--compact boq-package-select">
              <span className="sr-only">切换包件</span>
              <select
                value={current?.id ?? packages[0]?.id ?? ''}
                disabled={busy}
                onChange={(event) => onSelect(event.target.value)}
              >
                {packages.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                    {item.number ? ` · ${item.number}` : ''}
                    {item.has_boq ? '' : '（未上传）'}
                  </option>
                ))}
              </select>
            </label>
            <Badge tone={hasSnapshot ? 'info' : 'neutral'} subtle title={countLabel}>
              {countLabel}
            </Badge>
          </>
        ) : (
          <span className="boq-package-empty-copy">
            尚未启用包件，清单仍按项目单份管理。
          </span>
        )}
      </div>

      <div className="boq-package-actions">
        {hasPackages ? (
          <Button
            variant="ghost"
            size="sm"
            icon={<Settings2 size={14} />}
            onClick={onManage}
            title="查看、新增、改名或删除包件"
          >
            管理包件
          </Button>
        ) : (
          <Button
            variant="secondary"
            size="sm"
            icon={<PackagePlus size={14} />}
            disabled={!canWrite}
            title={!canWrite ? '只读成员不能创建包件' : '为项目启用多包件管理'}
            onClick={onEnable}
          >
            启用包件
          </Button>
        )}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- enable modal

export function EnablePackageModal({
  open,
  projectRef,
  onClose,
  onCreated,
}: {
  open: boolean
  projectRef: string
  onClose: () => void
  onCreated: (pkg: BidPackage) => void
}) {
  const toast = useToast()
  const [name, setName] = useState('')
  const [number, setNumber] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setName('')
    setNumber('')
    setSubmitting(false)
    setError(null)
  }, [open])

  const submit = async () => {
    const trimmed = name.trim()
    if (!trimmed || submitting) {
      if (!trimmed) setError('请填写包件名称。')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const input: BidPackageInput = { name: trimmed }
      const trimmedNumber = number.trim()
      if (trimmedNumber) input.number = trimmedNumber
      const created = await createBidPackage(projectRef, input)
      toast.success(
        '包件已启用',
        '原有清单快照（如有）已自动归入这个包件，下次新增包件需单独上传清单。',
      )
      onCreated(created)
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={submitting ? () => undefined : onClose}
      title="启用多包件"
      eyebrow="首个包件"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="enable-package-form"
            loading={submitting}
            disabled={!name.trim()}
          >
            创建包件
          </Button>
        </>
      }
    >
      <form
        id="enable-package-form"
        className="boq-package-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="boq-package-adopt-note">
          <Info size={16} aria-hidden />
          <div>
            <strong>首个包件会自动接管现有清单</strong>
            <p>
              当前项目已有的清单快照、对齐结果、报价与忽略记录都会归入这个包件。
              台账材料由所有包件共享，不会被复制或删除。
            </p>
          </div>
        </div>

        <div className="form-field">
          <label htmlFor="enable-package-name">
            包件名称 <span className="required-mark">必填</span>
          </label>
          <input
            id="enable-package-name"
            className={className('input', error && !name.trim() && 'input--error')}
            value={name}
            onChange={(event) => {
              setName(event.target.value)
              if (error) setError(null)
            }}
            placeholder="例如：一标段 / 包件1"
            maxLength={80}
            autoComplete="off"
            data-autofocus
          />
          <p className="field-hint">不超过 80 个字符；同一项目内不能重名。</p>
        </div>

        <div className="form-field">
          <label htmlFor="enable-package-number">
            包件编号 <span className="optional-mark">可选</span>
          </label>
          <input
            id="enable-package-number"
            className="input"
            value={number}
            onChange={(event) => setNumber(event.target.value)}
            placeholder="例如：A 包 / 招标编号后四位"
            maxLength={40}
            autoComplete="off"
          />
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

// ---------------------------------------------------------------- manage modal

export function ManagePackagesModal({
  open,
  projectRef,
  packages,
  currentId,
  readOnly,
  onClose,
  onChanged,
  onCreated,
  onDeleted,
  onSelect,
}: {
  open: boolean
  projectRef: string
  packages: BidPackage[]
  currentId: string | null
  readOnly: boolean
  onClose: () => void
  onChanged: () => void
  onCreated: (pkg: BidPackage) => void
  onDeleted: (id: string) => void
  onSelect: (id: string) => void
}) {
  const toast = useToast()
  const confirm = useConfirm()
  const [newName, setNewName] = useState('')
  const [newNumber, setNewNumber] = useState('')
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [editNumber, setEditNumber] = useState('')
  const [savingId, setSavingId] = useState<string | null>(null)
  const [rowError, setRowError] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setNewName('')
    setNewNumber('')
    setAddError(null)
    setEditingId(null)
    setRowError(null)
    setSavingId(null)
    setDeletingId(null)
  }, [open])

  const startEdit = (item: BidPackage) => {
    setEditingId(item.id)
    setEditName(item.name)
    setEditNumber(item.number)
    setRowError(null)
  }

  const cancelEdit = () => {
    setEditingId(null)
    setRowError(null)
  }

  const saveEdit = async (item: BidPackage) => {
    if (readOnly || savingId) return
    const trimmed = editName.trim()
    if (!trimmed) {
      setRowError('包件名称不能为空。')
      return
    }
    const patch: Partial<BidPackageInput> = {}
    if (trimmed !== item.name) patch.name = trimmed
    if (editNumber.trim() !== item.number) patch.number = editNumber.trim()
    if (!Object.keys(patch).length) {
      cancelEdit()
      return
    }
    setSavingId(item.id)
    setRowError(null)
    try {
      await updateBidPackage(projectRef, item.id, patch)
      toast.success('包件信息已更新', `「${trimmed}」已保存。`)
      setEditingId(null)
      onChanged()
    } catch (err) {
      setRowError(messageOf(err))
    } finally {
      setSavingId(null)
    }
  }

  const addPackage = async () => {
    if (readOnly || adding) return
    const trimmed = newName.trim()
    if (!trimmed) {
      setAddError('请填写包件名称。')
      return
    }
    setAdding(true)
    setAddError(null)
    try {
      const input: BidPackageInput = { name: trimmed }
      const trimmedNumber = newNumber.trim()
      if (trimmedNumber) input.number = trimmedNumber
      const created = await createBidPackage(projectRef, input)
      toast.success('包件已创建', `现在可以为「${trimmed}」单独上传清单。`)
      setNewName('')
      setNewNumber('')
      onChanged()
      onCreated(created)
    } catch (err) {
      setAddError(messageOf(err))
    } finally {
      setAdding(false)
    }
  }

  const removePackage = async (item: BidPackage) => {
    if (readOnly || deletingId) return
    const accepted = await confirm({
      title: `删除包件「${item.name}」？`,
      description: item.has_boq
        ? '将删除该包件的清单快照、对齐与报价记录，并移除已上传的清单文件。台账材料由所有包件共享，不受影响，此操作不可撤销。'
        : '该包件尚未上传清单，删除后不可恢复。台账材料由所有包件共享，不受影响。',
      confirmLabel: '删除包件',
      tone: 'danger',
    })
    if (!accepted) return
    setDeletingId(item.id)
    try {
      await deleteBidPackage(projectRef, item.id)
      toast.success('包件已删除', `「${item.name}」及其清单快照已移除。`)
      if (editingId === item.id) setEditingId(null)
      onChanged()
      onDeleted(item.id)
    } catch (err) {
      toast.error('删除包件失败', messageOf(err))
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="管理包件"
      eyebrow="多包件清单"
      size="md"
      footer={
        <Button variant="secondary" onClick={onClose}>
          关闭
        </Button>
      }
    >
      <div className="boq-package-manager">
        <p className="boq-package-intro">
          每个包件单独维护工程量清单、对齐与报价；台账材料由所有包件共享。
        </p>

        {packages.length === 0 && !readOnly && (
          <div className="boq-package-adopt-note">
            <Info size={16} aria-hidden />
            <div>
              <strong>首个包件会自动接管现有清单</strong>
              <p>
                项目当前若已有清单快照，会归入第一个创建的包件；台账材料不会被复制或删除。
              </p>
            </div>
          </div>
        )}

        {!readOnly ? (
          <form
            className="boq-package-add"
            onSubmit={(event) => {
              event.preventDefault()
              void addPackage()
            }}
          >
            <div className="boq-package-add-fields">
              <label className="boq-package-add-field">
                <span>新包件名称</span>
                <input
                  className="input input--sm"
                  value={newName}
                  onChange={(event) => {
                    setNewName(event.target.value)
                    if (addError) setAddError(null)
                  }}
                  placeholder="例如：二标段 / 包件2"
                  maxLength={80}
                  autoComplete="off"
                />
              </label>
              <label className="boq-package-add-field boq-package-add-field--number">
                <span>编号（可选）</span>
                <input
                  className="input input--sm"
                  value={newNumber}
                  onChange={(event) => setNewNumber(event.target.value)}
                  placeholder="A 包"
                  maxLength={40}
                  autoComplete="off"
                />
              </label>
              <Button
                type="submit"
                size="sm"
                variant="primary"
                icon={<PackagePlus size={13} />}
                loading={adding}
                disabled={!newName.trim()}
              >
                新增
              </Button>
            </div>
            {addError && (
              <div className="form-alert" role="alert">
                {addError}
              </div>
            )}
          </form>
        ) : (
          <div className="boq-package-readonly">
            <Info size={15} aria-hidden />
            <span>只读成员可以查看和切换包件；新增、改名与删除需要投标经理权限。</span>
          </div>
        )}

        {packages.length === 0 ? (
          <div className="boq-package-empty">
            <PackageIcon size={20} aria-hidden />
            <strong>还没有包件</strong>
            <p>创建第一个包件后，即可为不同标段分别上传清单。</p>
          </div>
        ) : (
          <ul className="boq-package-list">
            {packages.map((item) => {
              const editing = editingId === item.id
              const isCurrent = currentId === item.id
              return (
                <li
                  key={item.id}
                  className={className('boq-package-row', isCurrent && 'is-current')}
                >
                  {editing ? (
                    <div className="boq-package-edit">
                      <div className="boq-package-add-fields">
                        <label className="boq-package-add-field">
                          <span>包件名称</span>
                          <input
                            className="input input--sm"
                            value={editName}
                            onChange={(event) => {
                              setEditName(event.target.value)
                              if (rowError) setRowError(null)
                            }}
                            maxLength={80}
                            autoComplete="off"
                            autoFocus
                            onKeyDown={(event) => {
                              if (event.key === 'Escape') {
                                event.preventDefault()
                                cancelEdit()
                              }
                            }}
                          />
                        </label>
                        <label className="boq-package-add-field boq-package-add-field--number">
                          <span>编号</span>
                          <input
                            className="input input--sm"
                            value={editNumber}
                            onChange={(event) => setEditNumber(event.target.value)}
                            maxLength={40}
                            autoComplete="off"
                            onKeyDown={(event) => {
                              if (event.key === 'Escape') {
                                event.preventDefault()
                                cancelEdit()
                              }
                            }}
                          />
                        </label>
                      </div>
                      <div className="boq-package-row-actions">
                        <Button
                          size="sm"
                          variant="primary"
                          icon={<Check size={12} />}
                          loading={savingId === item.id}
                          disabled={!editName.trim()}
                          onClick={() => void saveEdit(item)}
                        >
                          保存
                        </Button>
                        <button
                          type="button"
                          className="boq-text-action"
                          disabled={savingId === item.id}
                          onClick={cancelEdit}
                        >
                          <X size={12} aria-hidden />
                          取消
                        </button>
                      </div>
                      {rowError && (
                        <div className="form-alert boq-package-row-error" role="alert">
                          {rowError}
                        </div>
                      )}
                    </div>
                  ) : (
                    <>
                      <div className="boq-package-row-main">
                        <div className="boq-package-row-name">
                          <strong>{item.name}</strong>
                          {isCurrent && (
                            <Badge tone="accent" subtle>
                              当前
                            </Badge>
                          )}
                          <Badge tone={item.has_boq ? 'info' : 'neutral'} subtle>
                            {item.has_boq ? '已有清单' : '未上传'}
                          </Badge>
                        </div>
                        <span className="boq-package-row-meta">
                          {item.number ? `编号 ${item.number}` : '未填写编号'}
                        </span>
                      </div>

                      <div className="boq-package-row-actions">
                        {!isCurrent && (
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={<ArrowRightLeft size={12} />}
                            onClick={() => {
                              onSelect(item.id)
                              onClose()
                            }}
                          >
                            切换
                          </Button>
                        )}
                        {!readOnly && (
                          <>
                            <Button
                              size="sm"
                              variant="ghost"
                              icon={<Pencil size={12} />}
                              disabled={Boolean(deletingId)}
                              onClick={() => startEdit(item)}
                            >
                              改名
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              icon={<Trash2 size={12} />}
                              loading={deletingId === item.id}
                              disabled={Boolean(savingId) || Boolean(deletingId)}
                              onClick={() => void removePackage(item)}
                            >
                              删除
                            </Button>
                          </>
                        )}
                      </div>
                    </>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </Modal>
  )
}

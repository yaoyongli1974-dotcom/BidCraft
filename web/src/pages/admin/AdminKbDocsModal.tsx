import { useEffect, useState } from 'react'
import { FileText, RefreshCw, Tags, Trash2 } from 'lucide-react'
import type { KbDocMetaItem, KbDocMetaResponse, KnowledgeBase } from '../../lib/types'
import { className } from '../../lib/format'
import {
  deleteKbDoc,
  getKbDocs,
  messageOf,
  updateKbDoc,
} from '../../lib/api'
import { Badge } from '../../components/Badge'
import { Button, IconButton } from '../../components/Button'
import { EmptyState, ErrorState, Skeleton } from '../../components/Feedback'
import { Modal } from '../../components/Modal'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'

const FIELD_LABELS: Record<string, string> = {
  cert_no: '证书编号',
  valid_to: '有效期至',
  contract_no: '合同编号',
  owner: '业主',
  amount: '金额',
  acceptance: '验收结论',
  social_months: '社保缴纳月份',
  locked: '在建锁定',
  source_url: '官方出处',
  verifiable_at: '可核验平台',
}

const LOCKED_VALUES = new Set(['true', '1', 'yes', '是', '已锁定'])

function fieldLabel(key: string): string {
  return FIELD_LABELS[key] || key
}

function isLockedValue(value: string): boolean {
  return LOCKED_VALUES.has(value.trim().toLowerCase())
}

function sourceName(source: string): string {
  const parts = source.split(/[\\/]/)
  return parts[parts.length - 1] || source
}

/** 素材元数据弹窗：左侧列出全部片段来源，右侧按策略登记 / 删除元数据。 */
export function AdminKbDocsModal({
  kb,
  onClose,
}: {
  kb: KnowledgeBase
  onClose: () => void
}) {
  const toast = useToast()
  const confirm = useConfirm()
  const [data, setData] = useState<KbDocMetaResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [selected, setSelected] = useState('')
  const [kind, setKind] = useState('')
  const [fields, setFields] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getKbDocs(kb.id)
      .then((next) => {
        if (cancelled) return
        setData(next)
        setSelected((current) => {
          if (current && next.sources.some((entry) => entry.source === current)) {
            return current
          }
          return next.sources[0]?.source || ''
        })
      })
      .catch((err) => {
        if (cancelled) return
        setError(messageOf(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [kb.id, reloadKey])

  // Reset the draft whenever the selected source (or refreshed server data) changes.
  useEffect(() => {
    if (!data) return
    const item = data.items.find((entry) => entry.source === selected)
    setKind(item?.kind || '')
    setFields(item ? { ...item.fields } : {})
  }, [selected, data])

  const sources = data?.sources || []
  const selectedItem: KbDocMetaItem | undefined = data?.items.find(
    (entry) => entry.source === selected,
  )
  const policy = data?.policy || {}
  const kindOptions = Object.entries(policy)
  const required = policy[kind]?.required || []

  const setField = (key: string, value: string) => {
    setFields((current) => ({ ...current, [key]: value }))
    if (actionError) setActionError(null)
  }

  const save = async () => {
    if (!selected || saving) return
    if (!kind) {
      setActionError('请先选择素材类型')
      return
    }
    setSaving(true)
    setActionError(null)
    try {
      const payloadFields: Record<string, string> = {}
      Object.entries(fields).forEach(([key, value]) => {
        payloadFields[key] = String(value ?? '')
      })
      await updateKbDoc(kb.id, {
        source: selected,
        kind,
        fields: payloadFields,
      })
      toast.success('素材元数据已保存', sourceName(selected))
      setReloadKey((value) => value + 1)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('保存素材元数据失败', message)
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!selectedItem || deleting) return
    const accepted = await confirm({
      title: '删除素材登记？',
      description: (
        <>
          将移除 <strong>{sourceName(selectedItem.source)}</strong> 的素材元数据。
        </>
      ),
      note: '只删除登记信息，知识库文件与索引片段不受影响。',
      confirmLabel: '删除登记',
      tone: 'danger',
    })
    if (!accepted) return
    setDeleting(true)
    setActionError(null)
    try {
      await deleteKbDoc(kb.id, selectedItem.source)
      toast.success('素材登记已删除', sourceName(selectedItem.source))
      setReloadKey((value) => value + 1)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('删除素材登记失败', message)
    } finally {
      setDeleting(false)
    }
  }

  const renderField = (key: string) => {
    const value = fields[key] ?? ''
    const label = fieldLabel(key)
    if (key === 'locked') {
      return (
        <div className="form-field" key={key}>
          <label htmlFor={`kb-doc-field-${key}`}>{label}</label>
          <select
            id={`kb-doc-field-${key}`}
            className="input"
            value={value === '' ? '' : isLockedValue(value) ? 'true' : 'false'}
            onChange={(event) => setField(key, event.target.value)}
            disabled={saving || deleting}
          >
            <option value="">未填写</option>
            <option value="true">是（在建锁定）</option>
            <option value="false">否</option>
          </select>
        </div>
      )
    }
    if (key === 'valid_to') {
      return (
        <div className="form-field" key={key}>
          <label htmlFor={`kb-doc-field-${key}`}>{label}</label>
          <input
            id={`kb-doc-field-${key}`}
            className="input"
            type="date"
            value={value}
            onChange={(event) => setField(key, event.target.value)}
            disabled={saving || deleting}
          />
        </div>
      )
    }
    if (key === 'social_months' || key === 'amount') {
      return (
        <div className="form-field" key={key}>
          <label htmlFor={`kb-doc-field-${key}`}>{label}</label>
          <input
            id={`kb-doc-field-${key}`}
            className="input"
            type="number"
            min={key === 'social_months' ? 0 : undefined}
            max={key === 'social_months' ? 600 : undefined}
            step={key === 'social_months' ? 1 : 'any'}
            value={value}
            onChange={(event) => setField(key, event.target.value)}
            disabled={saving || deleting}
          />
          {key === 'social_months' && (
            <p className="field-hint">0–600 个月。</p>
          )}
        </div>
      )
    }
    return (
      <div className="form-field" key={key}>
        <label htmlFor={`kb-doc-field-${key}`}>{label}</label>
        <input
          id={`kb-doc-field-${key}`}
          className="input"
          value={value}
          onChange={(event) => setField(key, event.target.value)}
          maxLength={key === 'source_url' ? 500 : 200}
          autoComplete="off"
          disabled={saving || deleting}
        />
      </div>
    )
  }

  return (
    <Modal
      open
      onClose={saving || deleting ? () => undefined : onClose}
      title="素材元数据"
      eyebrow={kb.name}
      size="lg"
      className="kb-docs-modal"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving || deleting}>
            关闭
          </Button>
          <Button
            variant="secondary"
            icon={<RefreshCw size={14} />}
            disabled={loading || saving || deleting}
            onClick={() => setReloadKey((value) => value + 1)}
          >
            刷新
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="kb-docs-form"
            loading={saving}
            disabled={!selected || deleting}
          >
            保存
          </Button>
        </>
      }
    >
      <div className="kb-docs">
        <div className="kb-docs-note">
          <Tags size={15} aria-hidden />
          <span>
            按素材类型登记可信字段；缺失必填项或证书过期的片段只会作为题录引用，
            不注入正文。左侧列出知识库内全部片段来源。
          </span>
        </div>

        {loading && !data ? (
          <div className="kb-docs-loading">
            <Skeleton width="38%" height={14} />
            <Skeleton width="100%" height={42} />
            <Skeleton width="100%" height={42} />
            <Skeleton width="82%" height={42} />
          </div>
        ) : error ? (
          <ErrorState
            message={error}
            onRetry={() => setReloadKey((value) => value + 1)}
          />
        ) : sources.length === 0 ? (
          <EmptyState
            icon={Tags}
            compact
            title="还没有可登记的素材"
            description="知识库还没有索引片段。先重建索引，再回来登记证书编号、合同信息或官方出处。"
            actions={
              <Button
                variant="secondary"
                icon={<RefreshCw size={14} />}
                onClick={() => setReloadKey((value) => value + 1)}
              >
                重新读取
              </Button>
            }
          />
        ) : (
          <div className="kb-docs-grid">
            <aside className="kb-docs-sources" aria-label="素材来源">
              <header className="kb-docs-sources-head">
                <span>素材来源</span>
                <span className="kb-docs-count">{sources.length}</span>
              </header>
              <div className="kb-docs-source-list">
                {sources.map((entry) => {
                  const registered = data?.items.some(
                    (item) => item.source === entry.source,
                  )
                  return (
                    <button
                      key={entry.source}
                      type="button"
                      className={className(
                        'kb-docs-source',
                        selected === entry.source && 'is-active',
                      )}
                      title={entry.source}
                      onClick={() => setSelected(entry.source)}
                      aria-pressed={selected === entry.source}
                    >
                      <span className="kb-docs-source-main">
                        <FileText size={13} aria-hidden />
                        <span className="kb-docs-source-name">
                          {sourceName(entry.source)}
                        </span>
                      </span>
                      <span className="kb-docs-source-meta">
                        <span>{entry.chunks} 片段</span>
                        {registered ? (
                          <Badge tone="success" subtle>
                            已登记
                          </Badge>
                        ) : (
                          <span className="kb-docs-unregistered">未登记</span>
                        )}
                      </span>
                    </button>
                  )
                })}
              </div>
            </aside>

            <div className="kb-docs-editor">
              <div className="kb-docs-editor-head">
                <div className="kb-docs-editor-title">
                  <strong title={selected}>{sourceName(selected)}</strong>
                  <span className="cell-sub mono" title={selected}>
                    {selected}
                  </span>
                </div>
                <div className="kb-docs-editor-actions">
                  {selectedItem ? (
                    <Badge
                      tone={selectedItem.ok ? 'success' : 'danger'}
                      dot
                      subtle
                    >
                      {selectedItem.ok ? '可全文引用' : '仅题录引用'}
                    </Badge>
                  ) : (
                    <Badge tone="neutral" subtle>
                      未登记
                    </Badge>
                  )}
                  {selectedItem && (
                    <IconButton
                      label={`删除素材登记 ${sourceName(selected)}`}
                      tone="danger"
                      disabled={saving || deleting}
                      onClick={() => void remove()}
                    >
                      {deleting ? (
                        <RefreshCw size={14} className="spin" />
                      ) : (
                        <Trash2 size={14} />
                      )}
                    </IconButton>
                  )}
                </div>
              </div>

              {selectedItem && !selectedItem.ok && selectedItem.reason && (
                <div className="kb-docs-reason" role="status">
                  <strong>{selectedItem.reason}</strong>
                  <span>补齐必填字段或更新有效期后重新保存。</span>
                </div>
              )}

              <form
                id="kb-docs-form"
                className="kb-docs-form"
                onSubmit={(event) => {
                  event.preventDefault()
                  void save()
                }}
              >
                <div className="form-field">
                  <label htmlFor="kb-doc-kind">
                    素材类型 <span className="required-mark">必填</span>
                  </label>
                  <select
                    id="kb-doc-kind"
                    className="input"
                    value={kind}
                    onChange={(event) => {
                      setKind(event.target.value)
                      if (actionError) setActionError(null)
                    }}
                    disabled={saving || deleting}
                  >
                    <option value="" disabled>
                      请选择素材类型
                    </option>
                    {kindOptions.map(([value, spec]) => (
                      <option key={value} value={value}>
                        {spec.label || value}
                        {value === 'other' ? '（需出处）' : ''}
                      </option>
                    ))}
                  </select>
                  {policy[kind]?.hint && (
                    <p className="field-hint">{policy[kind].hint}</p>
                  )}
                </div>

                {required.length > 0 ? (
                  <div className="kb-docs-field-grid">
                    {required.map((key) => renderField(key))}
                  </div>
                ) : (
                  <div className="kb-docs-no-fields">
                    该类型没有必填字段，保存后即视为可全文引用。
                  </div>
                )}

                {actionError && (
                  <div className="form-alert" role="alert">
                    {actionError}
                  </div>
                )}
              </form>
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}

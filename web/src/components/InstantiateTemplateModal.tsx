import { useEffect, useState } from 'react'
import { Modal } from './Modal'
import { Button } from './Button'
import { getProjectTemplates, instantiateTemplate, messageOf } from '../lib/api'
import type { Project, ProjectTemplate } from '../lib/types'
import { className, formatRelative } from '../lib/format'

interface InstantiateTemplateModalProps {
  open: boolean
  onClose: () => void
  onCreated: (project: Project) => void
}

const NAME_MAX = 255

/**
 * R23 从模板创建：打开时拉取模板列表，选择模板后默认名「{模板名} 副本」，
 * 清单/配图开关默认勾选；台账、大纲与正文始终复制。表单样式复用项目复制。
 */
export function InstantiateTemplateModal({
  open,
  onClose,
  onCreated,
}: InstantiateTemplateModalProps) {
  const [templates, setTemplates] = useState<ProjectTemplate[]>([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [templateId, setTemplateId] = useState('')
  const [name, setName] = useState('')
  const [nameTouched, setNameTouched] = useState(false)
  const [includeBoq, setIncludeBoq] = useState(true)
  const [includeImages, setIncludeImages] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    setError(null)
    setSubmitting(false)
    setIncludeBoq(true)
    setIncludeImages(true)
    setNameTouched(false)
    getProjectTemplates()
      .then((list) => {
        if (cancelled) return
        setTemplates(list)
        setLoading(false)
        const first = list[0]
        setTemplateId(first?.id || '')
        setName(first ? `${first.name} 副本` : '')
      })
      .catch((err) => {
        if (cancelled) return
        setLoading(false)
        setLoadError(messageOf(err))
      })
    return () => {
      cancelled = true
    }
  }, [open, reloadKey])

  const selected = templates.find((item) => item.id === templateId) || null
  const trimmedName = name.trim()
  const tooLong = trimmedName.length > NAME_MAX
  const canSubmit =
    Boolean(selected) && trimmedName.length > 0 && !tooLong && !submitting && !loading

  const selectTemplate = (id: string) => {
    setTemplateId(id)
    // 用户未手动改过名称时，跟随所选模板刷新默认名。
    if (!nameTouched) {
      const next = templates.find((item) => item.id === id)
      setName(next ? `${next.name} 副本` : '')
    }
  }

  const submit = async () => {
    if (!selected || !canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const created = await instantiateTemplate(selected.id, {
        name: trimmedName,
        includeBoq,
        includeImages,
      })
      setSubmitting(false)
      onCreated(created)
    } catch (err) {
      setSubmitting(false)
      setError(messageOf(err))
    }
  }

  return (
    <Modal
      open={open}
      onClose={submitting ? () => undefined : onClose}
      title="从模板创建"
      eyebrow="项目模板库"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="instantiate-template-form"
            loading={submitting}
            disabled={!canSubmit}
          >
            创建项目
          </Button>
        </>
      }
    >
      <form
        id="instantiate-template-form"
        className="new-project-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <p className="field-hint">
          模板全员可见；从模板创建的新项目归你负责，可放心修改，不影响模板本身。
        </p>

        <div className="form-field">
          <label htmlFor="instantiate-template">
            选择模板 <span className="required-mark">必选</span>
          </label>
          <div className="select-field select-field--block">
            <select
              id="instantiate-template"
              value={templateId}
              onChange={(event) => selectTemplate(event.target.value)}
              disabled={loading || submitting || templates.length === 0}
              data-autofocus
            >
              {loading && <option value="">正在读取模板…</option>}
              {!loading && templates.length === 0 && <option value="">暂无模板</option>}
              {templates.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                  {item.owner_name ? `（负责人：${item.owner_name}）` : ''}
                </option>
              ))}
            </select>
          </div>
          {selected && (
            <p className="field-hint">
              规则包 {selected.package || '未知'} · 更新于{' '}
              {formatRelative(selected.updated_at)}
              {selected.owner_name ? ` · 负责人 ${selected.owner_name}` : ''}
            </p>
          )}
          {!loading && !loadError && templates.length === 0 && (
            <p className="field-hint">
              还没有模板。项目负责人可在「编辑项目信息」中勾选「设为模板」，模板全员可见。
            </p>
          )}
          {loadError && (
            <div className="form-alert" role="alert">
              {loadError}
              <button
                type="button"
                className="text-btn"
                onClick={() => setReloadKey((value) => value + 1)}
              >
                重试
              </button>
            </div>
          )}
        </div>

        <div className="form-field">
          <div className="field-label-row">
            <label htmlFor="instantiate-project-name">
              新项目名称 <span className="required-mark">必填</span>
            </label>
            <span className={className('field-hint', 'mono', tooLong && 'field-error')}>
              {name.length}/{NAME_MAX}
            </span>
          </div>
          <input
            id="instantiate-project-name"
            className={className('input', (!trimmedName || tooLong) && 'input--error')}
            value={name}
            onChange={(event) => {
              setName(event.target.value)
              setNameTouched(true)
            }}
            maxLength={NAME_MAX}
            autoComplete="off"
            disabled={loading || !selected || submitting}
          />
          {!trimmedName && <p className="field-error">请填写项目名称。</p>}
          {tooLong && <p className="field-error">名称不能超过 {NAME_MAX} 个字符。</p>}
        </div>

        <div className="form-field">
          <label htmlFor="instantiate-include-boq">复制内容</label>
          <div className="admin-check-row">
            <label className="admin-check">
              <input
                id="instantiate-include-boq"
                type="checkbox"
                checked={includeBoq}
                onChange={(event) => setIncludeBoq(event.target.checked)}
                disabled={submitting}
              />
              <span>
                <strong>复制清单与报价</strong>
                <small>BOQ 包件与清单快照（含单价）</small>
              </span>
            </label>
            <label className="admin-check">
              <input
                type="checkbox"
                checked={includeImages}
                onChange={(event) => setIncludeImages(event.target.checked)}
                disabled={submitting}
              />
              <span>
                <strong>复制配图</strong>
                <small>已生成的章节配图资产</small>
              </span>
            </label>
          </div>
          <p className="field-hint">
            源文件、事实台账、大纲与正文始终复制；报价版本与审批记录不会复制。
          </p>
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
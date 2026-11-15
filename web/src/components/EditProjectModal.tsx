import { useEffect, useState } from 'react'
import { Modal } from './Modal'
import { Button } from './Button'
import { messageOf, updateProject } from '../lib/api'
import type { Project } from '../lib/types'
import { className } from '../lib/format'

interface EditProjectModalProps {
  project: Project | null
  onClose: () => void
  onSaved: (project: Project) => void
}

/** 轻量编辑：只改项目名称与标段/包件，其余信息由解析与台账维护。 */
export function EditProjectModal({ project, onClose, onSaved }: EditProjectModalProps) {
  const [name, setName] = useState('')
  const [bidSection, setBidSection] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!project) return
    setName(project.name)
    setBidSection(project.bid_section || '')
    setError(null)
    setSubmitting(false)
  }, [project])

  const trimmedName = name.trim()
  const trimmedSection = bidSection.trim()
  const dirty =
    Boolean(project) &&
    (trimmedName !== (project?.name || '') ||
      trimmedSection !== (project?.bid_section || ''))
  const canSubmit = Boolean(project) && trimmedName.length > 0 && dirty && !submitting

  const submit = async () => {
    if (!project || !canSubmit) return
    const patch: { name?: string; bid_section?: string } = {}
    if (trimmedName !== project.name) patch.name = trimmedName
    if (trimmedSection !== (project.bid_section || '')) patch.bid_section = trimmedSection
    setSubmitting(true)
    setError(null)
    try {
      const updated = await updateProject(project.id, patch)
      setSubmitting(false)
      onSaved(updated)
    } catch (err) {
      setSubmitting(false)
      setError(messageOf(err))
    }
  }

  return (
    <Modal
      open={Boolean(project)}
      onClose={submitting ? () => undefined : onClose}
      title="编辑项目信息"
      eyebrow="名称与标段"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="edit-project-form"
            loading={submitting}
            disabled={!canSubmit}
          >
            保存修改
          </Button>
        </>
      }
    >
      <form
        id="edit-project-form"
        className="new-project-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="form-field">
          <label htmlFor="edit-project-name">
            项目名称 <span className="required-mark">必填</span>
          </label>
          <input
            id="edit-project-name"
            className={className('input', !trimmedName && 'input--error')}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={80}
            autoComplete="off"
            data-autofocus
          />
          <p className="field-hint">1–80 个字符，会同步到工作区标题与封面信息。</p>
        </div>

        <div className="form-field">
          <label htmlFor="edit-project-section">
            标段 / 包件 <span className="optional-mark">可选</span>
          </label>
          <input
            id="edit-project-section"
            className="input"
            value={bidSection}
            onChange={(event) => setBidSection(event.target.value)}
            placeholder="例如：一标段 / 包件1"
            maxLength={60}
            autoComplete="off"
          />
          <p className="field-hint">不超过 60 个字符；清空即取消标段标识。</p>
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

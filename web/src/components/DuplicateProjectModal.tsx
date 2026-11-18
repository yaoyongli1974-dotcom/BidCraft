import { useEffect, useState } from 'react'
import { Modal } from './Modal'
import { Button } from './Button'
import { duplicateProject, messageOf } from '../lib/api'
import type { Project } from '../lib/types'
import { className } from '../lib/format'

interface DuplicateProjectModalProps {
  project: Project | null
  onClose: () => void
  onDuplicated: (project: Project) => void
}

const NAME_MAX = 255

/** R22 项目复制：新名称 + 清单/配图开关；台账、大纲与正文始终复制。 */
export function DuplicateProjectModal({
  project,
  onClose,
  onDuplicated,
}: DuplicateProjectModalProps) {
  const [name, setName] = useState('')
  const [includeBoq, setIncludeBoq] = useState(true)
  const [includeImages, setIncludeImages] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!project) return
    setName(`${project.name} 副本`)
    setIncludeBoq(true)
    setIncludeImages(true)
    setError(null)
    setSubmitting(false)
  }, [project])

  const trimmedName = name.trim()
  const tooLong = trimmedName.length > NAME_MAX
  const canSubmit =
    Boolean(project) && trimmedName.length > 0 && !tooLong && !submitting

  const submit = async () => {
    if (!project || !canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const created = await duplicateProject(project.id, {
        name: trimmedName,
        includeBoq,
        includeImages,
      })
      setSubmitting(false)
      onDuplicated(created)
    } catch (err) {
      setSubmitting(false)
      setError(messageOf(err))
    }
  }

  return (
    <Modal
      open={Boolean(project)}
      onClose={submitting ? () => undefined : onClose}
      title="复制项目"
      eyebrow="模板复用"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="duplicate-project-form"
            loading={submitting}
            disabled={!canSubmit}
          >
            创建副本
          </Button>
        </>
      }
    >
      <form
        id="duplicate-project-form"
        className="new-project-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <p className="field-hint">
          从「{project?.name}」创建独立副本，可放心修改，不影响源项目。
        </p>

        <div className="form-field">
          <div className="field-label-row">
            <label htmlFor="duplicate-project-name">
              新项目名称 <span className="required-mark">必填</span>
            </label>
            <span className={className('field-hint', 'mono', tooLong && 'field-error')}>
              {name.length}/{NAME_MAX}
            </span>
          </div>
          <input
            id="duplicate-project-name"
            className={className('input', (!trimmedName || tooLong) && 'input--error')}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={NAME_MAX}
            autoComplete="off"
            data-autofocus
          />
          {!trimmedName && <p className="field-error">请填写副本名称。</p>}
          {tooLong && <p className="field-error">名称不能超过 {NAME_MAX} 个字符。</p>}
        </div>

        <div className="form-field">
          <label htmlFor="duplicate-include-boq">复制内容</label>
          <div className="admin-check-row">
            <label className="admin-check">
              <input
                id="duplicate-include-boq"
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

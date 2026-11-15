import { useEffect, useState } from 'react'
import { Check, Loader2, PackageSearch } from 'lucide-react'
import { Modal } from './Modal'
import { Button } from './Button'
import { Badge } from './Badge'
import { createProject, messageOf } from '../lib/api'
import type { PackageInfo, Project } from '../lib/types'
import {
  className,
  normalizeProjectType,
  projectTypeMeta,
  SELECTABLE_PROJECT_TYPES,
} from '../lib/format'

interface NewProjectModalProps {
  open: boolean
  onClose: () => void
  packages: PackageInfo[]
  packagesLoading: boolean
  packagesError: string | null
  onRetryPackages: () => void
  onCreated: (project: Project) => void
}

export function NewProjectModal({
  open,
  onClose,
  packages,
  packagesLoading,
  packagesError,
  onRetryPackages,
  onCreated,
}: NewProjectModalProps) {
  const [name, setName] = useState('')
  const [bidSection, setBidSection] = useState('')
  const [packageName, setPackageName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) return
    setName('')
    setBidSection('')
    setError(null)
    setTouched(false)
    setSubmitting(false)
    setPackageName((current) =>
      current && packages.some((pkg) => pkg.name === current)
        ? current
        : defaultPackageName(packages),
    )
    // Reset only when the dialog opens; package data may arrive later.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    if (!packageName && packages.length) setPackageName(defaultPackageName(packages))
  }, [packages, packageName])

  const orderedGroups = buildPackageGroups(packages)

  const canSubmit = name.trim().length > 0 && Boolean(packageName) && !submitting

  const submit = async () => {
    setTouched(true)
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const project = await createProject(name.trim(), packageName, bidSection)
      setSubmitting(false)
      onCreated(project)
    } catch (err) {
      setSubmitting(false)
      setError(messageOf(err))
    }
  }

  return (
    <Modal
      open={open}
      onClose={submitting ? () => undefined : onClose}
      title="新建项目"
      eyebrow="从一份招标文件开始"
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="new-project-form"
            loading={submitting}
            disabled={!canSubmit}
          >
            创建并去导入
          </Button>
        </>
      }
    >
      <form
        id="new-project-form"
        className="new-project-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="form-field">
          <label htmlFor="project-name">
            项目名称 <span className="required-mark">必填</span>
          </label>
          <input
            id="project-name"
            className={className('input', touched && !name.trim() && 'input--error')}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：某园区智能化系统建设项目"
            maxLength={80}
            data-autofocus
            autoComplete="off"
          />
          <p className="field-hint">
            建议与招标文件封面一致，会用于事实台账、封面和导出文件名。
          </p>
          {touched && !name.trim() && <p className="field-error">请填写项目名称。</p>}
        </div>

        <div className="form-field">
          <label htmlFor="project-bid-section">
            标段 / 包件 <span className="optional-mark">可选</span>
          </label>
          <input
            id="project-bid-section"
            className="input"
            value={bidSection}
            onChange={(event) => setBidSection(event.target.value)}
            placeholder="例如：一标段 / 包件1"
            maxLength={60}
            autoComplete="off"
          />
          <p className="field-hint">
            同一招标下分标段投标时填写，用于列表筛选和标题识别。留空表示不区分标段。
          </p>
        </div>

        <div className="form-field">
          <div className="field-label-row">
            <label>
              规则包 <span className="required-mark">必填</span>
            </label>
            {packagesLoading && (
              <span className="field-inline-status">
                <Loader2 size={13} className="spin" /> 加载规则包
              </span>
            )}
          </div>
          {packagesError ? (
            <div className="inline-error" role="alert">
              <PackageSearch size={18} />
              <div>
                <strong>无法读取规则包</strong>
                <p>{packagesError}</p>
                <Button size="sm" variant="secondary" onClick={onRetryPackages}>
                  重试
                </Button>
              </div>
            </div>
          ) : (
            <div
              className="package-groups"
              role="radiogroup"
              aria-label="选择规则包"
            >
              {packagesLoading && !packages.length ? (
                <div className="package-grid">
                  {Array.from({ length: 4 }).map((_, index) => (
                    <div key={index} className="package-option package-option--skeleton">
                      <span className="skeleton" style={{ width: '45%', height: 14 }} />
                      <span className="skeleton" style={{ width: '70%', height: 11 }} />
                    </div>
                  ))}
                </div>
              ) : orderedGroups.length === 0 ? (
                <div className="package-empty">
                  <PackageSearch size={18} />
                  <div>
                    <strong>没有可选的规则包</strong>
                    <span>
                      {packages.length > 0
                        ? '当前只有抽象基包，无法直接创建项目。请联系管理员添加行业规则包。'
                        : '请确认后端已加载规则包，或稍后刷新重试。'}
                    </span>
                  </div>
                  <Button size="sm" variant="secondary" onClick={onRetryPackages}>
                    刷新
                  </Button>
                </div>
              ) : (
                orderedGroups.map((group) => (
                  <section
                    className="package-group"
                    key={group.type.id}
                    aria-label={group.type.label}
                  >
                    <div className="package-group-head">
                      <span className="package-group-title">{group.type.label}</span>
                      <span className="package-group-count">
                        {group.items.length} 个规则包
                      </span>
                    </div>
                    <div className="package-grid">
                      {group.items.map((pkg) => {
                        const selected = pkg.name === packageName
                        const type = projectTypeMeta(pkg.project_type)
                        const extendsList = Array.isArray(pkg.extends)
                          ? pkg.extends
                          : pkg.extends
                            ? [pkg.extends]
                            : []
                        return (
                          <button
                            key={pkg.name}
                            type="button"
                            role="radio"
                            aria-checked={selected}
                            className={className('package-option', selected && 'is-selected')}
                            onClick={() => setPackageName(pkg.name)}
                          >
                            <span className="package-check" aria-hidden>
                              {selected && <Check size={13} />}
                            </span>
                            <span className="package-option-head">
                              <span className="package-title">{pkg.title || pkg.name}</span>
                              <Badge tone={type.tone} subtle>
                                {type.label}
                              </Badge>
                            </span>
                            <span className="package-meta mono">
                              {pkg.name} · v{String(pkg.version)}
                            </span>
                            {extendsList.length > 0 && (
                              <span className="package-extends">
                                继承 {extendsList.join('、')}
                              </span>
                            )}
                          </button>
                        )
                      })}
                    </div>
                  </section>
                ))
              )}
            </div>
          )}
          <p className="field-hint">
            规则包决定事实字段、章节骨架与核对规则。创建后不能更换，换包请新建项目。
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

function defaultPackageName(packages: PackageInfo[]): string {
  return (
    packages.find((pkg) => pkg.name === 'weakcurrent-cabling')?.name ||
    packages.find((pkg) => pkg.name !== 'common-bid-facts')?.name ||
    packages[0]?.name ||
    ''
  )
}

function buildPackageGroups(packages: PackageInfo[]) {
  const selectable = packages.filter((pkg) => !pkg.abstract)
  return SELECTABLE_PROJECT_TYPES.map((type) => {
    const items = selectable
      .filter((pkg) => normalizeProjectType(pkg.project_type) === type.id)
      .sort((left, right) => {
        const rank = (name: string) => (name === 'weakcurrent-cabling' ? 0 : 1)
        return (
          rank(left.name) - rank(right.name) ||
          (left.title || left.name).localeCompare(right.title || right.name, 'zh-CN')
        )
      })
    return { type, items }
  }).filter((group) => group.items.length > 0)
}

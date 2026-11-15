import { useEffect, useRef, useState } from 'react'
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  FileCode2,
  Loader2,
  ShieldCheck,
  XCircle,
} from 'lucide-react'
import type {
  AdminRulePackage,
  RulePackageValidation,
} from '../../lib/types'
import {
  className,
  normalizeProjectType,
  projectTypeMeta,
  PROJECT_TYPES,
} from '../../lib/format'
import {
  createRulePackage,
  getRulePackage,
  messageOf,
  updateRulePackage,
  validateRulePackage,
} from '../../lib/api'
import { useSaveShortcut } from '../../lib/hooks'
import { useConfirm } from '../../context/ConfirmContext'
import { Modal } from '../../components/Modal'
import { Button } from '../../components/Button'
import { Badge } from '../../components/Badge'

export type RulePackageModalState =
  | { mode: 'create' }
  | { mode: 'edit'; pkg: AdminRulePackage }

interface RulePackageModalProps {
  open: boolean
  state: RulePackageModalState
  onClose: () => void
  onSaved: (pkg: AdminRulePackage, created: boolean) => void
}

type PackageHeader = {
  package: string
  title: string
  project_type: string
  project_type_label: string
}

const HEADER_KEYS: (keyof PackageHeader)[] = [
  'package',
  'title',
  'project_type',
  'project_type_label',
]

const EMPTY_HEADER: PackageHeader = {
  package: '',
  title: '',
  project_type: '',
  project_type_label: '',
}

const PACKAGE_NAME_RE = /^[a-z][a-z0-9-]{0,63}$/

function readScalar(raw: string): string {
  const value = raw.trim()
  if (!value) return ''
  if (value.startsWith('"')) {
    try {
      const parsed = JSON.parse(value) as unknown
      return typeof parsed === 'string' ? parsed : value
    } catch {
      return value.replace(/^"|"$/g, '')
    }
  }
  if (value.startsWith("'")) return value.slice(1, -1).replace(/''/g, "'")
  return value.replace(/\s+#.*$/, '').trim()
}

/** Read the editable top-level fields without parsing the whole YAML document. */
function readHeader(document: string): PackageHeader {
  const header = { ...EMPTY_HEADER }
  for (const line of document.split('\n')) {
    const match = /^([a-z_]+)\s*:\s*(.*)$/.exec(line)
    if (!match) continue
    const key = match[1] as keyof PackageHeader
    if (key in header) header[key] = readScalar(match[2])
  }
  return header
}

function renderScalar(key: keyof PackageHeader, value: string): string {
  // Plain scalars for identifiers; double-quoted JSON strings for display text.
  return key === 'package' || key === 'project_type'
    ? value.trim()
    : JSON.stringify(value)
}

/** Replace (or append) top-level fields while keeping the rest of the YAML intact. */
function patchHeader(document: string, updates: Partial<PackageHeader>): string {
  const lines = document.split('\n')
  for (const key of HEADER_KEYS) {
    const value = updates[key]
    if (value === undefined) continue
    const line = `${key}: ${renderScalar(key, value)}`
    const index = lines.findIndex((item) => new RegExp(`^${key}\\s*:`).test(item))
    if (index >= 0) {
      lines[index] = line
      continue
    }
    const packageIndex = lines.findIndex((item) => /^package\s*:/.test(item))
    const insertAt =
      key === 'package' ? 0 : packageIndex >= 0 ? packageIndex + 1 : 0
    lines.splice(insertAt, 0, line)
  }
  return lines.join('\n')
}

/** Minimal package that the backend can parse and validate. */
const PACKAGE_TEMPLATE = `# 自定义规则包 · 上方填写的规则包名称、包名与工程类型会同步到下面的顶层字段
package: custom-technical
version: 1
title: 自定义行业技术标
description: 说明这份规则包适用的工程类型、解析重点与章节口径。
project_type: other
project_type_label: 其他
extends: [common-bid-facts]

fields:
  - key: custom.scope
    label: 工程范围
    group: technical
    aliases: [招标范围, 工程内容, 建设内容]
    required: true
    type: text
  - key: custom.duration
    label: 计划工期
    group: commercial
    aliases: [工期, 计划工期, 合同工期]
    required: false
    type: duration

outline:
  mode: replace
  items:
    - {heading: 项目理解与需求分析, intent: 引用招标项目身份与建设目标，说明对招标文件的理解, target_words: 2200, needs: [project.name, project.content]}
    - {heading: 商务条款响应与承诺, intent: 逐项响应工期、质保、投标有效期、付款与验收条款, target_words: 2000, needs: [project.duration, project.warranty]}
    - {heading: 技术方案, intent: 说明技术路线、系统组成与关键参数响应, target_words: 4000, needs: [custom.scope]}
    - {heading: 施工组织与进度计划, intent: 施工流程、人员机具、工期节点与配合界面, target_words: 2600, needs: [custom.duration]}
    - {heading: 质量保证与验收, intent: 质量标准、测试方法、验收资料与整改承诺, target_words: 2200, needs: [custom.scope]}
    - {heading: 售后服务与质保承诺, intent: 质保年限、响应时限、备品备件与巡检, target_words: 1600, needs: [project.warranty]}

checks:
  - id: CUSTOM-001
    level: major
    category: 完整性
    assert: "contains(doc, '质量')"
    message: 正文未涉及质量保证内容
    recommendation: 在质量保证章节补充质量控制与验收安排
`

export function AdminRulePackageModal({
  open,
  state,
  onClose,
  onSaved,
}: RulePackageModalProps) {
  const confirm = useConfirm()
  const editing = state.mode === 'edit' ? state.pkg : null
  const [document, setDocument] = useState('')
  const [baseline, setBaseline] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [reloadNonce, setReloadNonce] = useState(0)
  const [validating, setValidating] = useState(false)
  const [validation, setValidation] = useState<RulePackageValidation | null>(null)
  const [validateError, setValidateError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const closingRef = useRef(false)
  const editorRef = useRef<HTMLTextAreaElement>(null)
  const validationRef = useRef<HTMLDivElement>(null)
  const documentRef = useRef(document)
  documentRef.current = document

  useEffect(() => {
    if (!open || loading || !editing) return
    // Focusing a textarea with existing content puts the caret (and scroll) at
    // the end. Open YAML documents at the top instead. Create mode focuses the
    // name field instead of the YAML body.
    const timer = window.setTimeout(() => {
      const editor = editorRef.current
      if (!editor) return
      editor.focus({ preventScroll: true })
      editor.setSelectionRange(0, 0)
      editor.scrollTop = 0
      editor.scrollLeft = 0
    }, 60)
    return () => window.clearTimeout(timer)
  }, [open, loading, state.mode, editing?.name])

  useEffect(() => {
    if (!open) return
    setSaving(false)
    setError(null)
    setValidation(null)
    setValidateError(null)
    setValidating(false)
    setLoadError(null)
    closingRef.current = false
    if (state.mode === 'create') {
      setDocument(PACKAGE_TEMPLATE)
      setBaseline(PACKAGE_TEMPLATE)
      setLoading(false)
      return
    }
    setDocument('')
    setBaseline('')
    setLoading(true)
    let active = true
    getRulePackage(state.pkg.name)
      .then((pkg) => {
        if (!active) return
        const text = typeof pkg.document === 'string' ? pkg.document : ''
        setDocument(text)
        setBaseline(text)
        if (!text) {
          setLoadError('后端未返回 YAML 原文，暂时无法编辑这个规则包。')
        }
      })
      .catch((err: unknown) => {
        if (!active) return
        setLoadError(messageOf(err))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
    // Reload only when the dialog opens or the edited package changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, state.mode, editing?.name, reloadNonce])

  useEffect(() => {
    setValidation(null)
    setValidateError(null)
    setError(null)
  }, [document])

  useEffect(() => {
    if (!open || validating) return
    if (!validation && !validateError) return
    const timer = window.setTimeout(() => {
      validationRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }, 40)
    return () => window.clearTimeout(timer)
  }, [open, validating, validation, validateError])

  const dirty = !loading && !loadError && document !== baseline

  const header = readHeader(document)
  const titleValid = Boolean(header.title.trim())
  const packageName = header.package.trim()
  const packageNameValid = PACKAGE_NAME_RE.test(packageName)
  const basicsValid = editing ? true : titleValid && packageNameValid
  const basicError = !titleValid
    ? '请填写规则包名称。'
    : !packageNameValid
      ? '包名只能以小写字母开头，由小写字母、数字或连字符组成，最长 64 个字符。'
      : ''

  const updateHeader = (updates: Partial<PackageHeader>) => {
    setDocument((current) => patchHeader(current, updates))
  }

  const requestClose = () => {
    if (saving || closingRef.current) return
    if (!dirty) {
      onClose()
      return
    }
    closingRef.current = true
    void confirm({
      title: '放弃未保存的修改？',
      description: 'YAML 编辑器里有尚未保存的内容，关闭后这些修改会丢失。',
      confirmLabel: '放弃修改',
      cancelLabel: '继续编辑',
      tone: 'danger',
    }).then((accepted) => {
      closingRef.current = false
      if (accepted) onClose()
    })
  }

  const runValidate = async () => {
    if (validating || saving || loading) return
    if (!document.trim()) {
      setValidateError('请先填写 YAML 内容。')
      return
    }
    if (basicError) {
      setError(basicError)
      return
    }
    setValidating(true)
    setValidateError(null)
    const snapshot = document
    try {
      const result = await validateRulePackage({
        ...(editing ? { name: editing.name } : { name: packageName }),
        document: snapshot,
      })
      // The editor may have changed while the request was in flight.
      if (documentRef.current === snapshot) setValidation(result)
    } catch (err) {
      if (documentRef.current === snapshot) {
        setValidation(null)
        setValidateError(messageOf(err))
      }
    } finally {
      setValidating(false)
    }
  }

  const submit = async () => {
    if (saving || loading || loadError) return
    if (!document.trim()) {
      setError('请先填写 YAML 内容。')
      return
    }
    if (basicError) {
      setError(basicError)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const saved = editing
        ? await updateRulePackage(editing.name, document)
        : await createRulePackage(document)
      onSaved(saved, !editing)
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSaving(false)
    }
  }

  useSaveShortcut(() => {
    void submit()
  }, open && !loading && !saving && !loadError)

  const summary = validation?.summary
  const lineCount = document ? document.split('\n').length : 0

  return (
    <Modal
      open={open}
      onClose={requestClose}
      title={editing ? '编辑规则包' : '新建规则包'}
      eyebrow={editing ? `${editing.name} · v${String(editing.version)}` : '从 YAML 定义字段、章节与核对'}
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={requestClose} disabled={saving}>
            取消
          </Button>
          <Button
            variant="secondary"
            icon={<ShieldCheck size={14} />}
            loading={validating}
            disabled={
              loading || Boolean(loadError) || saving || !document.trim() || !basicsValid
            }
            onClick={() => void runValidate()}
          >
            校验
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="rule-package-form"
            loading={saving}
            disabled={
              loading || Boolean(loadError) || !document.trim() || !basicsValid
            }
          >
            {editing ? '保存修改' : '创建规则包'}
          </Button>
        </>
      }
    >
      {loading ? (
        <div className="admin-package-loading" aria-busy="true">
          <Loader2 size={18} className="spin" />
          <div>
            <strong>正在读取 YAML 原文</strong>
            <span>读取完成后即可编辑与校验。</span>
          </div>
        </div>
      ) : loadError ? (
        <div className="inline-error" role="alert">
          <AlertCircle size={18} />
          <div>
            <strong>无法读取规则包</strong>
            <p>{loadError}</p>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setReloadNonce((value) => value + 1)}
            >
              重试
            </Button>
          </div>
        </div>
      ) : (
        <form
          id="rule-package-form"
          className="admin-form admin-package-editor"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <div className="admin-form-note">
            <Badge tone={editing?.builtin ? 'accent' : 'info'} subtle>
              {editing?.builtin ? '内置包' : editing ? '自定义包' : '新建'}
            </Badge>
            {editing?.overridden && (
              <Badge tone="warn" subtle>
                已覆盖
              </Badge>
            )}
            {editing?.hidden && (
              <Badge tone="neutral" subtle>
                已隐藏
              </Badge>
            )}
            <span>
              {editing
                ? '保存会把 YAML 写入本机用户目录的覆盖层，不会改动安装目录里的内置文件。'
                : '先填写规则包名称、工程类型与唯一包名；下方 YAML 会同步生成，再补充字段、章节与核对项。'}
            </span>
          </div>

          {error && (
            <div className="form-alert" role="alert">
              {error}
            </div>
          )}

          {!editing && (
            <div className="admin-form-grid">
              <div className="form-field admin-form-span">
                <label htmlFor="rule-package-title">
                  规则包名称 <span className="required-mark">必填</span>
                </label>
                <input
                  id="rule-package-title"
                  className={className('input', !titleValid && 'input--error')}
                  value={header.title}
                  onChange={(event) => updateHeader({ title: event.target.value })}
                  placeholder="例如：消防维保技术标"
                  maxLength={80}
                  disabled={saving}
                  data-autofocus
                  aria-invalid={!titleValid}
                />
                <p className="field-hint">
                  显示在新建项目卡片和规则包列表中，可随时在 YAML 的 title 中修改。
                </p>
                {!titleValid && <p className="field-error">请填写规则包名称。</p>}
              </div>

              <div className="form-field">
                <label htmlFor="rule-package-type">
                  工程类型 <span className="required-mark">必填</span>
                </label>
                <select
                  id="rule-package-type"
                  className="select"
                  value={normalizeProjectType(header.project_type)}
                  onChange={(event) => {
                    const type = event.target.value
                    updateHeader({
                      project_type: type,
                      project_type_label: projectTypeMeta(type).label,
                    })
                  }}
                  disabled={saving}
                >
                  {PROJECT_TYPES.map((type) => (
                    <option key={type.id} value={type.id}>
                      {type.label}
                    </option>
                  ))}
                </select>
                <p className="field-hint">决定新建项目时归入哪个工程分组。</p>
              </div>

              <div className="form-field">
                <label htmlFor="rule-package-name">
                  包名（唯一标识） <span className="required-mark">必填</span>
                </label>
                <input
                  id="rule-package-name"
                  className={className(
                    'input mono',
                    !packageNameValid && 'input--error',
                  )}
                  value={header.package}
                  onChange={(event) => updateHeader({ package: event.target.value })}
                  placeholder="fire-maintenance"
                  maxLength={64}
                  spellCheck={false}
                  autoCapitalize="off"
                  autoCorrect="off"
                  disabled={saving}
                  aria-invalid={!packageNameValid}
                />
                <p className="field-hint">
                  小写字母开头，可含数字与连字符；保存后不可重命名。
                </p>
                {!packageNameValid && (
                  <p className="field-error">
                    包名格式：小写字母开头，由小写字母、数字或连字符组成，最长 64 个字符。
                  </p>
                )}
              </div>
            </div>
          )}

          <div className="form-field">
            <div className="field-label-row">
              <label htmlFor="rule-package-document">
                YAML 原文 <span className="required-mark">必填</span>
              </label>
              <span className="field-inline-status mono">
                {lineCount} 行 · {document.length} 字符
              </span>
            </div>
            <textarea
              id="rule-package-document"
              ref={editorRef}
              className={className(
                'textarea mono admin-yaml-editor',
                validation && !validation.ok && 'is-invalid',
              )}
              rows={editing ? 20 : 16}
              value={document}
              onChange={(event) => setDocument(event.target.value)}
              wrap="off"
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
              data-autofocus={editing ? '' : undefined}
              disabled={saving}
              aria-describedby="rule-package-hint"
            />
            <p className="field-hint" id="rule-package-hint">
              保存前会完整解析继承链、字段、大纲与核对表达式。Ctrl / ⌘ + S 可直接保存；
              「校验」只检查内容，不写入文件。
            </p>
          </div>

          {(validation || validateError || validating) && (
            <div
              ref={validationRef}
              className={className(
                'admin-validate-panel',
                validating
                  ? 'is-checking'
                  : validation?.ok
                    ? 'is-ok'
                    : 'is-bad',
              )}
              role="status"
            >
              {validating ? (
                <div className="admin-validate-head">
                  <Loader2 size={16} className="spin" />
                  <strong>正在校验 YAML…</strong>
                </div>
              ) : validateError ? (
                <>
                  <div className="admin-validate-head">
                    <AlertCircle size={16} />
                    <strong>校验请求失败</strong>
                  </div>
                  <p className="admin-validate-error">{validateError}</p>
                </>
              ) : validation ? (
                <>
                  <div className="admin-validate-head">
                    {validation.ok ? (
                      <CheckCircle2 size={16} />
                    ) : (
                      <XCircle size={16} />
                    )}
                    <strong>
                      {validation.ok
                        ? '校验通过，可以保存'
                        : validation.problems.length > 0
                          ? `发现 ${validation.problems.length} 个问题，暂时不能保存`
                          : '校验未通过，暂时不能保存'}
                    </strong>
                  </div>
                  {validation.ok ? (
                    <div className="admin-validate-summary">
                      <span>
                        字段 <strong>{summary?.fields ?? 0}</strong>
                      </span>
                      <span>
                        章节 <strong>{summary?.outline ?? 0}</strong>
                      </span>
                      <span>
                        核对 <strong>{summary?.checks ?? 0}</strong>
                      </span>
                    </div>
                  ) : (
                    <ul className="admin-validate-problems">
                      {validation.problems.map((problem, index) => (
                        <li key={`${problem}-${index}`}>{problem}</li>
                      ))}
                    </ul>
                  )}
                </>
              ) : null}
            </div>
          )}

          {!validation && !validateError && !validating && (
            <div className="admin-editor-hint">
              <FileCode2 size={15} />
              <span>
                建议先点「校验」确认字段、章节与核对表达式可解析，再保存。校验失败时后端会返回具体的中文原因。
              </span>
            </div>
          )}

          {validation && !validation.ok && (
            <div className="admin-form-warning">
              <AlertTriangle size={15} />
              校验未通过的内容仍可修改；保存时后端会再次校验并拒绝写入。
            </div>
          )}
        </form>
      )}
    </Modal>
  )
}

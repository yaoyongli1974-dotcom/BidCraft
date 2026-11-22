import { useState } from 'react'
import {
  CheckCircle2,
  ChevronDown,
  Download,
  FileSpreadsheet,
  FileText,
  RotateCcw,
  Send,
  Trash2,
  XCircle,
} from 'lucide-react'
import type { CommercialDoc, CommercialExportFormat } from '../lib/types'
import { className, formatRelative } from '../lib/format'
import { COMMERCIAL_STATUS_META, type CommercialAction } from '../lib/commercialMeta'
import { Badge } from './Badge'
import { Button } from './Button'

/** 商务标卡片：状态、校验问题、章节表格预览与操作。 */
export function CommercialDocCard({
  doc,
  canWrite,
  canReview,
  busy,
  busyExport,
  onAction,
  onExport,
  onDelete,
}: {
  doc: CommercialDoc
  canWrite: boolean
  canReview: boolean
  busy?: boolean
  busyExport?: CommercialExportFormat | null
  onAction: (action: CommercialAction) => void
  onExport: (format: CommercialExportFormat) => void
  onDelete: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const status = COMMERCIAL_STATUS_META[doc.status] || COMMERCIAL_STATUS_META.draft
  const issues = doc.validation.issues || []
  const sections = doc.sections || []

  return (
    <article className={className('com-doc', `com-doc--${doc.status}`)}>
      <header className="com-doc-head">
        <div className="com-doc-title">
          <div className="eyebrow">
            商务标 · {doc.quote_version_id ? `报价版本 ${doc.quote_version_id}` : '未绑定报价版本'}
          </div>
          <h3>{doc.name || '未命名商务标'}</h3>
          <div className="com-doc-meta">
            <Badge tone={status.tone} dot>
              {status.label}
            </Badge>
            {doc.submitted_at && <span>提交于 {formatRelative(doc.submitted_at)}</span>}
            {doc.reviewed_at && <span>审批于 {formatRelative(doc.reviewed_at)}</span>}
            {doc.package_id && <span className="mono">标段 {doc.package_id}</span>}
          </div>
        </div>
        <div className="com-doc-actions">
          {(doc.status === 'draft' || doc.status === 'rejected') && (
            <Button
              size="sm"
              variant="primary"
              icon={<Send size={13} />}
              disabled={!canWrite || busy}
              title={!canWrite ? '只读成员不能提交商务标' : undefined}
              onClick={() => onAction('submit')}
            >
              提交审批
            </Button>
          )}
          {doc.status === 'submitted' && canReview && (
            <>
              <Button
                size="sm"
                variant="primary"
                icon={<CheckCircle2 size={13} />}
                disabled={busy}
                onClick={() => onAction('approve')}
              >
                审批通过
              </Button>
              <Button
                size="sm"
                variant="danger"
                icon={<XCircle size={13} />}
                disabled={busy}
                onClick={() => onAction('reject')}
              >
                驳回
              </Button>
            </>
          )}
          {doc.status === 'approved' && canReview && (
            <Button
              size="sm"
              variant="secondary"
              icon={<RotateCcw size={13} />}
              disabled={busy}
              onClick={() => onAction('unapprove')}
            >
              撤回定稿
            </Button>
          )}
          {(doc.status === 'draft' || doc.status === 'rejected') && (
            <Button
              size="sm"
              variant="ghost"
              icon={<Trash2 size={13} />}
              disabled={!canWrite || busy}
              onClick={onDelete}
            >
              删除
            </Button>
          )}
        </div>
      </header>

      {(doc.submit_note || doc.review_note) && (
        <div className="com-doc-notes">
          {doc.submit_note && <p>提交说明：{doc.submit_note}</p>}
          {doc.review_note && <p>审批意见：{doc.review_note}</p>}
        </div>
      )}

      {issues.length > 0 ? (
        <div className="com-validation" role="alert">
          <strong>
            <XCircle size={14} /> 校验未通过 · {issues.length} 项问题
          </strong>
          <ul>
            {issues.map((issue, index) => (
              <li key={`${issue.code || 'issue'}-${index}`}>
                {issue.code && <code>{issue.code}</code>}
                <span>{issue.message}</span>
                {issue.section && <span className="subtle">（{issue.section}）</span>}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="com-validation com-validation--ok">
          <strong>
            <CheckCircle2 size={14} /> 校验通过
          </strong>
        </div>
      )}

      <div className="com-doc-sections">
        <button
          type="button"
          className="com-sections-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          <FileText size={14} />
          章节预览 · {sections.length} 个章节
          <ChevronDown size={14} className={expanded ? 'is-open' : undefined} />
        </button>
        {expanded &&
          (sections.length === 0 ? (
            <p className="com-sections-empty">后端尚未返回章节内容。</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table com-sections-table">
                <thead>
                  <tr>
                    <th>章节</th>
                    <th>内容摘要</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {sections.map((section, index) => (
                    <tr key={section.key || `${section.title}-${index}`}>
                      <td>{section.title || `章节 ${index + 1}`}</td>
                      <td className="com-section-content">
                        {section.content || section.note || '—'}
                      </td>
                      <td>{section.status || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </div>

      <footer className="com-doc-foot">
        <span className="subtle">导出为交付格式，内容取自绑定报价版本。</span>
        <div className="com-export-actions">
          <Button
            size="sm"
            variant="secondary"
            icon={<FileSpreadsheet size={13} />}
            loading={busyExport === 'xlsx'}
            disabled={Boolean(busyExport)}
            onClick={() => onExport('xlsx')}
          >
            导出 xlsx
          </Button>
          <Button
            size="sm"
            variant="secondary"
            icon={<FileText size={13} />}
            loading={busyExport === 'docx'}
            disabled={Boolean(busyExport)}
            onClick={() => onExport('docx')}
          >
            导出 docx
          </Button>
          <Download size={14} className="com-download-icon" aria-hidden />
        </div>
      </footer>
    </article>
  )
}

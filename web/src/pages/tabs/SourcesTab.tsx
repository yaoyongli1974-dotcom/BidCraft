import { useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  FileText,
  Info,
  LockKeyhole,
  RefreshCw,
  UploadCloud,
  X,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import { messageOf, parseProject, sourceFileUrl, uploadSource } from '../../lib/api'
import type { SourceFile } from '../../lib/types'
import {
  className,
  formatDate,
  formatFileSize,
  formatRelative,
  parseState,
} from '../../lib/format'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ProgressBar } from '../../components/Feedback'
import { GateBanner, TabHeading } from '../../components/Workbench'
import { useToast } from '../../context/useToast'
import { useConfirm } from '../../context/useConfirm'

const ACCEPT = '.pdf,.docx,.txt,.xlsx,.xlsm'
const EXTENSIONS = ['pdf', 'docx', 'txt', 'xlsx', 'xlsm']

const ROLE_LABEL: Record<string, string> = {
  tender: '招标文件',
  annex: '附件',
  clarification: '澄清文件',
  other: '其他文件',
}

interface ParseFeedback {
  notes: string[]
  missing: string[]
}

export function SourcesTab() {
  const {
    ref,
    basePath,
    project,
    sources,
    sourcesLoading,
    reloadProject,
    reloadSources,
    canWrite,
  } = useWorkspace()
  const toast = useToast()
  const confirm = useConfirm()
  const navigate = useNavigate()
  const location = useLocation()
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragActive, setDragActive] = useState(false)
  const [uploading, setUploading] = useState<{ name: string; percent: number } | null>(null)
  const [parsing, setParsing] = useState(false)
  const [feedback, setFeedback] = useState<ParseFeedback | null>(null)
  const [justCreated, setJustCreated] = useState(
    Boolean((location.state as { justCreated?: boolean } | null)?.justCreated),
  )

  const openPicker = () => {
    if (!canWrite || uploading) return
    inputRef.current?.click()
  }

  const handleFiles = async (files: File[]) => {
    if (!canWrite) return
    const valid = files.filter((file) => {
      const extension = file.name.split('.').pop()?.toLowerCase() || ''
      return EXTENSIONS.includes(extension)
    })
    const rejected = files.length - valid.length
    if (rejected > 0) {
      toast.warning(
        '部分文件未加入',
        `仅支持 ${EXTENSIONS.join('、').toUpperCase()}。已跳过 ${rejected} 个文件。`,
      )
    }
    if (!valid.length) return

    const collectedNotes: string[] = []
    let lastMissing: string[] = []
    for (const file of valid) {
      setUploading({ name: file.name, percent: 0 })
      try {
        const result = await uploadSource(ref, file, (percent) =>
          setUploading({ name: file.name, percent }),
        )
        collectedNotes.push(...(result.notes || []))
        lastMissing = result.missing_required || []
      } catch (error) {
        toast.error(`「${file.name}」上传失败`, messageOf(error))
      }
    }
    setUploading(null)
    setFeedback({ notes: collectedNotes, missing: lastMissing })
    try {
      await Promise.all([reloadProject(), reloadSources()])
    } catch (error) {
      toast.warning('文件已上传，但刷新项目状态失败', messageOf(error))
    }
    if (collectedNotes.length === 0 && lastMissing.length === 0) {
      toast.success('导入完成', '招标文件已解析，事实台账已更新。')
    } else if (lastMissing.length > 0) {
      toast.warning(
        '解析完成，但仍有缺失',
        `还缺 ${lastMissing.length} 项必填事实，补齐后才能生成正文。`,
      )
    } else {
      toast.success('解析完成', '请查看下方解析提示，并校订事实台账。')
    }
  }

  const reparse = async () => {
    if (!canWrite) return
    const accepted = await confirm({
      title: '重新解析全部招标文件？',
      description:
        '系统会重新抽取事实、材料、参数与评分条目。人工校订过的事实可能被新的解析结果覆盖。',
      note: '如果只是个别字段有误，建议直接在「事实台账」中修改，不必重新解析。',
      confirmLabel: '重新解析',
      tone: 'warning',
    })
    if (!accepted) return
    setParsing(true)
    setFeedback(null)
    try {
      const result = await parseProject(ref)
      setFeedback({
        notes: result.notes || [],
        missing: result.missing_required || [],
      })
      await Promise.all([reloadProject(), reloadSources()])
      toast.success('重新解析完成', '事实台账与项目统计已刷新。')
    } catch (error) {
      toast.error('重新解析失败', messageOf(error))
    } finally {
      setParsing(false)
    }
  }

  const goToMissing = () => {
    navigate(`${basePath}/ledger`, {
      state: { highlight: feedback?.missing || project.missing_required || [] },
    })
  }

  return (
    <div className="tab-content sources-tab">
      <TabHeading
        eyebrow="招标文件"
        title="导入与解析"
        description="支持 PDF、DOCX、TXT、XLSX。文件仅在本机解析，解析结果会进入事实台账。"
        actions={
          <Button
            variant="secondary"
            icon={<RefreshCw size={15} className={parsing ? 'spin' : undefined} />}
            onClick={reparse}
            loading={parsing}
            disabled={!canWrite || sources.length === 0 || Boolean(uploading)}
            title={!canWrite ? '只读成员不能重新解析' : undefined}
          >
            重新解析
          </Button>
        }
      />

      {justCreated && (
        <div className="notice notice--info">
          <Info size={17} />
          <div>
            <strong>项目已创建</strong>
            <p>先导入招标文件。解析完成后，必填事实会自动进入台账。</p>
          </div>
          <button
            type="button"
            className="notice-close"
            onClick={() => setJustCreated(false)}
            aria-label="关闭提示"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {feedback && feedback.missing.length > 0 && (
        <GateBanner
          count={feedback.missing.length}
          keys={feedback.missing}
          onLocate={goToMissing}
        />
      )}

      {feedback && feedback.notes.length > 0 && (
        <section className="parse-notes" aria-label="解析提示">
          <div className="parse-notes-head">
            <AlertCircle size={16} />
            <strong>解析提示</strong>
            <button
              type="button"
              className="text-btn"
              onClick={() => setFeedback({ ...feedback, notes: [] })}
            >
              收起
            </button>
          </div>
          <ul>
            {feedback.notes.map((note, index) => (
              <li key={index}>{note}</li>
            ))}
          </ul>
        </section>
      )}

      <div className="sources-layout">
        <section className="upload-column">
          <div
            className={className(
              'dropzone',
              !canWrite && 'is-readonly',
              dragActive && 'is-dragging',
              uploading && 'is-uploading',
            )}
            role={canWrite ? 'button' : 'group'}
            tabIndex={canWrite ? 0 : undefined}
            aria-label={canWrite ? '上传招标文件' : '只读成员不能上传文件'}
            aria-disabled={!canWrite}
            onClick={canWrite ? openPicker : undefined}
            onKeyDown={
              canWrite
                ? (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      openPicker()
                    }
                  }
                : undefined
            }
            onDragOver={
              canWrite
                ? (event) => {
                    event.preventDefault()
                    if (!uploading) setDragActive(true)
                  }
                : undefined
            }
            onDragLeave={
              canWrite
                ? (event) => {
                    if (event.currentTarget.contains(event.relatedTarget as Node)) return
                    setDragActive(false)
                  }
                : undefined
            }
            onDrop={
              canWrite
                ? (event) => {
                    event.preventDefault()
                    setDragActive(false)
                    if (uploading) return
                    void handleFiles(Array.from(event.dataTransfer.files))
                  }
                : undefined
            }
          >
            {canWrite && (
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPT}
                multiple
                className="sr-only"
                onChange={(event) => {
                  const files = Array.from(event.target.files || [])
                  event.target.value = ''
                  void handleFiles(files)
                }}
              />
            )}
            {!canWrite ? (
              <>
                <div className="dropzone-icon" aria-hidden>
                  <LockKeyhole size={28} strokeWidth={1.5} />
                </div>
                <strong>只读成员不能上传文件</strong>
                <p>可以查看已导入文件、解析状态，并下载原件。</p>
                <p className="dropzone-hint">
                  如需补充或重新解析招标文件，请联系投标经理或超级管理员。
                </p>
              </>
            ) : uploading ? (
              <div className="upload-progress-state">
                <div className="upload-spinner">
                  <RefreshCw size={22} className="spin" />
                </div>
                <strong>正在上传并解析</strong>
                <p className="upload-filename" title={uploading.name}>
                  {uploading.name}
                </p>
                <div className="upload-meter">
                  <ProgressBar value={uploading.percent} tone="accent" />
                  <span className="mono">{uploading.percent}%</span>
                </div>
                <p className="dropzone-hint">解析耗时取决于文件页数与表格数量，请保持页面打开。</p>
              </div>
            ) : (
              <>
                <div className="dropzone-icon" aria-hidden>
                  <UploadCloud size={30} strokeWidth={1.5} />
                </div>
                <strong>拖拽招标文件到这里</strong>
                <p>或点击选择文件，可一次选择多份</p>
                <span className="dropzone-formats mono">PDF · DOCX · TXT · XLSX</span>
                <p className="dropzone-hint">
                  文件不会上传到外部服务。扫描版 PDF 需先 OCR，否则无法抽取文字。
                </p>
              </>
            )}
          </div>

          <div className="side-note">
            <div className="side-note-head">
              <Info size={15} />
              <strong>关于扫描件</strong>
            </div>
            <p>
              BidCraft 只解析 PDF 中的文本层。纯图片扫描件会得到空台账，请先用 OCR
              工具生成可搜索 PDF，再导入。
            </p>
          </div>

          <div className="side-note side-note--quiet">
            <div className="side-note-head">
              <CheckCircle2 size={15} />
              <strong>解析会做什么</strong>
            </div>
            <ul className="plain-list">
              <li>抽取项目、商务、资格与技术事实</li>
              <li>识别材料清单、技术参数、评分办法</li>
              <li>标记实质性要求与废标风险条款</li>
              <li>保留原文与页码，供人工核对</li>
            </ul>
          </div>
        </section>

        <section className="panel sources-panel">
          <div className="panel-head">
            <div>
              <div className="eyebrow">源文件</div>
              <h3>源文件</h3>
            </div>
            <span className="subtle">{sources.length} 份</span>
          </div>

          {sourcesLoading ? (
            <div className="source-list-loading" aria-busy="true">
              {Array.from({ length: 3 }).map((_, index) => (
                <div key={index} className="source-row source-row--skeleton">
                  <span className="skeleton" style={{ width: 36, height: 36, borderRadius: 8 }} />
                  <div>
                    <span className="skeleton" style={{ width: 200, height: 14 }} />
                    <span className="skeleton" style={{ width: 120, height: 10 }} />
                  </div>
                  <span className="skeleton" style={{ width: 60, height: 20 }} />
                </div>
              ))}
            </div>
          ) : sources.length === 0 ? (
            <EmptyState
              icon={FileText}
              title="还没有招标文件"
              description="从左侧拖入文件，或点击上传。第一份文件会被标记为招标文件。"
              actions={
                <Button variant="primary" size="sm" onClick={openPicker}>
                  选择文件
                </Button>
              }
            />
          ) : (
            <div className="source-list">
              {sources.map((source) => (
                <SourceRow key={source.id} refId={ref} source={source} />
              ))}
            </div>
          )}

          <div className="source-table-foot">
            <span>解析结果可在「事实台账」中逐项校订。</span>
            <span className="mono">原件可下载 · 只读</span>
          </div>
        </section>
      </div>
    </div>
  )
}

function SourceRow({ refId, source }: { refId: string; source: SourceFile }) {
  const meta = parseState(source.parse_state)
  const extension = source.filename.split('.').pop()?.toLowerCase()
  const Icon = extension === 'xlsx' || extension === 'xlsm' ? FileSpreadsheet : FileText
  const role = source.role ? ROLE_LABEL[source.role] || source.role : '招标文件'
  return (
    <div className="source-row">
      <span className="source-type" aria-hidden>
        <Icon size={18} />
      </span>
      <div className="source-main">
        <div className="source-title-line">
          <span className="source-name" title={source.filename}>
            {source.filename}
          </span>
          <Badge tone={meta.tone} subtle>
            {meta.label}
          </Badge>
          <span className="source-role">{role}</span>
        </div>
        <div className="source-meta">
          <span>{source.page_count ? `${source.page_count} 页` : '页数未知'}</span>
          <span>{formatFileSize(source.size)}</span>
          <span>导入于 {formatDate(source.created_at, false)}</span>
          {formatRelative(source.created_at) !== formatDate(source.created_at, false) && (
            <span className="subtle" title={formatDate(source.created_at)}>
              {formatRelative(source.created_at)}
            </span>
          )}
        </div>
        {source.parse_note && (
          <div className="source-note">
            <AlertCircle size={13} />
            <span>{source.parse_note}</span>
          </div>
        )}
      </div>
      <div className="source-side">
        <a
          className="source-download"
          href={sourceFileUrl(refId, source.id)}
          download={source.filename}
          title={`下载原件 ${source.filename}`}
        >
          <Download size={13} />
          下载原件
        </a>
        {source.parse_state === 'failed' ? (
          <Badge tone="danger" subtle>
            需处理
          </Badge>
        ) : source.parse_state === 'parsed' ? (
          <CheckCircle2 size={17} className="success-icon" aria-label="解析成功" />
        ) : (
          <span className="subtle">—</span>
        )}
      </div>
    </div>
  )
}

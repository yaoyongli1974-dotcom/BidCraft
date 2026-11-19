import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Download,
  FileText,
  FolderOpen,
  Info,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UploadCloud,
} from 'lucide-react'
import {
  createUserDoc,
  deleteUserDoc,
  getUserDocs,
  messageOf,
  userDocFileUrl,
} from '../lib/api'
import type { UserDoc } from '../lib/types'
import {
  className,
  formatDate,
  formatFileSize,
  formatRelative,
  type Tone,
} from '../lib/format'
import { useDocumentTitle, useQuery } from '../lib/hooks'
import { Badge } from '../components/Badge'
import { Button, IconButton } from '../components/Button'
import { EmptyState, ErrorState, ProgressBar, Skeleton } from '../components/Feedback'
import { TabHeading } from '../components/Workbench'
import { useConfirm } from '../context/useConfirm'
import { useToast } from '../context/useToast'

const MAX_BYTES = 50 * 1024 * 1024
const EXTENSIONS = ['pdf', 'docx', 'txt', 'md', 'xlsx'] as const
const ACCEPT = EXTENSIONS.map((extension) => `.${extension}`).join(',')

interface UploadingState {
  name: string
  percent: number
  index: number
  total: number
}

interface DocStatusMeta {
  label: string
  tone: Tone
}

/** 后端只约定 status 字符串；未知值原样展示，避免把新状态误报为失败。 */
function docStatusMeta(status: string): DocStatusMeta {
  const value = (status || '').toLowerCase()
  if (['ready', 'done', 'indexed', 'ok'].includes(value)) {
    return { label: '可检索', tone: 'success' }
  }
  if (['indexing', 'queued', 'pending', 'processing', 'running'].includes(value)) {
    return { label: '索引中', tone: 'info' }
  }
  if (['failed', 'error'].includes(value)) {
    return { label: '索引失败', tone: 'danger' }
  }
  return { label: status || '未知', tone: 'neutral' }
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
}

export function MyDocsPage() {
  useDocumentTitle('我的文档')
  const toast = useToast()
  const confirm = useConfirm()
  const query = useQuery('my-docs', getUserDocs)
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragActive, setDragActive] = useState(false)
  const [uploading, setUploading] = useState<UploadingState | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)

  const docs = useMemo(() => {
    const list = query.data || []
    return [...list].sort((left, right) => {
      const leftTime = new Date(left.created_at || 0).getTime() || 0
      const rightTime = new Date(right.created_at || 0).getTime() || 0
      return rightTime - leftTime
    })
  }, [query.data])

  const openPicker = () => {
    if (uploading) return
    inputRef.current?.click()
  }

  const desktopToken =
    typeof window !== 'undefined' ? window.__BIDCRAFT_CLIENT_TOKEN : undefined
  const refreshDocs = query.refresh

  useEffect(() => {
    if (!desktopToken) return
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; ok?: boolean } | null
      if (!data || data.type !== 'bidcraft-desktop-upload') return
      if (data.ok) {
        toast.success('本机文件已上传', '索引将在后台完成，完成后即可参与生成。')
        refreshDocs()
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [desktopToken, refreshDocs, toast])

  const openDesktopPicker = () => {
    if (!desktopToken) return
    const params = new URLSearchParams({ t: desktopToken, kind: 'doc' })
    window.open(
      `/__client/picker?${params.toString()}`,
      'bidcraft-picker',
      'width=760,height=620',
    )
  }

  const handleFiles = async (files: File[]) => {
    if (!files.length || uploading) return
    const accepted: File[] = []
    const rejected: string[] = []
    for (const file of files) {
      if (!EXTENSIONS.includes(extensionOf(file.name) as (typeof EXTENSIONS)[number])) {
        rejected.push(`${file.name}（格式不支持）`)
        continue
      }
      if (file.size > MAX_BYTES) {
        rejected.push(`${file.name}（超过 50MB）`)
        continue
      }
      accepted.push(file)
    }
    if (rejected.length > 0) {
      toast.error(
        rejected.length === 1 ? '文件未加入' : `${rejected.length} 个文件未加入`,
        `${rejected.join('；')}。仅支持 ${EXTENSIONS.join('、').toUpperCase()}，单份不超过 50MB。`,
      )
    }
    if (!accepted.length) return

    let uploaded = 0
    const uploadedNames: string[] = []
    for (let index = 0; index < accepted.length; index += 1) {
      const file = accepted[index]
      setUploading({ name: file.name, percent: 0, index: index + 1, total: accepted.length })
      try {
        await createUserDoc(file, (percent) =>
          setUploading((current) =>
            current && current.name === file.name ? { ...current, percent } : current,
          ),
        )
        uploaded += 1
        uploadedNames.push(file.name)
      } catch (error) {
        toast.error(`「${file.name}」上传失败`, messageOf(error))
      }
    }
    setUploading(null)
    if (uploaded > 0) {
      toast.success(
        '已上传，索引将在后台完成',
        uploaded === 1
          ? `「${uploadedNames[0]}」已保存到你的个人文档库，生成时会自动检索。`
          : `共 ${uploaded} 份文档已保存到你的个人文档库，生成时会自动检索。`,
      )
      query.refresh()
    }
  }

  const remove = async (doc: UserDoc) => {
    const accepted = await confirm({
      title: '删除这份文档？',
      description: (
        <>
          即将删除 <strong>「{doc.filename}」</strong> 及其个人知识库索引。
        </>
      ),
      note: '删除后无法恢复；不会影响项目文件与公共知识库。',
      confirmLabel: '删除文档',
      tone: 'danger',
    })
    if (!accepted) return
    setDeleting(doc.id)
    try {
      await deleteUserDoc(doc.id)
      query.setData((current) => (current || []).filter((item) => item.id !== doc.id))
      toast.success('文档已删除', `「${doc.filename}」已从个人文档库移除。`)
      query.refresh()
    } catch (error) {
      toast.error('删除失败', messageOf(error))
    } finally {
      setDeleting(null)
    }
  }

  return (
    <div className="mydocs-page">
      <TabHeading
        eyebrow="个人文档库"
        title="我的文档"
        description="上传的资料会自动建立个人索引，仅你本人可用。生成正文时会检索这些文档作为参考。"
        actions={
          <button
            type="button"
            className="text-btn"
            onClick={query.refresh}
            disabled={query.refreshing}
          >
            <RefreshCw size={13} className={query.refreshing ? 'spin' : undefined} />
            刷新列表
          </button>
        }
      />

      <div className="mydocs-layout">
        <section className="mydocs-upload-column" aria-label="上传个人文档">
          <div
            className={className(
              'dropzone',
              'mydocs-dropzone',
              dragActive && 'is-dragging',
              uploading && 'is-uploading',
            )}
            role="button"
            tabIndex={0}
            aria-label="上传个人文档"
            aria-busy={Boolean(uploading)}
            onClick={openPicker}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                openPicker()
              }
            }}
            onDragOver={(event) => {
              event.preventDefault()
              if (!uploading) setDragActive(true)
            }}
            onDragLeave={(event) => {
              if (event.currentTarget.contains(event.relatedTarget as Node)) return
              setDragActive(false)
            }}
            onDrop={(event) => {
              event.preventDefault()
              setDragActive(false)
              if (uploading) return
              void handleFiles(Array.from(event.dataTransfer.files))
            }}
          >
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              multiple
              className="sr-only"
              aria-label="选择个人文档"
              onChange={(event) => {
                const files = Array.from(event.target.files || [])
                event.target.value = ''
                void handleFiles(files)
              }}
            />
            {uploading ? (
              <div className="upload-progress-state">
                <div className="upload-spinner">
                  <RefreshCw size={22} className="spin" />
                </div>
                <strong>
                  正在上传
                  {uploading.total > 1
                    ? `（${uploading.index}/${uploading.total}）`
                    : ''}
                </strong>
                <p className="upload-filename" title={uploading.name}>
                  {uploading.name}
                </p>
                <div className="upload-meter">
                  <ProgressBar value={uploading.percent} tone="accent" label="上传进度" />
                  <span className="mono">{uploading.percent}%</span>
                </div>
                <p className="dropzone-hint">上传完成后会在后台建立索引，无需保持页面打开。</p>
              </div>
            ) : (
              <>
                <div className="dropzone-icon" aria-hidden>
                  <UploadCloud size={30} strokeWidth={1.5} />
                </div>
                <strong>拖拽文档到这里</strong>
                <p>或点击选择文件，可一次选择多份</p>
                <span className="dropzone-formats mono">PDF · DOCX · TXT · MD · XLSX</span>
                <p className="dropzone-hint">
                  单份不超过 50MB。文件保存在本机服务器，仅供你本人检索，不会进入公共知识库。
                </p>
              </>
            )}
          </div>

          {desktopToken && (
            <div className="mydocs-desktop">
              <Button
                variant="secondary"
                icon={<FolderOpen size={15} />}
                onClick={openDesktopPicker}
              >
                从本机选择文件
              </Button>
              <p className="subtle">
                桌面客户端模式：直接读取本机文件上传，无需先复制到服务器目录。
              </p>
            </div>
          )}

          <div className="side-note">
            <div className="side-note-head">
              <ShieldCheck size={15} />
              <strong>个人资料如何使用</strong>
            </div>
            <ul className="plain-list">
              <li>每个账号拥有独立的个人知识库</li>
              <li>生成正文时自动检索本人文档</li>
              <li>其他用户无法查看、下载或检索</li>
            </ul>
          </div>

          <div className="side-note side-note--quiet">
            <div className="side-note-head">
              <Info size={15} />
              <strong>关于索引</strong>
            </div>
            <p>
              上传成功后索引在后台建立，状态变为「可检索」即可参与生成。扫描版 PDF
              需先做 OCR，否则无法抽取文字。
            </p>
          </div>
        </section>

        <section className="panel mydocs-list" aria-labelledby="mydocs-heading">
          <div className="panel-head">
            <div>
              <div className="eyebrow">已上传</div>
              <h3 id="mydocs-heading">文档列表</h3>
            </div>
            <span className="subtle">
              {query.loading && !query.data ? '正在读取…' : `${docs.length} 份`}
            </span>
          </div>

          {query.error && !query.data ? (
            <ErrorState message={query.error} onRetry={query.refresh} />
          ) : query.loading && !query.data ? (
            <DocListSkeleton />
          ) : docs.length === 0 ? (
            <EmptyState
              icon={FileText}
              title="还没有上传文档"
              description="上传 PDF、Word、文本、Markdown 或 Excel 资料，系统会自动建立个人索引。"
              actions={
                <Button
                  variant="primary"
                  icon={<UploadCloud size={15} />}
                  onClick={openPicker}
                >
                  选择文件
                </Button>
              }
            />
          ) : (
            <div className="mydocs-rows">
              {docs.map((doc) => (
                <DocRow
                  key={doc.id}
                  doc={doc}
                  deleting={deleting === doc.id}
                  onDelete={() => void remove(doc)}
                />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function DocRow({
  doc,
  deleting,
  onDelete,
}: {
  doc: UserDoc
  deleting: boolean
  onDelete: () => void
}) {
  const status = docStatusMeta(doc.status)
  const failed = status.tone === 'danger'
  return (
    <article className={className('mydocs-row', failed && 'is-failed')}>
      <span className="mydocs-file-icon" aria-hidden>
        <FileText size={18} strokeWidth={1.6} />
      </span>
      <div className="mydocs-main">
        <div className="mydocs-name-line">
          <span className="mydocs-name" title={doc.filename}>
            {doc.filename}
          </span>
          <Badge tone={status.tone} dot subtle>
            {status.label}
          </Badge>
        </div>
        <div className="mydocs-meta">
          <span>{formatFileSize(doc.size)}</span>
          <span title={formatDate(doc.created_at || doc.updated_at)}>
            {doc.created_at || doc.updated_at
              ? `上传于 ${formatRelative(doc.created_at || doc.updated_at)}`
              : '上传时间未知'}
          </span>
          {doc.mime && <span className="mono">{doc.mime}</span>}
        </div>
        {failed && doc.error && (
          <p className="mydocs-error" role="status">
            {doc.error}
          </p>
        )}
      </div>
      <div className="mydocs-actions">
        <a
          className="btn btn--secondary btn--sm mydocs-download"
          href={userDocFileUrl(doc.id)}
          download={doc.filename}
          title={`下载 ${doc.filename}`}
        >
          <Download size={13} />
          <span className="btn-label">下载</span>
        </a>
        <IconButton
          label={`删除 ${doc.filename}`}
          tone="danger"
          disabled={deleting}
          onClick={onDelete}
        >
          {deleting ? <RefreshCw size={15} className="spin" /> : <Trash2 size={15} />}
        </IconButton>
      </div>
    </article>
  )
}

function DocListSkeleton() {
  return (
    <div className="mydocs-rows" aria-busy="true" aria-label="个人文档加载中">
      {Array.from({ length: 3 }).map((_, index) => (
        <div className="mydocs-row mydocs-row--skeleton" key={index}>
          <Skeleton width={38} height={38} radius={8} />
          <div className="mydocs-main">
            <Skeleton width="58%" height={14} />
            <Skeleton width="36%" height={10} />
          </div>
          <Skeleton width={120} height={28} radius={6} />
        </div>
      ))}
    </div>
  )
}

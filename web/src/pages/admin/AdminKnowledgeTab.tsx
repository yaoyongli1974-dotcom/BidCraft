import { useEffect, useMemo, useRef, useState } from 'react'
import { watchJob } from '../../hooks/watchJob'
import { messageOf } from '../../lib/http'
import {
  Cloud,
  Database,
  FolderOpen,
  Pencil,
  Plus,
  RefreshCw,
  RotateCw,
  Tags,
  Trash2,
  Waypoints,
  type LucideIcon,
} from 'lucide-react'
import type { KnowledgeBase, TestResult } from '../../lib/types'
import {
  className,
  formatNumber,
  formatRelative,
  kbKindMeta,
  kbProviderLabel,
  kbVectorMeta,
} from '../../lib/format'
import type { KbReindexResult } from '../../api/knowledge'
import { deleteKnowledgeBase, embedKnowledgeBase, parseKbReindexResult, reindexKnowledgeBase, testKnowledgeBase } from '../../api/knowledge'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'
import { Badge } from '../../components/Badge'
import { Button, IconButton } from '../../components/Button'
import { EmptyState, ErrorState } from '../../components/Feedback'
import {
  AdminActionError,
  AdminSectionLabel,
  AdminSegment,
  AdminSkeletonRows,
  LastTestStatus,
  TestFeedback,
} from './AdminShared'
import {
  AdminKnowledgeModal,
  type KnowledgeModalState,
} from './AdminKnowledgeModal'
import { AdminKbDocsModal } from './AdminKbDocsModal'

interface KnowledgeTabProps {
  kbs: KnowledgeBase[]
  loading: boolean
  refreshing: boolean
  error: string | null
  refresh: () => void
}

interface KbGroup {
  id: 'local' | 'api'
  label: string
  singular: string
  description: string
  icon: LucideIcon
}

const KB_GROUPS: KbGroup[] = [
  {
    id: 'local',
    label: '本地目录',
    singular: '本地',
    description: '扫描服务器目录中的 PDF、DOCX、TXT、MD 与 XLSX，建立本机片段索引，并按需构建语义向量。',
    icon: FolderOpen,
  },
  {
    id: 'api',
    label: '第三方接口',
    singular: '接口',
    description: '连接 Dify、RAGFlow 或通用检索 API，把返回片段作为生成参考。',
    icon: Cloud,
  },
]

/** Older servers send no `vector_*` fields at all — then we hide the vector row. */
function hasVectorInfo(kb: KnowledgeBase): boolean {
  return (
    kb.vector_enabled !== undefined ||
    kb.vector_total !== undefined ||
    kb.vector_model !== undefined
  )
}

export function AdminKnowledgeTab({
  kbs,
  loading,
  refreshing,
  error,
  refresh,
}: KnowledgeTabProps) {
  const toast = useToast()
  const confirm = useConfirm()
  const [kindFilter, setKindFilter] = useState('all')
  const [modal, setModal] = useState<KnowledgeModalState | null>(null)
  const [docsKb, setDocsKb] = useState<KnowledgeBase | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [reindexingId, setReindexingId] = useState<string | null>(null)
  const [reindexJobId, setReindexJobId] = useState<string | null>(null)
  const [embeddingId, setEmbeddingId] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({})
  const [reindexResults, setReindexResults] = useState<Record<string, string>>({})
  const [actionError, setActionError] = useState<string | null>(null)
  const reindexJobRef = useRef<{ id: string; kbId: string; kbName: string } | null>(null)
  const toastRef = useRef(toast)
  toastRef.current = toast
  /** Latest result reporter; polling reads it through a ref so the interval
   * is not torn down by parent re-renders. */
  const reportReindexRef = useRef<(kbId: string, kbName: string, result: KbReindexResult) => void>(
    () => {},
  )

  const reportReindexResult = (
    kbId: string,
    kbName: string,
    result: KbReindexResult,
  ) => {
    const vector = result.vector
    const vectorError = vector?.error?.trim() || ''
    setReindexResults((current) => ({
      ...current,
      [kbId]: `本次索引 ${formatNumber(result.indexed)} 个片段`,
    }))
    refresh()
    if (vectorError) {
      toastRef.current.warning(
        '索引已重建，向量未就绪',
        `${kbName} · ${formatNumber(result.indexed)} 个片段已写入；${vectorError}`,
      )
    } else if (vector && vector.embedded > 0) {
      toastRef.current.success(
        '索引与向量已重建',
        `${kbName} · ${formatNumber(result.indexed)} 个片段 · 向量 ${formatNumber(
          vector.embedded,
        )}/${formatNumber(vector.total)}`,
      )
    } else {
      toastRef.current.success(
        '索引已重建',
        `${kbName} · ${formatNumber(result.indexed)} 个片段`,
      )
    }
  }
  reportReindexRef.current = reportReindexResult

  // A rebuild can take minutes for a large directory: the server accepts the
  // job and the UI polls it at a coarse cadence until the terminal state.
  // Transport (polling / terminal guard / error counting) lives in watchJob.
  useEffect(() => {
    const target = reindexJobRef.current
    if (!target) return
    const jobId = target.id
    const { kbId, kbName } = target
    const stop = () => {
      if (reindexJobRef.current?.id === jobId) reindexJobRef.current = null
      setReindexJobId(null)
    }
    return watchJob(jobId, {
      onUpdate: (job) => {
        const message = job.message || ''
        const processed = typeof job.payload?.done === 'number' ? job.payload.done : 0
        const total = typeof job.payload?.total === 'number' ? job.payload.total : 0
        const progressText =
          message ||
          (total > 0
            ? `已处理 ${formatNumber(processed)}/${formatNumber(total)} 个片段`
            : '')
        if (progressText) {
          setReindexResults((current) => ({ ...current, [kbId]: progressText }))
        }
      },
      onDone: (job) => {
        setReindexingId(null)
        stop()
        reportReindexRef.current(kbId, kbName, parseKbReindexResult(job.result))
      },
      onFailed: (job) => {
        const detail = job.error || job.message || '请稍后重试。'
        setActionError(detail)
        toastRef.current.error('重建索引失败', detail)
        setReindexingId(null)
        stop()
      },
      onPollError: (message) => {
        setReindexingId(null)
        stop()
        setActionError(message)
        toastRef.current.error('无法读取重建进度', message)
      },
    })
  }, [reindexJobId])

  const counts = useMemo(() => {
    const result: Record<string, number> = { all: kbs.length }
    KB_GROUPS.forEach((group) => {
      result[group.id] = kbs.filter((kb) => kb.kind === group.id).length
    })
    return result
  }, [kbs])

  const defaultKb = kbs.find((kb) => kb.is_default && kb.enabled)
  const visibleGroups = KB_GROUPS.filter(
    (group) => kindFilter === 'all' || group.id === kindFilter,
  )

  const runTest = async (kb: KnowledgeBase) => {
    if (testingId) return
    setTestingId(kb.id)
    setActionError(null)
    setTestResults((current) => {
      const next = { ...current }
      delete next[kb.id]
      return next
    })
    try {
      const result = await testKnowledgeBase(kb.id)
      setTestResults((current) => ({ ...current, [kb.id]: result }))
      refresh()
      if (result.ok) {
        toast.success('知识库可用', `${kb.name} · ${result.message}`)
      } else {
        toast.warning('知识库测试失败', result.message)
      }
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('测试知识库失败', message)
    } finally {
      setTestingId(null)
    }
  }

  const reindex = async (kb: KnowledgeBase) => {
    if (reindexingId) return
    const accepted = await confirm({
      title: '重新建立本地索引？',
      description: `将重新扫描「${kb.name}」目录，清空旧片段并写入新索引。${
        kb.vector_model ? '同时按当前嵌入模型重建向量。' : ''
      }`,
      note: `目录：${kb.location || '未填写'}`,
      confirmLabel: '开始重建',
      tone: 'warning',
    })
    if (!accepted) return
    setReindexingId(kb.id)
    setActionError(null)
    let polling = false
    try {
      const started = await reindexKnowledgeBase(kb.id)
      if (started.job_id) {
        // New backend: the rebuild runs as a background job.
        polling = true
        reindexJobRef.current = { id: started.job_id, kbId: kb.id, kbName: kb.name }
        setReindexJobId(started.job_id)
        setReindexResults((current) => ({ ...current, [kb.id]: '重建任务已开始…' }))
        toast.info(
          '重建索引已开始',
          `${kb.name} · 任务在后台运行，完成后会提示结果。`,
        )
        return
      }
      // Older backend still answers synchronously: report its result directly.
      if (started.legacy) reportReindexRef.current(kb.id, kb.name, started.legacy)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('重建索引失败', message)
    } finally {
      if (!polling) setReindexingId(null)
    }
  }

  const buildVectors = async (kb: KnowledgeBase) => {
    if (embeddingId) return
    setEmbeddingId(kb.id)
    setActionError(null)
    try {
      const result = await embedKnowledgeBase(kb.id)
      refresh()
      const total = result.total || kb.chunk_count || 0
      const model = result.model || kb.vector_model || ''
      const failure = result.error.trim()
      if (failure && result.embedded === 0) {
        toast.error('向量构建失败', `${kb.name} · ${failure}`)
      } else if (failure) {
        toast.warning(
          '向量构建未完成',
          `${kb.name} · 已写入 ${formatNumber(result.embedded)}/${formatNumber(total)} 个片段；${failure}`,
        )
      } else if (result.embedded === 0) {
        toast.success(
          '向量已是最新',
          `${kb.name} · ${formatNumber(total)} 个片段均已有向量`,
        )
      } else {
        toast.success(
          '向量已构建',
          `${kb.name} · ${formatNumber(result.embedded)}/${formatNumber(
            total,
          )} 个片段${model ? ` · ${model}` : ''}`,
        )
      }
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('构建向量失败', message)
    } finally {
      setEmbeddingId(null)
    }
  }

  const remove = async (kb: KnowledgeBase) => {
    const accepted = await confirm({
      title: '删除知识库？',
      description: `「${kb.name}」及其已建立的本机索引片段将被删除。`,
      note: '服务器上的原文件不会被删除。',
      confirmLabel: '删除知识库',
      tone: 'danger',
    })
    if (!accepted) return
    try {
      await deleteKnowledgeBase(kb.id)
      refresh()
      toast.success('知识库已删除', kb.name)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('删除知识库失败', message)
    }
  }

  const onSaved = (kb: KnowledgeBase, created: boolean) => {
    setModal(null)
    refresh()
    toast.success(created ? '知识库已创建' : '知识库已更新', kb.name)
  }

  return (
    <div className="admin-knowledge">
      <div className="admin-tab-head">
        <AdminSectionLabel
          eyebrow="检索上下文"
          title="知识库"
          description="本地目录或第三方检索接口。检索片段只会作为生成参考注入，事实台账中的取值始终优先。"
          aside={
            <Button
              variant="primary"
              icon={<Plus size={15} />}
              onClick={() => setModal({ mode: 'create', kind: 'local' })}
            >
              添加知识库
            </Button>
          }
        />
      </div>

      {actionError && (
        <AdminActionError message={actionError} onClose={() => setActionError(null)} />
      )}

      <div className={className('admin-callout', 'admin-callout--ok')}>
        <Database size={18} />
        <div>
          <strong>
            {defaultKb
              ? `默认检索源为「${defaultKb.name}」`
              : '尚未设置默认知识库'}
          </strong>
          <p>
            生成时会检索默认知识库的相关片段作为参考，但不会覆盖事实台账。没有默认知识库时，
            正文仍会基于事实与规则包生成。
          </p>
        </div>
      </div>

      <div className="admin-list-toolbar">
        <AdminSegment
          label="按类型筛选知识库"
          value={kindFilter}
          onChange={setKindFilter}
          items={[
            { id: 'all', label: '全部', count: counts.all },
            ...KB_GROUPS.map((group) => ({
              id: group.id,
              label: group.singular,
              count: counts[group.id] || 0,
            })),
          ]}
        />
        <button type="button" className="text-btn" onClick={refresh} disabled={refreshing}>
          <RefreshCw size={13} className={refreshing ? 'spin' : undefined} />
          刷新
        </button>
      </div>

      {error ? (
        <div className="admin-panel-inner">
          <ErrorState message={error} onRetry={refresh} />
        </div>
      ) : loading ? (
        <AdminSkeletonRows rows={2} />
      ) : kbs.length === 0 ? (
        <div className="admin-panel-inner">
          <EmptyState
            icon={Database}
            title="还没有知识库"
            description="本地目录会把历史方案、技术资料或投标素材切成检索片段；第三方接口可连接 Dify 或 RAGFlow。"
            actions={
              <>
                <Button
                  variant="primary"
                  icon={<Plus size={15} />}
                  onClick={() => setModal({ mode: 'create', kind: 'local' })}
                >
                  添加本地目录
                </Button>
                <Button
                  variant="secondary"
                  icon={<Cloud size={15} />}
                  onClick={() => setModal({ mode: 'create', kind: 'api' })}
                >
                  连接第三方接口
                </Button>
              </>
            }
          />
          <div className="admin-empty-note">
            服务器目录需要有可读权限；图片与扫描件不会自动 OCR。
          </div>
        </div>
      ) : (
        <div className="admin-groups">
          {visibleGroups.map((group) => {
            const Icon = group.icon
            const groupKbs = kbs.filter((kb) => kb.kind === group.id)
            return (
              <section className="admin-group" key={group.id} aria-label={group.label}>
                <header className="admin-group-head">
                  <div className="admin-group-title">
                    <span className="admin-group-icon" aria-hidden>
                      <Icon size={17} />
                    </span>
                    <div>
                      <h3>
                        {group.label}
                        <span className="admin-group-count">{groupKbs.length}</span>
                      </h3>
                      <p>{group.description}</p>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<Plus size={13} />}
                    onClick={() => setModal({ mode: 'create', kind: group.id })}
                  >
                    添加
                  </Button>
                </header>

                {groupKbs.length === 0 ? (
                  <div className="admin-group-empty">
                    <span>暂无{group.label}知识库。</span>
                    {group.id === 'local' && <span>先准备服务器上的资料目录，再建立索引。</span>}
                  </div>
                ) : (
                  <div className="admin-kb-list">
                    {groupKbs.map((kb) => {
                      const result = testResults[kb.id]
                      const isLocal = kb.kind === 'local'
                      const vectorMeta = kbVectorMeta(kb)
                      const vectorHint = !isLocal
                        ? ''
                        : !kb.chunk_count
                          ? '先重建索引再构建向量'
                          : !kb.vector_model
                            ? '未配置嵌入模型'
                            : ''
                      const vectorTitle =
                        vectorHint ||
                        (vectorMeta.tone === 'success'
                          ? '检索会同时使用关键词与语义相似度'
                          : vectorMeta.tone === 'warn'
                            ? `已嵌入 ${formatNumber(
                                kb.vector_embedded ?? 0,
                              )} / ${formatNumber(
                                kb.vector_total ?? kb.chunk_count ?? 0,
                              )} 个片段`
                            : '语义向量未启用，检索只匹配关键词')
                      return (
                        <article
                          key={kb.id}
                          className={className('admin-kb-row', !kb.enabled && 'is-disabled')}
                        >
                          <div className="admin-kb-main">
                            <div className="admin-model-title-line">
                              <h4>{kb.name}</h4>
                              {kb.is_default && (
                                <Badge tone="accent" subtle>
                                  默认
                                </Badge>
                              )}
                              <Badge tone={kb.enabled ? 'success' : 'neutral'} subtle>
                                {kb.enabled ? '已启用' : '已停用'}
                              </Badge>
                              <Badge tone={kbKindMeta(kb.kind).tone} subtle>
                                {kbKindMeta(kb.kind).label}
                              </Badge>
                              {kb.kind !== 'local' && (
                                <span className="admin-provider">{kbProviderLabel(kb.provider)}</span>
                              )}
                            </div>
                            <div className="admin-kb-location mono" title={kb.location}>
                              {kb.location || '未填写位置'}
                            </div>
                            {kb.dataset_id && (
                              <div className="admin-kb-dataset">
                                <span className="admin-meta-label">数据集</span>
                                <span className="mono">{kb.dataset_id}</span>
                              </div>
                            )}
                            {isLocal && hasVectorInfo(kb) && (
                              <div className="admin-kb-vector">
                                <Badge
                                  tone={vectorMeta.tone}
                                  subtle
                                  dot
                                  title={vectorTitle}
                                >
                                  {vectorMeta.label}
                                </Badge>
                                {vectorHint ? (
                                  <span className="admin-kb-vector-hint">
                                    {vectorHint}
                                  </span>
                                ) : (
                                  <span className="admin-kb-vector-model mono">
                                    {kb.vector_model}
                                    {Boolean(kb.vector_dim) && (
                                      <span className="admin-kb-vector-dim">
                                        {formatNumber(kb.vector_dim)} 维
                                      </span>
                                    )}
                                  </span>
                                )}
                              </div>
                            )}
                            <div className="admin-model-meta">
                              <span>
                                <span className="admin-meta-label">片段</span>
                                {formatNumber(kb.chunk_count || 0)}
                              </span>
                              <span>
                                <span className="admin-meta-label">索引</span>
                                {kb.indexed_at ? formatRelative(kb.indexed_at) : '未建立索引'}
                              </span>
                              <span>
                                <span className="admin-meta-label">密钥</span>
                                {kb.kind === 'local'
                                  ? '本地目录无需密钥'
                                  : kb.has_api_key
                                    ? '已配置'
                                    : '未配置'}
                              </span>
                              <span>
                                <span className="admin-meta-label">更新</span>
                                {formatRelative(kb.updated_at)}
                              </span>
                            </div>
                          </div>

                          <div className="admin-kb-status">
                            <LastTestStatus
                              ok={kb.last_test_ok}
                              at={kb.last_test_at}
                              message={kb.last_test_message}
                            />
                            {kb.last_test_message && (
                              <p className="admin-last-test-message" title={kb.last_test_message}>
                                {kb.last_test_message}
                              </p>
                            )}
                            {reindexResults[kb.id] && (
                              <span className="admin-reindex-result">
                                {reindexResults[kb.id]}
                              </span>
                            )}
                          </div>

                          <div className="admin-row-actions">
                            <Button
                              variant="secondary"
                              size="sm"
                              icon={<RefreshCw size={12} />}
                              loading={testingId === kb.id}
                              disabled={
                                Boolean(testingId) ||
                                Boolean(reindexingId) ||
                                Boolean(embeddingId)
                              }
                              onClick={() => void runTest(kb)}
                            >
                              测试
                            </Button>
                            {isLocal && (
                              <>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  icon={<RotateCw size={12} />}
                                  loading={reindexingId === kb.id}
                                  disabled={
                                    Boolean(reindexingId) ||
                                    Boolean(testingId) ||
                                    Boolean(embeddingId)
                                  }
                                  onClick={() => void reindex(kb)}
                                >
                                  重建索引
                                </Button>
                                {kb.chunk_count > 0 && hasVectorInfo(kb) && (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    icon={<Waypoints size={12} />}
                                    loading={embeddingId === kb.id}
                                    disabled={
                                      Boolean(embeddingId) ||
                                      Boolean(testingId) ||
                                      Boolean(reindexingId)
                                    }
                                    title={`为「${kb.name}」的 ${formatNumber(
                                      kb.chunk_count,
                                    )} 个片段构建语义向量`}
                                    onClick={() => void buildVectors(kb)}
                                  >
                                    构建向量
                                  </Button>
                                )}
                              </>
                            )}
                            <Button
                              variant="ghost"
                              size="sm"
                              icon={<Tags size={12} />}
                              onClick={() => setDocsKb(kb)}
                            >
                              素材元数据
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              icon={<Pencil size={12} />}
                              onClick={() => setModal({ mode: 'edit', kb })}
                            >
                              编辑
                            </Button>
                            <IconButton
                              label={`删除知识库 ${kb.name}`}
                              tone="danger"
                              onClick={() => void remove(kb)}
                            >
                              <Trash2 size={14} />
                            </IconButton>
                          </div>
                          {result && (
                            <div className="admin-test-row">
                              <TestFeedback result={result} />
                            </div>
                          )}
                        </article>
                      )
                    })}
                  </div>
                )}
              </section>
            )
          })}
        </div>
      )}

      <div className="admin-footnote">
        本地索引只保存文本片段与来源；语义向量只用于检索排序，构建失败时会自动退回关键词匹配。第三方接口密钥加密保存。检索结果始终作为参考，事实台账优先。
      </div>

      {modal && (
        <AdminKnowledgeModal
          open
          mode={modal.mode}
          kb={modal.mode === 'edit' ? modal.kb : undefined}
          initialKind={modal.mode === 'create' ? modal.kind : 'local'}
          onClose={() => setModal(null)}
          onSaved={onSaved}
        />
      )}

      {docsKb && (
        <AdminKbDocsModal
          key={docsKb.id}
          kb={docsKb}
          onClose={() => setDocsKb(null)}
        />
      )}
    </div>
  )
}

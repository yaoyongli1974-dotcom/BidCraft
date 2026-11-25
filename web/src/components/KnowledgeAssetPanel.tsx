import { useMemo, useState } from 'react'
import {
  Copy,
  FileText,
  Inbox,
  Layers,
  Link2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import {
  extractKnowledgeAsset,
  listKnowledgeAssetDuplicates,
  listKnowledgeAssets,
  resolveKnowledgeAssetDuplicate,
  reviewKnowledgeAsset,
} from '../api/knowledge'
import {
  extractEnterpriseKnowledgeAsset,
  listEnterpriseKnowledgeAssetDuplicates,
  listEnterpriseKnowledgeAssets,
  resolveEnterpriseKnowledgeAssetDuplicate,
  reviewEnterpriseKnowledgeAsset,
} from '../api/enterprise'
import { messageOf } from '../lib/http'
import { className, formatRelative } from '../lib/format'
import { useQuery } from '../lib/hooks'
import {
  KNOWLEDGE_ASSET_DECISIONS,
  KNOWLEDGE_ASSET_DECISION_META,
  isDecisionAllowed,
  knowledgeAssetStatusMeta,
} from '../lib/knowledgeAssetMeta'
import type {
  KnowledgeAsset,
  KnowledgeAssetDecision,
  KnowledgeAssetDuplicateDecision,
  KnowledgeAssetDuplicateGroup,
} from '../lib/types'
import { Badge } from './Badge'
import { Button } from './Button'
import { EmptyState, ErrorState, LoadingBlock } from './Feedback'
import { RetrievalTraceBadges, RetrievalTraceMeta } from './RetrievalTraceBadges'

interface KnowledgeAssetApi {
  listAssets: () => Promise<KnowledgeAsset[]>
  listDuplicates: () => Promise<KnowledgeAssetDuplicateGroup[]>
  extract: (documentId: string) => Promise<KnowledgeAsset>
  review: (
    documentId: string,
    decision: KnowledgeAssetDecision,
    note: string,
  ) => Promise<KnowledgeAsset>
  resolve: (
    groupId: string,
    decision: KnowledgeAssetDuplicateDecision,
    note: string,
  ) => Promise<KnowledgeAssetDuplicateGroup>
}

export interface KnowledgeAssetPanelProps {
  /** 管理端知识库 id；传入时走 /admin/kbs/{kbId}/assets。 */
  kbId?: string
  /** 企业 id；传入时走 /enterprise/{enterpriseId}/assets（优先于 kbId）。 */
  enterpriseId?: string
  /** 提炼资产与重复组裁决权限。 */
  canManage?: boolean
  /** 审核流转权限。 */
  canReview?: boolean
}

function useKnowledgeAssetApi(kbId?: string, enterpriseId?: string): KnowledgeAssetApi | null {
  return useMemo(() => {
    if (enterpriseId) {
      return {
        listAssets: () => listEnterpriseKnowledgeAssets(enterpriseId),
        listDuplicates: () => listEnterpriseKnowledgeAssetDuplicates(enterpriseId),
        extract: (documentId) => extractEnterpriseKnowledgeAsset(enterpriseId, documentId),
        review: (documentId, decision, note) =>
          reviewEnterpriseKnowledgeAsset(enterpriseId, documentId, { decision, note }),
        resolve: (groupId, decision, note) =>
          resolveEnterpriseKnowledgeAssetDuplicate(enterpriseId, groupId, { decision, note }),
      }
    }
    if (kbId) {
      return {
        listAssets: () => listKnowledgeAssets(kbId),
        listDuplicates: () => listKnowledgeAssetDuplicates(kbId),
        extract: (documentId) => extractKnowledgeAsset(kbId, documentId),
        review: (documentId, decision, note) =>
          reviewKnowledgeAsset(kbId, documentId, { decision, note }),
        resolve: (groupId, decision, note) =>
          resolveKnowledgeAssetDuplicate(kbId, groupId, { decision, note }),
      }
    }
    return null
  }, [enterpriseId, kbId])
}

function fieldValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

/**
 * 知识资产工作台：来源文档 → 结构化字段 → 审核生命周期 → 来源追溯 → 重复组。
 * 所有读操作走 useQuery，所有写操作都要求显式能力开关；失败只展示服务端消息，
 * 不更新本地状态，也不声称成功。
 */
export function KnowledgeAssetPanel({
  kbId,
  enterpriseId,
  canManage = false,
  canReview = false,
}: KnowledgeAssetPanelProps) {
  const api = useKnowledgeAssetApi(kbId, enterpriseId)
  const scope = enterpriseId ? `enterprise:${enterpriseId}` : kbId ? `kb:${kbId}` : 'none'
  const assetsQuery = useQuery(
    `knowledge-assets:${scope}`,
    api ? () => api.listAssets() : () => Promise.reject(new Error('未指定资产范围')),
    { enabled: Boolean(api) },
  )
  const duplicatesQuery = useQuery(
    `knowledge-asset-duplicates:${scope}`,
    api ? () => api.listDuplicates() : () => Promise.reject(new Error('未指定资产范围')),
    { enabled: Boolean(api) },
  )

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [reviewNote, setReviewNote] = useState('')
  const [duplicateNotes, setDuplicateNotes] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const assets = assetsQuery.data || []
  const groups = duplicatesQuery.data || []
  const selected = assets.find((item) => item.id === selectedId) || assets[0] || null
  const statusMeta = knowledgeAssetStatusMeta(selected?.review_status)
  const refreshing = assetsQuery.refreshing || duplicatesQuery.refreshing

  /** 失败时只记录错误，apply 不会执行；状态表保持服务端上次返回值。 */
  const perform = async (key: string, apply: () => Promise<void>) => {
    if (!api || busy) return
    setBusy(key)
    setActionError(null)
    try {
      await apply()
    } catch (error) {
      setActionError(messageOf(error))
    } finally {
      setBusy(null)
    }
  }

  const onExtract = (asset: KnowledgeAsset) => {
    void perform(`extract:${asset.id}`, async () => {
      const saved = await api!.extract(asset.document_id || asset.id)
      assetsQuery.setData((current) => [
        saved,
        ...(current || []).filter((item) => item.id !== saved.id),
      ])
      setSelectedId(saved.id)
    })
  }

  const onReview = (decision: KnowledgeAssetDecision) => {
    if (!selected || !isDecisionAllowed(selected.review_status, decision)) return
    void perform(`review:${selected.id}`, async () => {
      const saved = await api!.review(selected.document_id || selected.id, decision, reviewNote)
      assetsQuery.setData((current) =>
        (current || []).map((item) => (item.id === saved.id ? saved : item)),
      )
    })
  }

  const onResolve = (group: KnowledgeAssetDuplicateGroup, decision: KnowledgeAssetDuplicateDecision) => {
    void perform(`resolve:${group.id}`, async () => {
      const saved = await api!.resolve(group.id, decision, duplicateNotes[group.id] || '')
      duplicatesQuery.setData((current) =>
        (current || []).map((item) => (item.id === saved.id ? saved : item)),
      )
    })
  }

  const refresh = () => {
    assetsQuery.refresh()
    duplicatesQuery.refresh()
  }

  return (
    <section className="panel ka-panel" aria-labelledby="ka-heading">
      <div className="panel-head">
        <div>
          <div className="eyebrow">知识资产引擎</div>
          <h3 id="ka-heading">{enterpriseId ? '企业知识资产' : '知识库资产'}</h3>
        </div>
        <Button
          size="sm"
          variant="ghost"
          icon={<RefreshCw size={13} className={refreshing ? 'spin' : undefined} />}
          disabled={!api || refreshing}
          onClick={refresh}
        >
          刷新
        </Button>
      </div>

      {!api ? (
        <div className="panel-body">
          <EmptyState
            compact
            icon={Inbox}
            title="未指定资产范围"
            description="请传入知识库 id 或企业 id 后再查看资产。"
          />
        </div>
      ) : (
        <div className="panel-body ka-body">
          {actionError && <ErrorState compact message={actionError} />}

          <section className="ka-section" aria-label="源文档">
            <div className="ka-section-head">
              <FileText size={15} aria-hidden />
              <h4>来源文档</h4>
              <span className="subtle">{assets.length} 项</span>
            </div>
            {assetsQuery.loading && !assetsQuery.data ? (
              <LoadingBlock lines={2} />
            ) : assetsQuery.error ? (
              <ErrorState compact message={assetsQuery.error} onRetry={assetsQuery.refresh} />
            ) : assets.length === 0 ? (
              <EmptyState
                compact
                icon={FileText}
                title="还没有可提炼的资产"
                description="先由服务端从知识库文档抽取资产，再在这里复核。"
              />
            ) : (
              <ul className="ka-doc-list">
                {assets.map((asset) => {
                  const meta = knowledgeAssetStatusMeta(asset.review_status)
                  const active = selected?.id === asset.id
                  return (
                    <li
                      key={asset.id}
                      className={className('ka-doc', active && 'is-active')}
                    >
                      <button
                        type="button"
                        className="ka-doc-select"
                        aria-pressed={active}
                        onClick={() => setSelectedId(asset.id)}
                      >
                        <strong>{asset.source || '未命名来源'}</strong>
                        <span className="mono">{asset.document_id || '无文档 ID'}</span>
                      </button>
                      <Badge tone={meta.tone} subtle>
                        {meta.label}
                      </Badge>
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={<Sparkles size={12} />}
                        loading={busy === `extract:${asset.id}`}
                        disabled={!canManage || Boolean(busy)}
                        title={
                          canManage
                            ? `从 ${asset.document_id || asset.id} 重新提炼`
                            : '当前账号没有管理知识资产的权限'
                        }
                        onClick={() => onExtract(asset)}
                      >
                        提炼
                      </Button>
                    </li>
                  )
                })}
              </ul>
            )}
          </section>

          <section className="ka-section" aria-label="提取字段">
            <div className="ka-section-head">
              <Layers size={15} aria-hidden />
              <h4>提取字段</h4>
            </div>
            {!selected ? (
              <EmptyState compact icon={Layers} title="未选择资产" description="选择一条资产后查看结构化字段。" />
            ) : Object.keys(selected.fields || {}).length === 0 ? (
              <EmptyState compact icon={Layers} title="尚无提取字段" description="服务端尚未返回该资产的结构化字段。" />
            ) : (
              <dl className="ka-fields">
                {Object.entries(selected.fields || {}).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>{fieldValue(value)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>

          <section className="ka-section" aria-label="生命周期">
            <div className="ka-section-head">
              <ShieldCheck size={15} aria-hidden />
              <h4>审核生命周期</h4>
            </div>
            {!selected ? (
              <EmptyState compact icon={ShieldCheck} title="未选择资产" description="选择资产后可审核其生命周期。" />
            ) : (
              <>
                <div className="ka-status-line">
                  <Badge tone={statusMeta.tone} dot>
                    {statusMeta.label}
                  </Badge>
                  <span className="subtle">{statusMeta.hint}</span>
                </div>
                <dl className="ka-fields">
                  <div>
                    <dt>审核人</dt>
                    <dd>{selected.reviewed_by || '—'}</dd>
                  </div>
                  <div>
                    <dt>审核时间</dt>
                    <dd>{formatRelative(selected.reviewed_at) || '—'}</dd>
                  </div>
                  <div>
                    <dt>发布时间</dt>
                    <dd>{formatRelative(selected.published_at) || '—'}</dd>
                  </div>
                  <div>
                    <dt>校验结果</dt>
                    <dd>
                      {selected.ok === undefined
                        ? '—'
                        : selected.ok
                          ? '通过'
                          : '未通过'}
                    </dd>
                  </div>
                  {selected.rejected_reason && (
                    <div>
                      <dt>驳回原因</dt>
                      <dd>{selected.rejected_reason}</dd>
                    </div>
                  )}
                  {selected.reason && (
                    <div>
                      <dt>说明</dt>
                      <dd>{selected.reason}</dd>
                    </div>
                  )}
                </dl>
                <label className="form-field">
                  <span>审核意见</span>
                  <textarea
                    className="textarea"
                    rows={2}
                    aria-label="审核意见"
                    value={reviewNote}
                    placeholder="可填写审核依据，随审核动作提交给服务端。"
                    disabled={!canReview || Boolean(busy)}
                    onChange={(event) => setReviewNote(event.target.value)}
                  />
                </label>
                <div className="ka-actions">
                  {KNOWLEDGE_ASSET_DECISIONS.map((decision) => {
                    const meta = KNOWLEDGE_ASSET_DECISION_META[decision]
                    const allowed =
                      canReview &&
                      isDecisionAllowed(selected.review_status, decision) &&
                      !busy
                    const title = !canReview
                      ? '当前账号没有审核知识资产的权限'
                      : allowed
                        ? undefined
                        : `当前状态（${statusMeta.label}）不允许「${meta.label}」`
                    return (
                      <Button
                        key={decision}
                        size="sm"
                        variant={meta.variant}
                        disabled={!allowed}
                        title={title}
                        onClick={() => onReview(decision)}
                      >
                        {meta.label}
                      </Button>
                    )
                  })}
                  {!canReview && (
                    <span className="subtle">仅审核人可变更生命周期状态。</span>
                  )}
                </div>
              </>
            )}
          </section>

          <section className="ka-section" aria-label="来源追溯">
            <div className="ka-section-head">
              <Link2 size={15} aria-hidden />
              <h4>来源追溯</h4>
            </div>
            {!selected ? (
              <EmptyState compact icon={Link2} title="未选择资产" description="选择资产后查看文档与片段来源。" />
            ) : (
              <>
                <dl className="ka-fields">
                  <div>
                    <dt>来源文档</dt>
                    <dd className="mono">{selected.document_id || '—'}</dd>
                  </div>
                  <div>
                    <dt>源文件 ID</dt>
                    <dd className="mono">{selected.source_file_id || '—'}</dd>
                  </div>
                  <div>
                    <dt>知识库 ID</dt>
                    <dd className="mono">{selected.kb_id || '—'}</dd>
                  </div>
                  <div>
                    <dt>来源</dt>
                    <dd>{selected.source || '—'}</dd>
                  </div>
                  <div>
                    <dt>SHA-256</dt>
                    <dd className="mono">{selected.sha256 || '—'}</dd>
                  </div>
                </dl>
                <RetrievalTraceBadges
                  rerank_score={selected.rerank_score}
                  trust_tier={selected.trust_tier}
                  degraded={selected.degraded}
                  compact
                />
                <RetrievalTraceMeta
                  chunk_id={selected.chunk_id}
                  chunk_index={selected.chunk_index}
                  document_id={selected.document_id}
                  page={typeof selected.page === 'number' ? selected.page : null}
                  heading={typeof selected.heading === 'string' ? selected.heading : null}
                  source={selected.source}
                  kb_id={selected.kb_id}
                  compact
                />
                {!selected.chunks || selected.chunks.length === 0 ? (
                  <p className="subtle">服务端未返回片段溯源。</p>
                ) : (
                  <ul className="ka-chunk-list">
                    {selected.chunks.map((chunk, index) => (
                      <li key={chunk.id || `chunk-${index}`}>
                        <span className="mono">{chunk.id || '—'}</span>
                        <span>{chunk.heading || '无标题'}</span>
                        <span className="subtle">
                          {typeof chunk.page === 'number' ? `第 ${chunk.page} 页` : '页码未知'}
                        </span>
                        {chunk.chunk_index !== null && chunk.chunk_index !== undefined && (
                          <span className="subtle">块序号 {chunk.chunk_index}</span>
                        )}
                        {typeof chunk.source === 'string' && chunk.source && (
                          <span className="subtle">{chunk.source}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </section>

          <section className="ka-section" aria-label="重复组">
            <div className="ka-section-head">
              <Copy size={15} aria-hidden />
              <h4>重复资产组</h4>
              <span className="subtle">{groups.length} 组</span>
            </div>
            {duplicatesQuery.loading && !duplicatesQuery.data ? (
              <LoadingBlock lines={2} />
            ) : duplicatesQuery.error ? (
              <ErrorState compact message={duplicatesQuery.error} onRetry={duplicatesQuery.refresh} />
            ) : groups.length === 0 ? (
              <EmptyState compact icon={Copy} title="没有重复组" description="服务端没有报告重复资产。" />
            ) : (
              <ul className="ka-group-list">
                {groups.map((group) => (
                  <li key={group.id} className="ka-group">
                    <div className="ka-group-head">
                      <strong className="mono">{group.fingerprint || group.id}</strong>
                      <Badge
                        tone={
                          group.status === 'resolved'
                            ? 'success'
                            : group.status === 'dismissed'
                              ? 'neutral'
                              : 'warn'
                        }
                        subtle
                      >
                        {group.status || 'pending'}
                      </Badge>
                      {typeof group.similarity === 'number' && (
                        <span className="mono">相似度 {group.similarity}</span>
                      )}
                    </div>
                    {group.reason && <p>{group.reason}</p>}
                    <ul className="ka-group-members">
                      {(group.members || []).map((member, index) => (
                        <li key={member.id || `${member.document_id}-${index}`}>
                          <span>{member.source || '未命名来源'}</span>
                          <span className="mono">{member.document_id || '—'}</span>
                          {typeof member.score === 'number' && (
                            <span className="subtle">分值 {member.score}</span>
                          )}
                          {member.role && <span className="subtle">角色 {member.role}</span>}
                        </li>
                      ))}
                    </ul>
                    <div className="ka-actions">
                      <input
                        className="input input--sm"
                        aria-label={`重复组 ${group.id} 处理意见`}
                        value={duplicateNotes[group.id] || ''}
                        placeholder="裁决说明（可选）"
                        disabled={!canManage || Boolean(busy)}
                        onChange={(event) =>
                          setDuplicateNotes((current) => ({
                            ...current,
                            [group.id]: event.target.value,
                          }))
                        }
                      />
                      <Button
                        size="sm"
                        variant="primary"
                        loading={busy === `resolve:${group.id}`}
                        disabled={!canManage || Boolean(busy)}
                        title={canManage ? undefined : '当前账号没有管理知识资产的权限'}
                        onClick={() => onResolve(group, 'resolve')}
                      >
                        解决
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={!canManage || Boolean(busy)}
                        title={canManage ? undefined : '当前账号没有管理知识资产的权限'}
                        onClick={() => onResolve(group, 'dismiss')}
                      >
                        忽略
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </section>
  )
}

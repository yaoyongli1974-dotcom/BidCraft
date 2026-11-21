import { useMemo, useState } from 'react'
import {
  AlertTriangle,
  Image as ImageIcon,
  MessagesSquare,
  Pencil,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
  Waypoints,
  type LucideIcon,
} from 'lucide-react'
import type { ModelConfig, TestResult } from '../../lib/types'
import {
  MODEL_KINDS,
  providerLabel,
  formatNumber,
  formatRelative,
  className,
} from '../../lib/format'
import { deleteModel, testModel } from '../../api/compliance'
import { messageOf } from '../../lib/http'
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
import { AdminModelModal, type ModelModalState } from './AdminModelModal'

interface ModelTabProps {
  models: ModelConfig[]
  loading: boolean
  refreshing: boolean
  error: string | null
  refresh: () => void
  refreshHealth: () => void
}

const KIND_ICONS: Record<string, LucideIcon> = {
  chat: MessagesSquare,
  image: ImageIcon,
  embedding: Waypoints,
}

export function AdminModelsTab({
  models,
  loading,
  refreshing,
  error,
  refresh,
  refreshHealth,
}: ModelTabProps) {
  const toast = useToast()
  const confirm = useConfirm()
  const [kindFilter, setKindFilter] = useState('all')
  const [modal, setModal] = useState<ModelModalState | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({})
  const [actionError, setActionError] = useState<string | null>(null)

  const counts = useMemo(() => {
    const result: Record<string, number> = { all: models.length }
    MODEL_KINDS.forEach((kind) => {
      result[kind.id] = models.filter((model) => model.kind === kind.id).length
    })
    return result
  }, [models])

  const defaultChat = models.find(
    (model) => model.kind === 'chat' && model.is_default && model.enabled,
  )
  const visibleKinds = MODEL_KINDS.filter(
    (kind) => kindFilter === 'all' || kind.id === kindFilter,
  )

  const runTest = async (model: ModelConfig) => {
    if (testingId) return
    setTestingId(model.id)
    setActionError(null)
    setTestResults((current) => {
      const next = { ...current }
      delete next[model.id]
      return next
    })
    try {
      const result = await testModel(model.id)
      setTestResults((current) => ({ ...current, [model.id]: result }))
      refresh()
      refreshHealth()
      if (result.ok) {
        toast.success('模型连接正常', `${model.name} · ${result.message}`)
      } else {
        toast.warning('模型连接失败', result.message)
      }
    } catch (err) {
      const message = messageOf(err)
      setTestResults((current) => ({
        ...current,
        [model.id]: { ok: false, message, latency_ms: 0 },
      }))
      setActionError(message)
      toast.error('测试失败', message)
    } finally {
      setTestingId(null)
    }
  }

  const remove = async (model: ModelConfig) => {
    const accepted = await confirm({
      title: '删除模型配置？',
      description: `「${model.name}」将从模型列表中移除。`,
      note:
        model.kind === 'chat' && model.is_default
          ? '这是当前的默认对话模型；删除后生成正文可能回落到其他模型或确定性草稿。'
          : '已保存的 API 密钥也会一并删除。',
      confirmLabel: '删除模型',
      tone: 'danger',
    })
    if (!accepted) return
    try {
      await deleteModel(model.id)
      refresh()
      refreshHealth()
      toast.success('模型已删除', model.name)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('删除模型失败', message)
    }
  }

  const onSaved = (model: ModelConfig, created: boolean) => {
    setModal(null)
    refresh()
    refreshHealth()
    toast.success(created ? '模型已创建' : '模型已更新', model.name)
  }

  return (
    <div className="admin-models">
      <div className="admin-tab-head">
        <AdminSectionLabel
          eyebrow="模型端点"
          title="模型配置"
          description="管理对话、图片与向量模型。生成正文使用对话类型的默认模型；密钥只保存密文，不会回显。"
          aside={
            <Button
              variant="primary"
              icon={<Plus size={15} />}
              onClick={() => setModal({ mode: 'create', kind: 'chat' })}
            >
              添加模型
            </Button>
          }
        />
      </div>

      {actionError && (
        <AdminActionError message={actionError} onClose={() => setActionError(null)} />
      )}

      <div
        className={className(
          'admin-callout',
          defaultChat ? 'admin-callout--ok' : 'admin-callout--warn',
        )}
      >
        {defaultChat ? <Sparkles size={18} /> : <AlertTriangle size={18} />}
        <div>
          <strong>
            {defaultChat
              ? `生成正文使用「${defaultChat.name}」`
              : '尚未配置可用的默认对话模型'}
          </strong>
          <p>
            {defaultChat
              ? `当前默认模型为 ${defaultChat.model || '未指定模型 ID'}；生成、续写和图片模型连通性测试都由管理员在此集中管理。`
              : '没有可用的对话模型时，正文生成会回落到确定性草稿。先在「对话模型」中添加并启用一个默认模型。'}
          </p>
        </div>
      </div>

      <div className="admin-list-toolbar">
        <AdminSegment
          label="按类型筛选模型"
          value={kindFilter}
          onChange={setKindFilter}
          items={[
            { id: 'all', label: '全部', count: counts.all },
            ...MODEL_KINDS.map((kind) => ({
              id: kind.id,
              label: kind.singular,
              count: counts[kind.id] || 0,
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
        <AdminSkeletonRows rows={3} />
      ) : models.length === 0 ? (
        <div className="admin-panel-inner">
          <EmptyState
            icon={MessagesSquare}
            title="还没有模型配置"
            description="未配置对话模型时，正文生成会使用确定性草稿，结构完整但文字质量有限。先添加一个 OpenAI 兼容或 Ollama 端点。"
            actions={
              <Button
                variant="primary"
                icon={<Plus size={15} />}
                onClick={() => setModal({ mode: 'create', kind: 'chat' })}
              >
                添加第一个模型
              </Button>
            }
          />
        </div>
      ) : (
        <div className="admin-groups">
          {visibleKinds.map((kind) => {
            const Icon = KIND_ICONS[kind.id] || MessagesSquare
            const groupModels = models.filter((model) => model.kind === kind.id)
            return (
              <section className="admin-group" key={kind.id} aria-label={kind.label}>
                <header className="admin-group-head">
                  <div className="admin-group-title">
                    <span className="admin-group-icon" aria-hidden>
                      <Icon size={17} />
                    </span>
                    <div>
                      <h3>
                        {kind.label}
                        <span className="admin-group-count">{groupModels.length}</span>
                      </h3>
                      <p>{kind.description}</p>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<Plus size={13} />}
                    onClick={() => setModal({ mode: 'create', kind: kind.id })}
                  >
                    添加
                  </Button>
                </header>

                {kind.id === 'image' && (
                  <div className="admin-kind-note">
                    <ImageIcon size={14} />
                    图片模型用于正文配图生成；生成入口在正文页的图片占位。测试按钮仅验证端点连通性，不实际生成图片。
                  </div>
                )}

                {groupModels.length === 0 ? (
                  <div className="admin-group-empty">
                    <span>暂无{kind.label}。</span>
                    {kind.id === 'chat' && <span>生成正文前建议至少启用一个默认对话模型。</span>}
                  </div>
                ) : (
                  <div className="admin-model-list">
                    {groupModels.map((model) => {
                      const result = testResults[model.id]
                      const isDefaultChat = model.kind === 'chat' && model.is_default
                      return (
                        <article
                          key={model.id}
                          className={className(
                            'admin-model-row',
                            !model.enabled && 'is-disabled',
                            isDefaultChat && 'is-default-chat',
                          )}
                        >
                          <div className="admin-model-main">
                            <div className="admin-model-title-line">
                              <h4>{model.name}</h4>
                              {isDefaultChat ? (
                                <Badge tone="accent" subtle>
                                  <Sparkles size={11} /> 生成默认
                                </Badge>
                              ) : model.is_default ? (
                                <Badge tone="accent" subtle>
                                  默认
                                </Badge>
                              ) : null}
                              <Badge tone={model.enabled ? 'success' : 'neutral'} subtle>
                                {model.enabled ? '已启用' : '已停用'}
                              </Badge>
                              <span className="admin-provider">{providerLabel(model.provider)}</span>
                            </div>
                            <div className="admin-model-id">
                              <span className="mono">{model.model || '未指定模型 ID'}</span>
                            </div>
                            <div className="admin-model-url mono" title={model.base_url}>
                              {model.base_url || '未配置 Base URL'}
                            </div>
                            <div className="admin-model-meta">
                              <span>
                                <span className="admin-meta-label">密钥</span>
                                {model.has_api_key ? '已配置' : '未配置'}
                              </span>
                              <span>
                                <span className="admin-meta-label">温度</span>
                                {model.temperature ?? 0.4}
                              </span>
                              <span>
                                <span className="admin-meta-label">上限</span>
                                {formatNumber(model.max_tokens || 0)} tokens
                              </span>
                              <span>
                                <span className="admin-meta-label">更新</span>
                                {formatRelative(model.updated_at)}
                              </span>
                            </div>
                          </div>

                          <div className="admin-model-status">
                            <LastTestStatus
                              ok={model.last_test_ok}
                              at={model.last_test_at}
                              message={model.last_test_message}
                            />
                            {model.last_test_message && (
                              <p className="admin-last-test-message" title={model.last_test_message}>
                                {model.last_test_message}
                              </p>
                            )}
                          </div>

                          <div className="admin-row-actions">
                            <Button
                              variant="secondary"
                              size="sm"
                              icon={<RefreshCw size={12} />}
                              loading={testingId === model.id}
                              disabled={Boolean(testingId)}
                              onClick={() => void runTest(model)}
                            >
                              测试
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              icon={<Pencil size={12} />}
                              onClick={() => setModal({ mode: 'edit', model })}
                            >
                              编辑
                            </Button>
                            <IconButton
                              label={`删除模型 ${model.name}`}
                              tone="danger"
                              onClick={() => void remove(model)}
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
        模型配置仅保存在本机数据库。密钥写入后不回显；测试图片模型只验证端点可达，不实际生成图片。
      </div>

      {modal && (
        <AdminModelModal
          open
          mode={modal.mode}
          model={modal.mode === 'edit' ? modal.model : undefined}
          initialKind={modal.mode === 'create' ? modal.kind : 'chat'}
          onClose={() => setModal(null)}
          onSaved={onSaved}
        />
      )}
    </div>
  )
}

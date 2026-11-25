import { useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  Building2,
  FilePlus2,
  Package as PackageIcon,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  UserPlus,
  Users,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import {
  addEnterpriseDocument,
  addEnterpriseMember,
  createEnterprise,
  getEnterpriseDocuments,
  getEnterpriseMembers,
  getEnterprisePackage,
  getEnterprises,
  matchEnterpriseAssets,
  removeEnterpriseMember,
} from '../../api/enterprise'
import { messageOf } from '../../lib/http'
import type {
  Enterprise,
  EnterpriseMatch,
  EnterprisePackage,
} from '../../lib/types'
import { formatDate, formatRelative } from '../../lib/format'
import { useQuery } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, Skeleton, Spinner } from '../../components/Feedback'
import { Modal } from '../../components/Modal'
import { TabHeading } from '../../components/Workbench'
import { EnterpriseAssetList } from '../../components/EnterpriseAssetList'
import { KnowledgeAssetPanel } from '../../components/KnowledgeAssetPanel'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'

const EMPTY_DOC = { source: '', content: '', kind: '', tags: '', valid_to: '' }

export function EnterpriseTab() {
  const { ref, canWrite } = useWorkspace()
  const toast = useToast()
  const confirm = useConfirm()

  const enterprisesQuery = useQuery('enterprises', () => getEnterprises())
  const enterprises = useMemo(() => enterprisesQuery.data || [], [enterprisesQuery.data])
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => {
    if (!selectedId && enterprises.length > 0) setSelectedId(enterprises[0].id)
  }, [selectedId, enterprises])

  const selected: Enterprise | undefined = enterprises.find(
    (enterprise) => enterprise.id === selectedId,
  )
  /** 企业资产写操作同时要求项目可写与当前用户是该企业 owner。 */
  const canManageAssets = Boolean(canWrite && selected?.is_owner)

  const membersQuery = useQuery(
    `enterprise-members:${selectedId || 'none'}`,
    () => getEnterpriseMembers(selectedId || ''),
    { enabled: Boolean(selectedId) },
  )
  const documentsQuery = useQuery(
    `enterprise-documents:${selectedId || 'none'}`,
    () => getEnterpriseDocuments(selectedId || ''),
    { enabled: Boolean(selectedId) },
  )
  const members = useMemo(() => membersQuery.data || [], [membersQuery.data])
  const documents = useMemo(() => documentsQuery.data || [], [documentsQuery.data])

  const [newName, setNewName] = useState('')
  const [creatingEnterprise, setCreatingEnterprise] = useState(false)

  const [memberName, setMemberName] = useState('')
  const [addingMember, setAddingMember] = useState(false)

  const [docForm, setDocForm] = useState(EMPTY_DOC)
  const [addingDoc, setAddingDoc] = useState(false)

  const [requirement, setRequirement] = useState('')
  const [matchKind, setMatchKind] = useState('')
  const [matching, setMatching] = useState(false)
  const [matchError, setMatchError] = useState<string | null>(null)
  const [matches, setMatches] = useState<EnterpriseMatch[] | null>(null)

  const [packageOpen, setPackageOpen] = useState(false)
  const [packageLoading, setPackageLoading] = useState(false)
  const [materialPackage, setMaterialPackage] = useState<EnterprisePackage | null>(null)
  const [packageError, setPackageError] = useState<string | null>(null)

  const createTeam = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const name = newName.trim()
    if (!canWrite || !name || creatingEnterprise) return
    setCreatingEnterprise(true)
    try {
      const created = await createEnterprise(name)
      enterprisesQuery.setData((current) => [...(current || []), created])
      setSelectedId(created.id)
      setNewName('')
      toast.success('企业已创建', created.name || name)
    } catch (error) {
      toast.error('创建企业失败', messageOf(error))
    } finally {
      setCreatingEnterprise(false)
    }
  }

  const addMember = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const username = memberName.trim()
    if (!canWrite || !selectedId || !username || addingMember) return
    setAddingMember(true)
    try {
      const created = await addEnterpriseMember(selectedId, { username })
      membersQuery.setData((current) => [...(current || []), created])
      setMemberName('')
      toast.success('成员已添加', created.username || username)
    } catch (error) {
      toast.error('添加成员失败', messageOf(error))
    } finally {
      setAddingMember(false)
    }
  }

  const removeMember = async (userId: string, username: string) => {
    if (!canWrite || !selectedId) return
    const accepted = await confirm({
      title: `移除成员「${username}」？`,
      description: '移除后该成员将不能继续使用企业共享资料，历史记录不受影响。',
      confirmLabel: '移除成员',
      tone: 'warning',
    })
    if (!accepted) return
    try {
      await removeEnterpriseMember(selectedId, userId)
      membersQuery.setData((current) =>
        (current || []).filter((member) => member.user_id !== userId),
      )
      toast.success('成员已移除', username)
    } catch (error) {
      toast.error('移除成员失败', messageOf(error))
    }
  }

  const addDocument = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!canWrite || !selectedId || addingDoc) return
    const source = docForm.source.trim()
    const content = docForm.content.trim()
    if (!source) {
      toast.warning('请填写资料名称', '资料名称用于在匹配结果中识别来源。')
      return
    }
    setAddingDoc(true)
    try {
      const created = await addEnterpriseDocument(selectedId, {
        source,
        content,
        kind: docForm.kind.trim() || undefined,
        tags: docForm.tags
          .split(/[,，、\s]+/)
          .map((tag) => tag.trim())
          .filter(Boolean),
        valid_to: docForm.valid_to || null,
      })
      documentsQuery.setData((current) => [created, ...(current || [])])
      setDocForm(EMPTY_DOC)
      toast.success('资料已入库', source)
    } catch (error) {
      toast.error('资料入库失败', messageOf(error))
    } finally {
      setAddingDoc(false)
    }
  }

  const runMatch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const text = requirement.trim()
    if (!text || matching) return
    setMatching(true)
    setMatchError(null)
    try {
      const result = await matchEnterpriseAssets(ref, {
        requirement: text,
        kind: matchKind.trim() || undefined,
      })
      setMatches(result)
    } catch (error) {
      setMatchError(messageOf(error))
      setMatches(null)
    } finally {
      setMatching(false)
    }
  }

  const openPackage = async () => {
    setPackageOpen(true)
    if (packageLoading) return
    setPackageLoading(true)
    setPackageError(null)
    try {
      setMaterialPackage(await getEnterprisePackage(ref))
    } catch (error) {
      setPackageError(messageOf(error))
    } finally {
      setPackageLoading(false)
    }
  }

  const packageItems = materialPackage?.items || []
  const packageDocuments = materialPackage?.documents || []

  return (
    <div className="tab-content ent-tab">
      <TabHeading
        eyebrow="企业知识库"
        title="企业共享与企业资产"
        description="把企业证照、业绩与人员资料沉淀为共享资产，在投标时按需求匹配并生成材料包。"
        actions={
          <Button
            size="sm"
            variant="secondary"
            icon={<RefreshCw size={13} className={enterprisesQuery.refreshing ? 'spin' : undefined} />}
            disabled={enterprisesQuery.refreshing}
            onClick={enterprisesQuery.refresh}
          >
            刷新
          </Button>
        }
      />

      <div className="ent-layout">
        <aside className="panel ent-side" aria-labelledby="ent-list-heading">
          <div className="panel-head">
            <div>
              <div className="eyebrow">企业账户</div>
              <h3 id="ent-list-heading">我的企业</h3>
            </div>
            <span className="subtle">{enterprises.length}</span>
          </div>

          {enterprisesQuery.loading && !enterprisesQuery.data ? (
            <div className="ent-enterprise-list" aria-busy="true">
              <Skeleton height={44} />
              <Skeleton height={44} />
            </div>
          ) : enterprisesQuery.error ? (
            <div className="ent-enterprise-list">
              <ErrorState
                compact
                message={enterprisesQuery.error}
                onRetry={enterprisesQuery.refresh}
              />
            </div>
          ) : enterprises.length === 0 ? (
            <div className="ent-enterprise-list">
              <EmptyState
                compact
                icon={Building2}
                title="还没有企业"
                description="创建企业后可邀请成员并共享资料。"
              />
            </div>
          ) : (
            <ul className="ent-enterprise-list">
              {enterprises.map((enterprise) => (
                <li key={enterprise.id}>
                  <button
                    type="button"
                    className={
                      enterprise.id === selectedId
                        ? 'ent-enterprise-item is-active'
                        : 'ent-enterprise-item'
                    }
                    aria-pressed={enterprise.id === selectedId}
                    onClick={() => setSelectedId(enterprise.id)}
                  >
                    <span>
                      <strong>{enterprise.name}</strong>
                      <span className="ent-enterprise-meta">
                        {typeof enterprise.member_count === 'number'
                          ? `${enterprise.member_count} 名成员`
                          : '成员数未知'}
                        {' · '}
                        {typeof enterprise.document_count === 'number'
                          ? `${enterprise.document_count} 份资料`
                          : '资料数未知'}
                      </span>
                    </span>
                    {enterprise.role && (
                      <Badge tone="info" subtle>
                        {enterprise.role}
                      </Badge>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}

          <form className="ent-create" onSubmit={(event) => void createTeam(event)}>
            <label className="form-field">
              <span>新建企业</span>
              <input
                className="input input--sm"
                value={newName}
                maxLength={80}
                placeholder="企业名称"
                disabled={!canWrite}
                onChange={(event) => setNewName(event.target.value)}
              />
            </label>
            <Button
              type="submit"
              size="sm"
              variant="primary"
              icon={<Plus size={13} />}
              loading={creatingEnterprise}
              disabled={!canWrite || !newName.trim()}
              title={!canWrite ? '只读成员不能创建企业' : undefined}
            >
              创建
            </Button>
          </form>
        </aside>

        <div className="ent-main">
          {selected ? (
            <>
              <section className="panel ent-panel" aria-labelledby="ent-members-heading">
                <div className="panel-head">
                  <div>
                    <div className="eyebrow">成员管理</div>
                    <h3 id="ent-members-heading">{selected.name} · 成员</h3>
                  </div>
                  <span className="subtle">{members.length} 人</span>
                </div>

                {membersQuery.loading && !membersQuery.data ? (
                  <div className="ent-panel-body" aria-busy="true">
                    <Skeleton height={38} />
                    <Skeleton height={38} />
                  </div>
                ) : membersQuery.error ? (
                  <div className="ent-panel-body">
                    <ErrorState
                      compact
                      message={membersQuery.error}
                      onRetry={membersQuery.refresh}
                    />
                  </div>
                ) : members.length === 0 ? (
                  <div className="ent-panel-body">
                    <EmptyState
                      compact
                      icon={Users}
                      title="还没有成员"
                      description="按用户名添加企业成员，共同维护共享资料。"
                    />
                  </div>
                ) : (
                  <ul className="ent-member-list">
                    {members.map((member) => (
                      <li key={member.user_id} className="ent-member">
                        <div>
                          <strong>{member.display_name || member.username || '未命名成员'}</strong>
                          {member.username && (
                            <span className="mono ent-member-username">@{member.username}</span>
                          )}
                        </div>
                        <div className="ent-member-actions">
                          {member.role && (
                            <Badge tone="neutral" subtle>
                              {member.role}
                            </Badge>
                          )}
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={<Trash2 size={13} />}
                            disabled={!canWrite}
                            onClick={() =>
                              void removeMember(member.user_id, member.display_name || member.username)
                            }
                          >
                            移除
                          </Button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}

                <form className="ent-inline-form" onSubmit={(event) => void addMember(event)}>
                  <input
                    className="input input--sm"
                    aria-label="成员用户名"
                    value={memberName}
                    placeholder="成员用户名"
                    disabled={!canWrite}
                    onChange={(event) => setMemberName(event.target.value)}
                  />
                  <Button
                    type="submit"
                    size="sm"
                    variant="secondary"
                    icon={<UserPlus size={13} />}
                    loading={addingMember}
                    disabled={!canWrite || !memberName.trim()}
                  >
                    添加成员
                  </Button>
                </form>
              </section>

              <section className="panel ent-panel" aria-labelledby="ent-docs-heading">
                <div className="panel-head">
                  <div>
                    <div className="eyebrow">共享资料</div>
                    <h3 id="ent-docs-heading">资料库</h3>
                  </div>
                  <span className="subtle">{documents.length} 份</span>
                </div>

                {documentsQuery.loading && !documentsQuery.data ? (
                  <div className="ent-panel-body" aria-busy="true">
                    <Skeleton height={58} />
                    <Skeleton height={58} />
                  </div>
                ) : documentsQuery.error ? (
                  <div className="ent-panel-body">
                    <ErrorState
                      compact
                      message={documentsQuery.error}
                      onRetry={documentsQuery.refresh}
                    />
                  </div>
                ) : (
                  <div className="ent-panel-body">
                    <EnterpriseAssetList documents={documents} />
                  </div>
                )}

                <form className="ent-doc-form" onSubmit={(event) => void addDocument(event)}>
                  <div className="ent-doc-form-grid">
                    <label className="form-field">
                      <span>资料名称</span>
                      <input
                        className="input input--sm"
                        value={docForm.source}
                        placeholder="例如：安全生产许可证"
                        disabled={!canWrite}
                        onChange={(event) =>
                          setDocForm((current) => ({ ...current, source: event.target.value }))
                        }
                      />
                    </label>
                    <label className="form-field">
                      <span>类型</span>
                      <input
                        className="input input--sm"
                        value={docForm.kind}
                        placeholder="业绩 / 证书 / 人员"
                        disabled={!canWrite}
                        onChange={(event) =>
                          setDocForm((current) => ({ ...current, kind: event.target.value }))
                        }
                      />
                    </label>
                    <label className="form-field">
                      <span>标签</span>
                      <input
                        className="input input--sm"
                        value={docForm.tags}
                        placeholder="用逗号分隔"
                        disabled={!canWrite}
                        onChange={(event) =>
                          setDocForm((current) => ({ ...current, tags: event.target.value }))
                        }
                      />
                    </label>
                    <label className="form-field">
                      <span>有效期至</span>
                      <input
                        className="input input--sm"
                        type="date"
                        value={docForm.valid_to}
                        disabled={!canWrite}
                        onChange={(event) =>
                          setDocForm((current) => ({ ...current, valid_to: event.target.value }))
                        }
                      />
                    </label>
                  </div>
                  <label className="form-field">
                    <span>资料内容摘要</span>
                    <textarea
                      className="textarea"
                      rows={3}
                      value={docForm.content}
                      placeholder="证书编号、业绩规模、人员履历等，用于需求匹配。"
                      disabled={!canWrite}
                      onChange={(event) =>
                        setDocForm((current) => ({ ...current, content: event.target.value }))
                      }
                    />
                  </label>
                  <div className="ent-doc-form-foot">
                    <span className="subtle">
                      <FilePlus2 size={13} /> 入库后可在项目需求匹配中复用。
                    </span>
                    <Button
                      type="submit"
                      size="sm"
                      variant="primary"
                      loading={addingDoc}
                      disabled={!canWrite || !docForm.source.trim()}
                    >
                      资料入库
                    </Button>
                  </div>
                </form>
              </section>

              <KnowledgeAssetPanel
                enterpriseId={selected.id}
                canManage={canManageAssets}
                canReview={canManageAssets}
              />
            </>
          ) : (
            <section className="panel ent-panel">
              <div className="ent-panel-body">
                <EmptyState
                  icon={Building2}
                  title="选择或创建一个企业"
                  description="企业提供共享资料、成员管理与项目资产匹配。"
                />
              </div>
            </section>
          )}

          <section className="panel ent-panel" aria-labelledby="ent-match-heading">
            <div className="panel-head">
              <div>
                <div className="eyebrow">项目资产匹配</div>
                <h3 id="ent-match-heading">按需求匹配企业资料</h3>
              </div>
              <Button
                size="sm"
                variant="secondary"
                icon={<PackageIcon size={13} />}
                onClick={() => void openPackage()}
              >
                查看材料包
              </Button>
            </div>
            <form className="ent-match-form" onSubmit={(event) => void runMatch(event)}>
              <label className="form-field">
                <span>需求描述</span>
                <textarea
                  className="textarea"
                  rows={3}
                  value={requirement}
                  placeholder="粘贴招标文件中的资格或业绩要求，用于匹配企业共享资料。"
                  onChange={(event) => setRequirement(event.target.value)}
                />
              </label>
              <div className="ent-match-foot">
                <label className="form-field ent-match-kind">
                  <span>资料类型（可选）</span>
                  <input
                    className="input input--sm"
                    value={matchKind}
                    placeholder="如 证书 / 业绩"
                    onChange={(event) => setMatchKind(event.target.value)}
                  />
                </label>
                <Button
                  type="submit"
                  variant="primary"
                  icon={<Search size={14} />}
                  loading={matching}
                  disabled={!requirement.trim()}
                >
                  匹配资产
                </Button>
              </div>
            </form>

            <div className="ent-match-results">
              {matchError ? (
                <ErrorState compact message={matchError} />
              ) : matches === null ? (
                <p className="ent-hint">填写需求后匹配，结果仅列出企业资料库中的命中项。</p>
              ) : matches.length === 0 ? (
                <EmptyState
                  compact
                  icon={Search}
                  title="没有匹配结果"
                  description="企业资料库中没有达到阈值的条目，可补充资料后重试。"
                />
              ) : (
                <ul className="ent-match-list">
                  {matches.map((match, index) => (
                    <li key={match.id || `${match.source}-${index}`} className="ent-match">
                      <div className="ent-match-head">
                        <strong>{match.source || '未命名资料'}</strong>
                        {match.kind && (
                          <Badge tone="info" subtle>
                            {match.kind}
                          </Badge>
                        )}
                        {typeof match.score === 'number' && (
                          <span className="mono">匹配度 {match.score}</span>
                        )}
                        {match.valid_to && (
                          <span className="subtle">
                            有效至 {formatDate(match.valid_to, false)}
                          </span>
                        )}
                      </div>
                      {match.excerpt && <p>{match.excerpt}</p>}
                      {match.reason && <p className="subtle">{match.reason}</p>}
                      {match.tags && match.tags.length > 0 && (
                        <div className="ent-asset-tags">
                          {match.tags.map((tag) => (
                            <span key={tag} className="ent-asset-tag">
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </div>
      </div>

      <Modal
        open={packageOpen}
        onClose={() => setPackageOpen(false)}
        size="md"
        eyebrow="企业材料包"
        title={materialPackage?.name || '项目材料包'}
        footer={
          <Button variant="secondary" onClick={() => setPackageOpen(false)}>
            关闭
          </Button>
        }
      >
        <div className="ent-package-body">
          {packageLoading ? (
            <div className="ent-package-loading" aria-busy="true">
              <Spinner size={18} />
              <span>正在整理材料包…</span>
            </div>
          ) : packageError ? (
            <ErrorState message={packageError} onRetry={() => void openPackage()} />
          ) : !materialPackage ? (
            <p className="ent-hint">尚未生成材料包。</p>
          ) : packageItems.length === 0 && packageDocuments.length === 0 ? (
            <EmptyState
              compact
              icon={PackageIcon}
              title="材料包为空"
              description="后端未返回条目。请先匹配企业资产并确认资料已入库。"
            />
          ) : (
            <>
              {materialPackage.generated_at && (
                <p className="subtle">
                  生成于 {formatRelative(materialPackage.generated_at)}
                </p>
              )}
              {packageItems.length > 0 && (
                <ul className="ent-package-list">
                  {packageItems.map((item, index) => (
                    <li key={item.id || `${item.label}-${index}`}>
                      <strong>{item.label || item.title || item.source || '条目'}</strong>
                      {item.kind && <span className="subtle"> · {item.kind}</span>}
                      {typeof item.count === 'number' && (
                        <span className="mono"> ×{item.count}</span>
                      )}
                      {item.note && <p>{item.note}</p>}
                    </li>
                  ))}
                </ul>
              )}
              {packageDocuments.length > 0 && (
                <EnterpriseAssetList documents={packageDocuments} />
              )}
            </>
          )}
        </div>
      </Modal>
    </div>
  )
}

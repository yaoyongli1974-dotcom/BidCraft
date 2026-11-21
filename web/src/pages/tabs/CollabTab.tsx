import { isProjectAdmin } from '../../lib/permissions'
import { useState, type FormEvent } from 'react'
import {
  History,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserPlus,
  Users,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import { getProjectAudit } from '../../api/jobs'
import { addMember, getMembers, removeMember, updateMember } from '../../api/project'
import { messageOf } from '../../lib/http'
import type { Member, MemberRole } from '../../lib/types'
import {
  actionLabel,
  auditActionTone,
  auditActor,
  auditDetailText,
  className,
  formatDate,
  formatNumber,
  memberRoleMeta,
  MEMBER_ROLE_HINTS,
} from '../../lib/format'
import { useAuditFeed, useQuery } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button, IconButton } from '../../components/Button'
import { EmptyState, ErrorState, Skeleton } from '../../components/Feedback'
import { TabHeading } from '../../components/Workbench'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'

const MEMBER_ROLE_OPTIONS: MemberRole[] = ['editor', 'viewer']

export function CollabTab() {
  const { ref, project } = useWorkspace()
  const confirm = useConfirm()
  const toast = useToast()
  const canManage = isProjectAdmin(project)

  const membersQuery = useQuery(`members:${ref}`, () => getMembers(ref))
  const audit = useAuditFeed(`audit:${ref}`, (before) =>
    getProjectAudit(ref, { limit: 20, before }),
  )

  const [username, setUsername] = useState('')
  const [role, setRole] = useState<MemberRole>('editor')
  const [adding, setAdding] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const members = membersQuery.data || []

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const name = username.trim()
    if (!canManage || !name || adding) return
    setAdding(true)
    setActionError(null)
    try {
      await addMember(ref, { username: name, role })
      setUsername('')
      setRole('editor')
      membersQuery.refresh()
      audit.reload()
      toast.success('成员已添加', `@${name} 现在可以访问本项目。`)
    } catch (error) {
      const message = messageOf(error)
      setActionError(message)
      toast.error('添加成员失败', message)
    } finally {
      setAdding(false)
    }
  }

  const changeRole = async (member: Member, next: MemberRole) => {
    if (!canManage || busyId || member.role === next) return
    const previousRole = member.role
    // Reflect the choice immediately; the server response below confirms it.
    membersQuery.setData((current) =>
      current?.map((item) =>
        item.user_id === member.user_id ? { ...item, role: next } : item,
      ),
    )
    setBusyId(member.user_id)
    setActionError(null)
    try {
      await updateMember(ref, member.user_id, { role: next })
      membersQuery.refresh()
      audit.reload()
      toast.success(
        '成员权限已更新',
        `${member.display_name || member.username} · ${memberRoleMeta(next).label}`,
      )
    } catch (error) {
      membersQuery.setData((current) =>
        current?.map((item) =>
          item.user_id === member.user_id ? { ...item, role: previousRole } : item,
        ),
      )
      const message = messageOf(error)
      setActionError(message)
      toast.error('更新成员权限失败', message)
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (member: Member) => {
    if (!canManage || busyId) return
    const accepted = await confirm({
      title: '移除这个成员？',
      description: (
        <>
          <strong>{member.display_name || member.username}</strong>（@
          {member.username}）将不能继续访问本项目。
        </>
      ),
      note: '成员的项目操作记录仍保留在审计日志中，可再次按用户名添加。',
      confirmLabel: '移除成员',
      tone: 'warning',
    })
    if (!accepted) return
    setBusyId(member.user_id)
    setActionError(null)
    try {
      await removeMember(ref, member.user_id)
      membersQuery.refresh()
      audit.reload()
      toast.success('成员已移除', member.display_name || member.username)
    } catch (error) {
      const message = messageOf(error)
      setActionError(message)
      toast.error('移除成员失败', message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="tab-content collab-tab">
      <TabHeading
        eyebrow="项目协作"
        title="成员与操作记录"
        description="邀请团队成员一起写作，并按时间回看每一次上传、编辑、生成与权限变更。"
        actions={
          <Button
            variant="ghost"
            size="sm"
            icon={<RefreshCw size={14} />}
            onClick={() => {
              membersQuery.refresh()
              audit.reload()
            }}
            disabled={membersQuery.refreshing || audit.loading}
          >
            刷新
          </Button>
        }
      />

      {actionError && (
        <div className="inline-error" role="alert">
          <p>{actionError}</p>
        </div>
      )}

      <div className="collab-layout">
        <div className="collab-column">
          <section className="panel collab-card" aria-labelledby="collab-members">
            <div className="panel-head">
              <div>
                <div className="eyebrow">项目成员</div>
                <h3 id="collab-members">谁可以一起写</h3>
                <p className="panel-sub">
                  {canManage
                    ? '按用户名添加已注册成员，并设置可编辑或只读权限。'
                    : '只有项目负责人和超级管理员可以添加、调整或移除成员。'}
                </p>
              </div>
              <Badge tone="neutral" subtle>
                <Users size={11} /> {members.length} 人
              </Badge>
            </div>

            {canManage && (
              <form className="member-add" onSubmit={(event) => void submit(event)}>
                <label className="member-add-field">
                  <span className="sr-only">成员用户名</span>
                  <input
                    className="input input--sm"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    placeholder="输入已注册用户的用户名"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </label>
                <label className="member-add-role">
                  <span className="sr-only">项目角色</span>
                  <select
                    className="select select--sm"
                    value={role}
                    onChange={(event) => setRole(event.target.value as MemberRole)}
                  >
                    {MEMBER_ROLE_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {memberRoleMeta(option).label}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  type="submit"
                  variant="secondary"
                  size="sm"
                  icon={<UserPlus size={14} />}
                  loading={adding}
                  disabled={!username.trim()}
                >
                  添加成员
                </Button>
              </form>
            )}

            {membersQuery.error && members.length > 0 && (
              <p className="collab-inline-error" role="status">
                {membersQuery.error}
              </p>
            )}

            {membersQuery.error && !members.length ? (
              <div className="collab-card-body">
                <ErrorState message={membersQuery.error} onRetry={membersQuery.refresh} />
              </div>
            ) : membersQuery.loading && !members.length ? (
              <div className="collab-loading" aria-busy="true">
                {Array.from({ length: 3 }).map((_, index) => (
                  <div key={index} className="collab-skeleton-row">
                    <Skeleton width={34} height={34} radius={7} />
                    <div>
                      <Skeleton width="52%" height={13} />
                      <Skeleton width="34%" height={10} />
                    </div>
                    <Skeleton width={74} height={22} radius={999} />
                  </div>
                ))}
              </div>
            ) : members.length === 0 ? (
              <EmptyState
                icon={Users}
                compact
                title="还没有项目成员"
                description={
                  canManage
                    ? '添加团队成员后，他们就能在这里一起编辑正文与台账。'
                    : '项目负责人还没有邀请其他成员加入。'
                }
              />
            ) : (
              <ul className="member-list">
                {members.map((member) => {
                  const meta = memberRoleMeta(member.role)
                  const isOwner = member.role === 'owner'
                  const busy = busyId === member.user_id
                  return (
                    <li key={member.user_id} className="member-row">
                      <span className="member-avatar" aria-hidden>
                        {(member.display_name || member.username).trim().slice(0, 1) || '员'}
                      </span>
                      <div className="member-copy">
                        <span className="member-name">
                          {member.display_name || member.username}
                        </span>
                        <span className="member-handle mono">
                          @{member.username}
                          {member.created_at
                            ? ` · ${formatDate(member.created_at, false)} 加入`
                            : ''}
                        </span>
                      </div>
                      <div className="member-role">
                        {isOwner ? (
                          <Badge tone={meta.tone} subtle>
                            <ShieldCheck size={11} /> {meta.label}
                          </Badge>
                        ) : canManage ? (
                          <select
                            className="select select--sm member-role-select"
                            value={member.role === 'editor' ? 'editor' : 'viewer'}
                            disabled={Boolean(busyId)}
                            aria-label={`调整 ${member.display_name || member.username} 的项目权限`}
                            onChange={(event) =>
                              void changeRole(member, event.target.value as MemberRole)
                            }
                          >
                            {MEMBER_ROLE_OPTIONS.map((option) => (
                              <option key={option} value={option}>
                                {memberRoleMeta(option).label}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <Badge tone={meta.tone} subtle>
                            {meta.label}
                          </Badge>
                        )}
                        <span className="member-role-hint">
                          {busy ? '保存中…' : MEMBER_ROLE_HINTS[member.role] || ''}
                        </span>
                      </div>
                      {canManage && (
                        <div className="member-actions">
                          {!isOwner && (
                            <IconButton
                              label={`移除成员 ${member.display_name || member.username}`}
                              tone="danger"
                              disabled={Boolean(busyId)}
                              onClick={() => void remove(member)}
                            >
                              <Trash2 size={14} />
                            </IconButton>
                          )}
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </section>

          <section className="panel collab-note" aria-label="项目角色说明">
            <div className="collab-note-head">
              <div className="eyebrow">权限说明</div>
              <h3>三种项目角色</h3>
            </div>
            <ul className="collab-role-list">
              <li>
                <Badge tone="accent" subtle>
                  负责人
                </Badge>
                <span>管理成员与删除项目，可编辑全部内容</span>
              </li>
              <li>
                <Badge tone="info" subtle>
                  可编辑
                </Badge>
                <span>上传、编辑、生成、核对与导出</span>
              </li>
              <li>
                <Badge tone="neutral" subtle>
                  只读
                </Badge>
                <span>查看项目内容，不能进行写操作</span>
              </li>
            </ul>
            <p className="collab-note-foot">
              超级管理员拥有全部项目的管理员权限；全局只读成员始终不能写入。
            </p>
          </section>
        </div>

        <section className="panel collab-card" aria-labelledby="collab-audit">
          <div className="panel-head">
            <div>
              <div className="eyebrow">操作审计</div>
              <h3 id="collab-audit">项目操作记录</h3>
              <p className="panel-sub">
                按时间倒序记录上传、编辑、生成、核对与成员变更，仅本机保存。
              </p>
            </div>
            <Badge tone="neutral" subtle>
              <History size={11} /> {formatNumber(audit.items.length)} 条
            </Badge>
          </div>

          {audit.error && !audit.items.length ? (
            <div className="collab-card-body">
              <ErrorState message={audit.error} onRetry={audit.reload} />
            </div>
          ) : audit.loading ? (
            <div className="collab-loading" aria-busy="true">
              {Array.from({ length: 4 }).map((_, index) => (
                <div key={index} className="collab-skeleton-row">
                  <Skeleton width={8} height={8} radius={999} />
                  <div>
                    <Skeleton width="46%" height={12} />
                    <Skeleton width="72%" height={10} />
                  </div>
                  <Skeleton width={64} height={10} />
                </div>
              ))}
            </div>
          ) : audit.items.length === 0 ? (
            <EmptyState
              icon={History}
              compact
              title="还没有操作记录"
              description="上传文件、保存正文或调整成员后，这里会出现一条记录。"
            />
          ) : (
            <ol className="audit-list">
              {audit.items.map((entry) => {
                const tone = auditActionTone(entry.action)
                const detail = auditDetailText(entry.detail)
                return (
                  <li key={entry.id} className="audit-item">
                    <span
                      className={className('audit-dot', `audit-dot--${tone}`)}
                      aria-hidden
                    />
                    <div className="audit-main">
                      <div className="audit-line">
                        <strong className="audit-action">
                          {actionLabel(entry.action)}
                        </strong>
                        <span className="audit-user">
                          {auditActor(entry)}
                        </span>
                      </div>
                      {detail && <p className="audit-detail">{detail}</p>}
                    </div>
                    <time
                      className="audit-time mono"
                      dateTime={entry.created_at || undefined}
                      title={formatDate(entry.created_at)}
                    >
                      {formatDate(entry.created_at)}
                    </time>
                  </li>
                )
              })}
            </ol>
          )}

          <div className="audit-foot">
            <span
              className={className('audit-foot-note', audit.error && 'audit-foot-note--error')}
            >
              {audit.error
                ? audit.error
                : audit.loading
                  ? '正在读取…'
                  : audit.items.length
                    ? `已载入 ${formatNumber(audit.items.length)} 条`
                    : '共 0 条'}
            </span>
            {audit.next !== null ? (
              <Button
                variant="ghost"
                size="sm"
                loading={audit.loadingMore}
                disabled={audit.loadingMore}
                onClick={audit.loadMore}
              >
                加载更多
              </Button>
            ) : audit.items.length > 0 ? (
              <span className="audit-foot-note">已显示全部</span>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  )
}
